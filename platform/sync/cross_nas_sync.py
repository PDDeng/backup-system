#!/usr/bin/env python3
"""Cross-NAS three-copy completion tool: rate-limited, add-only, read-only on the source.

Rules enforced here:
- Only a unit with a valid completion receipt is copied. A unit is <asset>/<date> (layout date_dir) or
  <asset>/<date>/<run> (layout date_run). Names starting with "." or ending with ".partial" are never complete.
- Receipts are read once as bytes and decoded with utf-8-sig, so a UTF-8 BOM is accepted. Supported receipt
  formats: core-bundle-1.0, svn-pascal, crm-single, kingdee-list, generic.
- Payload paths are relative. Windows separators are normalised; ".", ".." , absolute paths, drive letters and
  empty components are rejected. Empty directories listed by the receipt are created.
- Files stream from the source with exclusive-create writes on the target and are checked against the receipt
  size and, when present, SHA-256 while copying. The receipt is written last and the unit is published with a
  no-replace rename. An existing target is never overwritten.
- A target that already holds the unit is skipped only after its receipt, every payload file, every directory
  and every size are verified in place. Anything else is a conflict, reported and left alone.
- Nothing is deleted or rewritten on either side. The only file removed is none: the lock files stay.
- Targets must be real mounts: linux-cifs (findmnt exact mountpoint and fstype, not a symlink) or windows-unc.
  A reserve of free space is required before each unit.
- Per-unit and per-target OSErrors are recorded and the batch continues. The result is always written.
- Standard library only.
"""
import argparse
import datetime
import errno
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
import uuid

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None

SCHEMA_VERSION = 1
CHUNK = 1 << 20
DEFAULT_RESERVE = 20 * 1024 ** 3
BURST_SECONDS = 1.0
DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
ID_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,62}$')
ASSET_RE = re.compile(r'^[^\\/:*?"<>|\r\n\x00]+$')
RUN_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]*$')
REPARSE = getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400)
PLACEHOLDER = 'CHANGE_ME'
LAYOUTS = ('date_dir', 'date_run')
FORMATS = ('core-bundle-1.0', 'svn-pascal', 'crm-single', 'kingdee-list', 'generic')
MOUNT_MODES = ('linux-cifs', 'windows-unc')
CORE_SCHEMA = 'core-backup-bundle/1.0'


class SyncError(Exception):
    """A problem with one unit or one target. Recorded; the batch continues."""


class ConfigError(Exception):
    """A configuration problem. The run does not start."""


class Conflict(SyncError):
    """The target holds this unit with different or unverifiable content. Never overwritten."""


class LockBusy(SyncError):
    """Another run holds the lock for this job and target."""


def _walk_strings(value, path='config'):
    if isinstance(value, dict):
        for k, v in value.items():
            _walk_strings(v, path + '.' + str(k))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            _walk_strings(v, '%s[%d]' % (path, i))
    elif isinstance(value, str) and PLACEHOLDER in value:
        raise ConfigError('%s still contains the %s placeholder' % (path, PLACEHOLDER))


def _text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ConfigError('%s must be a non-empty string' % field)
    return value


def _norm(path):
    return os.path.normcase(os.path.abspath(path))


def _inside(child, parent):
    c, p = _norm(child), _norm(parent)
    return c == p or c.startswith(p.rstrip(os.sep) + os.sep)


class RateLimiter(object):
    """Token bucket by bytes: credit accrues at the configured rate and is capped at BURST_SECONDS worth of bytes."""

    def __init__(self, bytes_per_sec, clock=time.monotonic, sleep=time.sleep, burst_seconds=BURST_SECONDS):
        self.rate = float(bytes_per_sec)
        self.clock = clock
        self.sleep = sleep
        self.cap = self.rate * burst_seconds
        self.credit = 0.0
        self.last = None
        self.slept = 0.0

    def consume(self, n):
        now = self.clock()
        if self.last is None:
            self.last = now
        self.credit = min(self.credit + self.rate * (now - self.last), self.cap) - n
        self.last = now
        if self.credit < 0:
            wait = -self.credit / self.rate
            self.slept += wait
            self.sleep(wait)
            self.credit = 0.0
            self.last = self.clock()


def validate_config(data):
    if not isinstance(data, dict) or data.get('schema_version') != SCHEMA_VERSION:
        raise ConfigError('schema_version must be %d' % SCHEMA_VERSION)
    _walk_strings(data)
    jobs = data.get('jobs')
    if not isinstance(jobs, list) or not jobs:
        raise ConfigError('jobs must be a non-empty list')
    state_dir = _text(data.get('state_dir', ''), 'state_dir')
    result_file = data.get('result_file')
    if result_file is not None:
        _text(result_file, 'result_file')
    names = set()
    normalized = []
    for index, job in enumerate(jobs):
        if not isinstance(job, dict):
            raise ConfigError('job %d is not an object' % index)
        name = _text(job.get('name', ''), 'job.name')
        if not ID_RE.match(name):
            raise ConfigError('job.name %r is not a safe identifier' % name)
        if name in names:
            raise ConfigError('duplicate job name: %s' % name)
        names.add(name)
        asset = _text(job.get('asset', ''), name + '.asset')
        if not ASSET_RE.match(asset) or asset in ('.', '..') or asset.endswith('.'):
            raise ConfigError('%s.asset is not a safe directory name' % name)
        layout = job.get('layout', 'date_dir')
        if layout not in LAYOUTS:
            raise ConfigError('%s.layout must be one of %s' % (name, ', '.join(LAYOUTS)))
        fmt = job.get('receipt_format')
        if fmt not in FORMATS:
            raise ConfigError('%s.receipt_format must be one of %s' % (name, ', '.join(FORMATS)))
        if fmt == 'svn-pascal' and layout != 'date_run':
            raise ConfigError('%s: svn-pascal receipts require layout date_run' % name)
        source_root = _text(job.get('source_root', ''), name + '.source_root')
        receipt_name = job.get('receipt_name', 'complete.json')
        if not re.match(r'^[A-Za-z0-9][A-Za-z0-9._-]*$', receipt_name) or receipt_name.endswith('.partial'):
            raise ConfigError('%s.receipt_name is not a safe file name' % name)
        owner = job.get('expected_owner')
        if owner is not None:
            _text(owner, name + '.expected_owner')
        rate = job.get('rate_limit_bytes_per_sec')
        if not isinstance(rate, (int, float)) or isinstance(rate, bool) or rate <= 0:
            raise ConfigError('%s.rate_limit_bytes_per_sec must be a positive number' % name)
        priority = job.get('priority', 100)
        if not isinstance(priority, int) or isinstance(priority, bool):
            raise ConfigError('%s.priority must be an integer' % name)
        targets = job.get('targets')
        if not isinstance(targets, list) or not targets:
            raise ConfigError('%s.targets must be a non-empty list' % name)
        target_names = set()
        roots = []
        norm_targets = []
        for t in targets:
            if not isinstance(t, dict):
                raise ConfigError('%s target is not an object' % name)
            tname = _text(t.get('name', ''), name + '.targets.name')
            if not ID_RE.match(tname):
                raise ConfigError('target name %r is not a safe identifier' % tname)
            if tname in target_names:
                raise ConfigError('%s: duplicate target name %s' % (name, tname))
            target_names.add(tname)
            troot = _text(t.get('root', ''), name + '.targets.root')
            mount = t.get('mount')
            if not isinstance(mount, dict) or mount.get('mode') not in MOUNT_MODES:
                raise ConfigError('%s.%s.mount.mode must be one of %s' % (name, tname, ', '.join(MOUNT_MODES)))
            if mount['mode'] == 'linux-cifs':
                _text(mount.get('source', ''), name + '.' + tname + '.mount.source')
                if not os.path.isabs(troot):
                    raise ConfigError('%s.%s: linux-cifs root must be an absolute path' % (name, tname))
            elif not troot.startswith('\\\\'):
                raise ConfigError('%s.%s: windows-unc root must be a UNC path' % (name, tname))
            reserve = t.get('reserve_bytes', DEFAULT_RESERVE)
            if not isinstance(reserve, int) or isinstance(reserve, bool) or reserve < 0:
                raise ConfigError('%s.%s.reserve_bytes must be a non-negative integer' % (name, tname))
            if _inside(troot, source_root) or _inside(source_root, troot):
                raise ConfigError('%s: target %s overlaps the source' % (name, tname))
            for other in roots:
                if _inside(troot, other) or _inside(other, troot):
                    raise ConfigError('%s: target roots overlap' % name)
            roots.append(troot)
            norm_targets.append({'name': tname, 'root': troot, 'mount': mount, 'reserve_bytes': reserve})
        normalized.append({
            'name': name, 'priority': priority, 'asset': asset, 'layout': layout,
            'source_root': source_root, 'receipt_name': receipt_name, 'receipt_format': fmt,
            'expected_owner': owner, 'rate_limit_bytes_per_sec': float(rate), 'targets': norm_targets,
        })
    normalized.sort(key=lambda j: (j['priority'], j['name']))
    return {'state_dir': state_dir, 'result_file': result_file, 'jobs': normalized}


def load_config(path):
    with open(path, 'rb') as fh:
        data = json.loads(fh.read().decode('utf-8-sig'))
    return validate_config(data)


def safe_rel(name):
    """Returns a '/'-separated relative path, or raises SyncError. Accepts Windows separators."""
    if not isinstance(name, str) or not name or '\x00' in name or ':' in name:
        raise SyncError('unsafe payload path: %r' % (name,))
    norm = name.replace('\\', '/')
    if norm.startswith('/'):
        raise SyncError('absolute payload path rejected: %r' % (name,))
    parts = norm.split('/')
    for part in parts:
        if part in ('', '.', '..') or len(part.encode('utf-8')) > 255:
            raise SyncError('unsafe payload path component: %r' % (name,))
    return '/'.join(parts)


def _size(meta, where):
    size = meta.get('size', meta.get('bytes', meta.get('Bytes')))
    if not isinstance(size, int) or isinstance(size, bool) or size < 0:
        raise SyncError('receipt size invalid for %s' % where)
    return size


def _sha(meta, where):
    sha = meta.get('sha256')
    if sha is None:
        return None
    if not isinstance(sha, str) or not re.match(r'^[0-9a-f]{64}$', sha):
        raise SyncError('receipt sha256 invalid for %s' % where)
    return sha


def _entry(rel, size, sha):
    return {'rel': rel, 'size': size, 'sha256': sha}


def _check_identity(data, unit, expected_owner):
    owner = data.get('owner', data.get('Owner'))
    if expected_owner is not None and owner != expected_owner:
        raise SyncError('receipt owner mismatch')
    expected = {'asset': unit['asset'], 'date': unit['date'], 'run': unit['run']}
    for key, pascal in (('asset', 'Asset'), ('date', 'Date'), ('run', 'Run')):
        val = data.get(key, data.get(pascal))
        if val is None:
            if key == 'run' and unit['run'] is not None:
                raise SyncError('receipt run missing')
            continue
        if expected[key] is None:
            continue
        if val != expected[key]:
            raise SyncError('receipt %s does not match its directory' % key)


def parse_receipt(raw, unit, fmt, expected_owner=None):
    """raw: receipt bytes read once. unit: {'asset','date','run'}. Returns normalized receipt."""
    try:
        data = json.loads(raw.decode('utf-8-sig'))
    except ValueError:
        raise SyncError('receipt is not valid UTF-8 JSON')
    if not isinstance(data, dict):
        raise SyncError('receipt is not an object')
    if fmt == 'core-bundle-1.0':
        if data.get('schema') != CORE_SCHEMA:
            raise SyncError('receipt schema is not %s' % CORE_SCHEMA)
        _check_identity(data, unit, expected_owner)
        files = data.get('files')
        if not isinstance(files, dict) or not files:
            raise SyncError('receipt files must be a non-empty object')
        entries = [_entry(safe_rel(n), _size(m, n), _sha(m, n)) for n, m in sorted(files.items())
                   if isinstance(m, dict) or _raise('receipt file entry is not an object', n)]
        dirs = []
    elif fmt == 'svn-pascal':
        if data.get('Status') != 'hotcopy_verified':
            raise SyncError('receipt Status is not hotcopy_verified')
        if data.get('SmallTest') is True:
            raise SyncError('receipt is a SmallTest run; not a production copy')
        if unit['run'] is None:
            raise SyncError('svn-pascal requires a run directory')
        _check_identity(data, unit, expected_owner)
        files = data.get('Files')
        if not isinstance(files, list) or not files:
            raise SyncError('receipt Files must be a non-empty list')
        entries = []
        for f in files:
            if not isinstance(f, dict):
                raise SyncError('receipt Files entry is not an object')
            entries.append(_entry(safe_rel(f.get('Name')), _size(f, f.get('Name')), _sha(f, f.get('Name'))))
        dirs_raw = data.get('Directories', [])
        if not isinstance(dirs_raw, list):
            raise SyncError('receipt Directories must be a list')
        dirs = [safe_rel(d) for d in dirs_raw]
    elif fmt == 'crm-single':
        _check_identity(data, unit, expected_owner)
        entries = [_entry(safe_rel(data.get('file')), _size(data, 'file'), _sha(data, 'file'))]
        dirs = []
    elif fmt == 'kingdee-list':
        _check_identity(data, unit, expected_owner)
        files = data.get('files')
        if not isinstance(files, list) or not files:
            raise SyncError('receipt files must be a non-empty list')
        entries = []
        for f in files:
            if not isinstance(f, dict):
                raise SyncError('receipt files entry is not an object')
            entries.append(_entry(safe_rel(f.get('name')), _size(f, f.get('name')), _sha(f, f.get('name'))))
        dirs = []
    else:
        _check_identity(data, unit, expected_owner)
        files = data.get('files')
        if isinstance(files, dict):
            pairs = sorted(files.items())
        elif isinstance(files, list):
            pairs = [(f.get('name'), f) for f in files if isinstance(f, dict)]
        else:
            raise SyncError('receipt files must be a list or object')
        if not pairs:
            raise SyncError('receipt lists no files')
        entries = [_entry(safe_rel(n), _size(m, n), _sha(m, n)) for n, m in pairs]
        dirs = []
    rels = [e['rel'] for e in entries]
    if len(set(rels)) != len(rels):
        raise SyncError('duplicate payload path in receipt')
    return {'asset': unit['asset'], 'date': unit['date'], 'run': unit['run'], 'files': entries,
            'dirs': dirs, 'raw': raw}


def _raise(message, _):
    raise SyncError(message)


def same_manifest(a, b):
    def key(r):
        return (sorted((f['rel'], f['size'], f['sha256']) for f in r['files']), sorted(r['dirs']))
    return key(a) == key(b)


def _parts(rel):
    return rel.split('/')


def verify_existing(final, rec, receipt_name, fmt, expected_owner):
    """Verifies an existing target unit in place. Returns True only if it is an identical complete unit."""
    if os.path.islink(final):
        raise Conflict('existing target is a link')
    receipt_path = os.path.join(final, receipt_name)
    if not os.path.isfile(receipt_path) or os.path.islink(receipt_path):
        raise Conflict('existing target has no valid receipt')
    with open(receipt_path, 'rb') as fh:
        existing = parse_receipt(fh.read(), rec, fmt, expected_owner)
    if not same_manifest(existing, rec):
        raise Conflict('existing target manifest differs')
    for d in rec['dirs']:
        p = os.path.join(final, *_parts(d))
        if not os.path.isdir(p) or os.path.islink(p):
            raise Conflict('existing target directory missing: %s' % d)
    for f in rec['files']:
        p = os.path.join(final, *_parts(f['rel']))
        if not os.path.isfile(p) or os.path.islink(p):
            raise Conflict('existing target payload missing or not a regular file: %s' % f['rel'])
        if os.path.getsize(p) != f['size']:
            raise Conflict('existing target payload size mismatch: %s' % f['rel'])
    return True


def publish_no_replace(src, dst):
    """Renames src to dst only if dst does not exist. Raises FileExistsError otherwise. Fails closed."""
    if os.name == 'nt':
        if os.path.lexists(dst):
            raise FileExistsError(errno.EEXIST, 'destination exists', dst)
        os.rename(src, dst)
        return
    import ctypes
    lib = ctypes.CDLL(None, use_errno=True)
    try:
        fn = lib.renameat2
    except AttributeError:
        raise SyncError('renameat2 unavailable; refusing non-atomic publication')
    fn.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    if fn(-100, os.fsencode(src), -100, os.fsencode(dst), 1) != 0:
        err = ctypes.get_errno()
        if err == errno.EEXIST:
            raise FileExistsError(err, 'destination exists', dst)
        raise OSError(err, 'no-replace publication failed', dst)


def copy_file(src, dst, size, sha256, limiter):
    digest = hashlib.sha256() if sha256 else None
    written = 0
    with open(src, 'rb') as fi:
        with open(dst, 'xb') as fo:
            while True:
                chunk = fi.read(CHUNK)
                if not chunk:
                    break
                fo.write(chunk)
                written += len(chunk)
                if digest is not None:
                    digest.update(chunk)
                limiter.consume(len(chunk))
            fo.flush()
            os.fsync(fo.fileno())
    if written != size or os.path.getsize(dst) != size:
        raise SyncError('size mismatch copying %s: expected %d got %d' % (os.path.basename(src), size, written))
    if digest is not None and digest.hexdigest() != sha256:
        raise SyncError('sha256 mismatch copying %s' % os.path.basename(src))


class Guard(object):
    """Target safety: mount verification and free-space reserve. Injectable for tests."""

    def __init__(self, findmnt=None, disk_free=None):
        self.findmnt = findmnt or _findmnt
        self.disk_free = disk_free or (lambda p: shutil.disk_usage(p).free)

    def check_target(self, target):
        root = target['root']
        mount = target['mount']
        if os.path.islink(root):
            raise SyncError('target root is a link: %s' % target['name'])
        if mount['mode'] == 'linux-cifs':
            info = self.findmnt(root)
            if info is None or _norm(info.get('target', '')) != _norm(root):
                raise SyncError('target is not an exact mountpoint: %s' % target['name'])
            if info.get('fstype') not in ('cifs', 'smb3') or info.get('source') != mount['source']:
                raise SyncError('unexpected mount for %s' % target['name'])
        elif not os.path.isdir(root):
            raise SyncError('target root missing or not reachable: %s' % target['name'])
        if not os.path.isdir(root):
            raise SyncError('target root missing or not reachable: %s' % target['name'])

    def check_space(self, target, needed):
        free = self.disk_free(target['root'])
        if free < needed + target['reserve_bytes']:
            raise SyncError('free space %d below need %d plus reserve %d on %s'
                            % (free, needed, target['reserve_bytes'], target['name']))


def _findmnt(path):
    try:
        out = subprocess.check_output(['findmnt', '-J', '-M', path, '-o', 'SOURCE,FSTYPE,TARGET'], timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    fs = json.loads(out.decode('utf-8')).get('filesystems') or []
    if not fs:
        return None
    return {'source': fs[0].get('source'), 'fstype': fs[0].get('fstype'), 'target': fs[0].get('target')}


def list_units(job, source_asset_dir):
    """Returns (units, incomplete, errors). units: list of {'date','run','dir'}."""
    units, incomplete, errors = [], [], []
    if not os.path.isdir(source_asset_dir):
        raise SyncError('source asset directory missing')
    for date in sorted(os.listdir(source_asset_dir)):
        if not DATE_RE.match(date):
            continue
        date_dir = os.path.join(source_asset_dir, date)
        if os.path.islink(date_dir) or not os.path.isdir(date_dir):
            errors.append('%s: not a plain directory' % date)
            continue
        if job['layout'] == 'date_dir':
            units.append({'date': date, 'run': None, 'dir': date_dir})
            continue
        for run in sorted(os.listdir(date_dir)):
            if run.startswith('.') or run.endswith('.partial'):
                continue
            run_dir = os.path.join(date_dir, run)
            if not RUN_RE.match(run):
                errors.append('%s/%s: unexpected run name' % (date, run))
                continue
            if os.path.islink(run_dir) or not os.path.isdir(run_dir):
                errors.append('%s/%s: not a plain directory' % (date, run))
                continue
            units.append({'date': date, 'run': run, 'dir': run_dir})
    return units, incomplete, errors


def read_source_unit(job, unit):
    """Returns (receipt or None). Raises SyncError when a receipt exists but is invalid."""
    path = os.path.join(unit['dir'], job['receipt_name'])
    if not os.path.lexists(path):
        return None
    if os.path.islink(path):
        raise SyncError('source receipt is a link')
    with open(path, 'rb') as fh:
        raw = fh.read()
    ident = {'asset': job['asset'], 'date': unit['date'], 'run': unit['run']}
    return parse_receipt(raw, ident, job['receipt_format'], job['expected_owner'])


def source_payload(unit, rec):
    base = unit['dir']
    for f in rec['files']:
        p = os.path.join(base, *_parts(f['rel']))
        if os.path.islink(p) or not os.path.isfile(p):
            raise SyncError('source payload missing or not a regular file: %s' % f['rel'])
        if os.path.getsize(p) != f['size']:
            raise SyncError('source size differs from receipt, possibly still being written: %s' % f['rel'])


def copy_unit(job, target, unit, rec, dry_run, limiter, guard):
    unit_name = unit['date'] if unit['run'] is None else unit['date'] + '/' + unit['run']
    asset_dir = os.path.join(target['root'], job['asset'])
    if unit['run'] is None:
        container = asset_dir
        final = os.path.join(asset_dir, unit['date'])
    else:
        container = os.path.join(asset_dir, unit['date'])
        final = os.path.join(container, unit['run'])
    if os.path.lexists(final):
        verify_existing(final, rec, job['receipt_name'], job['receipt_format'], job['expected_owner'])
        return 'skipped'
    source_payload(unit, rec)
    if dry_run:
        return 'planned'
    guard.check_space(target, sum(f['size'] for f in rec['files']))
    os.makedirs(container, exist_ok=True)
    part = os.path.join(container, '.%s.%s.partial' % (unit['run'] or unit['date'], uuid.uuid4().hex[:12]))
    os.mkdir(part)
    try:
        for d in rec['dirs']:
            os.makedirs(os.path.join(part, *_parts(d)), exist_ok=True)
        for f in rec['files']:
            dst = os.path.join(part, *_parts(f['rel']))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            copy_file(os.path.join(unit['dir'], *_parts(f['rel'])), dst, f['size'], f['sha256'], limiter)
        with open(os.path.join(part, job['receipt_name']), 'xb') as fh:
            fh.write(rec['raw'])
            fh.flush()
            os.fsync(fh.fileno())
        verify_existing(part, rec, job['receipt_name'], job['receipt_format'], job['expected_owner'])
        publish_no_replace(part, final)
    except FileExistsError:
        raise Conflict('target appeared during copy of %s; left %s for inspection'
                       % (unit_name, os.path.basename(part)))
    return 'copied'


class RunLock(object):
    """Per job+target lock. POSIX: flock held for the run (released by the OS on crash). Windows: O_EXCL file."""

    def __init__(self, state_dir, job_name, target_name):
        if not ID_RE.match(job_name) or not ID_RE.match(target_name):
            raise SyncError('unsafe lock identifier')
        self.path = os.path.join(state_dir, '%s.%s.lock' % (job_name, target_name))
        self.fd = None

    def acquire(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        meta = json.dumps({'pid': os.getpid(), 'started': datetime.datetime.now().isoformat()}).encode('utf-8')
        if fcntl is not None:
            fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except (IOError, OSError):
                os.close(fd)
                raise LockBusy('another run holds %s' % os.path.basename(self.path))
            os.ftruncate(fd, 0)
            os.write(fd, meta)
            self.fd = fd
            return
        try:
            fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
        except OSError:
            raise LockBusy('another run holds %s (stale lock file? check manually)' % os.path.basename(self.path))
        os.write(fd, meta)
        self.fd = fd

    def release(self):
        if self.fd is None:
            return
        try:
            if fcntl is not None:
                fcntl.flock(self.fd, fcntl.LOCK_UN)
            os.close(self.fd)
        finally:
            self.fd = None
        if fcntl is None:
            os.remove(self.path)


def run_target(job, target, state_dir, dry_run, clock, sleep, guard):
    report = {'job': job['name'], 'target': target['name'], 'copied': [], 'planned': [], 'skipped': [],
              'errors': [], 'bytes': 0, 'throttle_seconds': 0.0}
    lock = RunLock(state_dir, job['name'], target['name'])
    try:
        lock.acquire()
    except LockBusy as exc:
        report['errors'].append(str(exc))
        return report
    try:
        guard.check_target(target)
        units, incomplete, errors = list_units(job, os.path.join(job['source_root'], job['asset']))
        report['errors'].extend(errors)
        report['incomplete_source_units'] = incomplete
        limiter = RateLimiter(job['rate_limit_bytes_per_sec'], clock, sleep)
        for unit in sorted(units, key=lambda u: (u['date'], u['run'] or ''), reverse=True):
            label = unit['date'] if unit['run'] is None else unit['date'] + '/' + unit['run']
            try:
                rec = read_source_unit(job, unit)
                if rec is None:
                    incomplete.append(label)
                    continue
                status = copy_unit(job, target, unit, rec, dry_run, limiter, guard)
            except (SyncError, OSError) as exc:
                report['errors'].append('%s: %s' % (label, exc))
                continue
            if status == 'copied':
                report['copied'].append(label)
                report['bytes'] += sum(f['size'] for f in rec['files'])
            elif status == 'planned':
                report['planned'].append(label)
            else:
                report['skipped'].append(label)
        report['throttle_seconds'] = round(limiter.slept, 3)
    except (SyncError, OSError) as exc:
        report['errors'].append(str(exc))
    finally:
        lock.release()
    return report


def run(config, dry_run=False, clock=time.monotonic, sleep=time.sleep, guard=None):
    started = datetime.datetime.now().isoformat()
    guard = guard or Guard()
    reports = []
    fatal = None
    try:
        os.makedirs(config['state_dir'], exist_ok=True)
        for job in config['jobs']:
            for target in job['targets']:
                try:
                    reports.append(run_target(job, target, config['state_dir'], dry_run, clock, sleep, guard))
                except Exception as exc:  # one broken target must not stop the batch
                    reports.append({'job': job['name'], 'target': target['name'],
                                    'errors': ['unexpected %s: %s' % (type(exc).__name__, exc)],
                                    'copied': [], 'planned': [], 'skipped': []})
    except (SyncError, OSError) as exc:
        fatal = str(exc)
    errors = sum(len(r['errors']) for r in reports) + (1 if fatal else 0)
    return {'schema_version': SCHEMA_VERSION, 'started': started,
            'finished': datetime.datetime.now().isoformat(), 'dry_run': dry_run,
            'status': 'ok' if errors == 0 else 'errors', 'error_count': errors,
            'fatal': fatal, 'results': reports}


def write_result(path, result):
    tmp = path + '.' + uuid.uuid4().hex[:8] + '.tmp'
    with open(tmp, 'wb') as fh:
        fh.write(json.dumps(result, ensure_ascii=False, indent=2).encode('utf-8'))
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--config', required=True)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--result', help='write JSON result here (overrides config result_file)')
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
    except (ConfigError, ValueError, OSError) as exc:
        sys.stderr.write('config error: %s\n' % exc)
        return 2
    target = args.result or config.get('result_file')
    try:
        result = run(config, dry_run=args.dry_run)
    except Exception as exc:  # the result is always written, even for an unexpected failure
        result = {'schema_version': SCHEMA_VERSION, 'status': 'errors', 'error_count': 1,
                  'fatal': 'unexpected %s: %s' % (type(exc).__name__, exc), 'results': []}
    if target:
        write_result(target, result)
    sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    return 0 if result['status'] == 'ok' else 1


if __name__ == '__main__':
    sys.exit(main())

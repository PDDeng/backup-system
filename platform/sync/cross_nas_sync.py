#!/usr/bin/env python3
"""Cross-NAS three-copy completion tool: rate-limited, read-only on the source, add-only on targets.

Rules enforced here:
- Only a source date directory with a valid completion receipt is copied. Directories whose names do not
  match YYYY-MM-DD, and every ".partial" staging directory, are ignored and never counted as complete.
- Files are streamed from the source with exclusive-create writes on the target; the source is never
  written, renamed or deleted.
- Each file is checked against the receipt size and, when the receipt provides one, its SHA-256 while copying.
- The target date is published with one directory rename after the receipt was copied last. An existing
  target date is never overwritten: identical receipt -> skipped; anything else -> conflict, reported, untouched.
- Nothing is deleted on any side. A failed copy leaves its .partial directory for inspection.
- Targets must exist before writing (an unmounted UNC path fails instead of writing to a local fallback).
- Standard library only.
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import stat
import sys
import time
import uuid

SCHEMA_VERSION = 1
CHUNK = 1 << 20
DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
NAME_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]*$')
ASSET_RE = re.compile(r'^[^\\/:*?"<>|\r\n\x00]+$')
REPARSE = getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400)
PLACEHOLDER = 'CHANGE_ME'


class SyncError(Exception):
    """A problem with one date or one target; reported and the run continues with the next date."""


class ConfigError(Exception):
    """A configuration problem; the run does not start."""


class Conflict(SyncError):
    """The target already holds this date with different or incomplete content. Never overwritten."""


class RateLimiter(object):
    """Caps throughput by sleeping so that cumulative bytes never run ahead of bytes_per_sec."""

    def __init__(self, bytes_per_sec, clock=time.monotonic, sleep=time.sleep):
        self.rate = float(bytes_per_sec)
        self.clock = clock
        self.sleep = sleep
        self.start = None
        self.sent = 0
        self.slept = 0.0

    def consume(self, n):
        now = self.clock()
        if self.start is None:
            self.start = now
        self.sent += n
        ahead = self.sent / self.rate - (now - self.start)
        if ahead > 0:
            self.slept += ahead
            self.sleep(ahead)


def is_link(path):
    st = os.lstat(path)
    return stat.S_ISLNK(st.st_mode) or bool(getattr(st, 'st_file_attributes', 0) & REPARSE)


def _text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ConfigError('%s must be a non-empty string' % field)
    if PLACEHOLDER in value:
        raise ConfigError('%s still contains the %s placeholder' % (field, PLACEHOLDER))
    return value


def _norm(path):
    return os.path.normcase(os.path.abspath(path))


def _inside(child, parent):
    c, p = _norm(child), _norm(parent)
    return c == p or c.startswith(p.rstrip(os.sep) + os.sep)


def validate_config(data):
    if not isinstance(data, dict) or data.get('schema_version') != SCHEMA_VERSION:
        raise ConfigError('schema_version must be %d' % SCHEMA_VERSION)
    jobs = data.get('jobs')
    if not isinstance(jobs, list) or not jobs:
        raise ConfigError('jobs must be a non-empty list')
    state_dir = _text(data.get('state_dir', ''), 'state_dir')
    names = set()
    normalized = []
    for index, job in enumerate(jobs):
        if not isinstance(job, dict):
            raise ConfigError('job %d is not an object' % index)
        name = _text(job.get('name', ''), 'job.name')
        if name in names:
            raise ConfigError('duplicate job name: %s' % name)
        names.add(name)
        asset = _text(job.get('asset', ''), name + '.asset')
        if not ASSET_RE.match(asset) or asset in ('.', '..') or asset.endswith('.'):
            raise ConfigError('%s.asset is not a safe directory name' % name)
        source_root = _text(job.get('source_root', ''), name + '.source_root')
        receipt_name = job.get('receipt_name', 'complete.json')
        if not NAME_RE.match(receipt_name):
            raise ConfigError('%s.receipt_name is not a safe file name' % name)
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
            troot = _text(t.get('root', ''), name + '.targets.root')
            if tname in target_names:
                raise ConfigError('%s: duplicate target name %s' % (name, tname))
            target_names.add(tname)
            if _inside(troot, source_root) or _inside(source_root, troot):
                raise ConfigError('%s: target %s overlaps the source' % (name, tname))
            for other in roots:
                if _inside(troot, other) or _inside(other, troot):
                    raise ConfigError('%s: target roots overlap' % name)
            roots.append(troot)
            norm_targets.append({'name': tname, 'root': troot})
        normalized.append({
            'name': name,
            'priority': priority,
            'asset': asset,
            'source_root': source_root,
            'receipt_name': receipt_name,
            'expected_owner': job.get('expected_owner'),
            'rate_limit_bytes_per_sec': float(rate),
            'targets': norm_targets,
        })
    normalized.sort(key=lambda j: (j['priority'], j['name']))
    return {'state_dir': state_dir, 'result_file': data.get('result_file'), 'jobs': normalized}


def load_config(path):
    with open(path, 'rb') as fh:
        data = json.loads(fh.read().decode('utf-8-sig'))
    return validate_config(data)


def _file_list(receipt):
    """Accepts both receipt shapes used on this platform: files as a dict or as a list."""
    files = receipt.get('files')
    out = []
    if isinstance(files, dict):
        items = [(k, v) for k, v in files.items()]
    elif isinstance(files, list):
        items = [(f.get('name'), f) for f in files if isinstance(f, dict)]
    else:
        raise SyncError('receipt files must be a list or object')
    seen = set()
    for name, meta in items:
        if not isinstance(name, str) or not NAME_RE.match(name) or name.endswith('.partial'):
            raise SyncError('unsafe file name in receipt: %r' % (name,))
        if name in seen:
            raise SyncError('duplicate file in receipt: %s' % name)
        seen.add(name)
        if not isinstance(meta, dict):
            raise SyncError('receipt entry for %s is not an object' % name)
        size = meta.get('size', meta.get('bytes'))
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise SyncError('receipt size invalid for %s' % name)
        sha = meta.get('sha256')
        if sha is not None and not (isinstance(sha, str) and re.match(r'^[0-9a-f]{64}$', sha)):
            raise SyncError('receipt sha256 invalid for %s' % name)
        out.append({'name': name, 'size': size, 'sha256': sha})
    if not out:
        raise SyncError('receipt lists no files')
    return out


def read_receipt(date_dir, date, asset, receipt_name, expected_owner=None):
    """Returns the normalized receipt, None when absent (incomplete), or raises SyncError when invalid."""
    path = os.path.join(date_dir, receipt_name)
    if not os.path.lexists(path):
        return None
    if is_link(path) or is_link(date_dir):
        raise SyncError('receipt or date directory is a link: %s' % date_dir)
    with open(path, 'rb') as fh:
        try:
            data = json.loads(fh.read().decode('utf-8'))
        except ValueError:
            raise SyncError('receipt is not valid JSON: %s' % path)
    if not isinstance(data, dict):
        raise SyncError('receipt is not an object: %s' % path)
    if data.get('date') != date:
        raise SyncError('receipt date does not match directory: %s' % path)
    if data.get('asset') != asset:
        raise SyncError('receipt asset does not match job: %s' % path)
    if expected_owner is not None and data.get('owner') != expected_owner:
        raise SyncError('receipt owner mismatch: %s' % path)
    return {'owner': data.get('owner'), 'date': date, 'asset': asset,
            'files': _file_list(data), 'raw': open_bytes(path)}


def open_bytes(path):
    with open(path, 'rb') as fh:
        return fh.read()


def same_manifest(a, b):
    def key(r):
        return sorted((f['name'], f['size'], f['sha256']) for f in r['files'])
    return key(a) == key(b)


def discover(asset_dir, asset, receipt_name, expected_owner):
    """Returns (complete: {date: receipt}, incomplete: [dates], errors: [str])."""
    complete, incomplete, errors = {}, [], []
    if not os.path.isdir(asset_dir):
        raise SyncError('source asset directory missing: %s' % asset_dir)
    for entry in sorted(os.listdir(asset_dir)):
        if not DATE_RE.match(entry):
            continue
        date_dir = os.path.join(asset_dir, entry)
        if not os.path.isdir(date_dir) or is_link(date_dir):
            errors.append('%s: not a plain directory' % entry)
            continue
        try:
            receipt = read_receipt(date_dir, entry, asset, receipt_name, expected_owner)
        except SyncError as exc:
            errors.append(str(exc))
            continue
        if receipt is None:
            incomplete.append(entry)
        else:
            complete[entry] = receipt
    return complete, incomplete, errors


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


def copy_date(job, target, date, receipt, dry_run, limiter):
    asset_dir = os.path.join(job['source_root'], job['asset'])
    src_dir = os.path.join(asset_dir, date)
    dst_asset = os.path.join(target['root'], job['asset'])
    final = os.path.join(dst_asset, date)
    if os.path.lexists(final):
        existing = read_receipt(final, date, job['asset'], job['receipt_name'])
        if existing is not None and same_manifest(existing, receipt):
            return 'skipped'
        raise Conflict('target already holds %s with different or incomplete content; not overwritten' % date)
    for f in receipt['files']:
        p = os.path.join(src_dir, f['name'])
        if is_link(p) or not os.path.isfile(p):
            raise SyncError('source file is not a regular file: %s' % f['name'])
        if os.path.getsize(p) != f['size']:
            raise SyncError('source size differs from receipt, possibly still being written: %s' % f['name'])
    if dry_run:
        return 'planned'
    part = os.path.join(dst_asset, '.%s.%s.partial' % (date, uuid.uuid4().hex[:12]))
    os.mkdir(part)
    for f in receipt['files']:
        copy_file(os.path.join(src_dir, f['name']), os.path.join(part, f['name']), f['size'], f['sha256'], limiter)
    receipt_tmp = os.path.join(part, job['receipt_name'])
    with open(receipt_tmp, 'xb') as fh:
        fh.write(receipt['raw'])
        fh.flush()
        os.fsync(fh.fileno())
    if os.path.lexists(final):
        raise Conflict('target appeared during copy; left %s for inspection' % os.path.basename(part))
    os.rename(part, final)
    return 'copied'


def _lock_path(state_dir, job, target):
    return os.path.join(state_dir, 'lock-%s-%s.lock' % (job['name'], target['name']))


def run_target(job, target, state_dir, dry_run, clock, sleep):
    report = {'job': job['name'], 'target': target['name'], 'copied': [], 'planned': [], 'skipped': [],
              'errors': [], 'bytes': 0}
    lock = _lock_path(state_dir, job, target)
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
    except OSError:
        report['errors'].append('locked: another run holds %s' % os.path.basename(lock))
        return report
    try:
        if not os.path.isdir(target['root']):
            raise SyncError('target root missing or not mounted: %s' % target['name'])
        complete, incomplete, errors = discover(os.path.join(job['source_root'], job['asset']),
                                                job['asset'], job['receipt_name'], job.get('expected_owner'))
        report['errors'].extend(errors)
        report['incomplete_source_dates'] = incomplete
        if not dry_run:
            os.makedirs(os.path.join(target['root'], job['asset']), exist_ok=True)
        limiter = RateLimiter(job['rate_limit_bytes_per_sec'], clock, sleep)
        for date in sorted(complete, reverse=True):
            try:
                status = copy_date(job, target, date, complete[date], dry_run, limiter)
            except SyncError as exc:
                report['errors'].append('%s: %s' % (date, exc))
                continue
            if status == 'copied':
                report['copied'].append(date)
                report['bytes'] += sum(f['size'] for f in complete[date]['files'])
            elif status == 'planned':
                report['planned'].append(date)
            else:
                report['skipped'].append(date)
        report['throttle_seconds'] = round(limiter.slept, 3)
    except SyncError as exc:
        report['errors'].append(str(exc))
    finally:
        os.remove(lock)
    return report


def run(config, dry_run=False, clock=time.monotonic, sleep=time.sleep):
    started = datetime.datetime.now().isoformat()
    os.makedirs(config['state_dir'], exist_ok=True)
    reports = []
    for job in config['jobs']:
        for target in job['targets']:
            reports.append(run_target(job, target, config['state_dir'], dry_run, clock, sleep))
    errors = sum(len(r['errors']) for r in reports)
    return {'schema_version': SCHEMA_VERSION, 'started': started,
            'finished': datetime.datetime.now().isoformat(), 'dry_run': dry_run,
            'status': 'ok' if errors == 0 else 'errors', 'error_count': errors, 'results': reports}


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
    result = run(config, dry_run=args.dry_run)
    target = args.result or config.get('result_file')
    if target:
        write_result(target, result)
    sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    return 0 if result['status'] == 'ok' else 1


if __name__ == '__main__':
    sys.exit(main())

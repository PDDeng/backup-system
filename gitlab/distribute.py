#!/usr/bin/env python3
"""Ops collector for core-complete markers. No remote writes or source cleanup."""
import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import subprocess
import sys
import time
from md5_verify import digest, source_hash, matches
from smb_upload import publish
from retention_policy import keep_cycles

def log(message):
    print('%s %s' % (dt.datetime.now().astimezone().isoformat(), message), flush=True)

def atomic(path, value):
    tmp = path.with_name(path.name + '.tmp')
    with tmp.open('w', encoding='utf-8') as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)

class Job:
    def __init__(self, c):
        self.c = c
        self.full = c.get('verify_md5', True)
        if type(self.full) is not bool:
            raise ValueError('verify_md5 must be true or false')
        self.state = Path(c['state_dir'])
        self.state.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.ssh = ['ssh', '-i', c['key'], '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
                    '-o', 'UserKnownHostsFile='+c['known_hosts'], '-o', 'ConnectTimeout=15',
                    '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=3',
                    c['user']+'@'+c['host']]

    def remote(self, command, output=None):
        return subprocess.run(self.ssh+[command], check=True, stdout=output or subprocess.PIPE,
                              timeout=self.c.get('transfer_timeout_seconds', 7200)).stdout

    def notice(self, event, text):
        import fcntl
        helper = Path(__file__).with_name('notify.py')
        shared=Path('/var/lib/core-backup-ops/modular-notify')
        shared.mkdir(parents=True,exist_ok=True,mode=0o700)
        with (shared/'send.lock').open('w') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            last=shared/'last-send'
            previous=float(last.read_text()) if last.exists() else 0
            time.sleep(max(0,previous+4-time.time()))
            record={'time':dt.datetime.now().astimezone().isoformat(),'asset':self.c['asset'],'event':event,'text':text}
            try:
                rc=subprocess.run([sys.executable,str(helper),self.c['notify_config'],event,self.c['asset'],text],timeout=60).returncode
            except Exception: rc=1
            record['delivered']=rc==0
            last.write_text(str(time.time()))
            with (shared/'events.jsonl').open('a',encoding='utf-8') as f:
                f.write(json.dumps(record,ensure_ascii=False)+'\n');f.flush();os.fsync(f.fileno())
            if rc:log('NOTIFY_FAILED event='+event+'; persisted to '+str(shared/'events.jsonl'))

    def mount(self, t):
        mount = Path(t['mount'])
        # --mountpoint verifies this exact mount, never a parent/root fallback.
        raw = subprocess.check_output(['findmnt', '-rn', '--mountpoint', str(mount),
                                       '-o', 'SOURCE,FSTYPE'], timeout=15).decode().strip().split()
        # findmnt may append [prefixpath] to CIFS source.
        source = re.sub(r'\\x([0-9a-fA-F]{2})',
                        lambda m: chr(int(m.group(1), 16)), raw[0]).encode('latin1').decode('utf-8') if raw and '\\x' in raw[0] else (raw[0] if raw else '')
        if len(raw) != 2 or raw[1] != 'cifs' or source.split('[')[0] != t['source']:
            raise ValueError('Unexpected CIFS mount '+t['id'])
        if mount.is_symlink():
            raise ValueError('Mount is symlink')
        base = mount / self.c['asset']
        if base.is_symlink():
            raise ValueError('Asset directory is symlink')
        base.mkdir(exist_ok=True)
        return base

    def checked(self, folder):
        if folder.is_symlink() or not folder.is_dir():
            raise ValueError('Unsafe bundle directory')
        receipt = folder/'complete.json'
        if not receipt.exists() and not self.full:
            receipt = folder/'pending.json'
        if receipt.is_symlink():
            raise ValueError('Unsafe receipt')
        r = json.loads(receipt.read_text(encoding='utf-8'))
        if r.get('owner') != 'core-distributor-v1' or r['asset'] != self.c['asset']:
            raise ValueError('Not owned')
        if self.full and r.get('verification', 'md5_verified') != 'md5_verified':
            raise ValueError('Unverified copy cannot count toward retention')
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', r['date']) or folder.parent.name != r['date'] or folder.name != r['run']:
            raise ValueError('Receipt path mismatch')
        if set(p.name for p in folder.iterdir()) != set(r['files']) | {receipt.name}:
            raise ValueError('Unexpected directory contents')
        if not r['files'] or not any(n.endswith('_gitlab_backup.tar') for n in r['files']):
            raise ValueError('Missing archive')
        for name, info in r['files'].items():
            if Path(name).name != name or name in ('.', '..', 'complete.json'):
                raise ValueError('Invalid filename')
            p = folder/name
            if p.is_symlink() or not p.is_file() or p.stat().st_size != info['size'] or (self.full and digest(p) != info['md5']):
                raise ValueError('MD5 mismatch '+str(p))
        return r

    def delivery_present(self, run):
        """Never trust last-result without checking all current mounted target files."""
        date=dt.datetime.strptime(run[:8], '%Y%m%d').date().isoformat()
        for target in self.c['targets']:
            base=self.mount(target)
            folder=base/date/run
            if folder.is_symlink() or not folder.is_dir():return False
            complete=folder/'complete.json';pending=folder/'pending.json'
            record=complete if complete.is_file() else pending
            if record.is_symlink() or not record.is_file():return False
            receipt=json.loads(record.read_text(encoding='utf-8'))
            proof=self.state/'receipts'/target['id']/(run+'.json')
            if not proof.is_file() or json.loads(proof.read_text(encoding='utf-8'))!=receipt:return False
            if receipt.get('owner')!='core-distributor-v1' or receipt.get('asset')!=self.c['asset'] or receipt.get('run')!=run or receipt.get('date')!=date:return False
            if set(p.name for p in folder.iterdir())!=set(receipt.get('files',{}))|{record.name}:return False
            if not receipt.get('files'):return False
            if self.full:
                if record.name!='complete.json':return False
                self.checked(folder)
            else:
                for name,info in receipt['files'].items():
                    if Path(name).name!=name or name in ('.','..'):return False
                    if not matches(folder/name,info,False):return False
        return True

    def clean(self, t, base):
        good = {}
        candidates = []
        for day in sorted(base.iterdir(), reverse=True):
            if day.is_symlink() or not day.is_dir() or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', day.name):
                continue
            for folder in day.iterdir():
                if folder.name.startswith('.') or not ((folder/'complete.json').exists() or (not self.full and (folder/'pending.json').exists())):
                    continue
                try:
                    r = self.checked(folder)
                    proof = self.state/'receipts'/t['id']/(folder.name+'.json')
                    if json.loads(proof.read_text(encoding='utf-8')) != r:
                        raise ValueError('Local ownership receipt mismatch')
                    good.setdefault(r['date'], []).append(folder)
                    candidates.append((r['date'], folder, r))
                except Exception as e:
                    # Corrupt/unknown bundles block cleanup, rather than get silently deleted.
                    raise ValueError('Cleanup blocked: '+str(e))
        keep = keep_cycles(self.c, t)
        dates = sorted(good, reverse=True)
        if len(dates) < keep:
            log('%s cleanup skipped: %d/%d complete dates' % (t['id'], len(dates), keep))
            return 0
        retained = set(dates[:keep])
        deleted = 0
        for date, folder, receipt in candidates:
            if date in retained:
                continue
            self.mount(t)
            self.checked(folder)
            # Durable intent includes full receipt and exact allowlisted filenames.
            audit = {'time':dt.datetime.now().astimezone().isoformat(), 'target':t['id'],
                     'path':str(folder), 'receipt':receipt, 'action':'delete-intent', 'check':'md5' if self.full else 'existence_size'}
            with (self.state/'cleanup-audit.jsonl').open('a', encoding='utf-8') as f:
                f.write(json.dumps(audit, ensure_ascii=False)+'\n'); f.flush(); os.fsync(f.fileno())
            if self.c.get('cleanup_mode', 'plan') != 'delete':
                log('CLEANUP_PLAN '+str(folder)); continue
            # Never recursive-delete: unexpected files cause rmdir failure and remain untouched.
            for name in receipt['files']:
                (folder/name).unlink()
            record = folder/'complete.json'
            if not record.exists(): record = folder/'pending.json'
            record.unlink()
            folder.rmdir()
            deleted += 1
            log('DELETED '+str(folder))
        return deleted

    def execute(self):
        root = self.c['backup_dir'].rstrip('/')
        marker_root = root+'/.core-complete'
        cmd = 'find '+shlex.quote(marker_root)+" -mindepth 2 -maxdepth 2 -type f -name DONE -print"
        markers = self.remote(cmd).decode().splitlines()
        valid = [p for p in markers if re.fullmatch(re.escape(marker_root)+r'/\d{8}T\d{6}-\d+/DONE', p)]
        if not valid:
            raise ValueError('No source completion markers')
        marker = sorted(valid)[-1]
        run = marker.split('/')[-2]
        last = self.state/'last-result.json'
        if last.exists():
            previous = json.loads(last.read_text(encoding='utf-8'))
            mode_ok = not self.full or previous.get('verification', 'md5_verified') == 'md5_verified'
            if mode_ok and previous.get('run') == run and previous.get('targets') == {t['id']:True for t in self.c['targets']}:
                try:present=self.delivery_present(run)
                except Exception as e:
                    present=False;log('DELIVERY_RECHECK_FAILED '+str(e))
                if present:
                    self.notice('SKIP','Current target files rechecked; '+('MD5 verified' if self.full else 'existence/size only; MD5 not rerun'))
                    log('SKIP current target files rechecked: '+('MD5' if self.full else 'existence/size'))
                    for target in self.c['targets']:
                        removed = self.clean(target, self.mount(target))
                        self.notice('SUCCESS', target['id']+' retention checked; check='+('md5' if self.full else 'existence_size')+'; cleanup='+str(removed))
                    return 0
                self.notice('WARNING','Old success record does not match current target files; retry collection, no skip')
                log('RETRY previous success record not sufficient; current targets missing/changed')
        date = dt.datetime.strptime(run[:8], '%Y%m%d').date().isoformat()
        data = self.remote('cat -- '+shlex.quote(marker)).decode()
        if 'COPIED_STATUS=SOURCE_COMPLETE_MD5_PENDING' not in data or 'ASSET='+self.c['asset'] not in data.splitlines():
            raise ValueError('Source marker mismatch')
        rows = self.remote('cat -- '+shlex.quote(marker.rsplit('/', 1)[0]+'/files.tsv')).decode().splitlines()
        files = {}
        for row in rows:
            path, size = row.split('\t')
            # Source script may contain double slash at join points; normalize before validation.
            path = str(PurePosixPath(path))
            name = Path(path).name
            archive = re.fullmatch(r'\d{10}_\d{4}_\d{2}_\d{2}_[A-Za-z0-9._+-]+_gitlab_backup.tar', name)
            expected = root+'/'+name if archive else marker_root+'/'+run+'/'+name
            if path != expected or (not archive and name not in ('gitlab-config.tar.gz', 'version-manifest.txt')):
                raise ValueError('Unexpected source path')
            if name in files or int(size) <= 0:
                raise ValueError('Invalid source manifest')
            files[name] = {'path':path, 'size':int(size)}
        if len(files) != 3 or sum(n.endswith('_gitlab_backup.tar') for n in files) != 1:
            raise ValueError('Incomplete source file set')
        self.notice('START', 'Collect '+run+'; three target results follow')
        stage = self.state/'staging'/run
        stage.mkdir(parents=True, exist_ok=True, mode=0o700)
        need = sum(v['size'] for v in files.values())
        if shutil.disk_usage(stage).free < need + self.c.get('minimum_free_gb', 5)*1024**3:
            raise ValueError('Insufficient ops staging space')
        receipt = {'owner':'core-distributor-v1', 'asset':self.c['asset'], 'run':run, 'date':date,
                   'verification':'md5_verified' if self.full else 'pending_md5', 'files':{}}
        collected_start=time.monotonic()
        self.notice('INFO','Source collection starting; mode='+('full MD5' if self.full else 'speed / size-only'))
        for name, info in files.items():
            started = time.monotonic()
            # Hash completed source at low priority, then compare downloaded bytes.
            expected_hash = source_hash(self.remote,info['path']) if self.full else None
            source_stat=self.remote('stat -c %s:%Y:%i -- '+shlex.quote(info['path'])).decode().strip()
            if int(source_stat.split(':')[0])!=info['size']:raise ValueError('Source size changed since completion')
            local = stage/name
            proof_info = {'size':info['size'],'md5':expected_hash}
            if not self.full or not matches(local,proof_info,True):
                tmp = stage/(name+'.partial')
                with tmp.open('wb') as f:
                    self.remote('cat -- '+shlex.quote(info['path']), output=f)
                    f.flush(); os.fsync(f.fileno())
                if not matches(tmp,proof_info,self.full):
                    raise ValueError('Source/local mismatch '+name)
                os.replace(tmp, local)
            if self.remote('stat -c %s:%Y:%i -- '+shlex.quote(info['path'])).decode().strip()!=source_stat:
                raise ValueError('Source file changed during collection')
            receipt['files'][name] = {'size':info['size'], 'md5':expected_hash}
            log('COLLECTED %s seconds=%.1f' % (name, time.monotonic()-started))
        self.notice('SUCCESS','Source collection finished; bytes=%d; seconds=%.1f; status=%s'%(need,time.monotonic()-collected_start,receipt['verification']))
        results = []
        for t in self.c['targets']:
            started = time.monotonic()
            try:
                base = self.mount(t)
                day = base/date
                if day.is_symlink(): raise ValueError('Date symlink')
                day.mkdir(exist_ok=True)
                self.notice('INFO',t['id']+' upload/verify starting')
                actual=publish(stage,day,run,receipt,self.full,lambda:self.mount(t),atomic,self.notice)
                proofs = self.state/'receipts'/t['id']
                proofs.mkdir(parents=True, exist_ok=True)
                atomic(proofs/(run+'.json'), actual)
                removed = self.clean(t, base)
                verification=actual.get('verification','md5_verified')
                message = '%s OK %s; cleanup=%d%s; seconds=%.1f' % (t['id'], verification, removed,
                          ' (retention check=md5)' if self.full else ' (retention check=existence_size; MD5 pending)',time.monotonic()-started)
                results.append(True)
                self.notice('SUCCESS', message)
                log(message)
            except Exception as e:
                results.append(False)
                log('%s FAILED %s' % (t['id'], e))
                self.notice('FAILURE', t['id']+' failed; inspect ops distribute.log; other targets continue')
        atomic(self.state/'last-result.json', {'run':run,'verification':receipt['verification'],
               'targets':dict(zip([t['id'] for t in self.c['targets']], results))})
        if all(results):
            for name in receipt['files']:
                (stage/name).unlink()
            # Unexpected files are retained, not recursively deleted.
            if not any(stage.iterdir()): stage.rmdir()
        return 0 if all(results) else 1

def main():
    os.umask(0o077)
    p = argparse.ArgumentParser()
    p.add_argument('--config', required=True)
    p.add_argument('--check', action='store_true')
    p.add_argument('--verify-md5',choices=('on','off'))
    a = p.parse_args()
    c = json.loads(Path(a.config).read_text(encoding='utf-8-sig'))
    if a.verify_md5: c['verify_md5']=a.verify_md5=='on'
    if not re.fullmatch(r'[A-Za-z0-9_.\-\u4e00-\u9fff]+', c['asset']) or (type(c.get('keep_days',15)) is not int or c.get('keep_days',15) < 1):
        raise ValueError('Invalid asset or retention')
    if c.get('cleanup_mode') not in ('plan','delete') or not c['targets']:
        raise ValueError('Invalid cleanup or targets')
    for target in c['targets']: keep_cycles(c,target)
    job = Job(c)
    with (job.state/'distribute.lock').open('w') as lock:
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log('SKIP already running'); return 0
        if a.check:
            job.remote('test -d '+shlex.quote(c['backup_dir']+'/.core-complete'))
            for t in c['targets']:
                base = job.mount(t)
                probe = base/('.write-test-'+str(os.getpid()))
                with probe.open('xb') as f: f.write(b'core-backup-write-test'); f.flush(); os.fsync(f.fileno())
                if probe.read_bytes() != b'core-backup-write-test': raise ValueError('Write/read test failed')
                probe.unlink()
            log('CHECK OK: strict SSH, exact CIFS mounts, target write/read/delete. No backup/deletion/webhook.')
            return 0
        try: return job.execute()
        except Exception as e:
            log('FAILED '+str(e)); job.notice('FAILURE','Collection failed; inspect ops distribute.log'); return 1

if __name__ == '__main__':
    sys.exit(main())

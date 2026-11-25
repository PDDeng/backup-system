#!/usr/bin/env python3
"""Read-only historical content audit. Never uploads, deletes or promotes receipts."""
import argparse
import datetime as dt
import fcntl
import json
import re
import shlex
import time
import os
from pathlib import Path, PurePosixPath
from distribute import Job, atomic, log
from md5_verify import digest, source_hash


def source_baseline(job, run, files):
    root = job.c['backup_dir'].rstrip('/')
    marker = root+'/.core-complete/'+run
    data = job.remote('cat -- '+shlex.quote(marker+'/DONE')).decode()
    if 'ASSET='+job.c['asset'] not in data.splitlines() or 'COPIED_STATUS=SOURCE_COMPLETE_MD5_PENDING' not in data:
        raise ValueError('Invalid source completion marker')
    rows = job.remote('cat -- '+shlex.quote(marker+'/files.tsv')).decode().splitlines()
    paths = {}
    for row in rows:
        path, size = row.split('\t')
        name = path.rsplit('/', 1)[-1]
        expected = root+'/'+name if name.endswith('_gitlab_backup.tar') else marker+'/'+name
        if str(PurePosixPath(path)) != expected or name not in files or int(size) != files[name]['size'] or name in paths:
            raise ValueError('Source manifest differs from target receipt')
        paths[name] = path
    if set(paths) != set(files):
        raise ValueError('Incomplete source manifest')
    result = {}
    for name, path in paths.items():
        command = 'stat -c %s:%Y:%i -- '+shlex.quote(path)
        before = job.remote(command).decode().strip()
        if int(before.split(':')[0]) != files[name]['size']:
            raise ValueError('Source size changed')
        result[name] = source_hash(job.remote, path)
        if job.remote(command).decode().strip() != before:
            raise ValueError('Source changed during MD5 audit')
    return result


def main():
    os.umask(0o077)
    p = argparse.ArgumentParser()
    p.add_argument('--servers', nargs='+', default=None)
    p.add_argument('--config-dir', default='/etc/core-backup-ops')
    a = p.parse_args()
    if a.servers is None:
        a.servers=sorted(p.stem[3:] for p in Path(a.config_dir).glob("ops*.json") if re.fullmatch(r"ops[0-9]+",p.stem))
    if not a.servers: p.error("No GitLab configurations")
    state = Path('/var/lib/core-backup-ops/history-md5')
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Coordinate with the normal all-server distribution entry point.
    lock = (state.parent/'backup-all.lock').open('a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log('HISTORY_MD5 SKIPPED: distribution running; retry after it finishes')
        c = json.loads((Path(a.config_dir)/('ops'+a.servers[0]+'.json')).read_text(encoding='utf-8-sig'))
        c.setdefault('notify_config', '/etc/core-backup-ops/notify.json')
        Job(c).notice('SUMMARY', '【历史MD5复核｜跳过】\n原因：备份分发正在运行。\n本次未校验；请在分发结束后补跑。')
        return 2
    started = time.monotonic()
    report = {'started':dt.datetime.now().astimezone().isoformat(), 'results':[]}
    notifier=None
    for sid in a.servers:
        if not re.fullmatch(r'\d+', sid):
            raise ValueError('Invalid server ID')
        job = None
        try:
            c = json.loads((Path(a.config_dir)/('ops'+sid+'.json')).read_text(encoding='utf-8-sig'))
            c['verify_md5'] = False
            c.setdefault('notify_config', '/etc/core-backup-ops/notify.json')
            job = Job(c)
            notifier=job
            with (job.state/'distribute.lock').open('a') as task_lock:
                fcntl.flock(task_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                job.notice('START', 'Historical MD5 audit; read only; no deletion')
                baseline_cache = {}
                for target in c['targets']:
                    count = 0
                    base = job.mount(target)
                    for day in sorted(base.iterdir()):
                        if day.is_symlink() or not day.is_dir() or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', day.name):
                            continue
                        for folder in sorted(day.iterdir()):
                            if folder.name.startswith('.'):
                                continue
                            row = {'server':sid, 'target':target['id'], 'run':folder.name}
                            tick = time.monotonic()
                            try:
                                receipt = job.checked(folder)  # Existence/size/path checks only.
                                proof = job.state/'receipts'/target['id']/(folder.name+'.json')
                                if json.loads(proof.read_text(encoding='utf-8')) != receipt:
                                    raise ValueError('Ownership proof mismatch')
                                files = receipt['files']
                                if receipt.get('verification', 'md5_verified') == 'md5_verified':
                                    hashes = {n:i['md5'] for n,i in files.items()}
                                else:
                                    cache_key = (folder.name, json.dumps(files, sort_keys=True))
                                    if cache_key not in baseline_cache:
                                        try:
                                            baseline_cache[cache_key] = source_baseline(job, folder.name, files)
                                        except Exception as exc:
                                            baseline_cache[cache_key] = exc
                                    hashes = baseline_cache[cache_key]
                                    if isinstance(hashes, Exception):
                                        row.update(status='unverifiable', error='Source baseline unavailable: '+str(hashes))
                                        hashes = None
                                if hashes is not None:
                                    for name, info in files.items():
                                        path = folder/name
                                        before = path.stat()
                                        actual = digest(path)
                                        after = path.stat()
                                        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or actual != hashes[name]:
                                            raise ValueError('MD5 mismatch or changed file: '+name)
                                        log('HISTORY_MD5 FILE_OK server='+sid+' target='+target['id']+' run='+folder.name+' file='+name)
                                    job.mount(target)
                                    row.update(status='verified', md5=hashes)
                            except Exception as exc:
                                row.update(status='failed', error=str(exc))
                            row['seconds'] = round(time.monotonic()-tick, 1)
                            report['results'].append(row)
                            atomic(state/'last-report.json', report)
                            log('HISTORY_MD5 '+json.dumps(row, ensure_ascii=False))
                            count += 1
                            if row['status'] != 'verified':
                                job.notice('FAILURE' if row['status']=='failed' else 'WARNING', 'Historical MD5 '+target['id']+' run='+folder.name+' '+row['status']+'; see history-md5 report')
                    rows = [r for r in report['results'] if r['server']==sid and r['target']==target['id']]
                    good = sum(r['status']=='verified' for r in rows)
                    job.notice('SUCCESS' if count and good==count else 'WARNING', 'Historical MD5 '+target['id']+' verified='+str(good)+'/'+str(count)+'; no deletion or receipt promotion')
        except Exception as exc:
            report['results'].append({'server':sid, 'status':'failed', 'error':str(exc)})
            log('HISTORY_MD5 SERVER_FAILED '+sid+' '+str(exc))
            if job:
                job.notice('FAILURE', 'Historical audit failed; see history-md5 report')
    report['finished'] = dt.datetime.now().astimezone().isoformat()
    report['seconds'] = round(time.monotonic()-started, 1)
    atomic(state/'last-report.json', report)
    atomic(state/('report-'+dt.datetime.now().strftime('%Y%m%d-%H%M%S')+'.json'), report)
    if notifier:
        counts={k:sum(r['status']==k for r in report['results']) for k in ('verified','failed','unverifiable')}
        lines=['【历史MD5复核｜'+('全部通过' if not counts['failed'] and not counts['unverifiable'] else '存在异常')+'】',
               '通过：%d；失败：%d；缺少源基准：%d'%(counts['verified'],counts['failed'],counts['unverifiable'])]
        for row in [r for r in report['results'] if r['status']!='verified'][:8]:
            lines.append(row['server']+' / '+row.get('target','源端')+' / '+row.get('run','')+'：'+row.get('error','')[:160])
        lines.append('完整报告：/var/lib/core-backup-ops/history-md5/last-report.json')
        notifier.notice('SUMMARY','\n'.join(lines))
    return 0 if report['results'] and all(r['status']=='verified' for r in report['results']) else 1


if __name__ == '__main__':
    raise SystemExit(main())

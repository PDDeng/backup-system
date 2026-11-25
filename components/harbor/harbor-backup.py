#!/usr/bin/env python3
"""Harbor Compose first backup: SSH streams to mounted NAS, no PGDATA copy/service stop."""
import argparse
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import time
import signal
from maintenance import Maintenance, read_only_value


def main():
    os.umask(0o077)
    p=argparse.ArgumentParser()
    p.add_argument('--config',default='/etc/core-backup-ops/harbor248.json')
    p.add_argument('--check',action='store_true')
    p.add_argument('--writers-quiesced',action='store_true',help='Operator confirms no running/scheduled replication, GC, push, delete or retention writes')
    p.add_argument('--restore-only',action='store_true',help='Restore recorded original maintenance state only; no backup')
    a=p.parse_args()
    c=json.loads(Path(a.config).read_text(encoding='utf-8-sig'))
    if not re.fullmatch(r'[A-Za-z0-9_.\-\u4e00-\u9fff]+',c['asset']):raise ValueError('Invalid asset')
    sys.path.insert(0,c.get('modules_dir','/opt/core-backup-modular'))
    from distribute import Job,atomic,log
    c.setdefault('backup_dir',c['deployment']);c['verify_md5']=False
    job=Job(c)
    lock=(job.state/'harbor.lock').open('a')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    ssh=job.ssh
    def remote(command,timeout=60):
        return subprocess.run(ssh+[command],stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=True,timeout=timeout).stdout
    def api(path,method='GET',body=None):
        command='curl -sS --connect-timeout 5 --max-time 20 --netrc-file '+shlex.quote(c['api_netrc'])
        command+=' -X '+method
        if body is not None:command+=' -H '+shlex.quote('Content-Type: application/json')+' --data-binary '+shlex.quote(json.dumps(body))
        command+=' -w '+shlex.quote('\nHARBOR_HTTP_STATUS=%{http_code}')
        command+=' '+shlex.quote('http://127.0.0.1:18888/api/v2.0'+path)
        output=remote(command)
        text=output.decode('utf-8',errors='replace')
        payload,sep,status=text.rpartition('\nHARBOR_HTTP_STATUS=')
        if not sep or not status.strip().startswith('2'):
            raise RuntimeError('Harbor API '+method+' '+path+' HTTP '+status.strip()+': '+payload[:1200])
        return json.loads(payload) if payload.strip() else None
    maintenance=Maintenance(api,job.state/'maintenance-state.json',atomic,log,timeout=c.get('drain_timeout_seconds',300))
    def interrupted(signum,frame):raise RuntimeError('Interrupted by signal '+str(signum))
    signal.signal(signal.SIGTERM,interrupted)
    signal.signal(signal.SIGINT,interrupted)
    def stream(command,destination):
        tick=time.monotonic()
        with destination.open('xb') as out:
            # stderr is a private local diagnostic file, never the data stream.
            with (job.state/'source-stderr.log').open('ab') as err:
                subprocess.run(ssh+[command],stdout=out,stderr=err,check=True,timeout=c.get('stream_timeout_seconds',21600))
            out.flush();os.fsync(out.fileno())
        if destination.stat().st_size<=0:raise ValueError('Empty streamed backup')
        log('COLLECTED '+destination.name+' seconds=%.1f'%(time.monotonic()-tick))
        return {'size':destination.stat().st_size,'seconds':round(time.monotonic()-tick,1)}
    def target(base):
        folder=job.mount(base)
        probe=folder/('.probe-'+str(os.getpid()))
        with probe.open('xb') as f:f.write(b'harbor-probe');f.flush();os.fsync(f.fileno())
        if probe.read_bytes()!=b'harbor-probe':raise ValueError('SMB probe mismatch')
        probe.unlink()
        return folder
    started=time.monotonic();result={'status':'failed','targets':{}}
    try:
        if a.restore_only:
            maintenance.restore();result['status']='success';return 0
        deploy=c['deployment'].rstrip('/')
        q=shlex.quote(deploy)
        remote('test -d '+q+'/data/registry && test -d '+q+'/data/secret')
        remote('docker exec harbor-db pg_isready -U postgres')
        initial_state=maintenance.inspect()
        registry_mount=json.loads(remote('docker inspect registry --format '+shlex.quote('{{json .Mounts}}')).decode())
        storage=[m['Source'] for m in registry_mount if m['Destination']=='/storage']
        if storage != [deploy+'/data/registry']:raise ValueError('Registry storage differs from configured deployment')
        for name in ('harbor-core','harbor-db','harbor-jobservice','nginx','registryctl','harbor-log','harbor-portal','redis'):
            mounts=json.loads(remote('docker inspect '+name+' --format '+shlex.quote('{{json .Mounts}}')).decode())
            for m in mounts:
                if m.get('Type')=='bind' and not (m['Source']==deploy or m['Source'].startswith(deploy+'/')):
                    raise ValueError('External bind mount not covered: '+name+' '+m['Source'])
        estimated=int(remote('du -sk '+q,timeout=180).decode().split()[0])*1024
        bases={}
        for t in c['targets']:
            bases[t['id']]=target(t)
            free=shutil.disk_usage(bases[t['id']]).free
            if free<estimated*1.15+5*1024**3:raise ValueError('Target capacity insufficient: '+t['id'])
            log('TARGET_OK '+t['id']+' free_GiB=%.1f'%(free/1024**3))
        job.notice('SUCCESS','Harbor checks passed; maintenance API supported; no state changes in check mode; estimated_GiB=%.1f'%(estimated/1024**3))
        if a.check:
            result['status']='success'
            return 0
        maintenance.enter()
        run=dt.datetime.now().astimezone().strftime('%Y%m%dT%H%M%S')+'-'+os.urandom(3).hex()
        date=dt.date.today().isoformat()
        first=c['targets'][0];day=bases[first['id']]/date;day.mkdir(exist_ok=True)
        pending=day/('.'+run+'.partial');pending.mkdir()
        result.update(run=run,date=date)
        job.notice('START','Harbor SSH streaming to NAS; keep readonly and background writes paused until collection completes')
        maintenance.require_readonly();maintenance.require_paused()
        files={}
        files['database.sql']=stream('docker exec harbor-db pg_dumpall -U postgres',pending/'database.sql')
        files['registry.tar']=stream('tar -C '+q+'/data/registry --exclude='+shlex.quote('./docker/registry/v2/repositories/*/_uploads')+' -cf - .',pending/'registry.tar')
        # Includes deployment config, secrets, certs; excludes live DB/Redis and registry (separate).
        excludes=('data/registry','data/database','data/redis','data/job_logs','log','logs')
        tar='tar -C '+q+' '+' '.join('--exclude='+shlex.quote('./'+x) for x in excludes)+' -cf - .'
        files['configuration.tar']=stream(tar,pending/'configuration.tar')
        maintenance.require_readonly();maintenance.require_paused()
        receipt={'owner':'harbor-ops-v1','asset':c['asset'],'run':run,'date':date,'verification':'size_only',
                 'files':files,'collection_seconds':round(time.monotonic()-started,1),
                 'consistency':'logical DB + registry with readonly and drained/paused worker queues; no Redis sessions'}
        result['files']=files
        atomic(pending/'backup-result.json',receipt)
        proof=job.state/'receipts';proof.mkdir(exist_ok=True);atomic(proof/(run+'.json'),receipt)
        final=day/run;pending.rename(final);result['targets'][first['id']]=True
        maintenance.restore()
        job.notice('SUCCESS','Harbor source collection complete on '+first['id']+'; original write/queue states restored; remaining copies are NAS-to-NAS')
        for t in c['targets'][1:]:
            tick=time.monotonic()
            try:
                job.mount(t);d=bases[t['id']]/date;d.mkdir(exist_ok=True)
                temp=d/('.'+run+'.partial');temp.mkdir()
                for name,info in files.items():
                    job.mount(t)
                    with (final/name).open('rb') as src,(temp/name).open('xb') as dst:
                        shutil.copyfileobj(src,dst,4*1024*1024);dst.flush();os.fsync(dst.fileno())
                    if (temp/name).stat().st_size!=info['size']:raise ValueError('Destination size mismatch')
                atomic(temp/'backup-result.json',receipt);temp.rename(d/run)
                result['targets'][t['id']]=True
                job.notice('SUCCESS',t['id']+' Harbor copy complete; size checked; seconds=%.1f'%(time.monotonic()-tick))
            except Exception as exc:
                result['targets'][t['id']]=False
                job.notice('FAILURE',t['id']+' Harbor copy failed; primary backup retained; '+str(exc)[:250])
        result['status']='success' if all(result['targets'].values()) else 'failed'
        return 0 if result['status']=='success' else 1
    except Exception as exc:
        result['error']=str(exc)
        job.notice('FAILURE','Harbor backup failed; partial retained; automatic restoration attempted in finally; '+str(exc)[:300])
        raise
    finally:
        try:
            if not a.check and maintenance.state is not None:maintenance.restore()
        except Exception as exc:
            result['status']='failed';result['restore_error']=str(exc)
            atomic(job.state/'last-result.json',result)
            job.notice('FAILURE',str(exc)+'; run --restore-only after fixing API/SSH connectivity')
            raise
        result['finished']=dt.datetime.now().astimezone().isoformat();result['seconds']=round(time.monotonic()-started,1)
        report_name='last-check.json' if a.check else ('last-restore.json' if a.restore_only else 'last-result.json')
        atomic(job.state/report_name,result)
        if not a.check or result['status']!='success':
            good=result['status']=='success'
            label='全部成功' if good else ('部分失败' if any(result['targets'].values()) else '失败')
            title='Harbor状态恢复' if a.restore_only else ('Harbor环境检查（未备份）' if a.check else 'Harbor备份')
            lines=['【'+title+'｜'+label+'】','资产：'+c['asset'],'批次：'+result.get('run','未生成')]
            lines.append('目标：'+' / '.join(t['id']+('成功' if result['targets'].get(t['id']) else '未完成') for t in c['targets']))
            lines.append('校验：仅存在/大小，未做MD5；清理：未启用')
            state=maintenance.state
            lines.append('业务状态：'+('已恢复原状态' if state and not state.get('active') else ('未修改' if state is None else '恢复失败，立即处理')))
            if state and not state.get('active'):lines.append('原只读设置：'+('只读（保持只读）' if state['original']['read_only'] else '可写'))
            lines.append('耗时：%d分%02d秒'%(result['seconds']//60,result['seconds']%60))
            if result.get('error'):lines.append('失败原因：'+result['error'][:350])
            lines.append('日志：/var/log/harbor-backup.log')
            job.notice('SUMMARY','\n'.join(lines))


if __name__=='__main__':
    raise SystemExit(main())

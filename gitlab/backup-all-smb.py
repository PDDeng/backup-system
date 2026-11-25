#!/usr/bin/env python3
"""Serial GitLab source generation (if no today's marker) plus 3-target distribution."""
import argparse
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time
from zoneinfo import ZoneInfo

ORDER = ('185','52','191','247','14','16')

def log(message):
    print(dt.datetime.now(ZoneInfo('Asia/Shanghai')).isoformat()+' '+message,flush=True)

def ssh(c):
    return ['ssh','-i',c['key'],'-o','BatchMode=yes','-o','StrictHostKeyChecking=yes',
            '-o','UserKnownHostsFile='+c['known_hosts'],'-o','ConnectTimeout=15',
            '-o','ServerAliveInterval=15','-o','ServerAliveCountMax=3',c['user']+'@'+c['host']]

def query(c, command):
    return subprocess.run(ssh(c)+[command],stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                          text=True,check=True,timeout=60).stdout

def has_today(c,today):
    root=c['backup_dir'].rstrip('/')+'/.core-complete'
    output=query(c,'if test -d '+shlex.quote(root)+'; then find '+shlex.quote(root)+
                 ' -mindepth 2 -maxdepth 2 -type f -name DONE -print; fi')
    return any(re.fullmatch(re.escape(root)+'/'+today+r'T\d{6}-\d+/DONE',line)
               for line in output.splitlines())

def ensure_identity(c):
    # Read only known, non-secret client configuration fields. Do not change remote config.
    out=query(c,"/bin/bash -c 'source /etc/core-backup/gitlab.conf; printf \"%s\\n%s\\n\" \"$ASSET\" \"$BACKUP_DIR\"'")
    parts=out.strip().splitlines()
    if len(parts)!=2 or parts[0]!=c['asset'] or parts[1].rstrip('/')!=c['backup_dir'].rstrip('/'):
        raise ValueError('Client asset/path differs from central config')

def source_busy(c):
    output=query(c,'ps -eo comm=,args=')
    for line in output.splitlines():
        if any(s in line for s in ('gitlab:backup:create','gitlab-backup create','/opt/core-backup/gitlab-backup.sh')):
            return True
    return False

def notify(script,config,event,text):
    try:
        subprocess.run([sys.executable,str(script),config,event,'ALL-GITLAB',text],timeout=60,check=False)
    except Exception:
        log('Batch notification failed; inspect local log')

def main():
    os.umask(0o077)
    p=argparse.ArgumentParser()
    p.add_argument('--config-dir',default='/etc/core-backup-ops')
    p.add_argument('--servers',nargs='+')
    p.add_argument('--collect-only',action='store_true',help='Only collect existing markers; never generate source backups')
    p.add_argument('--verify-md5',choices=('on','off'),default='off')
    args=p.parse_args()
    if args.servers is None:
        found={p.stem[3:] for p in Path(args.config_dir).glob("ops*.json") if re.fullmatch(r"ops[0-9]+",p.stem)}
        args.servers=[s for s in ORDER if s in found]+sorted(found-set(ORDER))
    if not args.servers or any(not re.fullmatch(r"[0-9]+",s) for s in args.servers):
        p.error("No valid GitLab server IDs")
    root=Path('/var/lib/core-backup-ops');root.mkdir(parents=True,exist_ok=True)
    lock=(root/'backup-all.lock').open('w')
    try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:log('Another batch is running; skipped');return 1
    scripts=Path(__file__).resolve().parent
    if not (scripts/'distribute.py').is_file():raise ValueError('Install next to distribute.py')
    results={};start=time.monotonic();notify_config=None
    for i in args.servers:
        started=time.monotonic();log('START server='+i)
        result={'status':'failed'}
        try:
            c=json.loads((Path(args.config_dir)/('ops'+i+'.json')).read_text(encoding='utf-8-sig'))
            notify_config=c['notify_config']
            if {t['id'] for t in c['targets']}!={'nas243','nas240','win254'}:
                raise ValueError('Three required SMB targets are not configured')
            # Check exact mounted share roots before doing expensive source work.
            from distribute import Job
            probe=Job(c)
            for target in c['targets']:
                probe.mount(target)
                fsroot=subprocess.check_output(['findmnt','-rn','--mountpoint',target['mount'],'-o','FSROOT'],timeout=15).decode().strip()
                if fsroot!='/':raise ValueError('Target must mount share root: '+target['id']+' FSROOT='+fsroot)
            ensure_identity(c)
            if source_busy(c):raise ValueError('Existing source backup active; skip to avoid overlap')
            today=dt.datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%d')
            if not args.collect_only and not has_today(c,today):
                log('GENERATE server='+i+' (source backup script; no service restart)')
                # Source script performs its own disk/inode preflight and source flock.
                rc=subprocess.call(ssh(c)+['/bin/bash /opt/core-backup/gitlab-backup.sh /etc/core-backup/gitlab.conf'])
                if rc!=0:raise ValueError('Source script failed rc='+str(rc)+'; inspect source log')
                if not has_today(c,today):raise ValueError('Source returned without a valid today marker')
            else:log('REUSE existing completed backup server='+i)
            child_env=dict(os.environ,CORE_BACKUP_BATCH_SUMMARY='1')
            rc=subprocess.call([sys.executable,'-u',str(scripts/'run.py'),'--config-dir',args.config_dir,
                                '--servers',i,'--action','upload','--verify-md5',args.verify_md5],env=child_env)
            if rc!=0:
                try:
                    failed_report=json.loads((root/'modular'/'last-report.json').read_text())['servers'][i]
                    result.update(source=failed_report.get('source',{}),delivery=failed_report.get('delivery',{}))
                    raise ValueError(failed_report.get('error','Distribution failed rc='+str(rc)))
                except (OSError,KeyError,json.JSONDecodeError):raise ValueError('Distribution failed rc='+str(rc))
            last=json.loads((Path(c['state_dir'])/'last-result.json').read_text())
            if last.get('targets')!={t['id']:True for t in c['targets']}:
                raise ValueError('Three-target result not all true')
            report=json.loads((root/'modular'/'last-report.json').read_text())['servers'][i]
            if report.get('status')!='success':raise ValueError('Modular report not successful')
            result.update(status='success',run=last['run'],targets=last['targets'],
                          delivery=last,unchanged=report.get('unchanged',False),cleanup_runs=report.get('cleanup_runs',0),
                          source=report.get('source',{}),
                          verification=last.get('verification','md5_verified'),verify_md5=args.verify_md5)
        except Exception as e:
            log('FAILED server=%s error=%s'%(i,e))
            result['error']=str(e)
            if notify_config:
                notify(scripts/'notify.py',notify_config,'FAILURE','Server '+i+' failed; '+str(e)[:300])
        result['seconds']=round(time.monotonic()-started,1)
        results[i]=result
        log('END server=%s status=%s seconds=%s'%(i,result['status'],result['seconds']))
    report={'finished':dt.datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),
            'seconds':round(time.monotonic()-start,1),'servers':results}
    temp=root/'backup-all-result.tmp'
    with temp.open('w',encoding='utf-8') as f:
        json.dump(report,f,ensure_ascii=False,indent=2);f.flush();os.fsync(f.fileno())
    os.replace(temp,root/'backup-all-result.json')
    good=sum(r['status']=='success' for r in results.values())
    message='Complete %d/%d; failed=%s; seconds=%.1f; see backup-all-result.json'%(good,len(results),','.join(i for i,r in results.items() if r['status']!='success') or 'none',time.monotonic()-start)
    log(message)
    from reporting import summary
    changed=any(r['status']!='success' or not r.get('unchanged',False) or r.get('cleanup_runs',0) for r in results.values())
    health=root/'alert-health.json'
    try:previous_health=json.loads(health.read_text())
    except (OSError,ValueError):previous_health={}
    recovered=previous_health.get('failed') is True and good==len(results)
    changed=changed or recovered
    if notify_config and changed:notify(scripts/'notify.py',notify_config,'SUMMARY',summary(results,report['seconds']))
    with health.open('w') as f:json.dump({'failed':good!=len(results)},f)
    return 0 if good==len(results) else 1

if __name__=='__main__':sys.exit(main())

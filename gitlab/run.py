#!/usr/bin/env python3
"""One entry point: explicit OS, source readiness, target checks, policy, upload, alerts."""
import argparse
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import sys
import time
import subprocess
from distribute import Job,atomic,log
from environment import check_source,check_targets

def main():
    os.umask(0o077)
    p=argparse.ArgumentParser()
    p.add_argument('--config-dir',default='/etc/core-backup-ops')
    p.add_argument('--servers',nargs='+',default=['185','52','191','247','14','16'])
    p.add_argument('--action',choices=('check','upload'),default='check')
    p.add_argument('--verify-md5',choices=('on','off'))
    p.add_argument('--report-dir',default='/var/lib/core-backup-ops/modular')
    p.add_argument('--settings',default='/etc/core-backup-modular/settings.json')
    a=p.parse_args()
    settings_path=Path(a.settings)
    settings=json.loads(settings_path.read_text(encoding='utf-8-sig')) if settings_path.exists() else {}
    state=Path(a.report_dir);state.mkdir(parents=True,exist_ok=True)
    for tool in ('ssh','findmnt','mountpoint'):
        if not shutil.which(tool):raise ValueError('Missing ops tool '+tool)
    batch=(state/'run.lock').open('w')
    try:fcntl.flock(batch,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:log('SKIP another modular run active');return 1
    result={};overall=time.monotonic()
    for key in a.servers:
        job=None;started=time.monotonic();row={'status':'failed'}
        try:
            if not re.fullmatch('[0-9]+',key):raise ValueError('Server IDs must be numeric')
            c=json.loads((Path(a.config_dir)/('ops'+key+'.json')).read_text(encoding='utf-8-sig'))
            c.setdefault('os',settings.get('os_by_server',{}).get(key,'linux'))
            c['os']=c['os'].lower()
            if 'verify_md5' in settings:c['verify_md5']=settings['verify_md5']
            if a.verify_md5:c['verify_md5']=a.verify_md5=='on'
            if not re.fullmatch(r'[A-Za-z0-9_.\-\u4e00-\u9fff]+',c['asset']):raise ValueError('Invalid asset ID')
            if (type(c.get('keep_days',15)) is not int or c.get('keep_days',15)<1) or c.get('cleanup_mode','plan') not in ('plan','delete'):raise ValueError('Invalid retention config')
            job=Job(c);row.update(asset=c['asset'],host=c['host'],os=c.get('os','linux'),verify_md5=job.full)
            with (job.state/'distribute.lock').open('w') as lock:
                try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                except BlockingIOError:raise ValueError('Task running in old portal/cron; skipped')
                job.notice('START','Environment/source/SMB checks; OS='+row['os']+'; verify_md5='+str(job.full))
                # Check targets even when source is down, so report contains independent failures.
                try:row['source']=check_source(job)
                except Exception as e:row['source']={'connection':'failed','error':str(e)}
                row['targets']=check_targets(job)
                for target in row['targets']:
                    job.notice('INFO' if target['status']=='ok' else 'FAILURE',
                       'SMB '+target['id']+' OS='+target['os']+' status='+target['status']+
                       '; seconds='+str(target['seconds'])+
                       ('; '+target['error'][:250] if target.get('error') else ''))
                row['ops_free_bytes']=shutil.disk_usage(job.state).free
                if row['source'].get('connection')!='ok':raise ValueError('Source connection/check failed')
                if c['os']=='linux' and row['source'].get('latest_marker'):
                    from zoneinfo import ZoneInfo
                    stamp=Path(row['source']['latest_marker']).parent.name.split('-')[0]
                    generated=dt.datetime.strptime(stamp,'%Y%m%dT%H%M%S').replace(tzinfo=ZoneInfo('Asia/Shanghai'))
                    age=(dt.datetime.now(ZoneInfo('Asia/Shanghai'))-generated).total_seconds()/3600
                    row['source']['age_hours']=round(age,2)
                    if age>settings.get('max_backup_age_hours',36) or age < -1:
                        raise ValueError('Source backup stale or future-dated; age_hours=%.2f'%age)
                if not all(t['status']=='ok' for t in row['targets']):raise ValueError('One or more SMB checks failed; no upload started')
                if a.action=='upload':
                    if row['os']!='linux':raise ValueError('Windows inspection supported; source upload adapter not enabled')
                    if not row['source'].get('collection_ready'):raise ValueError('No completed source backup; no source backup automatically triggered')
                    rc=job.execute()
                    row.update(unchanged=job.unchanged,cleanup_runs=job.cleanup_runs)
                    if job.delivery_result is not None:row['delivery']=job.delivery_result
                    if rc:raise ValueError('Upload or cleanup failed; see target results')
                    row['delivery']=json.loads((job.state/'last-result.json').read_text())
                row['status']='success'
                if not row['source'].get('collection_ready'):row['status']='warning'
                job.notice('SUCCESS' if row['status']=='success' else 'WARNING',
                    'Checks='+row['status']+'; OS='+row['os']+'; action='+a.action+
                    '; backup_ready='+str(row['source'].get('collection_ready'))+
                    '; verification='+('MD5 required' if job.full else 'SPEED / PENDING MD5; retention uses existence/size'))
        except Exception as e:
            row['error']=str(e);log('FAILED '+key+' '+str(e))
            if job:job.notice('FAILURE','Check/upload failed: '+str(e)[:400])
            else:
                try:
                    subprocess.run([sys.executable,str(Path(__file__).with_name('notify.py')),
                      settings.get('notify_config','/etc/core-backup-ops/notify.json'),'FAILURE',
                      'CONFIG-'+key,'Config/environment load failed; see modular last-report.json'],timeout=60)
                except Exception:log('NOTIFY_FAILED before job creation')
        row['seconds']=round(time.monotonic()-started,2);result[key]=row
        atomic(state/'last-report.json',{'finished':dt.datetime.now().astimezone().isoformat(),'action':a.action,'servers':result})
        atomic(job.state/'last-'+a.action+'-report.json',{'finished':dt.datetime.now().astimezone().isoformat(),'action':a.action,'result':row}) if job else None
        log('RESULT '+key+' '+json.dumps(row,ensure_ascii=False))
    good=sum(r['status']=='success' for r in result.values())
    log('BATCH %d/%d success seconds=%.1f'%(good,len(result),time.monotonic()-overall))
    if not os.environ.get('CORE_BACKUP_BATCH_SUMMARY'):
        from reporting import summary
        should_send=any(r.get('status')!='success' or not r.get('unchanged',False) or r.get('cleanup_runs',0) for r in result.values())
        if a.action=='check':should_send=any(r.get('status')!='success' for r in result.values())
        if should_send:
            message=summary(result,time.monotonic()-overall,action=a.action)
            if job:job.notice('SUMMARY',message)
            else:subprocess.run([sys.executable,str(Path(__file__).with_name('notify.py')),settings.get('notify_config','/etc/core-backup-ops/notify.json'),'SUMMARY','GitLab',message],timeout=60)
    return 0 if good==len(result) else 1

if __name__=='__main__':sys.exit(main())

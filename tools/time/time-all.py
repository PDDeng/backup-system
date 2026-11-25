#!/usr/bin/env python3
"""Ops entry point. Reuses GitLab SSH config, plus local ops clock."""
import argparse
import base64
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time

def main():
    os.umask(0o077)
    p=argparse.ArgumentParser()
    p.add_argument('--config',default='/etc/core-time/settings.json')
    p.add_argument('--apply',action='store_true',help='Set Shanghai zone and configure installed chrony, bounded gradual correction')
    p.add_argument('--servers',nargs='+',default=['local','185','52','191','247','14','16'])
    args=p.parse_args()
    settings=json.loads(Path(args.config).read_text(encoding='utf-8-sig'))
    state=Path('/var/lib/core-time');state.mkdir(parents=True,exist_ok=True)
    lock=(state/'time-all.lock').open('w')
    try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:print('SKIP time check already running');return 1
    root=Path(__file__).resolve().parent
    code=base64.b64encode((root/'time-agent.py').read_bytes()).decode()
    runner="import base64;exec(compile(base64.b64decode('"+code+"'),'time-agent.py','exec'))"
    client=dict(settings,apply=args.apply)
    payload=json.dumps(client).encode()
    results={}
    for key in args.servers:
        started=time.monotonic()
        try:
            if key=='local':command=[sys.executable,str(root/'time-agent.py')];host='ops-local'
            else:
                if not key.isdigit():raise ValueError('Invalid server ID')
                c=json.loads((Path(settings['backup_config_dir'])/('ops'+key+'.json')).read_text())
                if c.get('os','linux').lower()!='linux':raise ValueError('This tool manages Linux hosts only')
                host=c['host']
                remote='if command -v python3 >/dev/null 2>&1; then python3 -c '+shlex.quote(runner)+\
                       '; elif command -v python >/dev/null 2>&1; then python -c '+shlex.quote(runner)+\
                       '; else echo PYTHON_MISSING >&2; exit 1; fi'
                command=['ssh','-i',c['key'],'-o','BatchMode=yes','-o','StrictHostKeyChecking=yes',
                         '-o','UserKnownHostsFile='+c['known_hosts'],'-o','ConnectTimeout=10',
                         '-o','ServerAliveInterval=15','-o','ServerAliveCountMax=3',c['user']+'@'+host,remote]
            r=subprocess.run(command,input=payload,capture_output=True,timeout=90)
            try:result=json.loads(r.stdout.decode('utf-8'))
            except Exception:raise ValueError('No agent JSON response; '+r.stderr.decode(errors='replace')[:300])
            if r.returncode and result.get('status')!='failed':result.update(status='failed',error='Agent exit '+str(r.returncode))
            result['host']=host
        except Exception as e:result={'status':'failed','error':str(e)}
        result['seconds']=round(time.monotonic()-started,1);results[key]=result
        print(key+' '+json.dumps(result,ensure_ascii=False),flush=True)
    report={'finished':dt.datetime.now().astimezone().isoformat(),'apply':args.apply,'servers':results}
    tmp=state/'last-report.tmp'
    with tmp.open('w') as f:json.dump(report,f,ensure_ascii=False,indent=2);f.flush();os.fsync(f.fileno())
    os.replace(tmp,state/'last-report.json')
    failed=[k for k,v in results.items() if v['status']!='ok']
    if failed or settings.get('notify_success',True):
        lines=[]
        for key,value in results.items():
            text=key+' '+value['status']
            if 'offset_seconds' in value:text+=' offset=%.3fs'%value['offset_seconds']
            if value.get('error'):text+=' '+value['error'][:120]
            lines.append(text)
        helper=Path(settings['notify_script'])
        subprocess.run([sys.executable,str(helper),settings['notify_config'],
                        'FAILURE' if failed else 'SUCCESS','TIME-SHANGHAI','; '.join(lines)[:1600]],timeout=60)
    return 1 if failed else 0

if __name__=='__main__':sys.exit(main())

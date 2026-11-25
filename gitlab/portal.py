#!/usr/bin/env python3
"""Local ops UI; reuses distributor, serial queue, bearer auth, no source commands."""
import argparse
from collections import deque
import datetime
import fcntl
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import threading
import time
import shutil

HTML = Path(__file__).with_name('dashboard.html').read_text(encoding='utf-8')

class Controller:
    def __init__(self, directory, state):
        self.directory = Path(directory)
        self.state = Path(state); self.state.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock(); self.queue = deque(); self.active = None
        self.settings_path = self.state/'schedules.json'
        self.settings = json.loads(self.settings_path.read_text()) if self.settings_path.exists() else {}
        self.jobs = {}
        for p in sorted(self.directory.glob('ops*.json')):
            if not re.fullmatch(r'ops[0-9]+', p.stem): continue
            c = json.loads(p.read_text())
            self.jobs[p.stem] = (p.resolve(), c)
        if not self.jobs: raise ValueError('No opsNNN.json configurations found')

    def save(self):
        tmp = self.settings_path.with_suffix('.tmp')
        with tmp.open('w') as f:
            json.dump(self.settings,f); f.flush(); os.fsync(f.fileno())
        os.replace(tmp,self.settings_path)

    def snapshot(self):
        with self.lock:
            rows=[]
            for key,(path,c) in self.jobs.items():
                s = self.settings.get(key, {})
                last = Path(c['state_dir'])/'last-result.json'
                try: result=json.loads(last.read_text()) if last.exists() else None
                except Exception: result={'error':'结果文件读取失败'}
                status = '执行中' if self.active==key else ('排队中' if any(k==key for k,a in self.queue) else '空闲')
                runfile=self.state/(key+'.result.json')
                if status=='空闲' and runfile.exists():
                    result2=json.loads(runfile.read_text())
                    status=('最近执行成功' if result2['exit_code']==0 else '最近执行失败')+' · '+result2['finished']
                def report(name):
                    try: return json.loads((Path(c['state_dir'])/name).read_text())
                    except (OSError,ValueError): return None
                rows.append(dict(id=key,asset=c['asset'],host=c['host'],os=c.get('os','linux'),targets=[t['id'] for t in c['targets']],
                    status=status,last=result,check=report('last-check-report.json'),upload=report('last-upload-report.json'),
                    keep_days=c.get('keep_days',15),cleanup_mode=c.get('cleanup_mode','plan'),
                    enabled=s.get('enabled',False),interval_minutes=s.get('interval_minutes',60),next_due=s.get('next_due')))
            disk=shutil.disk_usage('/')
            return {'jobs':rows,'ops':{'total_bytes':disk.total,'free_bytes':disk.free},'time':datetime.datetime.now().astimezone().isoformat()}

    def action(self, key, action, minutes=60):
        with self.lock:
            if key=='all' and action in ('check','run'):
                for job in self.jobs:
                    if self.active!=job and not any(k==job for k,a in self.queue): self.queue.append((job,action))
                with (self.state/'actions.jsonl').open('a') as f:
                    f.write(json.dumps({'time':time.time(),'job':'all','action':action})+'\n')
                return
            if key not in self.jobs: raise ValueError('Unknown task')
            s=self.settings.setdefault(key,{'enabled':False,'interval_minutes':60})
            if action in ('run','check'):
                if self.active==key or any(k==key for k,a in self.queue): raise ValueError('Task already running or queued')
                self.queue.append((key,action))
            elif action=='pause': s['enabled']=False
            elif action=='enable': s.update(enabled=True,next_due=time.time()+60)
            elif action=='interval':
                if type(minutes) is not int or not 60<=minutes<=1440: raise ValueError('Interval must be 60..1440 minutes')
                s.update(interval_minutes=minutes,next_due=time.time()+minutes*60)
            else: raise ValueError('Unknown action')
            self.save()
            with (self.state/'actions.jsonl').open('a') as f:
                f.write(json.dumps({'time':time.time(),'job':key,'action':action,'minutes':minutes})+'\n')

    def worker(self):
        while True:
            with self.lock:
                for key in self.jobs:
                    s=self.settings.get(key,{})
                    if s.get('enabled') and time.time()>=s.get('next_due',0):
                        if self.active!=key and not any(k==key for k,a in self.queue): self.queue.append((key,'run'))
                        s['next_due']=time.time()+s.get('interval_minutes',60)*60; self.save()
                item=self.queue.popleft() if self.queue else None
                if item: self.active=item[0]
            if not item: time.sleep(1); continue
            key,action=item; started=time.time(); rc=1
            try:
                with (self.state/(key+'.log')).open('a',buffering=1) as log:
                    log.write('\nSTART %s %s\n'%(datetime.datetime.now().astimezone().isoformat(),action))
                    command=['flock','-n','/var/lib/core-backup-ops/backup-all.lock',sys.executable,'-u',
                        str(Path(__file__).with_name('run.py')),'--config-dir',str(self.directory),
                        '--servers',key[3:],'--action','check' if action=='check' else 'upload',
                        '--report-dir',str(self.state/'reports'/key)]
                    # UI deliberately follows speed-first policy; source generation is never exposed.
                    command += ['--verify-md5','off']
                    rc=subprocess.call(command,stdout=log,stderr=subprocess.STDOUT)
                    log.write('END exit_code=%d elapsed=%.1fs\n'%(rc,time.time()-started))
            except Exception as e:
                with (self.state/(key+'.log')).open('a') as log: log.write('Worker error: '+str(e)+'\n')
            finally:
                r={'exit_code':rc,'seconds':time.time()-started,'finished':datetime.datetime.now().astimezone().isoformat()}
                (self.state/(key+'.result.json')).write_text(json.dumps(r))
                with self.lock: self.active=None

class Handler(BaseHTTPRequestHandler):
    def reply(self,status,data,html=False):
        body=data.encode() if html else json.dumps(data,ensure_ascii=False).encode()
        self.send_response(status); self.send_header('Content-Type','text/html; charset=utf-8' if html else 'application/json; charset=utf-8')
        self.send_header('Cache-Control','no-store'); self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('X-Frame-Options','DENY'); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
    def authorized(self):
        return hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+self.server.token)
    def do_GET(self):
        from urllib.parse import urlsplit,parse_qs
        p=urlsplit(self.path)
        if p.path=='/': return self.reply(200,HTML,True)
        if not self.authorized(): return self.reply(401,{'error':'管理令牌无效'})
        if p.path=='/api/jobs': return self.reply(200,self.server.controller.snapshot())
        if p.path=='/api/logs':
            key=parse_qs(p.query).get('id',[''])[0]
            if key not in self.server.controller.jobs: return self.reply(404,{'error':'未知任务'})
            path=self.server.controller.state/(key+'.log')
            text='暂无页面执行日志；旧cron日志仍在原位置'
            if path.exists():
                with path.open('rb') as f:
                    f.seek(max(0,path.stat().st_size-32000)); text=f.read().decode('utf-8',errors='replace')
            return self.reply(200,{'text':text})
        self.reply(404,{'error':'Not found'})
    def do_POST(self):
        if not self.authorized(): return self.reply(401,{'error':'管理令牌无效'})
        if self.path!='/api/action': return self.reply(404,{'error':'Not found'})
        try:
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<=4096: raise ValueError('Invalid body size')
            body=json.loads(self.rfile.read(size))
            self.server.controller.action(body['id'],body['action'],body.get('minutes',60))
            self.reply(200,{'ok':True})
        except (ValueError,KeyError) as e: self.reply(400,{'error':str(e)})
    def log_message(self,*args): pass

def main():
    os.umask(0o077)
    p=argparse.ArgumentParser();p.add_argument('--config-dir',default='/etc/core-backup-ops')
    p.add_argument('--state',default='/var/lib/core-backup-ops/portal');p.add_argument('--port',type=int,default=8088)
    args=p.parse_args(); c=Controller(args.config_dir,args.state)
    instance=(c.state/'portal.lock').open('w');fcntl.flock(instance,fcntl.LOCK_EX|fcntl.LOCK_NB)
    tokenfile=c.state/'admin.token'
    if not tokenfile.exists(): tokenfile.write_text(secrets.token_urlsafe(32))
    tokenfile.chmod(0o600)
    server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler);server.token=tokenfile.read_text().strip();server.controller=c
    threading.Thread(target=c.worker,daemon=True).start()
    print('Portal http://127.0.0.1:%d ; token file %s ; schedules disabled by default'%(args.port,tokenfile),flush=True)
    server.serve_forever()

if __name__=='__main__': main()

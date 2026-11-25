"""Read Windows task/service/result evidence into ops; never change Windows services."""
import base64,datetime as dt,json,re,subprocess,sys
from pathlib import Path
from windows_transport import session,decode
p=Path('/var/lib/core-backup-ops/nexus51');p.mkdir(parents=True,exist_ok=True,mode=0o700)
def write(name,obj):
    target=p/name;tmp=target.with_suffix('.json.tmp');tmp.write_text(json.dumps(obj,ensure_ascii=False,indent=2));tmp.replace(target)
now=dt.datetime.now().astimezone()
script=r"""[Console]::WriteLine('SERVICE='+(Get-Service 'nexus-webapp').Status)
$s=New-Object -ComObject Schedule.Service;$s.Connect();$t=$s.GetFolder('\').GetTask('CoreBackup-Nexus51')
[Console]::WriteLine('TASK='+$t.State)
[Console]::WriteLine('START='+$t.LastRunTime.ToString('s'))
[Console]::WriteLine('NEXT='+$t.NextRunTime.ToString('s'))
[Console]::WriteLine('LAST_RC='+$t.LastTaskResult)
$p='C:\ProgramData\CoreNexusBackup\last-result.json'
if([IO.File]::Exists($p)){[Console]::WriteLine('RESULT_BEGIN');[Console]::WriteLine([Convert]::ToBase64String([IO.File]::ReadAllBytes($p)));[Console]::WriteLine('RESULT_END')}
"""
try:
    response=session('nexus51').run_ps(script);text=decode(response.std_out)
    values=dict(re.findall(r'^(SERVICE|TASK|START|NEXT|LAST_RC)=(.*)$',text,re.M));values={k:v.strip() for k,v in values.items()}
    if response.status_code or not {'SERVICE','TASK','START','NEXT'}<=values.keys():raise ValueError('Incomplete Windows evidence')
    record={'sampled':now.isoformat(),'os':'windows','connection':'ok','service':values['SERVICE'],'task_state':values['TASK'],'next_run':values['NEXT'],'last_task_result':values.get('LAST_RC')}
    match=re.search(r'RESULT_BEGIN\s*(.*?)\s*RESULT_END',text,re.S)
    if match:
        prior=json.loads(base64.b64decode(re.sub(r'\s+','',match.group(1))).decode('utf-8-sig'))
        write('last-result.json',{'status':prior.get('Status','unknown'),'run':prior.get('Run'),'seconds':prior.get('Seconds'),'targets':prior.get('Targets',{}),'error':prior.get('Error'),'verification':prior.get('Verification'),'observer':record})
    if values['TASK']=='4':write('current-result.json',dict(record,status='running',fresh_for_seconds=600,phase='source_backup',started=values['START']))
    else:write('current-result.json',dict(record,status='inactive'))
    health=p/'service-health.json'
    old=json.loads(health.read_text()) if health.exists() else {}
    if values['SERVICE']=='Running':old={'status':'running','sampled':now.isoformat()}
    else:
        since=old.get('stopped_since',now.isoformat());old.update(status=values['SERVICE'],stopped_since=since,sampled=now.isoformat())
        elapsed=(now-dt.datetime.fromisoformat(since)).total_seconds()
        if (values['TASK']!='4' or elapsed>=600) and not old.get('alerted'):
            cfg=Path('/etc/core-backup-ops/notify.json')
            if json.loads(cfg.read_text(encoding='utf-8-sig')).get('Enabled',True):
                message='【Nexus服务异常】服务='+values['SERVICE']+'；任务状态='+values['TASK']+'；请立即核查Windows恢复看门狗。此监测器没有重启服务。'
                rc=subprocess.run([sys.executable,'/opt/core-backup-modular/notify.py',str(cfg),'FAILURE','Nexus51',message],timeout=60).returncode
                old['alerted']=rc==0
    write('service-health.json',old);write('last-observation.json',record)
    print('OBSERVED Nexus51 service='+values['SERVICE']+' task='+values['TASK'])
except Exception as e:
    write('last-observation.json',{'sampled':now.isoformat(),'connection':'failed','error':type(e).__name__})
    print('Nexus observation unavailable; prior backup result preserved');raise SystemExit(1)

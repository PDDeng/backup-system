"""Observe approved Windows jobs only; never change database or Windows state."""
import base64,datetime as dt,hashlib,json,re,subprocess,sys
from pathlib import Path
from windows_transport import session,decode

def execute(asset,source):
    encoded=base64.b64encode(source.encode('utf-16-le')).decode()
    return session(asset).run_cmd(r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe', ['-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-EncodedCommand',encoded])

for asset,name in [('oracle46','approved-retry-state.json'),('oracle41','rman-once-state.json')]:
    now=dt.datetime.now().astimezone().isoformat()
    root=Path('/var/lib/core-backup-ops')/asset
    root.mkdir(mode=0o700,parents=True,exist_ok=True)
    def write(filename,data):
        p=root/filename;t=p.with_suffix('.tmp')
        t.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8');t.replace(p)
    try:
        path='C:\\ProgramData\\CoreOracleNas'+asset[-2:]+'\\'+name
        task='CoreOracle46-ApprovedRetry' if asset=='oracle46' else 'CoreOracle41-RmanOnce'
        ps="$ErrorActionPreference='Stop';$s=New-Object -ComObject Schedule.Service;$s.Connect();[Console]::WriteLine('TASK_STATE='+$s.GetFolder('\\').GetTask('"+task+"').State);[Console]::WriteLine('OBS_BEGIN');if([IO.File]::Exists('"+path+"')){[Console]::WriteLine([Convert]::ToBase64String([IO.File]::ReadAllBytes('"+path+"')))}else{[Console]::WriteLine('MISSING')};[Console]::WriteLine('OBS_END')"
        if asset=='oracle41':
            ps=ps.replace("$s.Connect();", "$s.Connect();$taskName='CoreOracle41-RmanOnce';$statePath='C:\\ProgramData\\CoreOracleNas41\\rman-once-state.json';try{$daily=$s.GetFolder('\\').GetTask('CoreOracle41-Daily');$taskName='CoreOracle41-Daily';$statePath='C:\\ProgramData\\CoreOracleNas41\\rman-daily-state.json'}catch{};[Console]::WriteLine('TASK_NAME='+$taskName);")
            ps=ps.replace("GetTask('CoreOracle41-RmanOnce')", "GetTask($taskName)").replace("[IO.File]::Exists('"+path+"')", "[IO.File]::Exists($statePath)").replace("[IO.File]::ReadAllBytes('"+path+"')", "[IO.File]::ReadAllBytes($statePath)")
        if asset=='oracle46':
            ps=ps.replace("$s.Connect();", "$s.Connect();$taskName='CoreOracle46-ApprovedRetry';$statePath='C:\\ProgramData\\CoreOracleNas46\\approved-retry-state.json';try{$daily=$s.GetFolder('\\').GetTask('CoreOracle46-Daily');$candidate='C:\\ProgramData\\CoreOracleNas46\\daily-state.json';if($daily.Enabled -and $daily.LastRunTime.Year -gt 2000 -and [IO.File]::Exists($candidate) -and [IO.File]::GetLastWriteTime($candidate) -ge $daily.LastRunTime){$taskName='CoreOracle46-Daily';$statePath=$candidate}}catch{};[Console]::WriteLine('TASK_NAME='+$taskName);")
            ps=ps.replace("GetTask('CoreOracle46-ApprovedRetry')", "GetTask($taskName)").replace("[IO.File]::Exists('"+path+"')", "[IO.File]::Exists($statePath)").replace("[IO.File]::ReadAllBytes('"+path+"')", "[IO.File]::ReadAllBytes($statePath)")
        response=execute(asset,ps);out=decode(response.std_out)
        if response.status_code or 'OBS_BEGIN' not in out or 'OBS_END' not in out:raise ValueError('Incomplete response')
        value=out.split('OBS_BEGIN',1)[1].split('OBS_END',1)[0].strip()
        state={} if value=='MISSING' else json.loads(base64.b64decode(value).decode('utf-8-sig'))
        task_match=re.search(r'TASK_STATE=(\d+)',out)
        if not task_match:raise ValueError('Task state missing')
        task_state=task_match.group(1)
        validation=None
        if asset=='oracle41' and task_state!='4' and 'TASK_NAME=CoreOracle41-Daily' not in out:
            ps2="$ErrorActionPreference='Stop';$s=New-Object -ComObject Schedule.Service;$s.Connect();[Console]::WriteLine('TASK_STATE='+$s.GetFolder('\\').GetTask('CoreOracle41-ValidateOnce').State);[Console]::WriteLine('OBS_BEGIN');[Console]::WriteLine([Convert]::ToBase64String([IO.File]::ReadAllBytes('C:\\ProgramData\\CoreOracleNas41\\rman-validation-state.json')));[Console]::WriteLine('OBS_END')"
            try:
                v=execute(asset,ps2);vout=decode(v.std_out)
                if v.status_code:raise ValueError('Validation observation failed')
                vm=re.search(r'TASK_STATE=(\d+)',vout)
                validation=json.loads(base64.b64decode(vout.split('OBS_BEGIN',1)[1].split('OBS_END',1)[0].strip()).decode('utf-8-sig'))
                validation['task_state']=vm.group(1) if vm else 'unknown'
            except Exception:validation={'observation':'unavailable'}
        write('source-observation.json',dict(sampled=now,connection='ok',source_state=state,task_state=task_state,validation=validation))
        running=state.get('Status')=='running' and task_state=='4'
        validating=validation and validation.get('Status')=='running' and validation.get('task_state')=='4'
        write('current-result.json',dict(sampled=now,fresh_for_seconds=600,status='running' if running or validating else 'inactive',run=state.get('Run'),phase='backupset_validation' if validating else state.get('Phase','logical_export'),native_validation=validation.get('Status') if validation else None,note='Source task only; copy receipts and actual restore remain independent'))
        if asset=='oracle46' and 'TASK_NAME=CoreOracle46-Daily' in out and (state.get('Status')=='failed' or state.get('Status')=='running' and task_state!='4'):
            reason='daily source failed' if state.get('Status')=='failed' else 'source state says running but task is terminal; review rollback guard'
            sig=hashlib.sha256(json.dumps([state.get('Run'),state.get('Finished'),reason],sort_keys=True).encode()).hexdigest()
            alert=root/'last-source-alert.json';previous=json.loads(alert.read_text()) if alert.exists() else {}
            if previous.get('signature')!=sig:
                msg='Oracle46 '+reason+'; inspect daily-state.json and daily-active-guard.json. Observer does not change database parameters.'
                sent=subprocess.run([sys.executable,'/opt/core-backup-modular/notify.py','/etc/core-backup-ops/notify.json','CRITICAL','Oracle46',msg],timeout=45)
                if sent.returncode==0:write('last-source-alert.json',dict(signature=sig,sent=now))
        print(asset+' observed '+str(state.get('Status','missing')))
    except Exception as exc:
        write('source-observation.json',dict(sampled=now,connection='failed',error=type(exc).__name__))
        print(asset+' observation failed; previous backup receipts preserved')

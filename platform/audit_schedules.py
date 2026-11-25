"""Read-only scheduler evidence, never register/run/disable tasks. No raw cron in UI."""
import argparse,base64,concurrent.futures,datetime,json,os,re,shlex,subprocess,sys
from pathlib import Path
os.umask(0o077)
sys.path.insert(0,'/opt/core-backup-platform')
from windows_transport import session,decode
root=Path('/var/lib/backup-console/schedules');root.mkdir(mode=0o700,parents=True,exist_ok=True)
def now():return datetime.datetime.now().astimezone().isoformat()
def save(asset,value):
    p=root/(asset+'.json');t=p.with_suffix('.tmp');t.write_text(json.dumps(value,ensure_ascii=False,indent=2));t.replace(p)
def audit_gitlab(asset):
    result={'sampled':now(),'status':'unavailable','owner':'client_root_cron'}
    try:
        c=json.loads(Path('/etc/core-backup-ops/ops'+asset+'.json').read_text())
        script="printf 'CLOCK_BEGIN\\n'; date -Is; printf 'CRON_BEGIN\\n'; crontab -l 2>/dev/null; printf 'CRON_END\\n'; test -x /opt/core-backup/gitlab-backup.sh && printf 'SCRIPT_OK\\n'; test -f /etc/core-backup/gitlab.conf && printf 'CONFIG_OK\\n'"
        args=['ssh','-i',c['key'],'-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile='+c['known_hosts'],'-o','ConnectTimeout=10',c['user']+'@'+c['host'],script]
        r=subprocess.run(args,capture_output=True,text=True,timeout=40)
        if r.returncode or 'CRON_END' not in r.stdout:raise ValueError('Incomplete SSH scheduler response')
        cron=r.stdout.split('CRON_BEGIN\n',1)[1].split('CRON_END',1)[0]
        jobs=[]
        for line in cron.splitlines():
            line=line.strip()
            if not line or line.startswith('#'):continue
            fields=line.split(None,5)
            if len(fields)!=6:continue
            command=fields[5];kind=None
            for marker,label in [('gitlab-backup.sh','core_source_backup'),('gitlab:backup:create','legacy_source_backup'),('rsync','legacy_distribution'),('sync.sh','legacy_distribution'),('clean','legacy_cleanup'),('chmod','legacy_permissions')]:
                if marker in command:kind=label;break
            if kind:jobs.append({'cron':' '.join(fields[:5]),'action':kind})
        backups=[j for j in jobs if j['action'] in ('core_source_backup','legacy_source_backup')]
        result.update(status='observed',clock=r.stdout.split('CLOCK_BEGIN\n',1)[1].splitlines()[0],jobs=jobs,source_backup_jobs=len(backups),script_present='SCRIPT_OK' in r.stdout,config_present='CONFIG_OK' in r.stdout,script='/opt/core-backup/gitlab-backup.sh',config='/etc/core-backup/gitlab.conf',log='/var/log/core-backup/gitlab.log',note='Cron inventory only; execution success requires backup receipts')
    except Exception as e:result['error']=type(e).__name__+': '+str(e)[-180:]
    save(asset,result);return asset,result['status']
def audit_windows(asset):
    result={'sampled':now(),'status':'unavailable','owner':'windows_task'}
    try:
        ps=r"""$ErrorActionPreference='Stop';$zone=(Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\TimeZoneInformation').TimeZoneKeyName;[Console]::WriteLine('ZONE|'+$zone);$s=New-Object -ComObject Schedule.Service;$s.Connect();foreach($t in $s.GetFolder('\').GetTasks(0)){if($t.Name -like 'CoreBackup*' -or $t.Name -like 'CoreOracle*' -or $t.Name -eq 'backup_sql' -or $t.Name -eq 'sync2nas'){[Console]::WriteLine('TASK|'+$t.Name+'|'+$t.State+'|'+$t.Enabled+'|'+$t.LastTaskResult+'|'+$t.LastRunTime.ToString('o')+'|'+$t.NextRunTime.ToString('o'))}};[Console]::WriteLine('AUDIT_END')"""
        r=session(asset).run_cmd(r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe',['-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-EncodedCommand',base64.b64encode(ps.encode('utf-16-le')).decode()]);out=decode(r.std_out)
        if r.status_code or 'AUDIT_END' not in out:raise ValueError('Incomplete scheduler response: '+decode(r.std_err)[-250:])
        tasks=[]
        for line in out.splitlines():
            f=line.strip().split('|')
            if len(f)==7 and f[0]=='TASK':tasks.append(dict(name=f[1],state=int(f[2]),enabled=f[3].lower()=='true',last_result=int(f[4]),last_run=f[5],next_run=f[6]))
        result.update(status='observed',tasks=tasks,timezone=next((x.split('|',1)[1] for x in out.splitlines() if x.startswith('ZONE|')),None),note='Task state4/result267009 means running, not a failed backup; one-shot validation tasks are not daily schedules')
    except Exception as e:result['error']=type(e).__name__+': '+str(e)[-180:]
    save(asset,result);return asset,result['status']
if __name__=='__main__':
    # Avoid repeated login attempts to endpoints already known to be resetting.
    defaults=('185','52','191','247','14','16','oracle46','oracle41','crm200','dse27')
    allowed=set(defaults)|{'svn29','nexus51','kingdee252'}
    parser=argparse.ArgumentParser(description='Read-only scheduler audit; no task changes')
    parser.add_argument('--assets',nargs='+',choices=sorted(allowed),default=defaults)
    args=parser.parse_args()
    work=[(audit_gitlab if x in ('185','52','191','247','14','16') else audit_windows,x) for x in dict.fromkeys(args.assets)]
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(fn,x) for fn,x in work]
        for f in concurrent.futures.as_completed(futures):print(*f.result(),flush=True)

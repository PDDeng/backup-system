"""Fixed inventory, bounded read-only source/OPS checks. No backup or cleanup."""
import base64,concurrent.futures,datetime as dt,json,os,shlex,shutil,subprocess,sys,time
from pathlib import Path
os.umask(0o077)
config_path=Path('/opt/backup-console/config.json')
c=json.loads(config_path.read_text(encoding='utf-8-sig'))
state=Path(c['state']);out=state/'diagnostics';out.mkdir(exist_ok=True)
def now():return dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat()
def save(path,value):
    temp=path.with_suffix('.tmp');temp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(path)
windows_script=r"""$ErrorActionPreference='Stop'
[Console]::WriteLine('HOST='+$env:COMPUTERNAME)
[Console]::WriteLine('NOW='+(Get-Date -Format o))
Get-WmiObject Win32_LogicalDisk -Filter 'DriveType=3' | ForEach-Object {[Console]::WriteLine('DRIVE='+$_.DeviceID+'|'+$_.Size+'|'+$_.FreeSpace)}
"""
probe=Path('/opt/core-backup-platform/check-windows-source.ps1')
probe.write_text(windows_script,encoding='ascii');probe.chmod(0o600)
def check(a):
    tick=time.monotonic();result={'sampled':now(),'os':'linux' if a['type'] in ('GitLab','Harbor') else 'windows','status':'unknown'}
    try:
        if a['type']=='GitLab':
            cfg=json.loads((Path('/etc/core-backup-ops')/('ops'+a['id']+'.json')).read_text())
            root=cfg['backup_dir'];key=cfg['key'];known=cfg['known_hosts'];host=cfg['host'];user=cfg['user']
        elif a['type']=='Harbor':
            root='/root/repository/tools/harbor';key='/opt/core-backup-ops/ssh/id_ed25519';known='/opt/core-backup-ops/ssh/known_hosts';host=a['host'];user='root'
        else:root=None
        if root:
            cmd=['ssh','-i',key,'-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile='+known,'-o','ConnectTimeout=10','-o','ServerAliveInterval=10','-o','ServerAliveCountMax=1',user+'@'+host,
                 'test -d '+shlex.quote(root)+' && uname -s && df -Pk -- '+shlex.quote(root)+' && df -Pi -- '+shlex.quote(root)]
            r=subprocess.run(cmd,capture_output=True,text=True,timeout=40)
            if r.returncode:raise RuntimeError('SSH/source check failed: '+r.stderr[-250:])
            lines=r.stdout.strip().splitlines()
            if not lines or lines[0]!='Linux':raise ValueError('Expected Linux response missing')
            fields=lines[2].split();free=int(fields[3])*1024;total=int(fields[1])*1024
            result.update(status='ok',connection='ok',disk={'path':root,'free_bytes':free,'total_bytes':total},summary='SSH连接成功；源目录剩余 %.1fGiB'%(free/1024**3))
        else:
            credential=Path('/etc/core-remote/credentials')/(a['id']+'.json')
            if not credential.exists():raise ValueError('Management credential reference missing')
            modern=Path('/opt/core-remote/venv-modern/bin/python')
            python=str(modern) if modern.exists() else sys.executable
            r=subprocess.run([python,'/opt/core-remote/run_ps_absolute.py',a['id'],str(probe)],capture_output=True,text=True,timeout=50)
            if r.returncode:raise RuntimeError('WinRM check failed; '+r.stderr.splitlines()[-1][:180] if r.stderr else 'WinRM check failed')
            hostline=next((x.strip() for x in r.stdout.splitlines() if x.startswith('HOST=')),None)
            if not hostline:raise ValueError('WinRM returned no host evidence')
            disks=[]
            for line in r.stdout.splitlines():
                if line.startswith('DRIVE='):
                    drive,total,free=line.strip()[6:].split('|');disks.append({'drive':drive,'total_bytes':int(total),'free_bytes':int(free)})
            if not disks:raise ValueError('WinRM returned no disk evidence')
            result.update(status='ok',connection='ok',computer=hostline[5:],disks=disks,summary='WinRM连接成功；'+ '；'.join(x['drive']+'剩余%.1fGiB'%(x['free_bytes']/1024**3) for x in disks))
    except subprocess.TimeoutExpired:result.update(status='unknown',connection='timeout',summary='连接/目录检查超时；不代表备份已终止')
    except Exception as exc:result.update(status='unknown',connection='failed',summary='源端检查未通过；'+str(exc)[:250])
    result['seconds']=round(time.monotonic()-tick,1);save(out/(a['id']+'.json'),result)
    print(a['id'],result['status'],flush=True)
    return result
record={'status':'running','started':now(),'phase':'checking_sources','scope':'read-only connections/capacity; no backup/deletion/webhook'}
save(state/'check-result.json',record)
try:
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:results=list(pool.map(check,c['assets']))
    disk=shutil.disk_usage('/')
    record.update(phase='collecting_nas',ok=sum(r['status']=='ok' for r in results),total=len(results),ops_disk={'free_bytes':disk.free,'total_bytes':disk.total})
    save(state/'check-result.json',record)
finally:
    # This uses the existing collection lock; if cron is collecting it reuses that run.
    try:
        rc=subprocess.run(['/usr/bin/flock','-n',str(state/'collect.lock'),sys.executable,'/opt/backup-console/console.py','--collect'],timeout=210,check=False).returncode
        record.update(status='finished',phase='finished',finished=now(),collection_rc=rc)
    except subprocess.TimeoutExpired:record.update(status='failed',phase='nas_collection_timeout',finished=now())
    save(state/'check-result.json',record)

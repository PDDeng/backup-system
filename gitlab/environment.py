"""Environment/source checks. OS is explicitly configured, never guessed from IP."""
import base64
import json
from pathlib import Path
import re
import shlex
import socket
import subprocess
import time

def ps_command(script):
    encoded=base64.b64encode(script.encode('utf-16le')).decode('ascii')
    return 'powershell.exe -NoProfile -NonInteractive -EncodedCommand '+encoded

def check_source(job):
    c=job.c
    system=c.get('os','linux').lower()
    if system not in ('linux','windows'): raise ValueError('os must be linux or windows')
    started=time.monotonic()
    if system=='windows':
        # Windows OpenSSH must be explicitly configured; no password or remote service installation.
        path=c['backup_dir'].replace("'","''")
        script="$ErrorActionPreference='Stop'; $p='"+path+"'; $d=Get-Item -LiteralPath $p; if(-not $d.PSIsContainer){throw 'Not a directory'}; $f=@(Get-ChildItem -LiteralPath $p -File | Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 5); @{os='windows';computer=$env:COMPUTERNAME;files=@($f|Select-Object Name,Length,LastWriteTimeUtc)} | ConvertTo-Json -Depth 4 -Compress"
        data=json.loads(job.remote(ps_command(script)).decode('utf-8-sig'))
        return {'os':system,'connection':'ok','backup_files':data,'seconds':round(time.monotonic()-started,2),
                'collection_ready':False,'reason':'Windows source export/ready adapter not connected; inspection only'}
    detected=job.remote('uname -s').decode().strip()
    if detected!='Linux': raise ValueError('Configured linux but remote returned '+detected)
    root=c['backup_dir'].rstrip('/')
    job.remote('test -d '+shlex.quote(root))
    disk=job.remote('df -Pk -- '+shlex.quote(root)).decode()
    inode=job.remote('df -Pi -- '+shlex.quote(root)).decode()
    marker=root+'/.core-complete'
    output=job.remote('if test -d '+shlex.quote(marker)+'; then find '+shlex.quote(marker)+
                      ' -mindepth 2 -maxdepth 2 -type f -name DONE -print; fi').decode()
    valid=[p for p in output.splitlines() if re.fullmatch(re.escape(marker)+r'/\d{8}T\d{6}-\d+/DONE',p)]
    latest=sorted(valid)[-1] if valid else None
    size=None
    if latest:
        text=job.remote('cat -- '+shlex.quote(latest.rsplit('/',1)[0]+'/files.tsv')).decode()
        size=sum(int(line.split('\t')[1]) for line in text.splitlines())
    return {'os':system,'connection':'ok','latest_marker':latest,'backup_bytes':size,
            'files_tsv':text if latest else None,
            'disk':disk,'inodes':inode,'collection_ready':bool(latest),'seconds':round(time.monotonic()-started,2)}

def check_targets(job):
    results=[]
    for target in job.c['targets']:
        started=time.monotonic()
        row={'id':target['id'],'os':target.get('os','windows' if target['id']=='win254' else 'nas')}
        try:
            host=target['source'].lstrip('/').split('/')[0]
            with socket.create_connection((host,445),timeout=5): pass
            base=job.mount(target)
            # Mounted root is validated by worker before any I/O to avoid writing root filesystem.
            import os
            p=base/('.environment-test-'+str(os.getpid()))
            with p.open('xb') as f:f.write(b'core-env-test');f.flush();os.fsync(f.fileno())
            if p.read_bytes()!=b'core-env-test':raise ValueError('SMB readback mismatch')
            p.unlink();row['status']='ok'
            disk=os.statvfs(base)
            row.update(total_bytes=disk.f_blocks*disk.f_frsize,free_bytes=disk.f_bavail*disk.f_frsize,
                       mount=target['mount'],source=target['source'])
        except Exception as e:row.update(status='failed',error=str(e))
        row['seconds']=round(time.monotonic()-started,2);results.append(row)
    return results

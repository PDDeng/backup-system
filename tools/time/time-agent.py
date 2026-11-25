#!/usr/bin/env python
from __future__ import print_function
import base64
import datetime
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import time

NTP_EPOCH=2208988800

def cmd(args,check=True):
    p=subprocess.Popen(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    out,err=p.communicate()
    if check and p.returncode:raise RuntimeError('Command failed: '+args[0]+' '+err.decode('utf-8','replace')[:300])
    return out.decode('utf-8','replace').strip()

def which(name):
    for path in os.environ.get('PATH','').split(os.pathsep):
        p=os.path.join(path,name)
        if os.path.isfile(p) and os.access(p,os.X_OK):return p
    return None

def ntp_sample(host):
    # Query only: never sets clock. Check mode, leap status, stratum and originate timestamp.
    s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);s.settimeout(4)
    try:
        s.connect((host,123)); t1=time.time();stamp=t1+NTP_EPOCH
        tx=struct.pack('!II',int(stamp),int((stamp-int(stamp))*2**32))
        request=b'\x23'+b'\x00'*39+tx
        s.send(request);data=s.recv(512);t4=time.time()
        if len(data)<48:raise ValueError('Short NTP response')
        first=data[0] if isinstance(data[0],int) else ord(data[0])
        stratum=data[1] if isinstance(data[1],int) else ord(data[1])
        if first&7!=4 or first>>6==3 or not 1<=stratum<=15 or data[24:32]!=tx:
            raise ValueError('Invalid/unsynchronised NTP reply')
        words=struct.unpack('!12I',data[:48])
        t2=words[8]+words[9]/float(2**32)-NTP_EPOCH
        t3=words[10]+words[11]/float(2**32)-NTP_EPOCH
        delay=(t4-t1)-(t3-t2)
        if delay<-.01 or delay>2:raise ValueError('Unreliable NTP network delay')
        return {'server':host,'offset_seconds':((t2-t1)+(t3-t4))/2,'delay_seconds':delay}
    finally:s.close()

def timezone_value(show,details):
    if show.startswith('Timezone=') and show.split('=',1)[1].strip():return show.strip()
    # CentOS7's older timedatectl has no show subcommand.
    found=re.search(r'^\s*Time zone:\s+(\S+)',details,re.M)
    return 'Timezone='+found.group(1) if found else show

def synchronized(details):
    return bool(re.search(r'^\s*(?:System clock synchronized|NTP synchronized):\s+yes\s*$',details,re.M))

def status():
    details=cmd(['timedatectl','status'],False)
    zone=timezone_value(cmd(['timedatectl','show','-p','Timezone'],False),details)
    return {'epoch':time.time(),'local_time':cmd(['date','-Is']),
      'timezone':zone,
      'chrony_tracking':cmd(['chronyc','tracking'],False) if which('chronyc') else 'chronyc not installed',
      'chrony_sources':cmd(['chronyc','-n','sources'],False) if which('chronyc') else '',
      'timedatectl':details}

def apply_timezone():
    wanted='/usr/share/zoneinfo/Asia/Shanghai'
    if not os.path.isfile(wanted):raise ValueError('Shanghai tzdata missing')
    if os.path.isdir('/etc/localtime') and not os.path.islink('/etc/localtime'):
        raise ValueError('/etc/localtime is a directory; requires separate reviewed repair')
    current=timezone_value(cmd(['timedatectl','show','-p','Timezone'],False),cmd(['timedatectl','status'],False))
    if current.strip()=='Timezone=Asia/Shanghai':return False
    backup='/root/core-time-backups/'+datetime.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')+'-'+str(os.getpid())
    os.makedirs(backup)
    if os.path.lexists('/etc/localtime'):
        if os.path.islink('/etc/localtime'):
            with open(backup+'/localtime-link.txt','w') as f:f.write(os.readlink('/etc/localtime'))
        else:shutil.copy2('/etc/localtime',backup+'/localtime')
    cmd(['timedatectl','set-timezone','Asia/Shanghai'])
    return backup

def configure_chrony(config):
    if not which('chronyd') or not which('chronyc'):raise ValueError('chrony not installed; no automatic package installation')
    # Refuse a clock controller migration or custom daemon instance as a side effect.
    for name in ('ntpd','systemd-timesyncd'):
        if cmd(['systemctl','is-active',name],False)=='active':raise ValueError(name+' is active; review migration before using chrony')
    service=None
    for name in ('chronyd','chrony'):
        load=cmd(['systemctl','show',name,'-p','LoadState'],False)
        if load=='LoadState=loaded':service=name;break
    if not service:raise ValueError('No chrony systemd service')
    path='/etc/chrony.conf' if os.path.isfile('/etc/chrony.conf') else '/etc/chrony/chrony.conf'
    if not os.path.isfile(path) or os.path.islink(path):raise ValueError('Unsupported chrony config path')
    processes=cmd(['ps','-C','chronyd','-o','args='],False)
    options=processes+'\n'+cmd(['systemctl','show',service,'-p','ExecStart','-p','ExecStartPre'],False)
    for optionfile in ('/etc/sysconfig/chronyd','/etc/default/chrony'):
        if os.path.isfile(optionfile):
            with open(optionfile) as f:options+='\n'+'\n'.join(line for line in f if not line.lstrip().startswith('#'))
    if re.search(r'(^|[\s\"\x27])-([A-Za-z]*[sxqQ])(?=[\s\"\x27]|$)',options):
        raise ValueError('Special chronyd options require manual review')
    custom=re.search(r'(?:^|\s)-f\s+(\S+)',processes)
    if custom and custom.group(1)!=path:raise ValueError('Custom chronyd config; not changed')
    # Full, explicit client-only configuration. No makestep/initstepslew directives.
    with open(path) as f:old=f.read()
    drift=re.search(r'^\s*driftfile\s+(\S+)\s*$',old,re.M)
    driftline='driftfile '+drift.group(1)+'\n' if drift else ''
    text='# Managed core-time v1: client-only, gradual correction, no automatic stepping\n'
    text+=''.join('server '+host+' iburst\n' for host in config['ntp_servers'])
    text+=driftline+'maxslewrate 1000\nrtcsync\n'
    was_active=cmd(['systemctl','is-active',service],False)=='active'
    if old==text and was_active:return {'changed':False,'service':service}
    # Existing NTP server/peer/refclock use is outside this client-only tool's scope.
    if re.search(r'^\s*(allow|peer|local|refclock)\b',old,re.M):
        raise ValueError('chrony serves clients/peers/refclocks; not overwritten')
    backup=path+'.before-core-time.'+datetime.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')+'-'+str(os.getpid())
    shutil.copy2(path,backup)
    with open(path,'w') as f:f.write(text);f.flush();os.fsync(f.fileno())
    try:
        cmd(['systemctl','restart' if was_active else 'start',service])
        cmd(['systemctl','enable',service])
        if cmd(['systemctl','is-active',service],False)!='active':raise ValueError('chrony did not start')
    except Exception:
        shutil.copy2(backup,path)
        raise ValueError('chrony start failed; original config restored; no further automatic restart')
    return {'changed':True,'service':service,'backup':backup,'correction':'slew only; no makestep'}

def main(config):
    os.umask(0o077)
    os.environ['LC_ALL']='C'
    if not config.get('ntp_servers') or any(not re.match(r'^[A-Za-z0-9.-]+$',s) for s in config['ntp_servers']):
        raise ValueError('Invalid NTP servers')
    result={'hostname':socket.gethostname(),'os':'linux','before':status(),'samples':[],'errors':[]}
    for host in config['ntp_servers']:
        try:result['samples'].append(ntp_sample(host))
        except Exception as e:result['errors'].append(host+': '+str(e))
    valid=result['samples']
    if len(valid)<2:raise ValueError('Fewer than two public NTP sources reachable: '+str(result['errors']))
    offsets=[r['offset_seconds'] for r in valid]
    if max(offsets)-min(offsets)>config.get('source_agreement_seconds',1):raise ValueError('NTP sources disagree; no changes')
    result['offset_seconds']=sum(offsets)/len(offsets)
    if config.get('apply'):
        if abs(result['offset_seconds'])>config.get('max_apply_offset_seconds',5):
            raise ValueError('Clock offset exceeds safe apply threshold; schedule reviewed correction window')
        result['timezone_change']=apply_timezone()
        result['chrony_change']=configure_chrony(config)
    result['after']=status()
    synced=('Normal' in result['after']['chrony_tracking'] or
            bool(re.search(r'^\s*[\^=#]\*',result['after']['chrony_sources'],re.M)) or
            synchronized(result['after'].get('timedatectl','')))
    result['status']='ok' if (abs(result['offset_seconds'])<=config.get('alert_offset_seconds',1)
       and result['after']['timezone']=='Timezone=Asia/Shanghai' and synced) else 'warning'
    return result

if __name__=='__main__':
    try:
        cfg=json.loads(sys.stdin.read());print(json.dumps(main(cfg),ensure_ascii=True))
    except Exception as e:
        print(json.dumps({'status':'failed','error':str(e)},ensure_ascii=True));sys.exit(1)

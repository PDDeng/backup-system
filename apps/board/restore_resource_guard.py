"""Bounded pause of our isolated registry restore only, with PID/start-time ownership."""
import datetime,json,os,signal,subprocess,sys
from pathlib import Path
unit='harbor248-registry-restore-20261124.service'
record=Path('/opt/backup-board/validation/restore-resource-guard.json')
def identity():
 p=int(subprocess.check_output(['systemctl','show',unit,'-p','MainPID','--value'],text=True).strip())
 if p<=0:raise RuntimeError('No live isolated restore worker')
 if b'restore-harbor-registry-isolated.py' not in Path('/proc/'+str(p)+'/cmdline').read_bytes():raise RuntimeError('Worker identity differs')
 start=Path('/proc/'+str(p)+'/stat').read_text().rsplit(')',1)[1].split()[19]
 return p,start
def save(g):
 tmp=record.with_suffix('.next');tmp.write_text(json.dumps(g,indent=2));tmp.replace(record)
if sys.argv[1:] == ['pause']:
 if record.exists():raise SystemExit('Prior resource guard retained; no repeated pause')
 pid,start=identity();g=dict(pid=pid,start=start,unit=unit,status='prepared',prepared=datetime.datetime.now().astimezone().isoformat());save(g)
 subprocess.run(['systemd-run','--unit=backup-board-resume-harbor-20261124','--on-active=5m','/usr/bin/python3',str(Path(__file__).resolve()),'resume'],check=True)
 state=subprocess.check_output(['systemctl','is-active','backup-board-resume-harbor-20261124.timer'],text=True).strip()
 if state!='active' or identity()!=(pid,start):raise RuntimeError('Auto resume or ownership not proven')
 os.kill(pid,signal.SIGSTOP);g.update(status='paused',paused=datetime.datetime.now().astimezone().isoformat());save(g);print('Isolated registry restore paused; automatic resume in five minutes')
elif sys.argv[1:] == ['resume']:
 g=json.loads(record.read_text())
 if identity()!=(g['pid'],g['start']):raise RuntimeError('Worker changed; no signal sent')
 os.kill(g['pid'],signal.SIGCONT);g.update(status='resumed',resumed=datetime.datetime.now().astimezone().isoformat());save(g);print('Owned isolated registry restore resumed')
else:raise SystemExit('pause or resume required')

"""Read snapshots and exact pending-bundle metadata; no payload reads or mutations."""
import datetime,json,os,re,subprocess
from pathlib import Path
from content_audit import verified_at
source=Path('/var/lib/backup-console/snapshot.json')
if source.stat().st_size>8*1024**2:raise SystemExit('Snapshot too large')
data=json.loads(source.read_text(encoding='utf-8-sig'))
keys=('id','name','type','host','asset','os','targets','max_age_days','copies','execution','diagnosis','restore','retention','schedule','note')
out=dict(sampled=data.get('sampled'),collecting=data.get('collecting',False),targets=data.get('targets',[]),assets=[{k:a[k] for k in keys if k in a} for a in data.get('assets',[])])
# A completed restore observation may be newer than the slower NAS inventory.
# Project only the standard status/scope fields; never expose private runtime config.
for a in out['assets']:
 ident=a.get('id','')
 if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',ident):continue
 p=Path('/var/lib/backup-console/restore')/(ident+'.json')
 try:
  if p.is_symlink() or p.stat().st_size>1024**2:continue
  r=json.loads(p.read_text(encoding='utf-8-sig'))
  stamp=datetime.datetime.fromisoformat(r['sampled'])
  age=(datetime.datetime.now(datetime.timezone.utc)-stamp).total_seconds()
  if not -300<=age<=1200:continue
  prior=a.get('restore') or {}
  if prior.get('sampled') and datetime.datetime.fromisoformat(prior['sampled'])>stamp:continue
  if r.get('status') not in ('passed','partial','running','failed','incomplete'):continue
  a['restore']={k:r[k] for k in ('sampled','status','scope','untested','deletion') if k in r}
 except (OSError,ValueError,KeyError,TypeError):pass

mounts={t['id']:t for t in data.get('targets',[]) if t.get('status')=='mounted'}
for a in out['assets']:
 run=(a.get('execution') or {}).get('run','');a['transfers']=[]
 if a.get('type')!='GitLab' or not re.fullmatch(r'\d{8}T\d{6}-\d+',run) or Path(a['asset']).name!=a['asset']:continue
 day=run[:4]+'-'+run[4:6]+'-'+run[6:8]
 for tid in a.get('targets',[]):
  t=mounts.get(tid)
  if not t:continue
  try:
   q=subprocess.run(['findmnt','-rn','-M',t['path'],'-o','FSTYPE'],capture_output=True,text=True,timeout=5)
   if q.returncode or q.stdout.strip()!='cifs':continue
   folder=Path(t['path'])/a['asset']/day/run;p=folder/'pending.json'
   if any(x.is_symlink() for x in (folder,folder.parent,folder.parent.parent)) or p.is_symlink() or p.stat().st_size>1024**2:continue
   r=json.loads(p.read_text(encoding='utf-8-sig'))
   if (r.get('owner'),r.get('asset'),r.get('run'),r.get('date'),r.get('verification'))!=('core-distributor-v1',a['asset'],run,day,'pending_md5'):continue
   files=r.get('files',{})
   if not 1<=len(files)<=128 or {f.name for f in folder.iterdir()}!=set(files)|{'pending.json'}:continue
   valid=True
   for name,entry in files.items():
    if Path(name).name!=name or any(x in name for x in ('/','\\','\x00')) or name in ('.','..'):valid=False;break
    f=folder/name
    if type(entry.get('size')) is not int or entry['size']<=0 or f.is_symlink() or not f.is_file() or f.stat().st_size!=entry['size']:valid=False;break
   if valid:
    stamp=None;proof=Path('/var/lib/core-backup-ops/content-audits')/(a['id']+'.json')
    try:
     if not proof.is_symlink() and proof.stat().st_size<=1024**2:
      stamp=verified_at(json.loads(proof.read_text(encoding='utf-8-sig')),a['asset'],run,tid,files)
    except (OSError,ValueError,TypeError):pass
    a['transfers'].append(dict(target=tid,status='files_md5_rechecked' if stamp else 'files_size_rechecked',run=run,date=day,verification=('md5_verified_at '+stamp) if stamp else 'pending_md5',files=len(files)))
  except (OSError,ValueError,KeyError,TypeError,subprocess.SubprocessError):pass
root=Path('/var/lib/backup-board-feed');root.mkdir(mode=0o755,exist_ok=True)
root.chmod(0o755)
p=root/'snapshot.json';tmp=root/'snapshot.next';tmp.write_text(json.dumps(out,ensure_ascii=False),encoding='utf-8');tmp.chmod(0o644);os.replace(tmp,p)

# Optional real-evidence extras: 7-day trend (from real NAS receipts + daily snapshots) & read-only SMB directory browser
try:
 from model import project as project_board
 from feed_extras import build_history_7d, build_smb_browser
 hist_path=root/'history-7d.json'
 prior_hist=None
 try:
  if hist_path.is_file() and not hist_path.is_symlink() and hist_path.stat().st_size<=2*1024**2:
   prior_hist=json.loads(hist_path.read_text(encoding='utf-8-sig'))
 except (OSError,ValueError):pass
 projected_today=project_board(out)
 hist_data=build_history_7d(out,projected_today,prior_history=prior_hist)
 htmp=root/'history-7d.next';htmp.write_text(json.dumps(hist_data,ensure_ascii=False),encoding='utf-8');htmp.chmod(0o644);os.replace(htmp,hist_path)

 smb_data=build_smb_browser(out.get('targets',[]))
 smb_path=root/'smb-browser.json'
 stmp=root/'smb-browser.next';stmp.write_text(json.dumps(smb_data,ensure_ascii=False),encoding='utf-8');stmp.chmod(0o644);os.replace(stmp,smb_path)
except Exception:
 pass

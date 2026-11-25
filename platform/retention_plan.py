"""Conservative retention inventory. No delete function and no apply switch."""
import argparse,datetime,json,os,re
from pathlib import Path
from bundle_transfer import exact_mount

def complete_run(run,asset,date,owner):
 if run.is_symlink() or not run.is_dir() or run.name.startswith('.') or '.partial' in run.name:raise ValueError('Unpublished/linked run')
 receipt=next((run/n for n in ('complete.json','backup-result.json') if (run/n).is_file()),None)
 if receipt is None or receipt.is_symlink() or receipt.stat().st_size>4*1024**2:raise ValueError('Receipt unavailable')
 r=json.loads(receipt.read_text(encoding='utf-8-sig'))
 if r.get('owner')!=owner or r.get('asset')!=asset or r.get('date')!=date or r.get('run')!=run.name:raise ValueError('Receipt identity mismatch')
 if str(r.get('status','')).lower() in ('failed','error','partial','running'):raise ValueError('Unsuccessful status')
 files=r.get('files')
 if not isinstance(files,dict) or not files:raise ValueError('Unsupported/empty inventory')
 if {p.name for p in run.iterdir()} != set(files)|{receipt.name}:raise ValueError('Extra/missing entries')
 total=0
 for name,v in files.items():
  if not isinstance(name,str) or Path(name).name!=name or any(c in name for c in '/\\\x00') or name in ('.','..'):raise ValueError('Unsafe member')
  size=v.get('size');p=run/name
  if type(size)!=int or size<=0 or p.is_symlink() or not p.is_file() or p.stat().st_size!=size:raise ValueError('Payload mismatch')
  total+=size
 return total

def plan(root,asset,owner,keep):
 if type(keep)!=int or keep<1:raise ValueError('Positive complete-cycle count required')
 if root.is_symlink():raise ValueError('Linked asset root')
 valid=[];protected=[]
 for day in sorted(root.iterdir(),reverse=True):
  if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',day.name):protected.append({'path':str(day),'reason':'Not a date cycle'});continue
  try:
   d=datetime.date.fromisoformat(day.name)
   if d>datetime.date.today() or day.is_symlink() or not day.is_dir():raise ValueError('Future/linked/non-directory date')
   runs=list(day.iterdir())
   if not runs:raise ValueError('Empty date')
   total=sum(complete_run(r,asset,day.name,owner) for r in runs)
   valid.append({'date':day.name,'path':str(day),'runs':len(runs),'bytes':total})
  except (ValueError,OSError,KeyError,TypeError) as e:protected.append({'path':str(day),'reason':str(e)})
 return {'mode':'plan_only','deletion_performed':False,'keep_complete_cycles':keep,'complete_cycles':len(valid),'retained':valid[:keep],'excess_complete_cycles':valid[keep:],'protected':protected,'note':'Informational inventory, not deletion authorization; existing scheduled cleanup unchanged'}

def main():
 a=argparse.ArgumentParser();a.add_argument('--config',required=True);a.add_argument('--output',required=True);x=a.parse_args();c=json.loads(Path(x.config).read_text())
 asset=c['asset']
 if Path(asset).name!=asset or asset in ('','.','..') or any(y in asset for y in '/\\\x00'):raise ValueError('Unsafe asset')
 results={}
 for target in c['targets']:
  exact_mount(target['mount'],target['source']);results[target['id']]=plan(Path(target['mount'])/asset,asset,c['required_owner'],target['keep_complete_cycles'])
 r={'asset':asset,'sampled':datetime.datetime.now().astimezone().isoformat(),'targets':results,'deletion_performed':False}
 p=Path(x.output);tmp=p.with_suffix('.tmp');tmp.write_text(json.dumps(r,ensure_ascii=False,indent=2));tmp.replace(p);print('Retention inventory completed; no deletion')
if __name__=='__main__':os.umask(0o077);main()

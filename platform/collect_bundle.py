"""Collect a published standard bundle. No source backup, deletion or credential changes."""
import argparse,concurrent.futures,datetime,fcntl,hashlib,json,os,re,signal,subprocess,threading,time
from pathlib import Path
from bundle_transfer import exact_mount,transfer,validate_manifest,receipt_digest,destination_paths
os.umask(0o077)
def now():return datetime.datetime.now().astimezone().isoformat()
def atomic(path,value):
    temp=path.with_name(path.name+'.'+str(os.getpid())+'.tmp')
    with temp.open('w') as f:json.dump(value,f,ensure_ascii=False,indent=2);f.flush();os.fsync(f.fileno())
    temp.replace(path)
def native_evidence(receipt):
    value=receipt.get('native_verification',receipt.get('verification'))
    if not isinstance(value,str):raise ValueError('Native verification must be an explicit string')
    legacy={'RMAN VALIDATE BACKUPSET (all logged pieces); transfer size_only':'all_logged_RMAN_pieces_validated'}
    return legacy.get(value,value)

def valid_run_name(name,style='rman'):
    patterns={'rman':r'RMAN\d{8}T\d{6}',
              'oracle_logical':r'\d{8}_\d{6}-[a-f0-9]{4}'}
    if style not in patterns:raise ValueError('Unknown source run style')
    return re.fullmatch(patterns[style],name) is not None

def choose(c):
    s=c['source'];exact_mount(s['mount'],s['source']);root=Path(s['mount'])/c['asset']
    if c.get('source_format')=='dse-nas-v1':
        from source_adapters import legacy_dse
        return legacy_dse(c)
    if c.get('source_format','standard_bundle')!='standard_bundle':raise ValueError('Unknown source adapter')
    for day in sorted(root.iterdir(),reverse=True):
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',day.name) or day.is_symlink() or not day.is_dir():continue
        for run in sorted(day.iterdir(),reverse=True):
            if not valid_run_name(run.name,c.get('run_style','rman')) or run.is_symlink() or not run.is_dir():continue
            p=run/'complete.json'
            if not p.is_file() or p.is_symlink():continue
            r=json.loads(p.read_text(encoding='utf-8-sig'));validate_manifest(r)
            if r['asset']!=c['asset'] or r['date']!=day.name or r['run']!=run.name or r.get('owner')!=c['required_owner'] or native_evidence(r)!=c['required_verification']:raise ValueError('Published source identity/native verification mismatch')
            age=(datetime.date.today()-datetime.date.fromisoformat(r['date'])).days
            if not 0<=age<=c.get('max_age_days',2):raise ValueError('Latest complete source is stale or in future')
            for name,entry in r['files'].items():
                f=run/name
                if f.is_symlink() or not f.is_file() or f.stat().st_size!=entry['size']:raise ValueError('Source file incomplete: '+name)
            if {x.name for x in run.iterdir()}!=set(r['files'])|{'complete.json'}:raise ValueError('Unexpected source files; bundle preserved')
            return run,r
    raise ValueError('No published native-validated source bundle')
def phase_times(c,receipt):
    values={}
    if isinstance(receipt.get('backup_seconds'),(int,float)):values['source_backup_seconds']=receipt['backup_seconds']
    if c.get('source_evidence_ref') and 'source_backup_seconds' not in values:
        try:
            old=json.loads(Path(c['source_evidence_ref']).read_text(encoding='utf-8-sig'))
            if old.get('Run')==receipt['run']:
                seconds=(datetime.datetime.fromisoformat(old['Finished'])-datetime.datetime.fromisoformat(old['Started'])).total_seconds()
                if seconds>=0:values['source_backup_seconds']=round(seconds,1)
        except (OSError,ValueError,KeyError,TypeError):pass
    finished=[]
    for target in c['targets']:
        try:
            _,final=destination_paths(target['mount'],receipt,c.get('layout','date_run'));p=final/'complete.json';r=json.loads(p.read_text(encoding='utf-8-sig'))
            if r['source_manifest_sha256']!=receipt_digest(receipt):continue
            finished.append(datetime.datetime.fromisoformat(r['transfer_finished']))
        except (OSError,ValueError,KeyError,TypeError):pass
    if len(finished)==len(c['targets']) and finished:
        try:
            seconds=(max(finished)-datetime.datetime.fromisoformat(receipt['finished'])).total_seconds()
            if seconds>=0:values['initial_distribution_seconds']=round(seconds,1)
        except (ValueError,KeyError,TypeError):pass
    return values

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--config',required=True);ap.add_argument('--check',action='store_true');a=ap.parse_args();c=json.loads(Path(a.config).read_text())
    ids=[c['source']['id']]+[t['id'] for t in c['targets']]
    if len(ids)!=len(set(ids)):raise ValueError('Duplicate source/target IDs')
    if Path(c['asset']).name!=c['asset'] or c['asset'] in ('','.','..'):raise ValueError('Invalid asset directory')
    state=Path(c['state_dir']);state.mkdir(parents=True,exist_ok=True,mode=0o700)
    with (state/'collect.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:print('Existing collection active; no duplicate started');return 0
        source,receipt=choose(c)
        for t in c['targets']:exact_mount(t['mount'],t['source'])
        if a.check:print(json.dumps(dict(status='check_ok',run=receipt['run'],files=len(receipt['files']),no_copy_or_cleanup=True)));return 0
        start=time.monotonic();cancel=threading.Event();mutex=threading.Lock()
        signal.signal(signal.SIGTERM,lambda *_:cancel.set());signal.signal(signal.SIGINT,lambda *_:cancel.set())
        result=dict(status='running',run=receipt['run'],started=now(),targets={c['source']['id']:True},native_verification=native_evidence(receipt),verification='size_only_transfer',restore='not_tested',details={})
        for t in c['targets']:result['targets'][t['id']]=False
        def save():result['sampled']=now();atomic(state/'last-result.json',result)
        def one(t):
            def progress(value):
                with mutex:result['details'][t['id']]=value;save()
            try:return t['id'],transfer(source,receipt,t,progress,rate_mib=c.get('rate_mib',8),cancelled=cancel.is_set,layout=c.get('layout','date_run'))
            except Exception as e:return t['id'],dict(status='failed',error=str(e),finished=now())
        save()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            for f in concurrent.futures.as_completed([pool.submit(one,t) for t in c['targets']]):
                tid,value=f.result()
                with mutex:result['details'][tid]=value;result['targets'][tid]=value['status']=='success';save()
        result.update(status='success' if all(result['targets'].values()) else 'failed',seconds=round(time.monotonic()-start,1),finished=now(),cleanup='not_executed');result['operation']='recheck_existing' if all(v.get('reused') for v in result['details'].values()) and result['status']=='success' else 'transfer';result.update(phase_times(c,receipt));save()
        if result['status']=='failed':
            signature=hashlib.sha256(json.dumps(dict(run=result['run'],targets=result['targets']),sort_keys=True).encode()).hexdigest();dedup=state/'last-alert.json'
            prior=json.loads(dedup.read_text()) if dedup.exists() else {}
            if prior.get('signature')!=signature:
                message=c.get('label',c['asset'])+'分发失败；批次 '+result['run']+'；失败目标 '+','.join(k for k,v in result['targets'].items() if not v)+'；副本和失败目录均保留，请查看控制台。'
                p=subprocess.run(['/usr/bin/python3','/opt/core-backup-modular/notify.py','/etc/core-backup-ops/notify.json','CRITICAL',c['asset'],message],timeout=45)
                if p.returncode==0:atomic(dedup,dict(signature=signature,sent=now()))
        print(json.dumps(result,ensure_ascii=False));return 0 if result['status']=='success' else 1
if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception as error:
        if '--check' in __import__('sys').argv:raise
        args=__import__('sys').argv
        if '--config' not in args:raise
        c=json.loads(Path(args[args.index('--config')+1]).read_text());state=Path(c['state_dir']);state.mkdir(parents=True,exist_ok=True,mode=0o700)
        result=dict(status='failed',phase='preflight_failed',sampled=now(),error=str(error),targets={},cleanup='not_executed')
        atomic(state/'last-result.json',result)
        signature=hashlib.sha256(('preflight:'+str(error)).encode()).hexdigest();dedup=state/'last-alert.json';prior=json.loads(dedup.read_text()) if dedup.exists() else {}
        if prior.get('signature')!=signature:
            message=c.get('label',c['asset'])+'收集预检查失败；未开始复制或清理，请查看控制台。原因：'+str(error)[:250]
            p=subprocess.run(['/usr/bin/python3','/opt/core-backup-modular/notify.py','/etc/core-backup-ops/notify.json','CRITICAL',c['asset'],message],timeout=45)
            if p.returncode==0:atomic(dedup,dict(signature=signature,sent=now()))
        print(json.dumps(result,ensure_ascii=False));raise SystemExit(1)


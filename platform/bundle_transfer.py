"""Transfer a completed bundle to exact mounted SMB targets; never remove files.
Source adapters own native validation. This module owns durable copy/publication.
"""
import concurrent.futures,ctypes,datetime,hashlib,json,os,re,subprocess,time,uuid
from pathlib import Path

def now():return datetime.datetime.now().astimezone().isoformat()

def exact_mount(mount,source):
    info=json.loads(subprocess.check_output(['findmnt','-J','-M',str(mount),'-o','SOURCE,FSTYPE'],timeout=10))['filesystems'][0]
    if info['fstype']!='cifs' or info['source']!=source:raise ValueError('Unexpected CIFS mount '+str(mount))

def publish_no_replace(source,target):
    lib=ctypes.CDLL(None,use_errno=True)
    lib.renameat2.argtypes=[ctypes.c_int,ctypes.c_char_p,ctypes.c_int,ctypes.c_char_p,ctypes.c_uint]
    if lib.renameat2(-100,os.fsencode(source),-100,os.fsencode(target),1)!=0:raise OSError(ctypes.get_errno(),'No-overwrite publication failed')

def validate_manifest(receipt):
    if receipt.get('schema')!='core-backup-bundle/1.0':raise ValueError('Unsupported bundle schema')
    for key in ('asset','run','date'):
        value=receipt[key]
        if not isinstance(value,str) or not value or Path(value).name!=value or any(x in value for x in '/\\\x00') or value in ('.','..'):raise ValueError('Invalid identity '+key)
    datetime.date.fromisoformat(receipt['date'])
    if not receipt.get('files'):raise ValueError('Empty bundle')
    for name,entry in receipt['files'].items():
        if Path(name).name!=name or any(x in name for x in '/\\\x00') or name in ('.','..','complete.json','pending.json') or not name:raise ValueError('Unsafe filename')
        if type(entry.get('size')) is not int or entry['size']<=0:raise ValueError('Invalid file size')

def receipt_digest(receipt):
    return hashlib.sha256(json.dumps(receipt,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()

def destination_paths(mount,receipt,layout='date_run'):
    asset=Path(mount)/receipt['asset']
    if layout=='date_run':return asset/receipt['date'],asset/receipt['date']/receipt['run']
    if layout=='date':return asset,asset/receipt['date']
    raise ValueError('Unsupported bundle layout')

def transfer(source,receipt,target,progress=lambda value:None,rate_mib=8,mount_guard=exact_mount,publisher=publish_no_replace,cancelled=lambda:False,layout='date_run'):
    """Copies once into a fresh attempt directory; failed attempts are retained."""
    validate_manifest(receipt)
    if rate_mib<=0:raise ValueError('Positive copy rate required')
    source=Path(source);mount=Path(target['mount']);mount_guard(mount,target['source'])
    digest=receipt_digest(receipt);day,final=destination_paths(mount,receipt,layout)
    if day.is_symlink() or day.parent.is_symlink() or final.is_symlink():raise ValueError('Destination symlink')
    day.mkdir(parents=True,exist_ok=True)
    if final.exists():
        old=json.loads((final/'complete.json').read_text(encoding='utf-8-sig'))
        if old.get('source_manifest_sha256')!=digest:raise ValueError('Existing bundle identity differs; preserved')
        if {x.name for x in final.iterdir()}!=set(receipt['files'])|{'complete.json'}:raise ValueError('Unexpected existing files; preserved')
        for name,entry in receipt['files'].items():
            p=final/name
            if p.is_symlink() or not p.is_file() or p.stat().st_size!=entry['size']:raise ValueError('Existing target mismatch; preserved')
        return dict(status='success',reused=True,run=receipt['run'],transfer_verification='size_only',finished=now())
    need=sum(x['size'] for x in receipt['files'].values())
    v=os.statvfs(mount)
    if v.f_bavail*v.f_frsize<need+20*1024**3:raise ValueError('Target free space below bundle plus reserve')
    stage=day/('.'+receipt['run']+'.attempt-'+uuid.uuid4().hex[:10]+'.partial');stage.mkdir()
    started=time.monotonic();total=0
    for name,entry in receipt['files'].items():
        mount_guard(mount,target['source']);src=source/name;dst=stage/name
        if src.is_symlink() or not src.is_file():raise ValueError('Source file missing or symlink')
        before=src.stat()
        if before.st_size!=entry['size']:raise ValueError('Source size mismatch')
        tick=time.monotonic();copied=0;last=0
        with src.open('rb') as a,dst.open('xb') as b:
            while True:
                if cancelled():raise InterruptedError('Transfer cancelled; partial preserved')
                block=a.read(1024**2)
                if not block:break
                b.write(block);copied+=len(block);total+=len(block)
                delay=copied/(rate_mib*1024**2)-(time.monotonic()-tick)
                if delay>0:time.sleep(delay)
                if time.monotonic()-last>=30:
                    progress(dict(status='running',sampled=now(),file=name,file_copied_bytes=copied,total_copied_bytes=total,total_bytes=need,stage=str(stage)));last=time.monotonic()
            b.flush();os.fsync(b.fileno())
        after=src.stat()
        if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns) or dst.stat().st_size!=entry['size']:raise ValueError('Source changed or destination size mismatch')
    out=dict(receipt,source_manifest_sha256=digest,transfer_verification='size_only',target=target['id'],transfer_finished=now())
    with (stage/'complete.json').open('x') as f:
        json.dump(out,f,ensure_ascii=False,indent=2);f.flush();os.fsync(f.fileno())
    mount_guard(mount,target['source']);publisher(stage,final)
    return dict(status='success',run=receipt['run'],seconds=round(time.monotonic()-started,1),transfer_verification='size_only',finished=now(),path=str(final))

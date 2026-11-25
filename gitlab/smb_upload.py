"""Mounted-SMB upload module; integrity policy supplied separately."""
import os
import shutil
from md5_verify import matches

def copy_file(source,target,info,verify_md5,guard):
    guard()
    if target.is_symlink(): raise ValueError('Target symlink')
    if not matches(target,info,verify_md5):
        # Fast mode recopies existing unverified partials; size alone is no proof of prior success.
        with source.open('rb') as src,target.open('wb') as dst:
            shutil.copyfileobj(src,dst,4*1024*1024)
            dst.flush();os.fsync(dst.fileno())
    if not matches(target,info,verify_md5): raise ValueError('Target size/MD5 mismatch')

def publish(stage,day,run,receipt,verify_md5,guard,atomic,notice):
    dest=day/run
    if dest.is_symlink(): raise ValueError('Destination symlink')
    status='complete.json' if verify_md5 else 'pending.json'
    if dest.exists():
        # Existing verified copies must never be downgraded or overwritten silently.
        import json
        complete=dest/'complete.json'
        pending=dest/'pending.json'
        existing=complete if complete.exists() else pending
        if existing.is_symlink() or not existing.is_file(): raise ValueError('Unmanaged existing directory')
        old=json.loads(existing.read_text(encoding='utf-8'))
        for key in ('owner','asset','run','date'):
            if old.get(key)!=receipt.get(key): raise ValueError('Existing identity conflict')
        if set(p.name for p in dest.iterdir()) != set(receipt['files'])|{existing.name}: raise ValueError('Unexpected existing files')
        for name,info in receipt['files'].items():
            if old['files'].get(name,{}).get('size')!=info['size'] or not matches(dest/name,info,verify_md5):
                raise ValueError('Existing file mismatch; retained for investigation')
        if verify_md5 and existing.name=='pending.json':
            guard();atomic(complete,receipt);pending.unlink()
            notice('SUCCESS','Pending copy upgraded to full MD5 verification')
            return receipt
        return old
    temp=day/('.'+run+'.partial')
    if temp.is_symlink(): raise ValueError('Partial symlink')
    temp.mkdir(exist_ok=True)
    for name,info in receipt['files'].items():
        if not verify_md5 and (temp/name).exists():
            if (temp/name).is_symlink(): raise ValueError('Partial file symlink')
            (temp/name).unlink()
        copy_file(stage/name,temp/name,info,verify_md5,guard)
    allowed=set(receipt['files'])|{'complete.json','pending.json'}
    if any(p.name not in allowed for p in temp.iterdir()): raise ValueError('Unknown partial contents')
    atomic(temp/status,receipt)
    guard();os.rename(temp,dest)
    return receipt

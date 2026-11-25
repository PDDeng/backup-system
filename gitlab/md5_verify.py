"""Integrity module. Fast mode never calls digest or labels bytes MD5-verified."""
import hashlib
import re
import shlex

def digest(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError('Expected regular file: '+str(path))
    h=hashlib.md5()
    with path.open('rb') as f:
        for data in iter(lambda:f.read(4*1024*1024),b''): h.update(data)
    return h.hexdigest()

def source_hash(remote,path):
    value=remote('nice -n 19 md5sum -- '+shlex.quote(path)).decode().split()[0]
    if not re.fullmatch('[0-9a-f]{32}',value): raise ValueError('Invalid source MD5')
    return value

def matches(path,info,full=True):
    if path.is_symlink() or not path.is_file() or path.stat().st_size!=info['size']: return False
    return not full or digest(path)==info['md5']

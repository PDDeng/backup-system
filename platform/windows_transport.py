"""Root-only Windows transport. Credentials and OpenSSL compatibility stay isolated."""
import ctypes,ctypes.util,json,os,re
from pathlib import Path
_providers=[]
def session(asset):
    if os.geteuid()!=0 or not re.fullmatch(r'[a-z]+[0-9]+',asset):raise ValueError('Root and registered asset ID required')
    p=Path('/etc/core-remote/credentials')/(asset+'.json')
    if p.stat().st_mode & 0o077:raise ValueError('Credential permissions must be private')
    x=json.loads(p.read_text())
    lib=ctypes.CDLL(ctypes.util.find_library('crypto'))
    lib.OSSL_PROVIDER_load.argtypes=[ctypes.c_void_p,ctypes.c_char_p];lib.OSSL_PROVIDER_load.restype=ctypes.c_void_p
    _providers.extend(lib.OSSL_PROVIDER_load(None,n) for n in (b'default',b'legacy'))
    import winrm
    return winrm.Session('http://'+x['host']+':5985/wsman',auth=(x['username'],x['password']),transport='ntlm',message_encryption='always',operation_timeout_sec=20,read_timeout_sec=35)

def decode(raw):
    try:return raw.decode('utf-8')
    except UnicodeDecodeError:return raw.decode('gb18030',errors='replace')

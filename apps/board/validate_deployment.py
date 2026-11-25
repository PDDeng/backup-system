"""Read-only runtime validation; never rebuild, restart or modify accounts."""
import argparse,hashlib,json,subprocess
from pathlib import Path

def main():
 p=argparse.ArgumentParser();p.add_argument('--container',default='core-backup-board-web-v1');p.add_argument('--source',type=Path,default=Path('/opt/backup-board'));a=p.parse_args()
 if not a.container.startswith('core-backup-board-'):raise SystemExit('Unexpected container name')
 def command(args):return subprocess.run(args,check=True,capture_output=True,text=True,timeout=30).stdout
 inspect=json.loads(command(['docker','inspect',a.container]))[0]
 if not inspect['State']['Running']:raise SystemExit('Dashboard not running; no restart attempted')
 if not inspect['HostConfig']['ReadonlyRootfs']:raise SystemExit('Expected read-only root filesystem')
 names=['app.py','model.py','acceptance.py']
 code="import hashlib,json;print(json.dumps({n:hashlib.sha256(open('/app/'+n,'rb').read()).hexdigest() for n in "+repr(names)+"}))"
 actual=json.loads(command(['docker','exec',a.container,'python','-c',code]))
 for name in names:
  if hashlib.sha256((a.source/name).read_bytes()).hexdigest()!=actual[name]:raise SystemExit('Source mismatch: '+name+'; no rebuild attempted')
 probe="""import urllib.request,urllib.error,json
base='http://127.0.0.1:8100'
assert urllib.request.urlopen(base+'/health',timeout=5).status==200
assert 'acceptdialog' in urllib.request.urlopen(base+'/',timeout=5).read().decode()
try:
 urllib.request.urlopen(base+'/api/board',timeout=5)
 raise AssertionError('Unauthenticated board was exposed')
except urllib.error.HTTPError as e:assert e.code==401
print(json.dumps({'health':200,'unauthenticated_board':401,'manual_form_present':True}))
"""
 print(command(['docker','exec',a.container,'python','-c',probe]))
 print('Read-only deployment checks passed; browser interaction, backup integrity and restore are separate gates.')
if __name__=='__main__':main()

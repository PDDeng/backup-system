#!/usr/bin/env python3
"""Set SMB retention policy, backing up every modified job config. Does not run jobs."""
import argparse, datetime, json, os, sys
from pathlib import Path
p=argparse.ArgumentParser()
p.add_argument('--days', type=int, required=True)
p.add_argument('--mode', choices=('plan','delete'), default='plan')
p.add_argument('--config-dir', default='/etc/core-backup-ops')
p.add_argument('--target',choices=('nas243','nas240','win254'),help='Only override this destination; global cleanup mode stays unchanged')
a=p.parse_args()
if not 1 <= a.days <= 3650: p.error('days must be 1..3650')
if a.target and '--mode' in sys.argv:p.error('--target changes retention only; omit --mode')
os.umask(0o077)
root=Path(a.config_dir)
paths=[root/('ops'+sid+'.json') for sid in ('185','52','191','247','14','16')]
configs=[json.loads(path.read_text(encoding='utf-8-sig')) for path in paths]
if a.target and any(sum(t.get('id')==a.target for t in c['targets'])!=1 for c in configs):p.error('Target must occur exactly once in every config')
stamp=datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
for path,c in zip(paths,configs):
    backup=path.with_name(path.name+'.before-retention.'+stamp)
    backup.write_bytes(path.read_bytes());backup.chmod(0o600)
    if a.target:
        next(t for t in c['targets'] if t['id']==a.target)['keep_days']=a.days
    else:c.update(keep_days=a.days,cleanup_mode=a.mode)
    temp=path.with_name(path.name+'.retention.tmp')
    temp.write_text(json.dumps(c,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    temp.chmod(0o600);os.replace(temp,path)
    print(str(path)+': '+(a.target+' ' if a.target else '')+'keep_complete_dates='+str(a.days)+' cleanup='+c.get('cleanup_mode','plan'))
print('No backup or deletion performed. Cleanup is independent of MD5; off uses existence/size checks.')

#!/usr/bin/env python3
"""Register a GitLab configuration from an existing trusted template. No SSH changes."""
import argparse
import json
import os
import re
from pathlib import Path

p=argparse.ArgumentParser()
p.add_argument('--id', required=True)
p.add_argument('--host', required=True)
p.add_argument('--asset', required=True)
p.add_argument('--backup-dir', required=True)
p.add_argument('--key', default='/opt/core-backup-ops/ssh/id_ed25519')
p.add_argument('--template', default='/etc/core-backup-ops/ops185.json')
p.add_argument('--config-dir', default='/etc/core-backup-ops')
a=p.parse_args()
if not re.fullmatch(r'\d+',a.id) or not re.fullmatch(r'[A-Za-z0-9_.\-\u4e00-\u9fff]+',a.asset):
    p.error('Invalid ID or asset')
if not a.backup_dir.startswith('/') or not re.fullmatch(r'[A-Za-z0-9.\-]+',a.host):
    p.error('Invalid source host/path')
os.umask(0o077)
c=json.loads(Path(a.template).read_text(encoding='utf-8-sig'))
c.update(asset=a.asset,host=a.host,key=a.key,backup_dir=a.backup_dir.rstrip('/'),
         state_dir='/var/lib/core-backup-ops/gitlab'+a.id,os='linux')
destination=Path(a.config_dir)/('ops'+a.id+'.json')
with destination.open('x',encoding='utf-8') as f:
    json.dump(c,f,ensure_ascii=False,indent=2);f.write('\n')
destination.chmod(0o600)
print('Created '+str(destination)+'. No trust changes, schedules, backup or upload performed.')

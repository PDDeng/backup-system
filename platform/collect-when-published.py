"""Dispatch only a recently observed, published source run; no source operations."""
import argparse,datetime,json,re,subprocess,sys
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--config',required=True);a=p.parse_args();c=json.loads(Path(a.config).read_text())
x=json.loads((Path(c['state_dir'])/'source-observation.json').read_text());s=x.get('source_state') or {}
age=(datetime.datetime.now(datetime.timezone.utc)-datetime.datetime.fromisoformat(x['sampled'])).total_seconds()
if x.get('connection')!='ok' or not -300<=age<=600 or s.get('Status')!='source_complete' or s.get('Phase')!='published' or not re.fullmatch(r'\d{8}_\d{6}-[a-f0-9]{4}',s.get('Run','')):
 print('DEFERRED: no fresh published daily source observation; no transfer started');raise SystemExit(0)
raise SystemExit(subprocess.run([sys.executable,'/opt/core-backup-platform/collect-oracle46-bundle.py','--config',a.config]).returncode)

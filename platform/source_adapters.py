"""Read-only compatibility adapters: producers keep their native generation scripts."""
import datetime,json,re
from pathlib import Path
from bundle_transfer import validate_manifest

def legacy_dse(c):
    root=Path(c['source']['mount'])/c['asset']
    if c['required_owner']!='dse-standard-adapter-v1' or c.get('required_verification')!='not_provided_by_source':raise ValueError('Unexpected DSE adapter owner')
    for day in sorted(root.iterdir(),reverse=True):
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',day.name) or day.is_symlink() or not day.is_dir():continue
        p=day/'complete.json'
        if not p.is_file() or p.is_symlink():continue
        old=json.loads(p.read_text(encoding='utf-8-sig'))
        if old.get('owner')!='dse-nas-v1' or old.get('asset')!=c['asset'] or old.get('date')!=day.name:raise ValueError('DSE source receipt identity mismatch')
        age=(datetime.date.today()-datetime.date.fromisoformat(day.name)).days
        if not 0<=age<=c.get('max_age_days',9):raise ValueError('Latest DSE source stale or future')
        if not isinstance(old.get('files'),list) or not old['files']:raise ValueError('DSE receipt file list missing')
        files={}
        for entry in old['files']:
            name=entry['name']
            if not re.fullmatch(re.escape(day.name)+r' \d{2}-\d{2}-\d{2}\.bak',name) or name in files:raise ValueError('Invalid or duplicate DSE file')
            payload=day/name;size=entry['bytes']
            if type(size)is not int or size<=0 or payload.is_symlink() or not payload.is_file() or payload.stat().st_size!=size:raise ValueError('DSE file size mismatch')
            files[name]={'size':size}
        if {x.name for x in day.iterdir()}!=set(files)|{'complete.json'}:raise ValueError('Unexpected DSE source files; preserved')
        receipt=dict(schema='core-backup-bundle/1.0',owner='dse-standard-adapter-v1',asset=c['asset'],run='DSE'+day.name.replace('-',''),date=day.name,files=files,verification='size_only',native_verification='not_provided_by_source',source_format='dse-nas-v1',finished=old['finished'],restore='not_tested_by_transfer')
        validate_manifest(receipt);return day,receipt
    raise ValueError('No completed DSE source cycle')

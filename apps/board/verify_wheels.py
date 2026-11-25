import hashlib,json,sys
from pathlib import Path
root=Path(sys.argv[1]);manifest=json.loads((root/'provenance.json').read_text())['files']
if {p.name for p in root.glob('*.whl')}!=set(manifest):raise SystemExit('Wheel inventory differs')
for name,entry in manifest.items():
 if Path(name).name!=name or hashlib.sha256((root/name).read_bytes()).hexdigest()!=entry['sha256']:raise SystemExit('Wheel integrity failure')
print('Wheel SHA256 inventory verified: '+str(len(manifest)))

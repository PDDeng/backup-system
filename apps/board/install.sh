#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
test "$(id -u)" = 0 || { echo 'Run as root'; exit 1; }
docker compose version >/dev/null
install -d -m 700 secrets
python3 - <<'PY'
import os,secrets
from pathlib import Path
for name in ('db_password','app_secret'):
 p=Path('secrets')/name
 if not p.exists():
  with p.open('x') as f:f.write(secrets.token_urlsafe(48)+'\n')
  p.chmod(0o444)
PY
if ! test -e .env; then printf 'PORT=8100\nBIND_IP=0.0.0.0\nCOOKIE_SECURE=false\n' > .env; chmod 600 .env; fi
install -m 600 content_audit.py /opt/core-backup-platform/content_audit.py
install -m 700 export_feed.py /opt/core-backup-platform/export-board-feed.py
python3 /opt/core-backup-platform/export-board-feed.py
cat > /etc/systemd/system/core-backup-board-feed.service <<'UNIT'
[Unit]
Description=Export backup evidence to executive board (read only)
[Service]
Type=oneshot
TimeoutStartSec=45
ExecStart=/usr/bin/python3 /opt/core-backup-platform/export-board-feed.py
UNIT
cat > /etc/systemd/system/core-backup-board-feed.timer <<'UNIT'
[Unit]
Description=Refresh executive board evidence every minute
[Timer]
OnBootSec=30
OnUnitActiveSec=60
[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload
systemctl enable --now core-backup-board-feed.timer
docker compose config --quiet
docker compose up -d --build
for i in $(seq 1 300); do
 if docker compose exec -T dashboard python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8100/health',timeout=3)" >/dev/null 2>&1; then break; fi
 sleep 2
done
docker compose exec -T dashboard python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8100/health',timeout=3)"
count=$(docker compose exec -T db psql -U board -d backup_board -Atc 'select count(*) from users')
if test "$count" = 0; then
 python3 - <<'PY'
import os,secrets,subprocess
from pathlib import Path
os.umask(0o077)
p=Path('secrets/initial-admin.txt')
if p.exists():raise SystemExit('Existing bootstrap credential retained; inspect before retry')
pw='Aa9!'+secrets.token_urlsafe(22)
with p.open('x') as f:f.write('username=admin\npassword='+pw+'\n')
subprocess.run(['docker','compose','exec','-T','dashboard','python','app.py','init-admin'],input=pw+'\n',text=True,check=True)
PY
fi
echo 'Ready on port 8100. Initial credential: secrets/initial-admin.txt (root only). Existing 8090 untouched.'

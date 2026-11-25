#!/bin/bash
set -euo pipefail
test "$(id -u)" = 0 || { echo 'Run as root'; exit 1; }
src=$(cd -- "$(dirname -- "$0")" && pwd)
python3 - "$src" <<'VERIFY_PACKAGE'
import hashlib,json,sys
from pathlib import Path
root=Path(sys.argv[1]);manifest=json.loads((root/'manifest.json').read_text())
for name,expected in manifest['files'].items():
    if Path(name).name!=name or hashlib.sha256((root/name).read_bytes()).hexdigest()!=expected:
        raise SystemExit('Package integrity mismatch: '+name)
print('Package SHA256 manifest verified')
VERIFY_PACKAGE
python3 -m py_compile "$src"/*.py
python3 -m json.tool "$src/inventory.example.json" >/dev/null
install -d -m 700 /opt/core-backup-platform /etc/core-backup-platform
for module in platform_core.py daily_report.py windows_transport.py oracle_readonly.py observe_nexus.py observe_oracle.py check_connections.py audit_schedules.py bundle_transfer.py collect_bundle.py source_adapters.py retention_plan.py; do
  if test -f "/opt/core-backup-platform/$module"; then
    cp -p "/opt/core-backup-platform/$module" "/opt/core-backup-platform/$module.before-$(date +%Y%m%dT%H%M%S)"
  fi
  install -m 700 "$src/$module" /opt/core-backup-platform/
done
install -m 600 "$src/inventory.example.json" /etc/core-backup-platform/inventory.example.json
for template in dse27-bundle.example.json oracle41-bundle.example.json; do
  install -m 600 "$src/$template" "/etc/core-backup-platform/$template"
done
install -d -m 700 /opt/core-remote
if test -f /opt/core-remote/run_ps_absolute.py; then
  cp -p /opt/core-remote/run_ps_absolute.py "/opt/core-remote/run_ps_absolute.py.before-$(date +%Y%m%dT%H%M%S)"
fi
install -m 700 "$src/run_ps_absolute.py" /opt/core-remote/run_ps_absolute.py
test -f /var/lib/backup-console/snapshot.json || { echo 'Existing overview snapshot required; scheduler not installed'; exit 1; }
python3 /opt/core-backup-platform/platform_core.py status --output /var/lib/backup-console/standard-result.json
python3 /opt/core-backup-platform/platform_core.py report --output /var/lib/backup-console/standard-report.md
if test -f /etc/cron.d/core-backup-standard-report; then
  cp -p /etc/cron.d/core-backup-standard-report "/etc/core-backup-platform/cron.before-$(date +%Y%m%dT%H%M%S)"
fi
cat > /etc/cron.d/core-backup-standard-report <<'CRON'
SHELL=/bin/bash
PATH=/usr/sbin:/usr/bin:/sbin:/bin
50 8 * * * root /usr/bin/timeout 360 /usr/bin/flock -n /run/core-schedule-audit.lock /usr/bin/python3 /opt/core-backup-platform/audit_schedules.py >> /var/log/core-backup-schedule-audit.log 2>&1
17 * * * * root /usr/bin/python3 /opt/core-backup-platform/platform_core.py status --output /var/lib/backup-console/standard-result.json >> /var/log/core-backup-standard-report.log 2>&1
10 9 * * * root /usr/bin/python3 /opt/core-backup-platform/daily_report.py --send >> /var/log/core-backup-standard-report.log 2>&1
CRON
chmod 600 /etc/cron.d/core-backup-standard-report
echo 'Installed compatibility layer and daily webhook summary09:10 (once/day). Backup/cleanup jobs unchanged; no webhook sent during installation.'

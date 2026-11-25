#!/bin/bash
# Copies a reviewed client configuration. Does not start backup, edit GitLab, or replace cron.
set -Eeuo pipefail
umask 077
[[ $EUID == 0 ]] || { echo 'Run as root'; exit 1; }
BASE=$(cd -- "$(dirname -- "$0")" && pwd)
CONF=${1:?Usage: bash install-client.sh gitlab-16.conf}
[[ -f "$CONF" ]] || CONF="$BASE/$CONF"
[[ -f "$CONF" && ! -L "$CONF" ]]
for tool in bash flock df stat tar install; do command -v "$tool" >/dev/null; done
bash -n "$BASE/gitlab-backup.sh"
bash -n "$CONF"
# conf is trusted shell code supplied by the administrator, never fetched over network.
source "$CONF"
[[ "$ASSET" != */* && -d "$BACKUP_DIR" && ! -L "$BACKUP_DIR" ]]
if [[ "$DEPLOY" == docker ]]; then
  docker inspect "$CONTAINER" --format '{{.State.Running}}' | grep -qx true
  docker exec "$CONTAINER" test -s /etc/gitlab/gitlab-secrets.json
else
  [[ "$DEPLOY" == native && -x /opt/gitlab/bin/gitlab-rake && -s /etc/gitlab/gitlab-secrets.json ]]
fi
mkdir -p /opt/core-backup /etc/core-backup
BACKUP=/opt/core-backup/versions/$(date +%Y%m%d-%H%M%S)-$$
mkdir -p "$BACKUP"
for path in /opt/core-backup/gitlab-backup.sh /opt/core-backup/notify.py /etc/core-backup/gitlab.conf /etc/core-backup/notify.json; do
  [[ ! -f "$path" ]] || cp -p "$path" "$BACKUP/$(basename "$path")"
done
install -m 700 "$BASE/gitlab-backup.sh" /opt/core-backup/gitlab-backup.sh
install -m 600 "$BASE/notify.py" /opt/core-backup/notify.py
install -m 600 "$CONF" /etc/core-backup/gitlab.conf
# Existing robot settings are preserved.
[[ -f /etc/core-backup/notify.json ]] || install -m 600 "$BASE/notify.json" /etc/core-backup/notify.json
echo "Installed $ASSET; prior files saved to $BACKUP"
echo 'No backup started; existing cron unchanged. Review disk/inodes and old cron before first run.'
df -h "$BACKUP_DIR"
df -i "$BACKUP_DIR"
echo '/bin/bash /opt/core-backup/gitlab-backup.sh /etc/core-backup/gitlab.conf'

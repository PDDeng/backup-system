#!/bin/bash
# Source only. Publishes a size-only completion marker; NOT the Go MD5-ready protocol.
set -Eeuo pipefail
umask 077
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export TZ=Asia/Shanghai
source "${1:?Usage: bash gitlab-backup.sh /etc/core-backup/gitlab.conf}"
SCRIPT_DIR=$(cd -- "$(dirname -- "$0")" && pwd)
notify() {
  local py
  py=$(command -v python3 || command -v python || true)
  if [[ -n "$py" ]]; then
    "$py" "$SCRIPT_DIR/notify.py" "${NOTIFY_CONFIG:-/etc/core-backup/notify.json}" "$1" "$ASSET" "$2" || true
  else
    echo 'NOTIFY_FAILED Python unavailable'
  fi
}
mkdir -p /var/log/core-backup
exec >>/var/log/core-backup/gitlab.log 2>&1
start=$SECONDS
finish() {
  local rc=$?
  trap - EXIT
  echo "$(date -Is) END rc=$rc elapsed=$((SECONDS-start))s"
  if (( rc == 0 )); then
    notify SUCCESS "Source complete; MD5 pending; NAS delivery NOT confirmed; elapsed=$((SECONDS-start))s"
  else
    notify FAILURE "Source backup failed rc=$rc elapsed=$((SECONDS-start))s; see /var/log/core-backup/gitlab.log"
  fi
  exit "$rc"
}
trap finish EXIT
exec 9>/var/run/core-gitlab-backup.lock
flock -n 9 || { echo "$(date -Is) Already running"; exit 1; }
echo "$(date -Is) START $ASSET"
notify START 'Source backup starting'
[[ -d "$BACKUP_DIR" && ! -L "$BACKUP_DIR" ]]
inodes=$(df -Pi "$BACKUP_DIR" | awk 'END {print $4}')
[[ "$inodes" =~ ^[0-9]+$ ]] && (( inodes >= ${MIN_FREE_INODES:-5000} )) || { echo 'Insufficient source free inodes'; exit 1; }
free=$(df -Pk "$BACKUP_DIR" | awk 'END {print $4}')
(( free >= MIN_FREE_GB * 1024 * 1024 )) || { echo 'Insufficient source disk'; exit 1; }
shopt -s nullglob
declare -A before
for f in "$BACKUP_DIR"/*_gitlab_backup.tar; do before["$f"]=1; done
if [[ "$DEPLOY" == native ]]; then
  [[ -s /etc/gitlab/gitlab-secrets.json ]]
  nice -n 10 /opt/gitlab/bin/gitlab-rake gitlab:backup:create
else
  docker inspect "$CONTAINER" --format '{{.State.Running}}' | grep -qx true
  docker exec "$CONTAINER" test -s /etc/gitlab/gitlab-secrets.json
  if [[ "$MODERN" == yes ]]; then
    docker exec "$CONTAINER" /opt/gitlab/bin/gitlab-backup create
  else
    docker exec "$CONTAINER" /opt/gitlab/bin/gitlab-rake gitlab:backup:create
  fi
fi
new=()
for f in "$BACKUP_DIR"/*_gitlab_backup.tar; do [[ -n ${before[$f]:-} ]] || new+=("$f"); done
(( ${#new[@]} == 1 )) || { echo 'Expected one new archive; no completion marker written'; exit 1; }
archive=${new[0]}
[[ -s "$archive" && ! -L "$archive" ]]
run=$(date +%Y%m%dT%H%M%S)-$$
dest="$BACKUP_DIR/.core-complete/$run"
mkdir -p "$dest"
if [[ "$DEPLOY" == native ]]; then
  tar -czf "$dest/gitlab-config.tar.gz.partial" -C / etc/gitlab
  cp /opt/gitlab/version-manifest.txt "$dest/version-manifest.txt"
else
  docker exec "$CONTAINER" tar -czf - -C / etc/gitlab > "$dest/gitlab-config.tar.gz.partial"
  docker exec "$CONTAINER" cat /opt/gitlab/version-manifest.txt > "$dest/version-manifest.txt"
fi
mv "$dest/gitlab-config.tar.gz.partial" "$dest/gitlab-config.tar.gz"
printf '%s\t%s\n' "$archive" "$(stat -c %s "$archive")" > "$dest/files.tsv.partial"
for f in "$dest/gitlab-config.tar.gz" "$dest/version-manifest.txt"; do
  [[ -s "$f" ]]; printf '%s\t%s\n' "$f" "$(stat -c %s "$f")" >> "$dest/files.tsv.partial"
done
mv "$dest/files.tsv.partial" "$dest/files.tsv"
printf 'COPIED_STATUS=SOURCE_COMPLETE_MD5_PENDING\nASSET=%s\nDATE=%s\n' "$ASSET" "$(date +%F)" > "$dest/DONE.partial"
mv "$dest/DONE.partial" "$dest/DONE"
echo "$(date -Is) SOURCE_COMPLETE archive=$archive marker=$dest/DONE backup_seconds=$((SECONDS-start))"

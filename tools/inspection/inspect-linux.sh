#!/bin/bash
# Usage: bash inspect-linux.sh [svn-root] [nexus-data]. Read only, bounded depth.
set -u
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export LC_ALL=C
echo '=== HOST ==='
hostname
date -Is
cat /etc/os-release 2>/dev/null || true
echo '=== SERVICES ==='
systemctl list-units --type=service --all --no-pager 2>/dev/null | grep -Ei 'svn|nexus|httpd|apache' || true
echo '=== VERSIONS ==='
for tool in svnadmin svnlook svnserve; do
  if command -v "$tool" >/dev/null; then command -v "$tool"; "$tool" --version --quiet; fi
done
echo '=== PROCESS EXECUTABLES ==='
ps -eo pid,comm | grep -Ei 'java|svnserve|httpd|apache' || true
echo '=== LOCAL DISKS ==='
df -lh
echo '=== LISTEN PORTS ==='
ss -lntp 2>/dev/null | grep -E ':3690 |:8081 |:80 |:443 ' || true
echo '=== CANDIDATE PATHS ==='
for dir in /opt/nexus /opt/sonatype-work /home/soft/Nexus /srv/svn /var/svn /home/svn /data/svn; do
  [[ ! -d "$dir" ]] || ls -ld -- "$dir"
done
if [[ -n "${1:-}" && -d "$1" ]]; then
  echo '=== SVN ROOT / REPOSITORIES ==='
  df -h "$1"
  find "$1" -xdev -maxdepth 3 -type f -name format -print | while IFS= read -r format; do
    repo=${format%/format}
    [[ -d "$repo/db" && -d "$repo/conf" ]] || continue
    echo "REPOSITORY=$repo"
    if command -v svnlook >/dev/null; then svnlook uuid "$repo"; svnlook youngest "$repo"; fi
  done
fi
if [[ -n "${2:-}" && -d "$2" ]]; then
  echo '=== NEXUS DATA CHILDREN ==='
  df -h "$2"
  ls -la -- "$2"
  for conf in "$2/conf/nexus.xml" "$2/etc/nexus.properties"; do [[ ! -f "$conf" ]] || ls -l -- "$conf"; done
fi
echo 'DONE: no service/data changes, no backup started.'

#!/bin/bash
set -euo pipefail
container=core-backup-board-acceptance-20261125
board_ip=$(/usr/bin/docker inspect --format '{{with index .NetworkSettings.Networks "core-backup-board_private"}}{{.IPAddress}}{{end}}' "$container")
state=$(/usr/bin/docker inspect --format '{{.State.Running}}' "$container")
[[ "$state" == true ]] || { echo 'Dashboard container is not running' >&2; exit 1; }
[[ "$board_ip" =~ ^172\.20\.[0-9]{1,3}\.[0-9]{1,3}$ ]] || { echo 'Unexpected private dashboard address' >&2; exit 1; }
exec /lib/systemd/systemd-socket-proxyd "$board_ip:8100"

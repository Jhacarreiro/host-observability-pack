#!/bin/sh
set -eu
USER_NAME=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --user) USER_NAME=${2:-}; shift 2 ;;
    -h|--help) echo "usage: sudo $0 --user USER"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
[ "$(id -u)" -eq 0 ] || { echo "must run as root" >&2; exit 1; }
[ -n "$USER_NAME" ] || { echo "--user is required" >&2; exit 2; }
id "$USER_NAME" >/dev/null 2>&1 || { echo "unknown user: $USER_NAME" >&2; exit 2; }
GROUP_NAME=$(id -gn "$USER_NAME")
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
LIB=/usr/local/lib/host-observability-pack
CFG=/etc/host-observability-pack
RUN=/var/lib/host-observability-pack/watchdog
UNIT=/etc/systemd/system
install -d -o root -g root -m 755 "$LIB" "$CFG"
install -d -o "$USER_NAME" -g "$GROUP_NAME" -m 750 "$RUN" "$RUN/events"
install -o root -g root -m 755 "$ROOT/watchdog/watchdog.py" "$LIB/watchdog.py"
[ -e "$CFG/watchdog.env" ] || install -o root -g root -m 644 "$ROOT/watchdog/watchdog.env.example" "$CFG/watchdog.env"
sed -e "s/@WATCHDOG_USER@/$USER_NAME/g" -e "s/@WATCHDOG_GROUP@/$GROUP_NAME/g" "$ROOT/watchdog/host-observability-watchdog.service.in" > "$UNIT/host-observability-watchdog.service"
chown root:root "$UNIT/host-observability-watchdog.service"; chmod 644 "$UNIT/host-observability-watchdog.service"
install -o root -g root -m 644 "$ROOT/watchdog/host-observability-watchdog.timer" "$UNIT/host-observability-watchdog.timer"
systemctl daemon-reload
systemctl enable --now host-observability-watchdog.timer
systemctl start host-observability-watchdog.service
echo "installed; inspect $RUN/current.json and systemctl list-timers host-observability-watchdog.timer --all"

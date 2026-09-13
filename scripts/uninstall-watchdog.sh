#!/bin/sh
set -eu
[ "$(id -u)" -eq 0 ] || { echo "must run as root" >&2; exit 1; }
systemctl disable --now host-observability-watchdog.timer 2>/dev/null || true
rm -f /etc/systemd/system/host-observability-watchdog.timer /etc/systemd/system/host-observability-watchdog.service
systemctl daemon-reload
echo "units removed; /var/lib/host-observability-pack/watchdog preserved"

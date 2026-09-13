#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"
./scripts/check-python.py
python3 -m json.tool observer-agent/probes.json >/dev/null
python3 -m json.tool grafana/dashboards/overview.json >/dev/null
tmpdir=$(mktemp -d /tmp/host-observability-pack.XXXXXX)
trap 'rm -rf "$tmpdir"' EXIT INT TERM
python3 scripts/configure.py --env .env.example --output "$tmpdir/alertmanager.yml"
./scripts/compose.sh config >/dev/null

docker run --rm --entrypoint /bin/promtool -v "$ROOT/prometheus:/etc/prometheus:ro" prom/prometheus:v3.14.0 check config /etc/prometheus/prometheus.yml
docker run --rm --entrypoint /bin/promtool -v "$ROOT/prometheus:/etc/prometheus:ro" prom/prometheus:v3.14.0 check rules /etc/prometheus/alerts.yml
docker run --rm --entrypoint /bin/amtool -v "$tmpdir/alertmanager.yml:/etc/alertmanager/alertmanager.yml:ro" prom/alertmanager:v0.34.0 check-config /etc/alertmanager/alertmanager.yml

tmp=$(mktemp /tmp/host-observability-watchdog.XXXXXX.service)
sed -e "s/@WATCHDOG_USER@/nobody/g" -e "s/@WATCHDOG_GROUP@/nogroup/g" watchdog/host-observability-watchdog.service.in > "$tmp"
if command -v systemd-analyze >/dev/null 2>&1; then
  systemd-analyze verify "$tmp" watchdog/host-observability-watchdog.timer >/dev/null 2>&1 || { rm -f "$tmp"; echo "systemd unit validation failed" >&2; exit 1; }
fi
rm -f "$tmp"
echo "validation: PASS"

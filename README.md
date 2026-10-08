# Host Observability Pack

[![CI](https://github.com/Jhacarreiro/host-observability-pack/actions/workflows/ci.yml/badge.svg)](https://github.com/Jhacarreiro/host-observability-pack/actions/workflows/ci.yml)

A compact, self-hosted Linux observability stack for diagnosing host stalls, blocked processes, swap pressure, Btrfs trouble, network failures, and Docker outages — without turning monitoring into another large platform to operate.

The default deployment uses **three containers**:

| Container | Responsibility |
|---|---|
| `observer-agent` | host/runtime metrics, Btrfs signals, ICMP/DNS/HTTP probes, incident snapshots |
| `prometheus` | time-series history and alert evaluation |
| `alertmanager` | grouping, retries, recovery notifications, optional Telegram delivery |

Grafana is optional. A host watchdog is also optional and intentionally runs **outside Docker** under systemd so it can still observe the machine when Docker itself is unhealthy.

Current release: **v0.1.1**.

## Why this exists

A monitoring stack should still be useful when the failure is messy: high load, blocked I/O, an unhealthy container runtime, a DNS outage, or a filesystem stall where one noisy metric is not enough to explain what happened.

Host Observability Pack is built around four ideas:

- keep the always-on footprint small;
- prefer persistent, multi-signal alert conditions over single noisy thresholds;
- capture bounded evidence when incidents fire and resolve;
- keep automatic remediation out of the monitoring path until it can be designed and tested separately.

## Architecture

```text
                 optional host watchdog
                    (systemd timer)
                         |
                         v
+---------------- observer-agent ----------------+
| host /proc + /sys       Btrfs signals          |
| ICMP / DNS / HTTP       incident snapshots     |
+-------------------------+-----------------------+
                          |
                          v
                     Prometheus
                          |
                          v
                    Alertmanager
                    /          \
                   v            v
              Telegram       snapshots
              optional       via /alert

                     Grafana
                     optional
```

The observer agent deliberately folds together the roles commonly handled by Node Exporter, Blackbox Exporter, and a separate incident-snapshot service. Prometheus and Alertmanager remain separate because their storage, rule evaluation, grouping, retry, and resolved-notification behaviour is mature and worth keeping.

## Requirements

- Linux host
- Docker Engine
- `docker compose` or `docker-compose`
- Python 3 + systemd only if you install the optional host watchdog

The stack is intended for native Linux. Docker Desktop on macOS/Windows is not a supported target because the host-observation model relies on Linux `/proc` and `/sys`.

## Quick start

Clone the repository and initialize a local deployment:

```bash
git clone https://github.com/Jhacarreiro/host-observability-pack.git
cd host-observability-pack
./scripts/init.sh
./scripts/compose.sh up -d --build
```

Check the three core services:

```bash
curl -fsS http://127.0.0.1:9199/healthz
curl -fsS http://127.0.0.1:9090/-/healthy
curl -fsS http://127.0.0.1:9093/-/healthy
```

Run the local validation and public-sanitization gates:

```bash
./scripts/validate.sh
./scripts/audit-public
./tests/test-audit-public.sh
```

`init.sh` creates a private `.env` using the current user's UID/GID and generates the initial Alertmanager configuration.

## Default network probes

Probe configuration lives in:

```text
observer-agent/probes.json
```

The repository ships only generic public targets. Replace or extend them for your own environment.

Supported probe types:

- ICMP reachability;
- DNS queries against explicit resolvers;
- HTTP/HTTPS success and duration.

The agent exports both success and latency metrics for every configured target.

Keep private routers, internal hostnames, production topology, and private service URLs in your local deployment config rather than committing them to a public fork.

## Host and filesystem signals

The observer agent reads host state from read-only `/proc` and `/sys` mounts and exports the signals required by the included alert model, including:

- `load1`;
- blocked (`D`) processes;
- zombie processes;
- swap totals and usage ratio;
- swap-in/swap-out counters;
- Btrfs last/max commit latency;
- Btrfs device-error counters where the kernel exposes them.

### Btrfs compatibility

v0.1.1 supports both of the Btrfs device-error layouts used by Linux kernels in the wild:

```text
*/devinfo/*/error_stats
*/devices/*/stats/*
```

The agent exports normalized error counters for the available layout.

If the host is not using Btrfs, the Btrfs-specific metrics simply remain absent/zero as appropriate; the rest of the observer continues to work.

## Alert model

The included Prometheus rules cover:

- probe target failures;
- complete loss of a probe class;
- observer-agent unavailability;
- persistent blocked processes;
- blocked processes combined with high load;
- sustained high load;
- heavy swap usage combined with active swap churn;
- Btrfs device errors;
- slow Btrfs commits;
- optional watchdog missing/stale/Docker-unresponsive/recovery-gate states;
- optional watchdog host health: hardware temperature, filesystem usage, degraded MD RAID, UPS telemetry/battery, failed systemd units, and Docker containers that are unhealthy, stuck restarting, stopped despite a restart policy, or repeatedly OOM-killed.

Host-health rules are gated on `observer_watchdog_extended_available`, so they stay silent with a watchdog that does not collect those fields.

The defaults are deliberately conservative. Tune thresholds for your hardware and workload before depending on them operationally.

A core design principle is: **do not reboot a machine because one metric crossed one threshold**.

## Incident snapshots

Alertmanager posts firing and resolved transitions back to the observer agent at `/alert`.

The agent writes bounded JSON evidence under:

```text
runtime/incidents/
```

Snapshots include the current observer/probe state plus host diagnostics such as:

- route and neighbour tables;
- `/proc/loadavg`;
- `/proc/meminfo`;
- `/proc/vmstat`;
- `/proc/diskstats`;
- `/proc/mdstat`;
- the bounded blocked/zombie process summary already collected by the agent.

Real incident captures may reveal internal topology. Review them before sharing or publishing.

## Telegram notifications

Telegram is optional. Without Telegram configured, Alertmanager still drives local incident snapshots.

To enable Telegram, create the token file locally:

```bash
printf '%s' 'YOUR_BOT_TOKEN' > secrets/telegram-bot-token
chmod 600 secrets/telegram-bot-token
```

Set the numeric chat ID in `.env`:

```text
TELEGRAM_CHAT_ID=123456789
```

Then regenerate the Alertmanager configuration and restart only Alertmanager:

```bash
python3 scripts/configure.py
./scripts/compose.sh restart alertmanager
```

The token file is ignored by Git and mounted read-only into Alertmanager.

Notifications include both firing and resolved states so an incident has a clear recovery signal rather than only an initial alarm.

## Optional host watchdog

The watchdog is intentionally **outside Docker**. Its purpose is to continue evaluating the host if Docker becomes slow or unavailable.

Install it only after the three-container core is healthy:

```bash
sudo ./scripts/install-watchdog.sh --user "$USER"
```

The selected user should be able to run `docker info` without an interactive password.

The default hard-candidate model is:

```text
D-state >= 3 AND load1 >= 8
OR
Btrfs last commit >= 10 s AND D-state >= 2
OR
Docker unavailable AND D-state >= 2 AND load1 >= 8
```

`would_recover=1` is reached only after five consecutive hard candidates.

Each run also records host health in `current.json`:

| Signal | Source | Watchdog setting |
|---|---|---|
| load5/load15, memory used ratio | `/proc/loadavg`, `/proc/meminfo` | — |
| highest hardware temperature | `/sys/class/hwmon/*/temp*_input` | `WATCHDOG_TEMP_WARN_C` |
| filesystem usage | `statvfs` on each listed path | `WATCHDOG_FILESYSTEMS`, `WATCHDOG_FILESYSTEM_WARN_RATIO` |
| MD RAID degraded / resync | `/proc/mdstat` | — |
| UPS configured / fresh / on battery / low battery | NUT `upsc`, skipped when not installed | `WATCHDOG_UPSC_BIN` |
| failed systemd units | `systemctl --failed` | `WATCHDOG_SYSTEMCTL_BIN` |
| Docker container states | `docker ps -aq` + `docker inspect` | — |
| Docker OOM kills | cgroup `oom_kill` counter per running container | `WATCHDOG_OOM_WINDOW_SECONDS`, `WATCHDOG_OOM_RECURRING_KILLS` |

#### Docker OOM semantics

Docker keeps `State.OOMKilled=true` until a container restarts, even when the kernel killed a single child process once and the container kept running. Alerting on that flag fires forever. The watchdog instead reads each running container's cgroup `oom_kill` counter (cgroup v2 `memory.events` for the systemd and cgroupfs drivers, cgroup v1 `memory.oom_control`) and keeps per-container kill timestamps in `state.json`.

A container counts towards `observer_watchdog_docker_oom_killed` only when it has at least `WATCHDOG_OOM_RECURRING_KILLS` (default 2) new kills within `WATCHDOG_OOM_WINDOW_SECONDS` (default 1800), or when it was stopped by an OOM and has a restart policy other than `no`. An isolated kill is listed in `current.json` under `docker_inventory.oom_recent` with `active=false` and does not alert. The first run baselines existing counters, so past kills never alert after an install or upgrade.

**v0.1.x is observe-only.** The watchdog records evidence and exposes when a future recovery actuator might be justified, but it does not kill processes, restart Docker, or reboot the machine.

After installation, point the observer agent at the host watchdog state in your private `.env`:

```text
WATCHDOG_EXPECTED=1
WATCHDOG_STATE_DIR=/var/lib/host-observability-pack/watchdog
```

Then recreate only the observer agent:

```bash
./scripts/compose.sh up -d --no-deps --force-recreate observer-agent
```

## Optional Grafana

Grafana is deliberately outside the default core:

```bash
./scripts/compose.sh --profile ui up -d grafana
```

By default it binds to loopback. Change `GRAFANA_BIND` only when you deliberately want to expose it on a trusted interface or behind your own authenticated reverse proxy.

The stack remains fully functional for collection, alerting, Telegram, and incident snapshots without Grafana.

## Security model

The default deployment is intentionally restrictive:

- core HTTP endpoints bind to loopback;
- no container receives the Docker socket;
- `/proc` and `/sys` are mounted read-only into the observer agent;
- the observer agent runs non-root by default;
- the observer agent root filesystem is read-only;
- `NET_RAW` is added only for ICMP;
- Telegram credentials stay outside Git;
- the watchdog runs as an unprivileged host user;
- the watchdog has no automatic remediation capability in v0.1.x.

Read [`SECURITY.md`](SECURITY.md) before exposing endpoints beyond localhost.

## Operations

Show the effective services:

```bash
./scripts/compose.sh config --services
```

Expected default core:

```text
observer-agent
prometheus
alertmanager
```

Check current observer status:

```bash
curl -fsS http://127.0.0.1:9199/status
```

Check probe metrics:

```bash
curl -fsS http://127.0.0.1:9199/metrics | grep '^observer_probe_'
```

Check Prometheus targets:

```bash
curl -fsS http://127.0.0.1:9090/api/v1/targets
```

Check rules:

```bash
curl -fsS http://127.0.0.1:9090/api/v1/rules
```

Tail the core logs:

```bash
./scripts/compose.sh logs --tail=200 observer-agent prometheus alertmanager
```

## Updating

Before updating a live deployment:

```bash
git fetch --tags
./scripts/audit-public
./scripts/validate.sh
```

Review the release notes, then update one component at a time where practical. Preserve Prometheus and Grafana named volumes if you recreate containers.

The project intentionally keeps environment-specific configuration separate from public source. Avoid carrying local runtime patches in the repository; upstream reusable fixes should be made in the public project and local differences should stay in private config.

## Validation and CI

The repository includes local and CI gates for:

- Python syntax;
- JSON syntax;
- Docker Compose validation;
- Prometheus config/rule validation;
- Alertmanager config validation;
- Telegram config generation;
- Btrfs parser regression coverage;
- observer-agent image build;
- public sanitization;
- negative leak fixtures for private IPs, operator paths, and credential-like material.

Before publishing a branch, tag, or release:

```bash
./scripts/audit-public
./tests/test-audit-public.sh
```

## Project scope and limits

This project is deliberately small. It is not intended to replace a full metrics platform, SIEM, distributed tracing system, or general-purpose automation framework.

It works best when you want a compact always-on safety layer for one Linux host or homelab server and care more about clear failure boundaries and incident evidence than hundreds of dashboards.

Known limitations:

- Linux only;
- the observer sees the host from the network interfaces available to that host;
- it cannot independently prove failures on another client path (for example a separate Wi-Fi-only path) without another vantage point;
- automatic remediation/reboot is intentionally out of scope for v0.1.x.

## Contributing

Contributions are welcome. Keep examples portable and synthetic, and do not submit private infrastructure details or real incident captures containing sensitive topology.

See [`CONTRIBUTING.md`](CONTRIBUTING.md).

## License

MIT

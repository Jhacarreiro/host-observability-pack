# Host Observability Pack

A compact Linux host + network observability stack focused on diagnosing stalls, blocked processes, swap pressure, filesystem trouble, network failures, and Docker outages.

## Core: 3 containers

The default deployment intentionally uses only three containers:

| Container | Role |
|---|---|
| `observer-agent` | host metrics, Btrfs signals, ICMP/DNS/HTTP probes, incident snapshots |
| `prometheus` | time-series history and alert rules |
| `alertmanager` | grouping, retries, recovery notifications, optional Telegram |

Grafana is optional and lives behind the Compose profile `ui`.

The host watchdog is **not a container**. It runs from a systemd timer so it can keep observing when Docker itself is unhealthy.

```text
systemd watchdog (host)
        |
        v
 observer-agent ---> Prometheus ---> Alertmanager ---> Telegram (optional)
     |                    |              |
     |                    |              +--> webhook back to observer-agent
     |                    |                   for incident snapshot
     |                    v
     +---------------> Grafana (optional)
```

## Why not six containers?

The observer agent deliberately replaces the usual Node Exporter + Blackbox Exporter + separate incident-snapshot service. It reads only the host data needed by this project from read-only `/proc` and `/sys` mounts and performs the configured network probes itself.

Prometheus and Alertmanager stay separate because they provide mature storage/rule evaluation and notification grouping/retry semantics that are not worth reimplementing.

## Requirements

- Linux host
- Docker Engine
- `docker compose` or `docker-compose`
- Python 3 + systemd only for the optional host watchdog

The stack is designed for native Linux, not Docker Desktop.

## Quick start

```bash
./scripts/init.sh
./scripts/compose.sh up -d --build
```

Check the core:

```bash
curl -fsS http://127.0.0.1:9199/healthz
curl -fsS http://127.0.0.1:9090/-/healthy
curl -fsS http://127.0.0.1:9093/-/healthy
```

Run the validation gates:

```bash
./scripts/validate.sh
./scripts/audit-public
```

## Optional Grafana

Grafana is not part of the default three-container core:

```bash
./scripts/compose.sh --profile ui up -d grafana
```

By default it binds to `127.0.0.1:3000`. Change `GRAFANA_BIND` deliberately if you want LAN access.

## Probe configuration

Edit `observer-agent/probes.json`.

The repository ships only generic public examples. Keep private routers, internal services and topology in local deployment config if the repository is public.

## Telegram

Telegram is optional. With no `TELEGRAM_CHAT_ID`, Alertmanager still posts firing/resolved transitions to the observer agent so incident snapshots are written locally.

To enable Telegram:

```bash
printf '%s' 'YOUR_BOT_TOKEN' > secrets/telegram-bot-token
chmod 600 secrets/telegram-bot-token
```

Set the numeric chat ID in `.env`, then regenerate and restart Alertmanager:

```bash
python3 scripts/configure.py
./scripts/compose.sh restart alertmanager
```

The token is mounted read-only and is ignored by Git.

## Included signals

The observer agent exports Prometheus metrics for:

- host `load1`;
- blocked (`D`) processes and zombies;
- swap totals, usage ratio and swap-in/swap-out counters;
- Btrfs commit latency and device error counters where the kernel exposes them;
- ICMP, DNS and HTTP probe success/duration;
- optional host-watchdog freshness, Docker availability, critical streak and `would_recover` state.

Alert defaults intentionally use persistence and multi-signal gating instead of reacting to one noisy sample.

## Incident snapshots

Alertmanager posts every firing/resolved transition to `observer-agent:/alert`. The agent stores bounded evidence under `runtime/incidents/`, including current metrics/probes and host route, neighbour, load, memory, vmstat, diskstats and mdraid state.

Do not publish real incident captures without reviewing them for private topology.

## Optional host watchdog

Install only after the Docker core is healthy:

```bash
sudo ./scripts/install-watchdog.sh --user "$USER"
```

The selected user must be able to run `docker info` non-interactively.

The watchdog runs every minute and is **observe-only** in v0.1. A hard candidate is one of:

```text
D-state >= 3 AND load1 >= 8
OR
Btrfs last commit >= 10 s AND D-state >= 2
OR
Docker unavailable AND D-state >= 2 AND load1 >= 8
```

`would_recover=1` is reached only after five consecutive hard candidates. No restart/reboot actuator is included.

Runtime state lives under `/var/lib/host-observability-pack/watchdog/`. After installing the watchdog, set these private deployment values in `.env` and recreate `observer-agent`:

```text
WATCHDOG_EXPECTED=1
WATCHDOG_STATE_DIR=/var/lib/host-observability-pack/watchdog
```

Without the watchdog, the default local `./runtime/watchdog` path is harmless and keeps the three-container core self-contained.

## Security model

- all core HTTP endpoints bind to loopback by default;
- no Docker socket is mounted into any container;
- `/proc` and `/sys` are mounted read-only into the observer agent;
- the agent gets only `NET_RAW` for ICMP;
- its root filesystem is read-only;
- Telegram credentials stay outside Git;
- the watchdog runs as an unprivileged user and v0.1 cannot perform recovery actions.

See `SECURITY.md` before exposing any endpoint beyond localhost.

## Public-release gate

Before publishing a branch or tag:

```bash
./scripts/audit-public
```

The gate rejects common credentials, tracked runtime/secrets, private IPv4 addresses and operator-specific home/workspace paths.

## Status

v0.1 is intentionally conservative: observe, record, alert and build evidence first. Automatic remediation belongs in a later opt-in actuator with explicit cooldown and anti-loop safeguards.

## License

MIT

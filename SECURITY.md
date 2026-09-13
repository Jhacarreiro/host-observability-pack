# Security

Host Observability Pack is intended for a trusted Linux server or homelab host.

Keep the default loopback bindings unless you deliberately add authentication and network controls. The observer agent sees host `/proc` and `/sys` read-only and has `NET_RAW` for ICMP, but no container receives the Docker socket.

Never commit `.env`, Telegram tokens, private keys, credentials, cookies, or production incident snapshots containing private topology.

Run `./scripts/audit-public` before public pushes and tags.

For vulnerabilities, prefer GitHub private vulnerability reporting when available rather than posting secrets or exploit details in a public issue.

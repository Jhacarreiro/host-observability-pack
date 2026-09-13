# Contributing

Keep examples generic and portable. Do not submit private IPs, operator paths, credentials, tokens or real incident captures.

Before opening a pull request:

```bash
cp .env.example .env
python3 scripts/configure.py
./scripts/validate.sh
./scripts/audit-public
./tests/test-audit-public.sh
```

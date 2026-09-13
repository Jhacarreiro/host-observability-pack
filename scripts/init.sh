#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"
if [ -e .env ]; then
  echo ".env already exists; refusing to overwrite" >&2
  exit 1
fi
cp .env.example .env
uid=$(id -u)
gid=$(id -g)
python3 - "$uid" "$gid" <<'PY'
from pathlib import Path
import sys
p=Path('.env')
s=p.read_text()
s=s.replace('PUID=1000',f'PUID={sys.argv[1]}').replace('PGID=1000',f'PGID={sys.argv[2]}')
p.write_text(s)
PY
mkdir -p runtime/incidents runtime/watchdog
python3 scripts/configure.py
echo "initialized .env for uid=$uid gid=$gid"

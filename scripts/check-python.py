#!/usr/bin/env python3
from pathlib import Path
FILES = [
    'observer-agent/app.py',
    'watchdog/watchdog.py',
    'scripts/configure.py',
]
for name in FILES:
    source = Path(name).read_text()
    compile(source, name, 'exec')
print('python syntax: PASS')

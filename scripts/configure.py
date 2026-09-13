#!/usr/bin/env python3
import argparse, os
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def load_env(path):
    out={}
    if not path.exists():return out
    for raw in path.read_text().splitlines():
        line=raw.strip()
        if not line or line.startswith('#') or '=' not in line:continue
        k,v=line.split('=',1); out[k.strip()]=v.strip()
    return out

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--env',default=str(ROOT/'.env')); ap.add_argument('--output',default=str(ROOT/'runtime'/'alertmanager.yml')); args=ap.parse_args()
    env=load_env(Path(args.env)); chat=os.getenv('TELEGRAM_CHAT_ID',env.get('TELEGRAM_CHAT_ID','')).strip()
    token=ROOT/'secrets'/'telegram-bot-token'; output=Path(args.output); output.parent.mkdir(parents=True,exist_ok=True)
    receiver='snapshot-only'; telegram=''
    if chat:
        try:int(chat)
        except ValueError:raise SystemExit('TELEGRAM_CHAT_ID must be numeric')
        if not token.exists() or not token.read_text().strip():raise SystemExit('Telegram enabled but secrets/telegram-bot-token is missing or empty')
        receiver='telegram-and-snapshot'
        telegram=f'''\n    telegram_configs:\n      - send_resolved: true\n        bot_token_file: /run/secrets/telegram-bot-token\n        chat_id: {chat}\n        parse_mode: ""\n        message: |\n          {{{{ if eq .Status "firing" }}}}🚨 HOST/NETWORK INCIDENT\n          {{{{ range .Alerts.Firing }}}}• ACTIVE — {{{{ .Labels.alertname }}}} — {{{{ .Annotations.summary }}}}\n          {{{{ end }}}}{{{{ else }}}}✅ HOST/NETWORK RECOVERY\n          {{{{ range .Alerts.Resolved }}}}• RESOLVED — {{{{ .Labels.alertname }}}} — {{{{ .Annotations.summary }}}}\n          {{{{ end }}}}{{{{ end }}}}\n'''
    cfg=f'''global:\n  resolve_timeout: 2m\n\nroute:\n  receiver: {receiver}\n  group_by: [scope]\n  group_wait: 20s\n  group_interval: 2m\n  repeat_interval: 4h\n\nreceivers:\n  - name: snapshot-only\n    webhook_configs:\n      - url: http://127.0.0.1:9199/alert\n        send_resolved: true\n        max_alerts: 0\n\n  - name: telegram-and-snapshot\n    webhook_configs:\n      - url: http://127.0.0.1:9199/alert\n        send_resolved: true\n        max_alerts: 0\n{telegram}'''
    output.write_text(cfg)
    print('alertmanager config:', 'telegram+snapshot' if chat else 'snapshot-only')

if __name__=='__main__':main()

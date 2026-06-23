#!/usr/bin/env python3
# chmod +x /home/<agent>/workspace/scripts/lib/<agent>-register-commands.py
"""One-shot script to register <Agent> commands in the Telegram bot menu via setMyCommands."""

import json
import os
import sys
import urllib.request
import urllib.parse

BOT_TOKEN = os.environ['TELEGRAM_BOT_TOKEN']

COMMANDS = [
    {'command': 'reset',   'description': 'Reiniciar sesión'},
    {'command': 'resume',  'description': 'Retomar sesión anterior'},
    {'command': 'context', 'description': 'Ver uso de contexto'},
    {'command': 'skills',  'description': 'Ver skills disponibles'},
    {'command': 'agents',  'description': 'Ver agentes disponibles'},
]

CHAT_ID = os.environ['TELEGRAM_CHAT_ID']


def api_call(method, payload):
    data = urllib.parse.urlencode(payload).encode()
    req = urllib.request.Request(
        f'https://api.telegram.org/bot{BOT_TOKEN}/{method}',
        data=data,
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def main():
    try:
        # Register under chat-specific scope (highest priority — overrides
        # all_private_chats where the Telegram plugin registers its own
        # /start /help /status commands)
        r = api_call('setMyCommands', {
            'commands': json.dumps(COMMANDS),
            'scope': json.dumps({'type': 'chat', 'chat_id': int(CHAT_ID)}),
        })
        if not r.get('ok'):
            print(f'ERROR setMyCommands(chat): {r}', file=sys.stderr)
            sys.exit(1)

        print('OK — comandos registrados en Telegram')
    except Exception as e:
        print(f'ERROR — {e}', file=sys.stderr)
        sys.exit(1)


main()

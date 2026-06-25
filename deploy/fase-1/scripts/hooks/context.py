#!/usr/bin/env python3
"""context.py — Porcentaje de contexto usado en la sesión activa.

Uso:
  context.py --mode hook      Solo envía a Telegram si >30%
  context.py --mode command   Siempre envía

  --transcript PATH   Opcional. Si no se pasa, busca el .jsonl más reciente.
"""

import argparse
import json
import os
import glob
import urllib.request
import urllib.parse

BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
CHAT_ID   = os.environ.get('TELEGRAM_CHAT_ID', '')

MODEL_WINDOWS = {
    'claude-opus-4':   1_000_000,
    'claude-sonnet-4':   200_000,
    'claude-haiku-4':    200_000,
}

TRANSCRIPT_GLOB = '/home/<agent>/claude/.claude/projects/-home-<agent>-claude/*.jsonl'


def max_tokens_for(model: str) -> int:
    for prefix, size in MODEL_WINDOWS.items():
        if model.startswith(prefix):
            return size
    return 200_000


def find_latest_transcript() -> str | None:
    files = glob.glob(TRANSCRIPT_GLOB)
    return max(files, key=os.path.getmtime) if files else None


def parse_transcript(path: str) -> tuple[int, str]:
    last_usage = None
    model = 'claude-sonnet-4-6'
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            msg = obj.get('message')
            if not isinstance(msg, dict):
                continue
            if obj.get('type') == 'assistant' and msg.get('model'):
                model = msg['model']
            usage = msg.get('usage')
            if usage:
                last_usage = usage

    if not last_usage:
        return 0, model

    used = (
        last_usage.get('input_tokens', 0) +
        last_usage.get('cache_creation_input_tokens', 0) +
        last_usage.get('cache_read_input_tokens', 0)
    )
    return used, model


def color_square(pct: float) -> str:
    if pct < 30:
        return '🟩'
    elif pct < 40:
        return '🟨'
    elif pct < 50:
        return '🟧'
    else:
        return '🟥'


def progress_bar(pct: float, width: int = 9) -> str:
    square = color_square(pct)
    filled = max(0, min(width, round(pct / 100 * width)))
    return square * filled + '⬜' * (width - filled)


def fmt_k(n: int) -> str:
    return f"{n // 1000}K" if n >= 1000 else str(n)


def tg_escape(text: str) -> str:
    special = r'\_*[]()~`>#+-=|{}.!'
    return ''.join(f'\\{c}' if c in special else c for c in text)


def tg_send(text: str) -> None:
    if not BOT_TOKEN or not CHAT_ID:
        return
    try:
        data = urllib.parse.urlencode({
            'chat_id': CHAT_ID,
            'text': text,
            'parse_mode': 'MarkdownV2',
        }).encode()
        urllib.request.urlopen(
            urllib.request.Request(
                f'https://api.telegram.org/bot{BOT_TOKEN}/sendMessage', data=data
            ),
            timeout=8,
        )
    except Exception:
        pass


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=['hook', 'command'], required=True)
    parser.add_argument('--transcript', default=None)
    args = parser.parse_args()

    transcript = args.transcript or find_latest_transcript()
    if not transcript or not os.path.exists(transcript):
        if args.mode == 'command':
            tg_send('⚠️ No encontré transcript de sesión activa\\.')
        return

    used, model = parse_transcript(transcript)
    if used == 0:
        if args.mode == 'command':
            tg_send('⚠️ No se pudo leer el uso de contexto\\.')
        return

    max_tok = max_tokens_for(model)
    pct = used / max_tok * 100

    if args.mode == 'hook' and pct <= 30:
        return

    bar = progress_bar(pct)
    msg = (
        f"{bar}  `{pct:.1f}%`\n"
        f"`{fmt_k(used)} / {fmt_k(max_tok)} · {tg_escape(model)}`"
    )
    tg_send(msg)


main()

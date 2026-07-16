#!/usr/bin/env python3
# chmod +x /home/<agent>/workspace/scripts/hooks/sessionend-hook.py
"""sessionend-hook.py — SessionEnd.

1. Llama a chronicler.py pasándole el hook input (memorias + notificación de cierre).
2. Envía a Telegram un bloque de código con `/resume_{session_id}` para retomar
   la sesión: no es un comando real de Telegram (un UUID supera el límite de
   32 caracteres de un bot_command), es texto en formato `code` que el usuario
   copia y pega. Sin límite de longitud, sin truncar el session_id.

Guards: solo contexto main, sin subagentes (sdk-cli), sin reentradas.
"""
import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request

CHRONICLER = "/home/<agent>/workspace/scripts/lib/chronicler.py"


def tg_send(token, chat_id, text, parse_mode=None):
    try:
        payload = {"chat_id": chat_id, "text": text}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        data = urllib.parse.urlencode(payload).encode()
        urllib.request.urlopen(
            urllib.request.Request(
                f"https://api.telegram.org/bot{token}/sendMessage", data=data
            ),
            timeout=8,
        )
    except Exception:
        pass


def main():
    if os.environ.get("<AGENT>_CONTEXT", "main") != "main":
        sys.exit(0)
    if os.environ.get("CLAUDE_CODE_ENTRYPOINT") == "sdk-cli":
        sys.exit(0)
    if os.environ.get("<AGENT>_HOOK_RUNNING"):
        sys.exit(0)

    raw = sys.stdin.read()
    try:
        data = json.loads(raw)
    except Exception:
        sys.exit(0)
    if not isinstance(data, dict):
        sys.exit(0)

    session_id = data.get("session_id", "")

    # 1. Llama a chronicler.py con el mismo hook input.
    try:
        subprocess.run(
            [sys.executable, CHRONICLER],
            input=raw,
            text=True,
            timeout=180,
        )
    except Exception:
        pass

    # 2. Envía el código para retomar, como bloque `code` (copiar y pegar).
    if session_id:
        bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
        if bot_token and chat_id:
            resume_msg = f"Sesión guardada\\. Para retomar, copia y pega:\n`/resume_{session_id}`"
            tg_send(bot_token, chat_id, resume_msg, parse_mode="MarkdownV2")


main()

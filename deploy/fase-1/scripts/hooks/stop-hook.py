#!/usr/bin/env python3
# chmod +x /home/<agent>/workspace/scripts/hooks/stop-hook.py
"""stop-hook.py — Stop (guardrail de cierre, garantía de respuesta).

Determinista, local, FAIL-OPEN: si la lógica peta, deja cerrar el turno (un Stop
hook que bloquea por su propio fallo cuelga la sesión).

Guardas en orden (§4.1 §5.3):
  1. stop_hook_active → exit 0 (anti-deadlock D8).
  2. Contexto: <AGENT>_CONTEXT != main → exit 0.
  3. Origen: sin bandera /tmp/<agent>-telegram-turn o > 600s → exit 0.
  4. Reply en transcript → limpia contador + borra bandera + exit 0.
  5. Rewake-counter >= 4 en 60s → force-release.
  6. Block con mensaje de rewake.
"""
import json
import os
import re
import sys
import time
import urllib.request
import urllib.parse

sys.path.insert(0, "/home/<agent>/workspace/scripts/lib")
from common import (read_hook_input, is_main_context, log_permission,
                        TELEGRAM_TURN_FLAG, REWAKE_COUNTER, reply_in_transcript)


def _ticker_state_path(session_id: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", session_id)
    return f"/tmp/<agent>-ticker-{safe}.json"


def _kill_ticker(session_id: str) -> None:
    path = _ticker_state_path(session_id)
    try:
        with open(path) as f:
            state = json.load(f)
    except Exception:
        return
    pid = state.get("ticker_pid")
    if pid:
        try:
            os.kill(pid, 15)  # SIGTERM
        except Exception:
            pass
    msg_id = state.get("tg_message_id")
    if msg_id:
        _tg_delete(msg_id)
    try:
        os.unlink(path)
    except Exception:
        pass


def _tg_delete(message_id: int) -> None:
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id   = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not bot_token or not chat_id:
        return
    try:
        data = urllib.parse.urlencode(
            {"chat_id": chat_id, "message_id": str(message_id)}
        ).encode()
        urllib.request.urlopen(
            urllib.request.Request(
                f"https://api.telegram.org/bot{bot_token}/deleteMessage", data=data
            ),
            timeout=8,
        )
    except Exception:
        pass

try:
    data = read_hook_input()

    # Guarda 1: stop_hook_active
    if data.get("stop_hook_active"):
        sys.exit(0)

    # Guarda de contexto (D9): solo la sesión principal exige reply.
    if not is_main_context():
        sys.exit(0)

    # Guarda de origen (D9)
    if not TELEGRAM_TURN_FLAG.exists():
        sys.exit(0)
    try:
        flag = json.loads(TELEGRAM_TURN_FLAG.read_text())
    except Exception:
        flag = {}
    if time.time() - flag.get("ts", 0) > 600:
        sys.exit(0)

    # ¿Reply emitido?
    if reply_in_transcript(data.get("transcript_path")):
        session_id = data.get("session_id") or ""
        if session_id:
            _kill_ticker(session_id)
        REWAKE_COUNTER.write_text(json.dumps({"n": 0, "t0": time.time()}))
        TELEGRAM_TURN_FLAG.unlink(missing_ok=True)
        sys.exit(0)

    # Guarda 2: rewake-counter (máx 4 en 60s)
    state = json.loads(REWAKE_COUNTER.read_text()) if REWAKE_COUNTER.exists() else {"n": 0, "t0": time.time()}
    if time.time() - state.get("t0", 0) > 60:
        state = {"n": 0, "t0": time.time()}
    if state.get("n", 0) >= 4:
        session_id = data.get("session_id") or ""
        if session_id:
            _kill_ticker(session_id)
        REWAKE_COUNTER.write_text(json.dumps({"n": 0, "t0": time.time()}))
        log_permission("Stop", "force-release", "rewake-counter agotado")
        sys.exit(0)
    state["n"] = state.get("n", 0) + 1
    REWAKE_COUNTER.write_text(json.dumps(state))

    log_permission("Stop", "rewake", f"intento {state['n']}")
    print(json.dumps({
        "decision": "block",
        "reason": "Turno cerrado sin responder al usuario propietario. Envía la respuesta "
                  "por Telegram (mcp__plugin_telegram_telegram__reply) antes de cerrar.",
    }, ensure_ascii=False))
    sys.exit(0)

except SystemExit:
    raise
except Exception:
    # FAIL-OPEN: nunca colgar el cierre por un fallo propio.
    sys.exit(0)

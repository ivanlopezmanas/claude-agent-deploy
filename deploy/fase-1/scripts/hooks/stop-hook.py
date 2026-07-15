#!/usr/bin/env python3
# chmod +x /home/<agent>/workspace/scripts/hooks/stop-hook.py
"""stop-hook.py — Stop (guardrail de cierre, garantía de respuesta).

Determinista, local, FAIL-OPEN: si la lógica peta, deja cerrar el turno (un Stop
hook que bloquea por su propio fallo cuelga la sesión).

Guardas en orden (§4.1 §5.3):
  1. Contexto: <AGENT>_CONTEXT != main → exit 0.
  2. Origen: sin bandera /tmp/<agent>-telegram-turn o > 600s → exit 0 (limpia contador).
  3. Reply en transcript → limpia contador + borra bandera + exit 0.
  4. Rewake-counter > 3 intentos → rescate (manda el último texto del asistente
     o un aviso genérico) y cierra el turno sin volver a bloquear.
  5. Si no, block pidiendo responder — cuenta como un intento más.

No se usa el campo stop_hook_active para cortar el bucle: la documentación oficial
de Claude Code confirma que se pone a true en TODAS las llamadas a Stop posteriores
a un bloqueo previo (el harness admite hasta 8 bloqueos consecutivos antes de forzar
el cierre por su cuenta). Cortar en esa guarda limitaba los reintentos a 1 en vez de
los 3 que este hook necesita — el contador de abajo es la única fuente de verdad, y
se mantiene muy por debajo del tope duro del harness a propósito, para que el rescate
de este hook dispare siempre antes de que el harness corte en silencio.
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
                        TELEGRAM_TURN_FLAG, REWAKE_COUNTER, check_reply_status)

MAX_ATTEMPTS = 3


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


def _tg_send(text: str) -> str | None:
    """Envía el mensaje de rescate. Devuelve None si fue bien, o el motivo del fallo."""
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id   = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not bot_token or not chat_id:
        return "TELEGRAM_BOT_TOKEN/CHAT_ID no configurados"
    try:
        data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
        urllib.request.urlopen(
            urllib.request.Request(
                f"https://api.telegram.org/bot{bot_token}/sendMessage", data=data
            ),
            timeout=10,
        )
        return None
    except Exception as e:
        return f"{type(e).__name__}: {e}"


def _launch_context_check(transcript_path: str) -> None:
    """Lanza context.py en background; no bloquea el cierre del turno."""
    if not transcript_path:
        return
    try:
        import subprocess as _sp
        _sp.Popen(
            [sys.executable,
             "/home/<agent>/workspace/scripts/lib/context.py",
             "--mode", "hook",
             "--transcript", transcript_path],
            start_new_session=True,
            stdin=_sp.DEVNULL,
            stdout=_sp.DEVNULL,
            stderr=_sp.DEVNULL,
        )
    except Exception as e:
        log_permission("Stop", "context-launch-failed", f"{type(e).__name__}: {e}")


def _clear_turn_state() -> None:
    REWAKE_COUNTER.unlink(missing_ok=True)
    TELEGRAM_TURN_FLAG.unlink(missing_ok=True)


try:
    data = read_hook_input()

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
        _clear_turn_state()
        sys.exit(0)

    session_id = data.get("session_id") or ""
    transcript_path = data.get("transcript_path") or ""
    reply_ok, last_assistant_text = check_reply_status(transcript_path)

    if reply_ok:
        if session_id:
            _kill_ticker(session_id)
        _clear_turn_state()
        # Muestra barra de contexto si >30% (no bloquea el cierre del turno)
        _launch_context_check(transcript_path)
        sys.exit(0)

    # Sin reply: contador de intentos (fuente de verdad única, ver docstring).
    state = json.loads(REWAKE_COUNTER.read_text()) if REWAKE_COUNTER.exists() else {"n": 0}
    n = state.get("n", 0) + 1

    if n > MAX_ATTEMPTS:
        # Se agotaron los reintentos: rescate en vez de silencio.
        if session_id:
            _kill_ticker(session_id)
        if last_assistant_text:
            error = _tg_send(f"⚠️ (Respuesta rescatada — no llegó vía reply tras {n - 1} intentos):\n\n"
                              f"{last_assistant_text[:3900]}")
            decision = "rescue" if not error else "rescue-send-failed"
            reason = f"intentos={n - 1} chars={len(last_assistant_text)}"
        else:
            error = _tg_send(f"⚠️ Terminé sin responderte tras {n - 1} intentos. "
                              f"Revisa el log de permisos.")
            decision = "rescue-empty" if not error else "rescue-empty-send-failed"
            reason = f"intentos={n - 1}"
        log_permission("Stop", decision, f"{reason} error={error}" if error else reason)
        _clear_turn_state()
        sys.exit(0)

    REWAKE_COUNTER.write_text(json.dumps({"n": n}))
    log_permission("Stop", "rewake", f"intento {n} de {MAX_ATTEMPTS}")
    print(json.dumps({
        "decision": "block",
        "reason": f"No has respondido al usuario todavía. Usa "
                  f"mcp__plugin_telegram_telegram__reply para responder antes de "
                  f"cerrar el turno. Intento {n} de {MAX_ATTEMPTS}.",
    }, ensure_ascii=False))
    sys.exit(0)

except SystemExit:
    raise
except Exception:
    # FAIL-OPEN: nunca colgar el cierre por un fallo propio.
    sys.exit(0)

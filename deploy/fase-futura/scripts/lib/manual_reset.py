#!/usr/bin/env python3
# chmod +x /home/<agent>/workspace/scripts/lib/manual_reset.py
"""manual_reset.py — Atajo manual para reiniciar la sesión de Telegram colgada.

Sustituye a autoreset.py (eliminado): no hay reinicio automático, solo esto.
Uso previsto: el usuario abre un terminal nuevo, arranca una sesión de Claude
Code ahí y le pide "resetea la sesión colgada". Esa sesión nueva ejecuta este
script.

El problema que resuelve: un terminal nuevo no puede adivinar de forma
fiable qué sesión es "la de producción" (su propio transcript también es
reciente). La solución no es heurística: sessionstart-hook.py, cuando
detecta <AGENT>_SERVICE=telegram (marca que solo pone claude-telegram-start.sh),
vuelca su {session_id, transcript_path} en TELEGRAM_SESSION_FILE en cada
arranque. Este script solo lee ese fichero y llama a reset_sequence.py —
el mismo código ya probado que usa el /reset manual — con esos datos.
"""
import json
import os
import subprocess
import sys

TELEGRAM_SESSION_FILE = "/home/<agent>/data/telegram-session.json"
RESET_SEQUENCE = "/home/<agent>/workspace/scripts/lib/reset_sequence.py"
# reset_sequence.py tiene su propio tope interno (CHRONICLER_TIMEOUT=600s) más
# el reinicio del servicio; margen generoso para no cortarlo a mitad.
TIMEOUT_SECONDS = 700


def main() -> int:
    try:
        with open(TELEGRAM_SESSION_FILE) as f:
            info = json.load(f)
    except FileNotFoundError:
        print(f"No existe {TELEGRAM_SESSION_FILE} — el servicio de Telegram "
              "nunca ha arrancado con la marca <AGENT>_SERVICE=telegram puesta, "
              "o aún no ha pasado por SessionStart.")
        return 1
    except (json.JSONDecodeError, OSError) as e:
        print(f"No pude leer {TELEGRAM_SESSION_FILE}: {e}")
        return 1

    session_id = info.get("session_id") or ""
    transcript_path = info.get("transcript_path") or ""
    if not session_id or not transcript_path:
        print(f"{TELEGRAM_SESSION_FILE} existe pero le faltan datos: {info!r}")
        return 1

    print(f"Sesión objetivo: {session_id}\nTranscript: {transcript_path}")
    print("Guardando memoria y reiniciando claude-telegram.service…")

    try:
        result = subprocess.run(
            [sys.executable, RESET_SEQUENCE, session_id, transcript_path],
            timeout=TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        print(f"reset_sequence.py superó los {TIMEOUT_SECONDS}s — revisa "
              "/home/<agent>/logs/<agent>-reset.log a mano.")
        return 1

    if result.returncode != 0:
        print(f"reset_sequence.py salió con código {result.returncode} — "
              "revisa /home/<agent>/logs/<agent>-reset.log.")
        return 1

    print("Hecho. Revisa Telegram para confirmar el reinicio.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

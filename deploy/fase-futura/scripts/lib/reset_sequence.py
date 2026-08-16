#!/usr/bin/env python3
# chmod +x /home/<agent>/workspace/scripts/lib/reset_sequence.py
"""reset_sequence.py — Guardar memoria y DESPUÉS reiniciar el servicio.

Por qué existe (incidente 2026-07-28): el chronicler vivía en `SessionEnd`, es
decir, se lanzaba en el instante exacto en que el proceso `claude` moría. Con
`KillMode=control-group`, systemd manda SIGTERM a todo el cgroup, así que el
nieto `claude --print` del chronicler nacía dentro de un árbol que ya se estaba
derribando y moría en ~250ms. Nunca llegó a escribir una sola memoria: 8 cierres
de sesión, 0 filas en `agent_memory`.

La solución no es sobrevivir al apagado, es no correr durante él. Este script
invierte el orden:

    guardar memoria  →  esperar a que termine  →  systemctl restart

Mientras el chronicler trabaja nadie está matando nada: el reinicio todavía no
se ha pedido. Cuando termina, y solo entonces, se dispara.

Secuencia de mensajes que ve el usuario:
    1. "Guardando memoria…"     (lo manda quien invoca este script)
    2. resumen de lo guardado   (lo manda el propio chronicler.py)
    3. "Reiniciando…"           (este script)
    4. saludo de la sesión nueva

TOPE DE TIEMPO: el `/reset` es la vía de escape del usuario cuando algo está
colgado. Si el chronicler se atasca (modelo sin responder, BD caída) y
esperásemos indefinidamente, el reinicio no llegaría nunca y el único comando
útil en esa situación quedaría inservible. Por eso hay un tope generoso: no
corta el trabajo normal (~78s), solo el patológico, y en ese caso avisa y
reinicia igual. El reinicio SIEMPRE ocurre — es la garantía de este script.

Uso:
    reset_sequence.py <session_id> <transcript_path>
"""
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request

# Overrides solo para test (mismo patrón que <AGENT>_TMP_OVERRIDE en conftest.py):
# sin ellos, un test escribiría en el log de producción y ejecutaría el
# chronicler de verdad. En producción nadie define estas variables.
CHRONICLER = os.environ.get("<AGENT>_CHRONICLER_OVERRIDE",
                            "/home/<agent>/workspace/scripts/lib/chronicler.py")
SERVICE = "claude-telegram.service"
LOG = os.environ.get("<AGENT>_RESET_LOG_OVERRIDE", "/home/<agent>/logs/<agent>-reset.log")

# Trabajo normal medido: ~78s para un transcript de 22K caracteres. El tope solo
# existe para que un chronicler atascado no deje al usuario sin reinicio.
CHRONICLER_TIMEOUT = int(os.environ.get("<AGENT>_RESET_TIMEOUT_OVERRIDE", "600"))

# Margen para que terminen subprocesos secundarios (greeting, ticker) antes de
# derribar el cgroup. Corto y acotado: nadie debe quedarse esperando por esto.
SECONDARY_WAIT = 30
SECONDARY_POLL = 0.5


def log(msg: str) -> None:
    try:
        with open(LOG, "a") as f:
            from datetime import datetime
            f.write(f"[{datetime.now().isoformat()}] {msg}\n")
    except Exception:
        pass


def tg_send(text: str) -> None:
    """FAIL-OPEN: sin credenciales o sin red, seguimos con el reinicio igual."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return
    try:
        data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
        urllib.request.urlopen(
            urllib.request.Request(
                f"https://api.telegram.org/bot{token}/sendMessage", data=data
            ),
            timeout=8,
        )
    except Exception as e:
        log(f"[WARN] no se pudo enviar '{text[:30]}': {e}")


def run_chronicler(session_id: str, transcript_path: str) -> str:
    """Ejecuta el chronicler y espera. Devuelve un veredicto para el log.

    El chronicler manda su propio resumen por Telegram, así que aquí no se
    duplica el mensaje: solo se avisa cuando algo va mal.
    """
    if not transcript_path or not os.path.isfile(transcript_path):
        log(f"[SKIP] transcript inexistente: {transcript_path!r}")
        return "sin-transcript"

    payload = json.dumps({"session_id": session_id, "transcript_path": transcript_path})
    env = dict(os.environ)
    # chronicler.py se planta si ve esta bandera (guard anti-reentrada pensado
    # para cuando lo llamaba un hook). Aquí no hay hook en curso: la limpiamos o
    # saldría sin hacer nada.
    env.pop("<AGENT>_HOOK_RUNNING", None)
    env["<AGENT>_CONTEXT"] = "main"

    started = time.time()
    try:
        result = subprocess.run(
            [sys.executable, CHRONICLER],
            input=payload, text=True, capture_output=True,
            timeout=CHRONICLER_TIMEOUT, env=env,
        )
    except subprocess.TimeoutExpired:
        log(f"[TIMEOUT] chronicler superó {CHRONICLER_TIMEOUT}s — reinicio igualmente")
        tg_send(f"El guardado de memoria tardó más de {CHRONICLER_TIMEOUT // 60} min. "
                "Reinicio sin esperar más.")
        return "timeout"
    except Exception as e:
        log(f"[ERROR] no se pudo ejecutar chronicler: {type(e).__name__}: {e}")
        tg_send("No pude lanzar el guardado de memoria. Reinicio igualmente.")
        return "error"

    elapsed = time.time() - started
    if result.returncode != 0:
        log(f"[ERROR] chronicler rc={result.returncode} en {elapsed:.1f}s: "
            f"{(result.stderr or '').strip()[-300:]}")
        tg_send("El guardado de memoria falló. Reinicio igualmente.")
        return "failed"

    log(f"[OK] chronicler terminó en {elapsed:.1f}s")
    return "ok"


def secondary_processes_alive() -> bool:
    """¿Queda algún `claude --print` de este usuario trabajando?

    Son los subprocesos secundarios del harness (chronicler, saludo). Reiniciar
    con uno a medias es exactamente el fallo que este script viene a arreglar.
    """
    try:
        out = subprocess.run(["pgrep", "-u", str(os.getuid()), "-f", "claude --print"],
                             capture_output=True, text=True, timeout=5)
        return bool(out.stdout.strip())
    except Exception:
        return False


def wait_for_secondaries() -> None:
    deadline = time.time() + SECONDARY_WAIT
    while time.time() < deadline:
        if not secondary_processes_alive():
            return
        time.sleep(SECONDARY_POLL)
    log(f"[WARN] seguían vivos subprocesos secundarios tras {SECONDARY_WAIT}s — reinicio igualmente")


def restart() -> None:
    log("[RESTART] sudo systemctl restart")
    tg_send("Reiniciando…")
    try:
        subprocess.run(["sudo", "systemctl", "restart", SERVICE],
                       capture_output=True, text=True, timeout=120)
    except Exception as e:
        # Si ni esto funciona, el usuario se queda sin reinicio: hay que decirlo.
        log(f"[ERROR] el reinicio falló: {type(e).__name__}: {e}")
        tg_send("No pude reiniciar el servicio. Hazlo a mano.")


def main() -> None:
    session_id = sys.argv[1] if len(sys.argv) > 1 else ""
    transcript_path = sys.argv[2] if len(sys.argv) > 2 else ""
    log(f"[START] session={session_id or '(vacío)'} transcript={transcript_path or '(vacío)'}")

    verdict = run_chronicler(session_id, transcript_path)
    wait_for_secondaries()
    restart()
    log(f"[END] veredicto del guardado: {verdict}")


main()

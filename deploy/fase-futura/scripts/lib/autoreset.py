#!/usr/bin/env python3
# chmod +x /home/<agent>/workspace/scripts/lib/autoreset.py
"""autoreset.py — Reinicio nocturno automático de <Agent> (core_task 'autoreset').

Ejecutado por heartbeat.py como script_path de la fila que midnight.py
materializa en agent_inbox a partir de core_task. Sigue el contrato
obligatorio de los scripts de `task`: imprime SIEMPRE un único JSON
{"ok": bool, "notify": null|{"severity","message","context"}} en stdout,
pase lo que pase. El exit code no se usa para nada.

Si la última actividad fue hace >1h -> reinicia el servicio.
Si fue hace <=1h (sesión activa) -> programa un reintento en 1h vía systemd-run.
Ambos son la operación normal y no necesitan al modelo (ok=true, notify=null);
solo un fallo real del propio reinicio/reintento se escala.
"""
import glob
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

TRANSCRIPT_GLOB = '/home/<agent>/claude/.claude/projects/-home-<agent>-claude/*.jsonl'
SERVICE = 'claude-telegram.service'
SCRIPT = '/home/<agent>/workspace/scripts/lib/autoreset.py'
LOG = '/home/<agent>/logs/<agent>-autoreset.log'
IDLE_THRESHOLD_SECONDS = 3600


def log(msg: str) -> None:
    try:
        with open(LOG, 'a') as f:
            f.write(f"[{datetime.now().isoformat()}] {msg}\n")
    except Exception:
        pass


def ok_result(notify=None) -> dict:
    return {'ok': True, 'notify': notify}


def fail_result(severity: str, context: str) -> dict:
    return {'ok': False, 'notify': {'severity': severity, 'message': None, 'context': context}}


def find_latest_transcript() -> str | None:
    files = glob.glob(TRANSCRIPT_GLOB)
    return max(files, key=os.path.getmtime) if files else None


def last_message_timestamp(path: str) -> datetime | None:
    """Return the timestamp of the last human or assistant message in the transcript."""
    last_ts = None
    try:
        with open(path, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if obj.get('type') not in ('user', 'assistant'):
                    continue
                ts_str = obj.get('timestamp')
                if not ts_str:
                    continue
                try:
                    ts = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
                    if last_ts is None or ts > last_ts:
                        last_ts = ts
                except ValueError:
                    continue
    except OSError:
        pass
    return last_ts


def restart_service() -> tuple:
    """(success: bool, error: str|None)."""
    log(f"[RESTART] Ejecutando sudo systemctl restart {SERVICE}")
    result = subprocess.run(
        ['sudo', 'systemctl', 'restart', SERVICE],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        log(f"[ERROR] Reinicio fallido: {result.stderr.strip()}")
        return False, result.stderr.strip()
    log("[OK] Servicio reiniciado correctamente")
    return True, None


def schedule_retry() -> tuple:
    """Programa una nueva ejecución en 1h usando systemd-run. (success, error)."""
    log("[RETRY] Sesión activa — programando reintento en 1h vía systemd-run")
    result = subprocess.run(
        [
            'systemd-run',
            '--user',
            '--on-active=3600',
            sys.executable, SCRIPT,
        ],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        log(f"[ERROR] systemd-run falló: {result.stderr.strip()}")
        return False, result.stderr.strip()
    log("[OK] Reintento programado para dentro de 1h")
    return True, None


def main() -> dict:
    log("[START] autoreset iniciado")

    transcript = find_latest_transcript()
    if not transcript:
        log("[INFO] No se encontró transcript — nada que hacer")
        return ok_result(None)

    last_ts = last_message_timestamp(transcript)
    if last_ts is None:
        log("[INFO] Transcript vacío — servicio recién reiniciado, nada que hacer")
        return ok_result(None)

    now = datetime.now(timezone.utc)
    elapsed = (now - last_ts).total_seconds()
    log(f"[INFO] Último mensaje hace {elapsed:.0f}s (umbral: {IDLE_THRESHOLD_SECONDS}s)")

    if elapsed > IDLE_THRESHOLD_SECONDS:
        success, error = restart_service()
        if success:
            return ok_result(None)
        return fail_result('high', f"autoreset: fallo al reiniciar {SERVICE}: {error}")

    success, error = schedule_retry()
    if success:
        return ok_result(None)
    return fail_result('high', f"autoreset: fallo al programar el reintento en 1h: {error}")


if __name__ == '__main__':
    try:
        outcome = main()
    except Exception as e:
        log(f"[ERROR] excepción no controlada: {e}")
        outcome = fail_result('high', f"excepción no controlada en autoreset.py: {e}")
    print(json.dumps(outcome))
    sys.exit(0)

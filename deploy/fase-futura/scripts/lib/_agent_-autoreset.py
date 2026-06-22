#!/usr/bin/env python3
# chmod +x /home/<agent>/workspace/scripts/lib/<agent>-autoreset.py
"""
<agent>-autoreset.py — Reinicio nocturno automático de <Agent>.

Ejecutado por core_task / cron a las 4:00 AM Europe/Madrid.
Si la última actividad fue hace >1h → reinicia el servicio.
Si fue hace <=1h (sesión activa) → programa un reintento en 1h vía systemd-run.
"""

import glob
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

TRANSCRIPT_GLOB = '/home/<agent>/claude/.claude/projects/-home-<agent>-claude/*.jsonl'
SERVICE = 'claude-telegram.service'
SCRIPT = '/home/<agent>/workspace/scripts/lib/<agent>-autoreset.py'
LOG = '/home/<agent>/logs/<agent>-autoreset.log'
IDLE_THRESHOLD_SECONDS = 3600


def log(msg: str) -> None:
    try:
        with open(LOG, 'a') as f:
            f.write(f"[{datetime.now().isoformat()}] {msg}\n")
    except Exception:
        pass


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


def restart_service() -> None:
    log(f"[RESTART] Ejecutando sudo systemctl restart {SERVICE}")
    result = subprocess.run(
        ['sudo', 'systemctl', 'restart', SERVICE],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        log(f"[ERROR] Reinicio fallido: {result.stderr.strip()}")
    else:
        log("[OK] Servicio reiniciado correctamente")


def schedule_retry() -> None:
    """Programa una nueva ejecución en 1h usando systemd-run."""
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
    else:
        log("[OK] Reintento programado para dentro de 1h")


def main() -> None:
    log("[START] <agent>-autoreset iniciado")

    transcript = find_latest_transcript()
    if not transcript:
        log("[INFO] No se encontró transcript — nada que hacer")
        return

    last_ts = last_message_timestamp(transcript)
    if last_ts is None:
        log("[INFO] Transcript vacío — servicio recién reiniciado, nada que hacer")
        return

    now = datetime.now(timezone.utc)
    elapsed = (now - last_ts).total_seconds()
    log(f"[INFO] Último mensaje hace {elapsed:.0f}s (umbral: {IDLE_THRESHOLD_SECONDS}s)")

    if elapsed > IDLE_THRESHOLD_SECONDS:
        restart_service()
    else:
        schedule_retry()


main()

#!/usr/bin/env python3
"""heartbeat.py — consumidor determinista del inbox de <Agent> (heartbeat.service).

Sustituye la invocación incondicional de `claude --print` que hacía
heartbeat.service directamente. Python, no el modelo, reclama de forma
atómica las filas elegibles de agent_inbox. Si no hay ninguna, termina sin
tocar el modelo — la inmensa mayoría de los disparos del timer (cada 5 min,
~288 veces/día) caían sobre un inbox vacío.

De las filas reclamadas, las que son event_type='task' con `script_path`
en el payload se resuelven ejecutando ese script directamente. Todo script
de este tipo tiene que cumplir un contrato de salida único y obligatorio
(ver `run_task_script`): imprime siempre un JSON `{"ok": bool, "notify":
null|{...}}` en stdout, nunca se infiere el resultado por heurística sobre
el exit code o si stdout viene vacío. Con `ok=true` y `notify=null` la fila
se cierra ahí mismo, sin pasar por el modelo. Todo lo demás — lo que no
tiene handler determinista, un script que reporta `ok=false`, o que pide
notificar algo — se le pasa al modelo, pero ya reclamado y por `id`
explícito: el modelo deja de hacer su propio UPDATE...RETURNING genérico
(evita la carrera de reclamación Python-vs-modelo).

Commit 2 de la migración. Deliberadamente fuera de alcance todavía (ver
informe de diseño): plantillas para `reminder`/`alert` con `payload.text`,
un `notify()` centralizado con candado atómico sobre `daily_schedule`,
tiering de modelo (Haiku para redacción pura), y manejo de reintentos con
un contador de `attempts` para scripts que fallan repetidamente.

Script standalone con psycopg2, sin dependencias externas adicionales.
Connection string en POSTGRES_CONNECTION_STRING (inyectada por EnvironmentFile).
"""
import json
import os
import subprocess
import sys
from datetime import datetime

import psycopg2
import psycopg2.extras

LOG = '/home/<agent>/logs/heartbeat.log'
DB_DSN = os.environ.get('POSTGRES_CONNECTION_STRING', '')
AGENT_HOME = '/home/<agent>'
CLAUDE_BIN = '/home/<agent>/claude/.local/bin/claude'
PROMPT_FILE = '/home/<agent>/workspace/scripts/lib/prompts/heartbeat.md'
MODEL_TIMEOUT_SECONDS = 220  # por debajo de TimeoutStartSec=4min del .service
SCRIPT_TIMEOUT_SECONDS = 60

CLAIM_QUERY = """
    UPDATE agent_inbox
       SET claimed_at = now()
     WHERE claimed_at IS NULL
       AND processed_at IS NULL
       AND process_after <= now()
    RETURNING id, source, event_type, payload, severity, agent, dedupe_key,
              scheduled_task_id, target_task_id, created_at, process_after
"""


def log(msg: str) -> None:
    try:
        with open(LOG, 'a') as f:
            f.write(f"[{datetime.now().isoformat()}] {msg}\n")
    except Exception:
        pass


# --------------------------------------------------------------------------
# Reclamación
# --------------------------------------------------------------------------
def claim_pending(cur) -> list:
    """Reclama de forma atómica todas las filas elegibles ahora mismo. Una
    única UPDATE...RETURNING: nunca SELECT seguido de UPDATE, para que dos
    heartbeats que coincidieran no procesen la misma fila dos veces."""
    cur.execute(CLAIM_QUERY)
    return cur.fetchall()


# --------------------------------------------------------------------------
# Handler determinista: event_type='task' con payload.script_path
# --------------------------------------------------------------------------
def resolve_script_path(script_path: str) -> str:
    if os.path.isabs(script_path):
        return script_path
    return os.path.join(AGENT_HOME, script_path)


def _contract_violation(reason: str) -> dict:
    return {'resolved': False, 'ok': None, 'notify': None,
            'error': f"incumplimiento de contrato: {reason}"}


def run_task_script(row: dict) -> dict:
    """Ejecuta payload['script_path'] y valida su salida contra el contrato
    obligatorio de los scripts de `task`: SIEMPRE imprimen en stdout un único
    JSON con esta forma, pase lo que pase (el exit code no se usa para nada:
    la fuente de verdad es este JSON, no hay inferencia por heurística):

        {"ok": bool, "notify": null | {"severity": "critical|high|medium|low",
                                        "message": str|null, "context": ...}}

    - ok=true, notify=null            -> resuelto del todo, no hace falta el modelo.
    - ok=true, notify.message ya listo -> el modelo solo tiene que enviarlo.
    - ok=true, notify.context sin message -> el modelo tiene que redactarlo.
    - ok=false                        -> la tarea falló; el modelo decide con
                                          lo que haya en notify (si lo hay).

    Cualquier desviación de este contrato (no imprime JSON, JSON sin 'ok'
    booleano, el proceso ni siquiera arranca) se trata como incumplimiento de
    contrato -- un error de nivel distinto al de que la tarea falle -- y se
    escala al modelo con la salida cruda para que decida.
    """
    script_path = resolve_script_path(row['payload']['script_path'])
    try:
        result = subprocess.run(
            [script_path], capture_output=True, timeout=SCRIPT_TIMEOUT_SECONDS, text=True,
        )
    except subprocess.TimeoutExpired:
        return _contract_violation(f"timeout tras {SCRIPT_TIMEOUT_SECONDS}s")
    except (FileNotFoundError, PermissionError) as e:
        return _contract_violation(str(e))

    stdout = (result.stdout or '').strip()
    try:
        data = json.loads(stdout) if stdout else None
    except json.JSONDecodeError:
        data = None

    if not isinstance(data, dict) or not isinstance(data.get('ok'), bool):
        detail = (f"exit={result.returncode} stdout={stdout[:300]!r} "
                  f"stderr={result.stderr.strip()[:300]!r}")
        return _contract_violation(detail)

    notify = data.get('notify')
    if data['ok'] and notify is None:
        return {'resolved': True, 'ok': True, 'notify': None, 'error': None}
    if data['ok']:
        return {'resolved': False, 'ok': True, 'notify': notify, 'error': None}
    return {'resolved': False, 'ok': False, 'notify': notify,
            'error': 'la tarea falló (ok=false)'}


def close_row(cur, row_id, decision: str) -> None:
    cur.execute(
        "UPDATE agent_inbox SET processed_at = now(), decision = %s WHERE id = %s",
        (decision, row_id),
    )


def is_task_with_script(row: dict) -> bool:
    payload = row.get('payload')
    return (row.get('event_type') == 'task'
            and isinstance(payload, dict)
            and bool(payload.get('script_path')))


# --------------------------------------------------------------------------
# Dispatch
# --------------------------------------------------------------------------
def classify_and_dispatch(cur, rows: list) -> list:
    """Resuelve en Python lo que tiene handler determinista; devuelve la
    lista de filas que todavía necesitan el modelo (enriquecidas con el
    resultado del intento determinista, si lo hubo, para que el modelo no
    tenga que repetir trabajo)."""
    needs_model = []
    for row in rows:
        if is_task_with_script(row):
            outcome = run_task_script(row)
            if outcome['resolved']:
                close_row(cur, row['id'], 'dropped')
                log(f"[TASK] '{row['source']}' resuelta por script, sin modelo")
                continue
            row['_script_outcome'] = outcome
            log(f"[TASK] '{row['source']}' no resuelta por script "
                f"({outcome['error'] or 'algo que reportar'}), pasa al modelo")
        needs_model.append(row)
    return needs_model


# --------------------------------------------------------------------------
# Modelo (solo para lo que no se pudo resolver en Python)
# --------------------------------------------------------------------------
def build_model_prompt(rows: list) -> str:
    with open(PROMPT_FILE) as f:
        base = f.read()
    rows_json = json.dumps(rows, indent=2, default=str)
    return (
        f"{base}\n\n---\n\n"
        "## Filas ya reclamadas a procesar\n\n"
        "Python ya ha reclamado estas filas de `agent_inbox` (no vuelvas a "
        "reclamar nada por tu cuenta: no ejecutes el UPDATE...RETURNING "
        "genérico, procesa EXACTAMENTE estos ids). Para las que traen "
        "`_script_outcome`, Python ya intentó resolverlas con el script "
        "determinista de la tarea y no lo consiguió — revisa el motivo "
        "antes de decidir.\n\n"
        f"```json\n{rows_json}\n```\n"
    )


def run_model(rows: list) -> int:
    prompt_text = build_model_prompt(rows)
    try:
        result = subprocess.run(
            [CLAUDE_BIN, '--print', '--strict-mcp-config'],
            input=prompt_text, capture_output=True, timeout=MODEL_TIMEOUT_SECONDS, text=True,
        )
    except subprocess.TimeoutExpired:
        log(f"[ERROR] claude --print superó el timeout de {MODEL_TIMEOUT_SECONDS}s")
        return 1
    if result.returncode != 0:
        log(f"[ERROR] claude --print salió con código {result.returncode}: {result.stderr[:500]}")
    return result.returncode


# --------------------------------------------------------------------------
def main() -> int:
    if not DB_DSN:
        log("[ERROR] POSTGRES_CONNECTION_STRING no definida")
        return 1

    try:
        conn = psycopg2.connect(DB_DSN)
    except Exception as e:
        log(f"[ERROR] no se pudo conectar a la base de datos: {e}")
        return 1

    try:
        conn.autocommit = False
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            rows = claim_pending(cur)
            if not rows:
                conn.commit()
                log("[IDLE] sin items elegibles, no se invoca el modelo")
                return 0
            needs_model = classify_and_dispatch(cur, rows)
        conn.commit()
    except Exception as e:
        conn.rollback()
        log(f"[ERROR] fallo reclamando/despachando agent_inbox: {e}")
        return 1
    finally:
        conn.close()

    if not needs_model:
        log(f"[OK] {len(rows)} item(s) resueltos por script, sin invocar el modelo")
        return 0

    log(f"[WORK] {len(needs_model)} de {len(rows)} item(s) necesitan el modelo")
    return run_model(needs_model)


if __name__ == '__main__':
    sys.exit(main())

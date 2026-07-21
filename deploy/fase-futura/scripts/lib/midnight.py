#!/usr/bin/env python3
# chmod +x /home/<agent>/workspace/scripts/lib/midnight.py
"""midnight.py — Job de medianoche de <Agent> (midnight.service).

Cada día a las 00:00 rehace el día de mañana a partir de `schedule_config`,
la única fuente de verdad:
1. Resuelve el day_type de mañana (ISO weekday, más '*' que aplica siempre
   y 'S' por fecha concreta -- ver resolve_calendar_day_type() para el hueco
   de festivo/viaje, todavía sin activar).
2. Recorre schedule_config para ese day_type: las filas kind='slot'
   materializan daily_schedule; las filas kind='task' encolan en
   agent_inbox la ejecución del scheduled_task que referencian -- tanto si
   es kind='core' (script determinista, lo resuelve heartbeat.py sin pasar
   por el modelo) como si es 'briefing'/'monitor' (lo redacta el modelo a
   partir de prompt_file). No hay dos caminos de reconciliación distintos:
   todo scheduled_task, sea o no mantenimiento, vive y se agenda igual.
3. Loguea el resultado a /home/<agent>/logs/midnight.log.

Script standalone con psycopg2, sin dependencias externas adicionales.
Connection string en POSTGRES_CONNECTION_STRING (inyectada por EnvironmentFile).
"""

import json
import os
import sys
from datetime import datetime, date, timedelta

import psycopg2
import psycopg2.extras

LOG = '/home/<agent>/logs/midnight.log'
DB_DSN = os.environ.get('POSTGRES_CONNECTION_STRING', '')

# Mapeo day_type (§1.2 chk_day_type): '1'..'7' = lunes..domingo (ISO),
# '*' = todos los días, 'H' = festivo, 'T' = viaje, 'S' = fecha específica.
ISO_TO_DAYTYPE = {1: '1', 2: '2', 3: '3', 4: '4', 5: '5', 6: '6', 7: '7'}


def log(msg: str) -> None:
    try:
        with open(LOG, 'a') as f:
            f.write(f"[{datetime.now().isoformat()}] {msg}\n")
    except Exception:
        pass


# --------------------------------------------------------------------------
# Resolución de day_type: hoy solo ISO weekday. 'H' (festivo) y 'T' (viaje)
# están reservados en el schema pero nadie los resuelve todavía -- depende
# de a qué calendario mirar y cómo se marca un festivo/viaje en él, pendiente
# de definir. El acceso a calendario será vía API HTTP genérica servida por
# n8n (no MCP, no CalDAV directo -- decisión explícita: el modelo no toca el
# calendario en crudo, y midnight es determinista, sin invocar al modelo).
# --------------------------------------------------------------------------
N8N_CALENDAR_WEBHOOK_URL = os.environ.get('N8N_CALENDAR_WEBHOOK_URL', '')


def resolve_calendar_day_type(target: date) -> str | None:
    """Devuelve 'H'/'T' si `target` es festivo/viaje según el calendario, o
    None si no aplica ninguno de los dos (o si el webhook no está configurado
    todavía). None dice a reconcile_day() que se quede solo con el day_type
    por ISO weekday -- comportamiento actual, sin cambios, hasta que
    N8N_CALENDAR_WEBHOOK_URL exista de verdad."""
    if not N8N_CALENDAR_WEBHOOK_URL:
        return None
    # TODO: GET a N8N_CALENDAR_WEBHOOK_URL con `target` (ISO date), esperar
    # {"day_type": "H"|"T"|None}. Sin implementar -- pendiente de que exista
    # el workflow de n8n y de decidir qué calendario mirar.
    return None


# --------------------------------------------------------------------------
# Reconciliación unificada: schedule_config -> daily_schedule / agent_inbox.
# --------------------------------------------------------------------------
def reconcile_day(cur, target: date) -> dict:
    iso = target.isoweekday()
    day_types = {ISO_TO_DAYTYPE[iso], '*'}
    calendar_type = resolve_calendar_day_type(target)
    if calendar_type:
        day_types.add(calendar_type)

    cur.execute(
        "SELECT sc.id, sc.kind, sc.time_from, sc.time_to, "
        "       st.name AS slot_name, st.is_modifier, "
        "       st.critical_limit, st.high_limit, st.medium_limit, st.low_limit, "
        "       t.id AS task_id, t.name AS task_name, t.kind AS task_kind, "
        "       t.script_path, t.prompt_file, t.severity AS task_severity "
        "FROM schedule_config sc "
        "LEFT JOIN slot_type st ON st.id = sc.slot_type_id "
        "LEFT JOIN scheduled_task t ON t.id = sc.scheduled_task_id "
        "WHERE sc.enabled = true "
        "  AND ( sc.day_type = ANY(%s) "
        "        OR (sc.day_type = 'S' AND %s BETWEEN sc.date_from "
        "            AND COALESCE(sc.date_to, sc.date_from)) )",
        (list(day_types), target)
    )
    rows = cur.fetchall()

    materialized = 0
    enqueued = 0
    for r in rows:
        if r['kind'] == 'slot':
            if materialize_slot(cur, target, r):
                materialized += 1
        else:
            if enqueue_scheduled_task(cur, target, r):
                enqueued += 1
    return {'materialized': materialized, 'enqueued': enqueued}


def materialize_slot(cur, target: date, r: dict) -> bool:
    start_ts = datetime.combine(target, r['time_from'])
    end_ts = datetime.combine(target, r['time_to'])
    if end_ts <= start_ts:
        log(f"[SKIP] ventana inválida slot={r['slot_name']} {start_ts}-{end_ts}")
        return False
    try:
        cur.execute(
            "INSERT INTO daily_schedule "
            "(date, slot_type_name, is_modifier, start_ts, end_ts, "
            " critical_limit, high_limit, medium_limit, low_limit) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT DO NOTHING",
            (target, r['slot_name'], r['is_modifier'], start_ts, end_ts,
             r['critical_limit'], r['high_limit'], r['medium_limit'], r['low_limit'])
        )
        return bool(cur.rowcount)
    except Exception as e:
        log(f"[ERROR] insert daily_schedule slot={r['slot_name']}: {e}")
        return False


def enqueue_scheduled_task(cur, target: date, r: dict) -> bool:
    """Encola en agent_inbox la ejecución de mañana del scheduled_task de la
    fila `r`, si no hay ya una entrada pendiente para él. 'core' se resuelve
    como script determinista (event_type='task' + payload.script_path,
    exactamente el contrato que espera heartbeat.py); 'briefing'/'monitor'
    lo redacta el modelo (event_type='scheduled_task' + prompt_file)."""
    source = f"scheduled_task:{r['task_name']}"
    cur.execute(
        "SELECT 1 FROM agent_inbox "
        "WHERE source = %s AND processed_at IS NULL AND process_after >= now() "
        "LIMIT 1",
        (source,)
    )
    if cur.fetchone():
        return False

    process_after = datetime.combine(target, r['time_from'])
    dedupe_key = f"{source}:{target.isoformat()}"

    if r['task_kind'] == 'core':
        event_type = 'task'
        payload = {"core_task": r['task_name'], "script_path": r['script_path']}
    else:
        event_type = 'scheduled_task'
        payload = {"scheduled_task": r['task_name'], "prompt_file": r['prompt_file']}

    cur.execute(
        "INSERT INTO agent_inbox (source, event_type, payload, severity, agent, "
        "dedupe_key, scheduled_task_id, process_after) "
        "VALUES (%s, %s, %s::jsonb, %s, 'any', %s, %s, %s)",
        (source, event_type, json.dumps(payload), r['task_severity'],
         dedupe_key, r['task_id'], process_after)
    )
    log(f"[SCHEDULED_TASK] encolada '{r['task_name']}' (kind={r['task_kind']}) "
        f"para {process_after.isoformat()}")
    return True


def main():
    if not DB_DSN:
        log("[ERROR] POSTGRES_CONNECTION_STRING no definida")
        sys.exit(1)

    log("[START] midnight job")
    tomorrow = date.today() + timedelta(days=1)

    try:
        conn = psycopg2.connect(DB_DSN)
        conn.autocommit = False
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        result = reconcile_day(cur, tomorrow)

        conn.commit()
        cur.close()
        conn.close()
        log(f"[OK] daily_schedule materializadas={result['materialized']} "
            f"scheduled_task encoladas={result['enqueued']} para {tomorrow.isoformat()}")
    except Exception as e:
        log(f"[ERROR] midnight job: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()

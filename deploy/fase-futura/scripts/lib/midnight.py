#!/usr/bin/env python3
# chmod +x /home/<agent>/workspace/scripts/lib/midnight.py
"""midnight.py — Job de medianoche de <Agent> (midnight.service).

Cada día a las 00:00:
1. Lee las entradas habilitadas de core_task.
2. Para cada tarea, verifica si hay una entrada futura en inbox; si no, la crea.
3. Materializa el daily_schedule del día siguiente a partir de schedule_config.
4. Loguea el resultado a /home/<agent>/logs/midnight.log.

Script standalone con psycopg2, sin dependencias externas adicionales.
Connection string en POSTGRES_CONNECTION_STRING (inyectada por EnvironmentFile).
"""

import json
import os
import sys
from datetime import datetime, date, timedelta, time as dtime

import psycopg2
import psycopg2.extras

LOG = '/home/<agent>/logs/midnight.log'
DB_DSN = os.environ.get('POSTGRES_CONNECTION_STRING', '')

# Mapeo day_type (§1.2 chk_day_type): '1'..'7' = lunes..domingo (ISO),
# 'H' = festivo, 'T' = teletrabajo, 'S' = fecha específica.
ISO_TO_DAYTYPE = {1: '1', 2: '2', 3: '3', 4: '4', 5: '5', 6: '6', 7: '7'}


def log(msg: str) -> None:
    try:
        with open(LOG, 'a') as f:
            f.write(f"[{datetime.now().isoformat()}] {msg}\n")
    except Exception:
        pass


# --------------------------------------------------------------------------
# 1-2. Reconciliación de core_task: cada tarea habilitada tiene una entrada
#       futura en inbox.
# --------------------------------------------------------------------------
def reconcile_core_tasks(cur) -> int:
    cur.execute("SELECT id, name, schedule_cron, script_path FROM core_task WHERE enabled = true")
    tasks = cur.fetchall()
    created = 0
    for t in tasks:
        # ¿Hay una entrada futura pendiente para esta tarea?
        cur.execute(
            "SELECT 1 FROM inbox "
            "WHERE source = %s AND processed_at IS NULL AND process_after >= now() "
            "LIMIT 1",
            (f"core_task:{t['name']}",)
        )
        if cur.fetchone():
            continue

        process_after = next_run_from_cron(t['schedule_cron'])
        payload = {
            "core_task": t['name'],
            "script_path": t['script_path'],
        }
        cur.execute(
            "INSERT INTO inbox (source, event_type, payload, severity, agent, "
            "dedupe_key, process_after) "
            "VALUES (%s, 'task', %s::jsonb, 'low', 'any', %s, %s)",
            (
                f"core_task:{t['name']}",
                json.dumps(payload),
                f"core_task:{t['name']}:{process_after.date().isoformat()}",
                process_after,
            )
        )
        cur.execute(
            "UPDATE core_task SET last_enqueued_at = now() WHERE id = %s",
            (t['id'],)
        )
        created += 1
        log(f"[CORE_TASK] encolada '{t['name']}' para {process_after.isoformat()}")
    return created


def next_run_from_cron(cron_expr: str) -> datetime:
    """Calcula la próxima ejecución a partir de una expresión cron simple
    'min hour dom mon dow'. Soporta '*' y valores numéricos en min/hour/dow.
    Si no se puede parsear, devuelve mañana a las 03:00."""
    now = datetime.now()
    fallback = datetime.combine(now.date() + timedelta(days=1), dtime(3, 0))
    parts = cron_expr.split()
    if len(parts) != 5:
        return fallback
    minute, hour, dom, mon, dow = parts
    try:
        m = 0 if minute == '*' else int(minute)
        h = 0 if hour == '*' else int(hour)
    except ValueError:
        return fallback

    # Día de la semana objetivo (cron: 0/7 = domingo)
    target_dow = None
    if dow != '*':
        try:
            d = int(dow)
            target_dow = 7 if d == 0 else d  # ISO: 1..7 lunes..domingo
        except ValueError:
            target_dow = None

    candidate = datetime.combine(now.date(), dtime(h, m))
    if candidate <= now:
        candidate += timedelta(days=1)

    if target_dow is not None:
        for _ in range(8):
            if candidate.isoweekday() == target_dow and candidate > now:
                break
            candidate += timedelta(days=1)
    return candidate


# --------------------------------------------------------------------------
# 3. Materialización de daily_schedule para el día siguiente.
# --------------------------------------------------------------------------
def materialize_daily_schedule(cur, target: date) -> int:
    iso = target.isoweekday()
    day_type = ISO_TO_DAYTYPE[iso]

    # Slots aplicables: por day_type general + fechas específicas ('S').
    cur.execute(
        "SELECT sc.time_from, sc.time_to, st.name AS slot_name, st.is_modifier, "
        "       st.critical_limit, st.high_limit, st.medium_limit, st.low_limit "
        "FROM schedule_config sc "
        "JOIN slot_type st ON st.id = sc.slot_type_id "
        "WHERE sc.enabled = true AND sc.kind = 'slot' "
        "  AND ( sc.day_type = %s "
        "        OR (sc.day_type = 'S' AND %s BETWEEN sc.date_from "
        "            AND COALESCE(sc.date_to, sc.date_from)) )",
        (day_type, target)
    )
    rows = cur.fetchall()

    inserted = 0
    for r in rows:
        start_ts = datetime.combine(target, r['time_from'])
        end_ts = datetime.combine(target, r['time_to'])
        if end_ts <= start_ts:
            log(f"[SKIP] ventana inválida slot={r['slot_name']} {start_ts}-{end_ts}")
            continue
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
            if cur.rowcount:
                inserted += 1
        except Exception as e:
            log(f"[ERROR] insert daily_schedule slot={r['slot_name']}: {e}")
    return inserted


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

        created = reconcile_core_tasks(cur)
        materialized = materialize_daily_schedule(cur, tomorrow)

        conn.commit()
        cur.close()
        conn.close()
        log(f"[OK] core_task encoladas={created} daily_schedule materializadas={materialized} "
            f"para {tomorrow.isoformat()}")
    except Exception as e:
        log(f"[ERROR] midnight job: {e}")
        sys.exit(1)


main()

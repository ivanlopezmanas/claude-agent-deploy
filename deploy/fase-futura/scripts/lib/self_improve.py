#!/usr/bin/env python3
# chmod +x /home/<agent>/workspace/scripts/lib/self_improve.py
"""self_improve.py — recopilación determinista de evidencia para el agente
`self-improve` (core_task 'self-improve').

Sustituye a self-improve.sh, que invocaba `claude --print --agent
self-improve` directamente desde cron — el patrón viejo que el rediseño de
heartbeat quería evitar (el script decidía y llamaba al modelo él mismo, en
vez de que heartbeat decidiera si hacía falta). Ahora es heartbeat.py quien
lo ejecuta como script_path de la fila que midnight.py materializa en
agent_inbox a partir de core_task, y sigue el contrato obligatorio de los
scripts de `task`: imprime SIEMPRE un único JSON {"ok": bool, "notify":
null|{"severity","message","context"}} en stdout, pase lo que pase.

Este script NUNCA resuelve la fila por sí solo: la síntesis y la
priorización son juicio del modelo, no algo mecanizable. Su función es
recoger en Python, sin gastar turnos de modelo, la evidencia mecánica que el
agente self-improve necesitaría reunir con sus propias tools (pasos 0-2 y 5
de `self-improve.md`): mapa de ficheros, resultado de tests, validez de
settings.json + hooks presentes, tail del log de permisos, y memorias
recientes/recurrentes de agent_memory. Por eso siempre devuelve ok=true con
notify.context relleno (nunca notify=null): heartbeat.md reconoce
payload.core_task == 'self-improve' y delega en
Agent(subagent_type='self-improve') pasándole ese context, en vez de
redactar él mismo un mensaje de texto plano.

Cada sección de evidencia se recoge de forma independiente: si una falla
(DB caída, comando ausente...) se anota el error dentro de esa sección en
vez de tirar todo el script — el agente sigue pudiendo trabajar con lo que
sí se pudo reunir.

Script standalone; solo psycopg2 como dependencia externa (agent_memory).
"""
import glob
import json
import os
import subprocess
import sys
from datetime import datetime

import psycopg2
import psycopg2.extras

AGENT_HOME = '/home/<agent>'
CLAUDE_HOME = f'{AGENT_HOME}/claude'
WORKSPACE = f'{AGENT_HOME}/workspace'
LOG = f'{AGENT_HOME}/logs/<agent>-self-improve.log'
PERMISSIONS_LOG = f'{AGENT_HOME}/logs/<agent>-permissions.log'
SETTINGS_PATH = f'{CLAUDE_HOME}/.claude/settings.json'
IMPROVEMENTS_DIR = f'{WORKSPACE}/docs/improvements'
TAREAS_PENDIENTES = f'{WORKSPACE}/docs/tareas-pendientes.md'
DB_DSN = os.environ.get('POSTGRES_CONNECTION_STRING', '')

TERRITORY_DIRS = {
    'scripts': f'{WORKSPACE}/scripts',
    'agents': f'{CLAUDE_HOME}/.claude/agents',
    'skills': f'{CLAUDE_HOME}/.claude/skills',
    'improvements': IMPROVEMENTS_DIR,
    'tests': f'{WORKSPACE}/tests',
}
SYSTEM_BIN_DIR = '/usr/local/bin'
SYSTEM_BIN_PREFIX = '<agent>-'

TEST_TIMEOUT_SECONDS = 60
PERMISSIONS_LOG_TAIL_LINES = 50
MEMORY_RECENT_DAYS = 7
MEMORY_CHRONIC_DAYS = 30
MEMORY_CHRONIC_MIN_OCCURRENCES = 3


def log(msg: str) -> None:
    try:
        with open(LOG, 'a') as f:
            f.write(f"[{datetime.now().isoformat()}] {msg}\n")
    except Exception:
        pass


def ok_result(notify) -> dict:
    return {'ok': True, 'notify': notify}


def fail_result(severity: str, context: str) -> dict:
    return {'ok': False, 'notify': {'severity': severity, 'message': None, 'context': context}}


# --------------------------------------------------------------------------
# Paso 1 (self-improve.md) -- mapa de ficheros, inventario sin contenido
# --------------------------------------------------------------------------
def gather_territory() -> dict:
    territory = {}
    for label, path in TERRITORY_DIRS.items():
        try:
            entries = sorted(os.listdir(path)) if os.path.isdir(path) else None
            territory[label] = {'path': path, 'entries': entries}
        except Exception as e:
            territory[label] = {'path': path, 'error': str(e)}

    try:
        entries = sorted(
            os.path.basename(p)
            for p in glob.glob(os.path.join(SYSTEM_BIN_DIR, f'{SYSTEM_BIN_PREFIX}*'))
        )
        territory['system_bin'] = {'path': SYSTEM_BIN_DIR, 'entries': entries}
    except Exception as e:
        territory['system_bin'] = {'path': SYSTEM_BIN_DIR, 'error': str(e)}

    return territory


# --------------------------------------------------------------------------
# Paso 2 (self-improve.md) -- tests + estado del sistema
# --------------------------------------------------------------------------
def gather_tests() -> dict:
    try:
        result = subprocess.run(
            [sys.executable, '-m', 'pytest', f'{WORKSPACE}/tests', '-q', '--tb=no'],
            capture_output=True, text=True, timeout=TEST_TIMEOUT_SECONDS, cwd=WORKSPACE,
        )
        tail = '\n'.join((result.stdout or '').strip().splitlines()[-5:])
        return {'summary': tail, 'returncode': result.returncode}
    except subprocess.TimeoutExpired:
        return {'error': f'timeout tras {TEST_TIMEOUT_SECONDS}s'}
    except Exception as e:
        return {'error': str(e)}


def gather_settings_check() -> dict:
    try:
        with open(SETTINGS_PATH) as f:
            settings = json.load(f)
    except Exception as e:
        return {'valid_json': False, 'error': str(e)}

    missing_hooks = set()
    hooks = settings.get('hooks', {})
    for entries in (hooks.values() if isinstance(hooks, dict) else []):
        for entry in (entries if isinstance(entries, list) else []):
            for hook in (entry.get('hooks', []) if isinstance(entry, dict) else []):
                cmd = hook.get('command', '') if isinstance(hook, dict) else ''
                for tok in cmd.split():
                    if tok.startswith('/') and not os.path.exists(tok):
                        missing_hooks.add(tok)

    return {'valid_json': True, 'missing_hooks': sorted(missing_hooks)}


def gather_permissions_log_tail() -> dict:
    try:
        with open(PERMISSIONS_LOG) as f:
            lines = f.readlines()
        return {'tail': ''.join(lines[-PERMISSIONS_LOG_TAIL_LINES:])}
    except FileNotFoundError:
        return {'tail': None, 'note': 'sin log de permisos todavía'}
    except Exception as e:
        return {'error': str(e)}


# --------------------------------------------------------------------------
# Paso 5 (self-improve.md) -- memorias recientes + patrones recurrentes
# --------------------------------------------------------------------------
def gather_memory() -> dict:
    if not DB_DSN:
        return {'error': 'POSTGRES_CONNECTION_STRING no definida'}
    try:
        conn = psycopg2.connect(DB_DSN)
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT id, fecha, category, keywords, content FROM agent_memory "
                    f"WHERE fecha >= NOW() - INTERVAL '{MEMORY_RECENT_DAYS} days' "
                    "ORDER BY fecha ASC"
                )
                recent = [dict(r) for r in cur.fetchall()]

                cur.execute(
                    "SELECT k, COUNT(*) AS occurrences, MAX(fecha) AS last_seen "
                    "FROM agent_memory, unnest(keywords) AS k "
                    f"WHERE fecha >= NOW() - INTERVAL '{MEMORY_CHRONIC_DAYS} days' "
                    "GROUP BY k HAVING COUNT(*) >= %s "
                    "ORDER BY occurrences DESC LIMIT 20",
                    (MEMORY_CHRONIC_MIN_OCCURRENCES,),
                )
                chronic = [dict(r) for r in cur.fetchall()]
        finally:
            conn.close()
        return {f'recent_{MEMORY_RECENT_DAYS}d': recent, f'chronic_patterns_{MEMORY_CHRONIC_DAYS}d': chronic}
    except Exception as e:
        return {'error': str(e)}


# --------------------------------------------------------------------------
def latest_previous_report() -> str | None:
    try:
        files = glob.glob(os.path.join(IMPROVEMENTS_DIR, '*.md'))
        return max(files, key=os.path.getmtime) if files else None
    except Exception:
        return None


def main() -> dict:
    log("[START] self-improve: recopilando evidencia")

    context = {
        'territory': gather_territory(),
        'tests': gather_tests(),
        'settings_check': gather_settings_check(),
        'permissions_log_tail': gather_permissions_log_tail(),
        'memory': gather_memory(),
        'tareas_pendientes_path': TAREAS_PENDIENTES if os.path.exists(TAREAS_PENDIENTES) else None,
        'previous_report_path': latest_previous_report(),
    }

    log("[OK] evidencia recopilada, delega en el modelo")
    return ok_result({'severity': 'low', 'message': None, 'context': context})


if __name__ == '__main__':
    try:
        outcome = main()
    except Exception as e:
        log(f"[ERROR] excepción no controlada: {e}")
        outcome = fail_result('high', f"excepción no controlada en self_improve.py: {e}")
    print(json.dumps(outcome, default=str))
    sys.exit(0)

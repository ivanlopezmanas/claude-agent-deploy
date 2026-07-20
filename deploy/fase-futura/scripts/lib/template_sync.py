#!/usr/bin/env python3
# chmod +x /home/<agent>/workspace/scripts/lib/template_sync.py
"""template_sync.py — Pull diario de ~/template/ (core_task 'template-sync').

Ejecutado por heartbeat.py como script_path de la fila que midnight.py
materializa en agent_inbox a partir de core_task. Sigue el contrato
obligatorio de los scripts de `task`: imprime SIEMPRE un único JSON
{"ok": bool, "notify": null|{"severity","message","context"}} en stdout,
pase lo que pase. El exit code no se usa para nada.

Alcance de esta fase (fase 1, solo lectura): status -> fetch -> merge
--ff-only contra origin/main. Nunca merge/rebase/reset automático más allá
de un fast-forward limpio. El push (instancia -> template) queda para
fase 2.

Decisiones de diseño (ver sesión de diseño, revisión de Opus):
- Lock no bloqueante fuera del working tree: si está tomado (alguien
  generalizando a mano en ~/template ahora mismo), sale en silencio.
- Si ~/template está sucio (`git status --porcelain`), no toca nada.
  Notifica UNA VEZ, no cada día — el estado "ya notificado" se persiste
  aparte para no repetir el aviso mientras el problema siga vivo.
- Si `git fetch` falla, cuenta fallos consecutivos: silencio los primeros
  días, escala aviso a partir de FAILURE_SILENCE_THRESHOLD (nunca "todo
  ok" indefinido con la red caída).
- La comparación es contra el último SHA *notificado* (persistido aparte),
  no contra el HEAD local del clon — así un día ignorado no se confunde
  con "adoptado", y el puntero sobrevive a un crash a mitad de ejecución.
- Si el fast-forward falla (divergencia), se trata igual que el árbol
  sucio: revisar a mano, notificar una vez.
- El `git log --oneline` se pasa crudo en el contexto; nada de resumen
  del modelo en este camino barato (heartbeat ya decide si hace falta
  que el modelo redacte el mensaje final, ver notify.context).

Script standalone, stdlib only.
"""
import fcntl
import json
import os
import subprocess
import sys
from datetime import datetime

AGENT_HOME = '/home/<agent>'
TEMPLATE_DIR = f'{AGENT_HOME}/template'
STATE_DIR = f'{AGENT_HOME}/workspace/state'
STATE_FILE = f'{STATE_DIR}/template-sync.json'
LOCK_FILE = f'{STATE_DIR}/template-sync.lock'
LOG = '/home/<agent>/logs/<agent>-template-sync.log'

BRANCH = 'main'
GIT_TIMEOUT_SECONDS = 20
FAILURE_SILENCE_THRESHOLD = 3  # días consecutivos de fetch fallido antes de escalar

DEFAULT_STATE = {
    'last_notified_sha': None,
    'consecutive_fetch_failures': 0,
    'needs_manual_review': False,
    'manual_review_reason': None,
}


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


# --------------------------------------------------------------------------
# Estado persistido (aparte del HEAD del clon y fuera del working tree)
# --------------------------------------------------------------------------
def load_state() -> dict:
    try:
        with open(STATE_FILE) as f:
            data = json.load(f)
        state = dict(DEFAULT_STATE)
        state.update(data)
        return state
    except FileNotFoundError:
        return dict(DEFAULT_STATE)
    except Exception as e:
        log(f"[WARN] estado corrupto, se reinicia: {e}")
        return dict(DEFAULT_STATE)


def save_state(state: dict) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp_path = f"{STATE_FILE}.tmp"
    with open(tmp_path, 'w') as f:
        json.dump(state, f, indent=2)
    os.replace(tmp_path, STATE_FILE)


# --------------------------------------------------------------------------
# Lock no bloqueante
# --------------------------------------------------------------------------
def acquire_lock():
    os.makedirs(STATE_DIR, exist_ok=True)
    fd = open(LOCK_FILE, 'w')
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fd.close()
        return None
    return fd


# --------------------------------------------------------------------------
# Git
# --------------------------------------------------------------------------
def run_git(*args) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env['GIT_TERMINAL_PROMPT'] = '0'
    return subprocess.run(
        ['git', '-C', TEMPLATE_DIR, *args],
        capture_output=True, text=True, timeout=GIT_TIMEOUT_SECONDS, env=env,
    )


def git_status_porcelain() -> tuple:
    """(dirty: bool, error: str|None)."""
    try:
        result = run_git('status', '--porcelain')
    except (subprocess.TimeoutExpired, OSError) as e:
        return True, str(e)
    if result.returncode != 0:
        return True, result.stderr.strip()
    return bool(result.stdout.strip()), None


def git_fetch() -> tuple:
    """(success: bool, error: str|None)."""
    try:
        result = run_git('fetch', 'origin', BRANCH)
    except (subprocess.TimeoutExpired, OSError) as e:
        return False, str(e)
    if result.returncode != 0:
        return False, result.stderr.strip()
    return True, None


def git_rev_parse(ref: str) -> str | None:
    try:
        result = run_git('rev-parse', ref)
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def git_merge_ff_only() -> tuple:
    """(success: bool, error: str|None)."""
    try:
        result = run_git('merge', '--ff-only', f'origin/{BRANCH}')
    except (subprocess.TimeoutExpired, OSError) as e:
        return False, str(e)
    if result.returncode != 0:
        return False, (result.stderr.strip() or result.stdout.strip())
    return True, None


def git_log_range(old_sha: str, new_sha: str) -> str:
    try:
        result = run_git('log', '--oneline', f'{old_sha}..{new_sha}')
    except (subprocess.TimeoutExpired, OSError) as e:
        return f"(no se pudo leer git log: {e})"
    if result.returncode != 0:
        return f"(git log falló: {result.stderr.strip()})"
    return result.stdout.strip()


# --------------------------------------------------------------------------
def main() -> dict:
    if not os.path.isdir(os.path.join(TEMPLATE_DIR, '.git')):
        return fail_result('high', f"{TEMPLATE_DIR} no existe o no es un repo git — retrofit no se ejecutó")

    lock_fd = acquire_lock()
    if lock_fd is None:
        log("[SKIP] lock tomado, alguien está trabajando en ~/template ahora mismo")
        return ok_result(None)

    try:
        state = load_state()
        state_dirty = False

        # 1. Árbol sucio -> no tocar nada.
        dirty, status_error = git_status_porcelain()
        if status_error:
            log(f"[ERROR] git status: {status_error}")
            return fail_result('high', f"git status falló en {TEMPLATE_DIR}: {status_error}")

        if dirty:
            if state['needs_manual_review'] and state['manual_review_reason'] == 'dirty':
                log("[SKIP] árbol sucio, ya notificado")
                return ok_result(None)
            state['needs_manual_review'] = True
            state['manual_review_reason'] = 'dirty'
            save_state(state)
            log("[NOTIFY] árbol sucio en ~/template")
            return ok_result({
                'severity': 'medium', 'message': None,
                'context': f"~/template tiene cambios sin commitear. No se toca nada hasta que se revise a mano.",
            })

        if state['needs_manual_review'] and state['manual_review_reason'] == 'dirty':
            state['needs_manual_review'] = False
            state['manual_review_reason'] = None
            state_dirty = True
            log("[OK] árbol ya no está sucio, se limpia el aviso pendiente")

        # 2. Fetch, con escalado de fallos consecutivos.
        fetched, fetch_error = git_fetch()
        if not fetched:
            state['consecutive_fetch_failures'] += 1
            save_state(state)
            log(f"[WARN] fetch falló ({state['consecutive_fetch_failures']} consecutivos): {fetch_error}")
            if state['consecutive_fetch_failures'] < FAILURE_SILENCE_THRESHOLD:
                return ok_result(None)
            return fail_result(
                'high',
                f"git fetch lleva fallando {state['consecutive_fetch_failures']} días consecutivos. "
                f"Último error: {fetch_error}",
            )

        if state['consecutive_fetch_failures'] > 0:
            log(f"[OK] fetch se recupera tras {state['consecutive_fetch_failures']} fallo(s)")
            state['consecutive_fetch_failures'] = 0
            state_dirty = True

        # 3. Comparar origin/main contra el último SHA notificado (no el HEAD local).
        origin_sha = git_rev_parse(f'origin/{BRANCH}')
        if not origin_sha:
            if state_dirty:
                save_state(state)
            return fail_result('high', f"no se pudo resolver origin/{BRANCH} tras el fetch")

        if state['last_notified_sha'] is None:
            # Primera vez que corre: fija la base sin notificar (no hay "novedad" real).
            state['last_notified_sha'] = origin_sha
            save_state(state)
            log(f"[OK] baseline fijada en {origin_sha}")
            return ok_result(None)

        if origin_sha == state['last_notified_sha']:
            if state_dirty:
                save_state(state)
            log("[OK] sin cambios respecto al último SHA notificado")
            return ok_result(None)

        # 4. Hay diferencia -> fast-forward, nunca merge/rebase/reset.
        merged, merge_error = git_merge_ff_only()
        if not merged:
            if state['needs_manual_review'] and state['manual_review_reason'] == 'ff_failed':
                log("[SKIP] fast-forward falló, ya notificado")
                return ok_result(None)
            state['needs_manual_review'] = True
            state['manual_review_reason'] = 'ff_failed'
            save_state(state)
            log(f"[NOTIFY] fast-forward falló: {merge_error}")
            return ok_result({
                'severity': 'medium', 'message': None,
                'context': f"origin/{BRANCH} divergió de ~/template y el fast-forward falló. "
                           f"Revisar a mano. Detalle: {merge_error}",
            })

        if state['needs_manual_review'] and state['manual_review_reason'] == 'ff_failed':
            state['needs_manual_review'] = False
            state['manual_review_reason'] = None
            log("[OK] fast-forward recuperado, se limpia el aviso pendiente")

        old_sha = state['last_notified_sha']
        log_output = git_log_range(old_sha, origin_sha)
        state['last_notified_sha'] = origin_sha
        save_state(state)
        log(f"[NOTIFY] nuevo contenido en template, {old_sha}..{origin_sha}")
        return ok_result({
            'severity': 'low', 'message': None,
            'context': log_output or f"fast-forward a {origin_sha}",
        })
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        except Exception:
            pass
        lock_fd.close()


if __name__ == '__main__':
    try:
        outcome = main()
    except Exception as e:
        log(f"[ERROR] excepción no controlada: {e}")
        outcome = fail_result('high', f"excepción no controlada en template_sync.py: {e}")
    print(json.dumps(outcome))
    sys.exit(0)

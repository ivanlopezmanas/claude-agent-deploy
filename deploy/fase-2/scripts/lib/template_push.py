#!/usr/bin/env python3
# chmod +x /home/<agent>/workspace/scripts/lib/template_push.py
"""template_push.py — orquestador de push (fase 2): instancia -> template.

A diferencia de template_sync.py (que corre solo, vía heartbeat, sin
intervención humana), este script NUNCA se invoca sin que Iván haya
confirmado antes por chat el conjunto exacto de ficheros a propagar. La
confirmación vive en la conversación con el modelo, no en este script.

Dos subcomandos, pensados para ejecutarse como pasos separados con la
confirmación de Iván en medio (los invoca la skill propagate-to-template):

  template_push.py preview <ruta> [<ruta> ...]
      Clasifica cada ruta contra propagation-manifest.json, reversa
      (template_reverse.py) y pasa el guard anti-fuga (template_guard.py,
      pasada 1), SIN escribir nada ni tocar git. Imprime un único JSON con
      el resultado de cada ruta -- diff propuesto, o motivo de rechazo --
      para que el modelo se lo enseñe a Iván y pida confirmación.

  template_push.py apply <ruta> [<ruta> ...] [--pr|--direct]
      Repite clasificación + reversa + guard (no se fía de una preview que
      corrió en otro proceso) y, si todo pasa: toma el lock compartido con
      template_sync.py, escribe en ~/template/, corre el guard otra vez
      sobre el diff en staging (pasada 2), commitea, y empuja -- por
      defecto a una rama + PR en borrador (nunca push directo a main sin
      --direct explícito). Solo debe llamarse tras la confirmación
      explícita de Iván.

Autenticación: usa GITHUB_TOKEN del entorno (mismo secreto que el flujo
manual documentado en la sección 2 de tareas-pendientes.md). Nunca se
persiste en `.git/config` -- se construye la URL autenticada en el momento
de cada `git push` y se descarta. La creación del PR usa la API REST de
GitHub por curl (no depende de tener `gh` instalado en el LXC). Si
GITHUB_TOKEN no está configurado, `apply` falla con un mensaje claro en
vez de intentarlo a ciegas -- ver el aviso en el mensaje de fase 2: hoy
ningún agente desplegado tiene GITHUB_TOKEN provisionado por defecto, es
un prerrequisito pendiente de resolver (M3 de la revisión de Opus: la
auth es aparcable para pull, pero es justo la pieza para push).

Script standalone, stdlib only.
"""
import json
import os
import subprocess
import sys
from datetime import datetime

import template_guard as guard
import template_reverse as reverse
from template_sync import (
    AGENT_HOME, TEMPLATE_DIR, GIT_TIMEOUT_SECONDS,
    acquire_lock, load_state, save_state, run_git, git_status_porcelain,
)

MANIFEST_PATH = f"{TEMPLATE_DIR}/propagation-manifest.json"
IDENTITY_PATH = f"{AGENT_HOME}/workspace/state/instance-identity.json"
LOG = f"{AGENT_HOME}/logs/<agent>-template-push.log"
REPO_OWNER = "ivanlopezmanas"
REPO_NAME = "claude-agent-deploy"


def log(msg: str) -> None:
    try:
        with open(LOG, 'a') as f:
            f.write(f"[{datetime.now().isoformat()}] {msg}\n")
    except Exception:
        pass


class RejectedPath(Exception):
    def __init__(self, path, reason):
        self.path = path
        self.reason = reason
        super().__init__(f"{path}: {reason}")


# --------------------------------------------------------------------------
# Manifiesto / clasificación
# --------------------------------------------------------------------------
def load_manifest(path: str = None) -> dict:
    # Sin valor por defecto vinculado en tiempo de definición: MANIFEST_PATH
    # se relee en cada llamada, para que un cambio en el nombre del módulo
    # (tests, u otra instancia con otro layout) se respete.
    if path is None:
        path = MANIFEST_PATH
    with open(path) as f:
        return json.load(f)


def relative_to_home(path: str) -> str:
    """Ruta candidata relativa a $HOME, como la usa el manifiesto.

    Una ruta ya-relativa se devuelve tal cual (normalizada) -- NUNCA se
    resuelve contra el directorio de trabajo actual, que podría coincidir
    por casualidad con estar bajo $HOME (p.ej. corriendo desde
    workspace/tests/) y dar un resultado incorrecto. Solo las rutas
    absolutas se recortan quitándoles el prefijo de $HOME."""
    if not os.path.isabs(path) and not path.startswith("~"):
        return os.path.normpath(path)
    abs_path = os.path.abspath(os.path.expanduser(path))
    home = os.path.abspath(AGENT_HOME)
    if abs_path.startswith(home + os.sep):
        return abs_path[len(home) + 1:]
    return abs_path.lstrip("/")


def _resolve_match_pattern(match: str, identity: dict) -> str:
    """El manifiesto vive en el repo en forma agnóstica (p.ej.
    "etc/<agent>/secrets.env"). Antes de comparar contra una ruta real hay
    que resolver sus placeholders con la identidad de ESTA instancia --
    si no, una regla como esa nunca haría match contra nada y la entrada
    'never' correspondiente no protegería nada."""
    for value, placeholder in reverse.build_substitution_pairs(identity):
        match = match.replace(placeholder, value)
    return match


def classify(candidate_path: str, manifest: dict, identity: dict) -> dict:
    """{classification, repo_path|repo_dir, match} de la primera regla que
    matchea (el manifiesto ya está ordenado de más a menos específico), o
    {classification: 'uncovered'} si ninguna aplica."""
    rel = relative_to_home(candidate_path)
    for rule in manifest["rules"]:
        match = _resolve_match_pattern(rule["match"], identity)
        if match.endswith("/"):
            if rel.startswith(match):
                return dict(rule)
        elif rel == match:
            return dict(rule)
    return {"classification": "uncovered", "match": rel}


def resolve_repo_destination(candidate_path: str, rule: dict, identity: dict) -> str:
    """Ruta destino relativa a la raíz del repo (== ~/template/)."""
    if "repo_path" in rule:
        return rule["repo_path"]
    basename = reverse.reverse_filename(os.path.basename(candidate_path), identity)
    return rule["repo_dir"].rstrip("/") + "/" + basename


# --------------------------------------------------------------------------
# Construcción de la propuesta (reverse + guard pasada 1) -- sin escribir nada
# --------------------------------------------------------------------------
def build_proposal(candidate_path: str, manifest: dict, identity: dict) -> dict:
    """Clasifica y, si aplica, reversa + guard(1). No escribe nada ni toca
    git. Lanza RejectedPath si la ruta es 'never' o no está cubierta --
    eso nunca se resuelve a ciegas, hace falta intervención humana."""
    rule = classify(candidate_path, manifest, identity)
    classification = rule["classification"]

    if classification == "never":
        raise RejectedPath(candidate_path, "en la lista 'never' del manifiesto -- nunca se propaga")
    if classification == "uncovered":
        raise RejectedPath(candidate_path, "no está en propagation-manifest.json -- añádelo antes de propagar")
    if not os.path.isfile(candidate_path):
        raise RejectedPath(candidate_path, "no existe o no es un fichero regular")

    with open(candidate_path, encoding="utf-8") as f:
        production_text = f.read()

    repo_dest = resolve_repo_destination(candidate_path, rule, identity)
    template_file = f"{TEMPLATE_DIR}/{repo_dest}"

    if classification == "propagable":
        new_text = reverse.reverse_content(production_text, identity)
        guard.check(new_text, identity)
        old_text = ""
        if os.path.isfile(template_file):
            with open(template_file, encoding="utf-8") as f:
                old_text = f.read()
        return {
            "path": candidate_path, "repo_dest": repo_dest, "classification": classification,
            "new_content": new_text, "old_content": old_text, "changed": new_text != old_text,
        }

    if classification == "mixed":
        if not reverse.has_marked_sections(production_text):
            raise RejectedPath(candidate_path, "fichero mixto sin marcadores TEMPLATE:BEGIN/END -- nada que propagar")
        if not os.path.isfile(template_file):
            raise RejectedPath(candidate_path, f"no existe copia previa en template/{repo_dest} para fusionar los marcadores -- crearla a mano la primera vez")
        reversed_sections = reverse.reverse_marked_sections(production_text, identity)
        for section in reversed_sections:
            guard.check(section, identity)
        with open(template_file, encoding="utf-8") as f:
            old_text = f.read()
        new_text = reverse.splice_marked_sections(old_text, reversed_sections)
        return {
            "path": candidate_path, "repo_dest": repo_dest, "classification": classification,
            "new_content": new_text, "old_content": old_text, "changed": new_text != old_text,
        }

    raise RejectedPath(candidate_path, f"clasificación desconocida: {classification!r}")


# --------------------------------------------------------------------------
# preview
# --------------------------------------------------------------------------
def cmd_preview(paths: list) -> dict:
    manifest = load_manifest()
    identity = reverse.load_identity(IDENTITY_PATH)
    results = []
    for path in paths:
        try:
            proposal = build_proposal(path, manifest, identity)
            results.append({"path": path, "ok": True, **proposal})
        except RejectedPath as e:
            results.append({"path": path, "ok": False, "reason": e.reason})
        except guard.LeakFound as e:
            results.append({"path": path, "ok": False, "reason": f"guard anti-fuga: {'; '.join(e.findings)}"})
    return {"results": results}


# --------------------------------------------------------------------------
# apply
# --------------------------------------------------------------------------
def _slugify(paths: list) -> str:
    base = os.path.basename(paths[0]).rsplit(".", 1)[0] if paths else "cambio"
    slug = "".join(c if c.isalnum() or c in "-_" else "-" for c in base).strip("-").lower()
    return slug or "cambio"


def _abort_branch(branch):
    run_git("reset", "--hard", "HEAD")
    if branch:
        run_git("checkout", "main")
        run_git("branch", "-D", branch)


def _push_branch(branch: str, token: str) -> subprocess.CompletedProcess:
    url = f"https://{token}@github.com/{REPO_OWNER}/{REPO_NAME}.git"
    return subprocess.run(
        ["git", "-C", TEMPLATE_DIR, "push", url, f"{branch}:{branch}"],
        capture_output=True, text=True, timeout=GIT_TIMEOUT_SECONDS,
    )


def _push_main_direct(token: str) -> subprocess.CompletedProcess:
    url = f"https://{token}@github.com/{REPO_OWNER}/{REPO_NAME}.git"
    return subprocess.run(
        ["git", "-C", TEMPLATE_DIR, "push", url, "HEAD:main"],
        capture_output=True, text=True, timeout=GIT_TIMEOUT_SECONDS,
    )


def _create_pr(branch: str, title: str, token: str) -> dict:
    """PR en borrador vía API REST de GitHub (curl, sin depender de `gh`).
    Best-effort: si falla, se devuelve sin url y con la url de comparación
    manual como fallback -- el push de la rama ya es válido igualmente."""
    compare_url = f"https://github.com/{REPO_OWNER}/{REPO_NAME}/compare/main...{branch}?expand=1"
    payload = json.dumps({
        "title": title, "head": branch, "base": "main", "draft": True,
        "body": "Propuesto por template_push.py, revisado por Iván antes de confirmar el apply.",
    })
    try:
        result = subprocess.run(
            ["curl", "-s", "-X", "POST",
             "-H", f"Authorization: Bearer {token}",
             "-H", "Accept: application/vnd.github+json",
             f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/pulls",
             "-d", payload],
            capture_output=True, text=True, timeout=30,
        )
        data = json.loads(result.stdout) if result.stdout else {}
        if isinstance(data, dict) and data.get("html_url"):
            return {"pr_url": data["html_url"], "compare_url": compare_url}
    except Exception as e:
        log(f"[WARN] creación de PR vía API falló: {e}")
    return {"pr_url": None, "compare_url": compare_url}


def cmd_apply(paths: list, use_pr: bool = True) -> dict:
    manifest = load_manifest()
    identity = reverse.load_identity(IDENTITY_PATH)

    proposals = [p for p in (build_proposal(path, manifest, identity) for path in paths) if p["changed"]]
    if not proposals:
        return {"ok": True, "pushed": False, "reason": "nada que propagar (sin cambios respecto al template actual)"}

    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        return {"ok": False, "pushed": False,
                "reason": "GITHUB_TOKEN no está configurado en el entorno -- prerrequisito pendiente para fase 2, no se puede empujar"}

    lock_fd = acquire_lock()
    if lock_fd is None:
        return {"ok": False, "pushed": False, "reason": "lock de template ocupado -- reintenta en un momento"}

    branch = None
    try:
        dirty, status_error = git_status_porcelain()
        if dirty or status_error:
            return {"ok": False, "pushed": False,
                    "reason": f"~/template ya está sucio antes de empezar -- revisar a mano ({status_error or 'cambios sin commitear'})"}

        if use_pr:
            branch = f"propagate-{_slugify(paths)}-{datetime.now().strftime('%Y%m%d%H%M%S')}"
            result = run_git("checkout", "-b", branch)
            if result.returncode != 0:
                return {"ok": False, "pushed": False, "reason": f"no se pudo crear la rama: {result.stderr.strip()}"}

        for proposal in proposals:
            dest = f"{TEMPLATE_DIR}/{proposal['repo_dest']}"
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "w", encoding="utf-8") as f:
                f.write(proposal["new_content"])

        add_result = run_git("add", *[p["repo_dest"] for p in proposals])
        if add_result.returncode != 0:
            _abort_branch(branch)
            return {"ok": False, "pushed": False, "reason": f"git add falló: {add_result.stderr.strip()}"}

        diff_result = run_git("diff", "--staged")
        try:
            guard.check(diff_result.stdout, identity)  # guard, pasada 2
        except guard.LeakFound as e:
            _abort_branch(branch)
            return {"ok": False, "pushed": False, "reason": f"guard anti-fuga (pasada 2, diff staged): {'; '.join(e.findings)}"}

        commit_message = "propagate: " + ", ".join(p["repo_dest"] for p in proposals)
        commit_result = run_git("commit", "-m", commit_message)
        if commit_result.returncode != 0:
            _abort_branch(branch)
            return {"ok": False, "pushed": False, "reason": f"git commit falló: {commit_result.stderr.strip()}"}

        new_sha = run_git("rev-parse", "HEAD").stdout.strip()

        if use_pr:
            push_result = _push_branch(branch, token)
        else:
            push_result = _push_main_direct(token)
        if push_result.returncode != 0:
            log(f"[ERROR] push falló tras commitear {new_sha}: {push_result.stderr.strip()}")
            return {"ok": False, "pushed": False, "commit": new_sha,
                    "reason": f"git push falló (el commit local {new_sha} existe, revisar a mano): {push_result.stderr.strip()}"}

        response = {"ok": True, "pushed": True, "commit": new_sha, "branch": branch,
                    "files": [p["repo_dest"] for p in proposals]}

        if use_pr:
            pr_info = _create_pr(branch, commit_message, token)
            response.update(pr_info)
            run_git("checkout", "main")
        else:
            # Push directo a main: avanzar last_notified_sha bajo el mismo
            # lock, para que el pull diario no se autonotifique mañana
            # sobre este mismo commit. Con PR esto no hace falta -- el
            # merge llegará más tarde y el pull lo tratará como cualquier
            # otro cambio de origen.
            state = load_state()
            state["last_notified_sha"] = new_sha
            save_state(state)

        log(f"[OK] propagado {response['files']} -> commit {new_sha}" + (f" (rama {branch})" if branch else " (main directo)"))
        return response
    finally:
        try:
            import fcntl
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        except Exception:
            pass
        lock_fd.close()


# --------------------------------------------------------------------------
def main() -> int:
    if len(sys.argv) < 3:
        print(json.dumps({"ok": False, "reason": "uso: template_push.py preview|apply <ruta> [<ruta> ...] [--pr|--direct]"}))
        return 1

    subcommand = sys.argv[1]
    args = sys.argv[2:]
    use_pr = True
    if "--direct" in args:
        use_pr = False
        args = [a for a in args if a != "--direct"]
    if "--pr" in args:
        args = [a for a in args if a != "--pr"]
    paths = args

    if subcommand == "preview":
        print(json.dumps(cmd_preview(paths)))
        return 0
    if subcommand == "apply":
        print(json.dumps(cmd_apply(paths, use_pr=use_pr)))
        return 0

    print(json.dumps({"ok": False, "reason": f"subcomando desconocido: {subcommand!r}"}))
    return 1


if __name__ == "__main__":
    sys.exit(main())

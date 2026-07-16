#!/usr/bin/env python3
"""common.py — utilidades compartidas por todos los hooks del agente.

Reglas de diseño:
- Sin dependencias externas (stdlib only) en el camino crítico.
- Las funciones de salida (allow/block/ask/review) terminan el proceso (sys.exit).
- Fallo de logging nunca propaga: el logging no debe tumbar un guardrail.
"""
import os
import sys
import json
import time
import datetime
import subprocess
from pathlib import Path

# ---------------------------------------------------------------- Constantes
def _tmp(name: str) -> Path:
    """Ruta de fichero transitorio. Respeta <AGENT>_TMP_OVERRIDE para aislar tests."""
    base = os.environ.get("<AGENT>_TMP_OVERRIDE", "/tmp")
    return Path(base) / name

LOG_PATH            = Path("/home/<agent>/logs/<agent>-permissions.log")
WORKSPACE_TABLE     = Path("/home/<agent>/workspace/scripts/lib/workspace.json")
SETTINGS_BACKGROUND = Path("/home/<agent>/claude/.claude/settings-background.json")
TELEGRAM_TURN_FLAG  = _tmp("<agent>-telegram-turn")
REWAKE_COUNTER      = _tmp("<agent>-stop-rewake-counter")
TICKER_STATE        = _tmp("<agent>-ticker-state.json")
APPROVAL_PENDING    = _tmp("<agent>-approval-pending")

PACKAGE_MANAGERS = ("apt install", "apt-get install", "pip install", "pip3 install",
                    "npm install -g", "npm i -g", "snap install", "cargo install",
                    "brew install", "pipx install", "gem install")

DANGEROUS_PIPES = ("curl", "wget")          # combinados con | sh/bash
REMOTE_EXEC_SIG = ("| bash", "| sh", "|bash", "|sh", "base64 -d", "base64 --decode",
                   "nc -e", "ncat -e", "ssh", "-R")

MEMORY_PATHS = ("/home/<agent>/claude/.claude/projects/-home-<agent>-claude/memory",
                "/.claude/projects/-home-<agent>-claude/memory")

COSTLY_MODELS = ("opus",)                    # modelos que exigen permiso

# Pesos del modelo de riesgo (§4.7)
BASE_RISK = {
    "Read": 0.0, "Glob": 0.0, "Grep": 0.0,
    "Bash": 0.3, "Write": 0.4, "Edit": 0.4, "NotebookEdit": 0.4,
    "mcp__postgres__query_data": 0.0,
    "mcp__postgres__count_rows": 0.0,
    "mcp__postgres__insert_data": 0.4,
    "mcp__postgres__update_data": 0.5,
    "mcp__postgres__delete_data": 0.8,
    "mcp__postgres__execute_raw_query": 0.6,
    "mcp__postgres__alter_table": 0.7,
    "mcp__postgres__create_table": 0.5,
}

# ---------------------------------------------------------------- Contexto
def context() -> str:
    """Contexto de ejecución: main | subagent | background | cron.

    Prioridad: <AGENT>_CONTEXT (señal explícita nuestra). Si no está presente,
    se usa CLAUDE_CODE_ENTRYPOINT como red de seguridad: 'sdk-cli' indica un
    subagente interno lanzado con Agent(...) (§1.6), por lo que se trata como
    'subagent' aunque <AGENT>_CONTEXT no se haya propagado. Solo si ninguna
    señal está presente se asume 'main'.
    """
    explicit = os.environ.get("<AGENT>_CONTEXT")
    if explicit:
        return explicit
    if os.environ.get("CLAUDE_CODE_ENTRYPOINT") == "sdk-cli":
        return "subagent"
    return "main"

def is_main_context() -> bool:
    return context() == "main"

# ---------------------------------------------------------------- Sub-agentes aislados (§7.2)
def call_isolated_agent(prompt: str, *, agent: str = None, model: str = None,
                         mcp_config: str = None, allowed_tools: list = None,
                         max_turns: int = None, output_format: str = None,
                         timeout: int = 60) -> str:
    """Lanza `claude --print` completamente aislado del canal principal.

    Único punto de entrada permitido para invocar Claude desde un hook o script
    (regla inviolable del kernel). Combina los tres mecanismos de §7.2:
    --strict-mcp-config (cierra todos los MCP del usuario, incluido Telegram),
    --settings settings-background.json (sin hooks Session*/Notification/
    PostToolUse — no hay bucle posible aunque el sub-proceso abra su propia
    sesión) y <AGENT>_CONTEXT=subagent como señal explícita adicional.

    - agent: nombre del agente configurado a usar (trae su propio modelo/
      allowlist/prompt, ej. "the-chronicler").
    - model: fuerza un modelo cuando no hay agente dedicado (ej. saludo suelto
      con Haiku).
    - mcp_config: MCP adicional sobre el aislamiento base (ej. postgres-only
      para que the-chronicler pueda escribir memorias).
    - allowed_tools: restringe/amplía herramientas para esta llamada concreta,
      sin tocar el .md del agente.
    - max_turns: tope de turnos/tool calls del propio agente — defensa extra
      contra un bucle de tool-calls, además del timeout (que corta por tiempo,
      no por número de pasos).
    - output_format: "text" (default) | "json" | "stream-json".

    FAIL-OPEN: cualquier fallo (timeout, exit != 0, excepción) devuelve None,
    nunca propaga. El caller decide qué hacer con un resultado vacío.
    """
    cmd = ["claude", "--print", "--strict-mcp-config", "--settings", str(SETTINGS_BACKGROUND)]
    if agent:
        cmd += ["--agent", agent]
    if model:
        cmd += ["--model", model]
    if mcp_config:
        cmd += ["--mcp-config", mcp_config]
    if allowed_tools:
        cmd += ["--allowed-tools", ",".join(allowed_tools)]
    if max_turns:
        cmd += ["--max-turns", str(max_turns)]
    if output_format:
        cmd += ["--output-format", output_format]
    env = dict(os.environ)
    env["<AGENT>_CONTEXT"] = "subagent"
    try:
        result = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                                 timeout=timeout, env=env)
        if result.returncode != 0:
            return None
        return result.stdout.strip()
    except Exception:
        return None

# ---------------------------------------------------------------- I/O hook
def read_hook_input() -> dict:
    """Lee y parsea el JSON que el harness envía por stdin."""
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    return json.loads(raw)

# ---------------------------------------------------------------- Logging
def log_permission(tool: str, decision: str, reason: str = "") -> None:
    """Append-only log de decisiones. Nunca propaga excepciones."""
    try:
        entry = {
            "ts": datetime.datetime.utcnow().isoformat() + "Z",
            "tool": tool,
            "decision": decision,
            "reason": reason,
            "context": context(),
            "session": os.environ.get("CLAUDE_SESSION_ID", "unknown"),
        }
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass

def log_event(data: dict) -> None:
    """Telemetría de PostToolUse (feedback). Nunca propaga."""
    try:
        log_permission(data.get("tool_name", "?"), "executed", "")
    except Exception:
        pass

# ---------------------------------------------------------------- Salidas PreToolUse
def _emit_pretool(decision: str, reason: str = "") -> None:
    out = {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                  "permissionDecision": decision}}
    if reason:
        out["hookSpecificOutput"]["permissionDecisionReason"] = reason
    print(json.dumps(out, ensure_ascii=False))

def allow(tool: str = "") -> None:
    log_permission(tool, "allow")
    _emit_pretool("allow")
    sys.exit(0)

def review(tool: str = "", score: float = 0.0) -> None:
    """Ejecuta pero registra y (en main) avisa al feed. No bloquea."""
    log_permission(tool, "review", f"score={score:.2f}")
    if is_main_context():
        _signal_feed(tool, "review")
    _emit_pretool("allow")
    sys.exit(0)

def ask(reason: str, tool: str = "") -> None:
    """RequireConfirmation: bloquea pidiendo OK. No llama a Telegram (D7)."""
    log_permission(tool, "ask", reason)
    try:
        APPROVAL_PENDING.write_text(json.dumps({"tool": tool, "reason": reason, "ts": time.time()}))
    except Exception:
        pass
    _emit_pretool("ask", reason)
    sys.exit(0)

def block(reason: str, tool: str = "") -> None:
    log_permission(tool, "block", reason)
    _emit_pretool("deny", reason)
    sys.exit(0)

# ---------------------------------------------------------------- Salidas UserPromptSubmit
def inject_context(text: str) -> None:
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                             "additionalContext": text}}, ensure_ascii=False))
    sys.exit(0)

# ---------------------------------------------------------------- Feed/ticker
def _signal_feed(tool: str, kind: str) -> None:
    try:
        state = json.loads(TICKER_STATE.read_text()) if TICKER_STATE.exists() else {"actions": 0}
        state["last_tool"] = tool
        state["actions"] = state.get("actions", 0) + 1
        state["kind"] = kind
        state["ts"] = time.time()
        TICKER_STATE.write_text(json.dumps(state, ensure_ascii=False))
    except Exception:
        pass

def update_ticker_state(tool: str, result=None) -> None:
    _signal_feed(tool, "done")

# ---------------------------------------------------------------- Tabla de rutas
_workspace_cache = None
def _load_workspace() -> list:
    global _workspace_cache
    if _workspace_cache is None:
        try:
            _workspace_cache = json.loads(WORKSPACE_TABLE.read_text())["paths"]
        except Exception:
            _workspace_cache = []
    return _workspace_cache

def normalize_path(p: str) -> str:
    if not p:
        return ""
    p = os.path.expanduser(p)
    if not os.path.isabs(p):
        p = os.path.abspath(p)
    return os.path.normpath(p)

def lookup_tier(path: str) -> str:
    """Devuelve el tier de la ruta: T1|T2|T3|never. Más específico gana."""
    np = normalize_path(path)
    best, best_len = "T1", -1     # default: lectura/escritura libre salvo match
    for entry in _load_workspace():
        prefix = normalize_path(entry["path"])
        if np == prefix or np.startswith(prefix.rstrip("/") + "/"):
            if len(prefix) > best_len:
                best, best_len = entry["tier"], len(prefix)
    return best

# ---------------------------------------------------------------- Extracción de ruta/args
def extract_path(tool: str, args: dict) -> str:
    if tool in ("Write", "Edit", "NotebookEdit", "Read"):
        return args.get("file_path") or args.get("path") or ""
    if tool == "Bash":
        # heurística: primera ruta absoluta o ~ del comando
        cmd = args.get("command", "")
        for tok in cmd.split():
            if tok.startswith("/") or tok.startswith("~"):
                return tok
    return ""

# ---------------------------------------------------------------- Detectores de reglas inviolables
def is_memory_path(path: str) -> bool:
    np = normalize_path(path)
    return any(m in np for m in MEMORY_PATHS)

def is_package_manager(tool: str, args: dict) -> bool:
    if tool != "Bash":
        return False
    cmd = args.get("command", "")
    return any(pm in cmd for pm in PACKAGE_MANAGERS)

def is_dangerous_pipe(tool: str, args: dict) -> bool:
    if tool != "Bash":
        return False
    cmd = args.get("command", "")
    if any(d in cmd for d in DANGEROUS_PIPES) and ("| sh" in cmd or "| bash" in cmd
                                                   or "|sh" in cmd or "|bash" in cmd):
        return True
    if "base64 -d" in cmd and ("| bash" in cmd or "| sh" in cmd):
        return True
    if "nc -e" in cmd or "ncat -e" in cmd:
        return True
    if "ssh" in cmd and " -R " in cmd:
        return True
    return False

def is_costly_agent(tool: str, args: dict) -> bool:
    # tool_name "Agent" verificado en logs de hook de producción (PreToolUse recibe "Agent", no "Task")
    if tool != "Agent":
        return False
    model = str(args.get("model", "")).lower()
    if any(m in model for m in COSTLY_MODELS):
        return True
    thinking = args.get("thinking", {})
    if isinstance(thinking, dict) and thinking.get("type") == "enabled":
        return True
    return False

# ---------------------------------------------------------------- Blast radius / irreversibilidad
def estimate_blast_radius(tool: str, args: dict) -> float:
    """0.0–1.0. Heurística sin ejecutar nada."""
    if tool == "Bash":
        cmd = args.get("command", "")
        if " -r" in cmd or " -R" in cmd or "--recursive" in cmd or "/*" in cmd:
            return 0.8
        if "rm " in cmd or "mv " in cmd:
            return 0.5
        return 0.2
    if tool == "mcp__postgres__delete_data":
        return 0.9 if "where" not in json.dumps(args).lower() else 0.4
    if tool == "mcp__postgres__update_data":
        return 0.7 if "where" not in json.dumps(args).lower() else 0.3
    if tool in ("Write", "Edit"):
        return 0.3
    return 0.1

def is_irreversible(tool: str, args: dict) -> bool:
    if tool == "Bash":
        cmd = args.get("command", "")
        return any(x in cmd for x in ("rm ", "dd ", "mkfs", "git push", "truncate"))
    if tool in ("mcp__postgres__delete_data", "mcp__postgres__alter_table"):
        return True
    return False

# ---------------------------------------------------------------- Sensibilidad de ruta
def path_sensitivity(path: str) -> float:
    np = normalize_path(path)
    if any(s in np for s in (".ssh", ".env", "secret", ".pem", ".key")):
        return 1.0
    if np.startswith("/dev/"):
        return 1.0
    if any(s in np for s in ("/etc/", "/usr/", "settings.json", "workspace")):
        return 0.8
    if np.startswith("/tmp/") or "/home/<agent>/logs/<agent>-" in np:
        return 0.0
    if np.startswith("/home/<agent>/workspace/docs") or np.startswith("/home/<agent>/workspace/tests"):
        return 0.1
    return 0.3

# ---------------------------------------------------------------- Modelo de riesgo (§4.7)
def score_tool_call(tool: str, args: dict, path: str = "") -> tuple:
    """Devuelve (score 0.0–1.0, decision Allow|Review|RequireConfirmation|Block)."""
    score = 0.0
    score += BASE_RISK.get(tool, 0.2) * 0.3
    score += path_sensitivity(path) * 0.3
    score += estimate_blast_radius(tool, args) * 0.2
    score += (0.8 if is_irreversible(tool, args) else 0.0) * 0.2

    if score < 0.30:
        return score, "Allow"
    if score < 0.55:
        return score, "Review"
    if score < 0.75:
        return score, "RequireConfirmation"
    return score, "Block"

# ---------------------------------------------------------------- Transcript (Stop hook)
def _is_telegram_message(content) -> bool:
    def _has_tag(text):
        return '<channel' in text and 'plugin:telegram:telegram' in text

    if isinstance(content, str):
        return _has_tag(content)
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get('type') == 'text':
                if _has_tag(block.get('text', '')):
                    return True
    return False

def _content_to_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [b.get('text', '') for b in content
                 if isinstance(b, dict) and b.get('type') == 'text' and b.get('text')]
        return '\n'.join(parts)
    return ''

def check_reply_status(transcript_path: str) -> tuple:
    """Escanea los mensajes posteriores al último mensaje de Telegram del transcript.

    Devuelve (reply_ok, last_assistant_text):
    - reply_ok: True solo si se llamó a la tool de reply de Telegram y su
      tool_result no fue error (matching por tool_use_id, no una simple
      búsqueda de substring en todo el fichero — un reply de una pregunta
      anterior en la misma sesión no debe contar para la actual).
    - last_assistant_text: último texto de asistente visto tras ese mensaje,
      para que el Stop hook pueda rescatarlo si el reply nunca llega.
    """
    if not transcript_path or not os.path.exists(transcript_path):
        return False, ""

    messages = []
    try:
        with open(transcript_path, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    messages.append(json.loads(line))
                except Exception:
                    continue
    except Exception:
        return False, ""

    last_tg_idx = -1
    for i, msg in enumerate(messages):
        if msg.get('type') != 'user':
            continue
        content = (msg.get('message') or {}).get('content', '')
        if _is_telegram_message(content):
            last_tg_idx = i

    if last_tg_idx == -1:
        return False, ""

    pending_reply_ids = set()
    reply_ok = False
    last_text = ""

    for msg in messages[last_tg_idx + 1:]:
        msg_type = msg.get('type')
        content = (msg.get('message') or {}).get('content', [])

        if msg_type == 'assistant':
            text = _content_to_text(content)
            if text:
                last_text = text
            if isinstance(content, list):
                for block in content:
                    if (isinstance(block, dict)
                            and block.get('type') == 'tool_use'
                            and block.get('name') == 'mcp__plugin_telegram_telegram__reply'
                            and block.get('id') is not None):
                        pending_reply_ids.add(block['id'])

        elif msg_type == 'user' and pending_reply_ids:
            if isinstance(content, list):
                for block in content:
                    if (isinstance(block, dict)
                            and block.get('type') == 'tool_result'
                            and block.get('tool_use_id') in pending_reply_ids):
                        if not block.get('is_error', False):
                            reply_ok = True
                        pending_reply_ids.discard(block.get('tool_use_id'))

    return reply_ok, last_text

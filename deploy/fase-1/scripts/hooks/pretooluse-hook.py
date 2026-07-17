#!/usr/bin/env python3
# chmod +x /home/<agent>/workspace/scripts/hooks/pretooluse-hook.py
"""pretooluse-hook.py — PreToolUse (guardrail más crítico).

Síncrono, sin canal Telegram, FAIL-CLOSED: si la lógica peta, bloquea con razón
explícita — nunca exit 0 silencioso. Corre en TODOS los contextos (los guardrails
se aplican también en subagentes).

Orden de evaluación (§4.1 §5.2):
  1. Lee tool_name, tool_input, normaliza la ruta destino.
  2. Deny binario por tabla de rutas (tier never).
  3. Reglas inviolables (memoria, pipe peligroso, gestor de paquetes, modelo costoso).
  4. Modelo de riesgo graduado → Allow/Review/RequireConfirmation/Block.
"""
import sys

sys.path.insert(0, "/home/<agent>/workspace/scripts/lib")
from common import (read_hook_input, allow, block, ask, review,
                        extract_path, lookup_tier, score_tool_call,
                        is_package_manager, is_dangerous_pipe, is_costly_agent,
                        is_memory_path, check_agent_policy)

try:
    data = read_hook_input()
    tool = data.get("tool_name", "")
    args = data.get("tool_input", {}) or {}
    if not isinstance(args, dict):
        args = {}
    agent_type = data.get("agent_type") or ""
    path = extract_path(tool, args)

    # 3. Deny binario por tabla de rutas
    if path and lookup_tier(path) == "never":
        block(f"Ruta protegida (tier never): {path}", tool=tool)

    # 4. Reglas inviolables (deterministas, prevalecen sobre cualquier otra capa —
    #    aplican igual al hilo principal y a subagentes, sin excepción)
    if is_memory_path(path):
        block("La memoria vive en PostgreSQL, no en disco.", tool=tool)
    if is_dangerous_pipe(tool, args):
        block("Patrón de ejecución remota bloqueado.", tool=tool)
    if is_package_manager(tool, args):
        ask("Instalación de paquetes: requiere permiso explícito del usuario propietario.", tool=tool)
    if is_costly_agent(tool, args):
        ask("Modelo costoso (>= Opus): requiere permiso del usuario propietario.", tool=tool)

    # 5. Subagentes: tabla de permisos por agent_type (agent-permissions.json),
    #    allow-only / default-deny. No cae al modelo de riesgo genérico de abajo
    #    — ese es solo para el hilo principal (§ regla de Iván: settings.json
    #    sigue gobernando main, esta tabla es exclusiva de subagentes).
    if agent_type:
        if check_agent_policy(agent_type, tool, args):
            allow(tool=tool)
        else:
            block(f"No permitido para agent_type={agent_type!r} (agent-permissions.json).", tool=tool)

    # 6-7. Hilo principal: modelo de riesgo graduado (sin cambios)
    score, decision = score_tool_call(tool, args, path)
    if decision == "Allow":
        allow(tool=tool)
    elif decision == "Review":
        review(tool=tool, score=score)        # log + (main) ticker; exit 0
    elif decision == "RequireConfirmation":
        ask(f"Confirmación requerida (riesgo {score:.2f}).", tool=tool)
    else:
        block(f"Operación bloqueada (riesgo {score:.2f}).", tool=tool)

except SystemExit:
    raise
except Exception as e:
    # FAIL-CLOSED: un guardrail que peta debe bloquear, no dejar pasar.
    block(f"Hook PreToolUse falló: {e}. Bloqueo por seguridad.", tool="UNKNOWN")

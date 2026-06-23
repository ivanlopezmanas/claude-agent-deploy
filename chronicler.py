#!/usr/bin/env python3
# chmod +x /home/<agent>/workspace/scripts/hooks/<agent>-pretooluse-hook.py
"""<agent>-pretooluse-hook.py — PreToolUse (guardrail más crítico).

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
from <agent>_common import (read_hook_input, allow, block, ask, review,
                        extract_path, lookup_tier, score_tool_call,
                        is_package_manager, is_dangerous_pipe, is_costly_agent,
                        is_memory_path)

try:
    data = read_hook_input()
    tool = data.get("tool_name", "")
    args = data.get("tool_input", {}) or {}
    if not isinstance(args, dict):
        args = {}
    path = extract_path(tool, args)

    # 3. Deny binario por tabla de rutas
    if path and lookup_tier(path) == "never":
        block(f"Ruta protegida (tier never): {path}", tool=tool)

    # 4. Reglas inviolables (deterministas, prevalecen sobre el scoring)
    if is_memory_path(path):
        block("La memoria vive en PostgreSQL, no en disco.", tool=tool)
    if is_dangerous_pipe(tool, args):
        block("Patrón de ejecución remota bloqueado.", tool=tool)
    if is_package_manager(tool, args):
        ask("Instalación de paquetes: requiere permiso explícito del usuario propietario.", tool=tool)
    if is_costly_agent(tool, args):
        ask("Modelo costoso (>= Opus): requiere permiso del usuario propietario.", tool=tool)

    # 5-6. Modelo de riesgo graduado
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

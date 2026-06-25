#!/usr/bin/env python3
# chmod +x /home/<agent>/workspace/scripts/hooks/sessionstart-hook.py
"""sessionstart-hook.py — SessionStart (feedback).

No bloquea. FAIL-OPEN. Parte que pertenece al §4 (harness):
  1. Detecta primer arranque (ONBOARDING_PENDING en CLAUDE.md) e inyecta contexto.
  2. Registra el inicio de sesión en el log de permisos.
  3. Limpia ficheros-bandera huérfanos de /tmp/<agent>-* de sesiones anteriores
     (evita que una bandera vieja dispare un rewake espurio).
  4. Reserva el punto de integración de §6 Memoria (carga top-N): no-op en F1-F4.
"""
import json
import os
import sys
from pathlib import Path

CLAUDE_MD = Path("/home/<agent>/claude/CLAUDE.md")

ONBOARDING_CONTEXT = (
    "PRIMER ARRANQUE — ONBOARDING OBLIGATORIO\n\n"
    "Antes de cualquier otra cosa, completa el proceso de onboarding:\n\n"
    "1. Envía un mensaje de bienvenida por Telegram presentándote: eres el asistente "
    "personal del usuario, aún no sabes nada de él y necesitas unos datos para "
    "personalizar la experiencia.\n\n"
    "2. Entrevista al usuario vía Telegram, una pregunta cada vez, para obtener:\n"
    "   - Nombre completo (y cómo prefiere que le llames)\n"
    "   - Familia (pareja, hijos, edades)\n"
    "   - Profesión o contexto laboral\n"
    "   - Tono de comunicación (formal/informal, humor sí/no, respuestas cortas o detalladas)\n"
    "   - Idioma preferido (español por defecto; indicar si quiere otro)\n"
    "   - Para qué quiere usar el asistente principalmente\n\n"
    "3. Con toda la información, edita /home/<agent>/claude/CLAUDE.md:\n"
    "   - Reemplaza <owner_name>, <profession>, <family>, <tech_level>, <use_cases> "
    "con los datos reales.\n"
    "   - Reemplaza <tone_style> con viñetas que describan el tono preferido.\n"
    "   - Reemplaza <language_preference> con la preferencia de idioma "
    "(ej: 'Respondo siempre en español salvo indicación contraria.').\n"
    "   - Elimina la línea <!-- ONBOARDING_PENDING -->.\n\n"
    "4. Confirma al usuario por Telegram que la configuración está completa.\n\n"
    "No omitas este proceso. CLAUDE.md contiene placeholders que deben sustituirse "
    "por información real."
)

# Detectar primer arranque — si CLAUDE.md contiene el marcador, inyectar onboarding.
if os.environ.get("CLAUDECODE") and CLAUDE_MD.exists():
    try:
        if "ONBOARDING_PENDING" in CLAUDE_MD.read_text():
            print(json.dumps({
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "additionalContext": ONBOARDING_CONTEXT
                }
            }))
            sys.exit(0)
    except Exception:
        pass

sys.path.insert(0, "/home/<agent>/workspace/scripts/lib")
from common import (read_hook_input, log_permission,
                        TELEGRAM_TURN_FLAG, REWAKE_COUNTER, APPROVAL_PENDING)

ORPHAN_FLAGS = (TELEGRAM_TURN_FLAG, REWAKE_COUNTER, APPROVAL_PENDING)

try:
    data = read_hook_input()
    source = data.get("source", "")

    # 1. Log de inicio de sesión.
    log_permission("SessionStart", "session-start", source)

    # 2. Limpia banderas huérfanas de sesiones anteriores.
    for flag in ORPHAN_FLAGS:
        try:
            flag.unlink(missing_ok=True)
        except Exception:
            pass

    # 3. Punto de integración §6 Memoria (carga de contexto): no-op en F1-F4.
except Exception:
    pass
sys.exit(0)

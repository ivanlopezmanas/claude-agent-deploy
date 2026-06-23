#!/usr/bin/env python3
# chmod +x /home/<agent>/workspace/scripts/hooks/<agent>-notification-hook.py
"""<agent>-notification-hook.py — Notification (feedback).

Nunca bloquea. FAIL-OPEN. Solo actúa en contexto main.

Al recibir una notificación de aprobación pendiente, escribe
/tmp/<agent>-approval-pending para que el ticker CONGELE el activity feed y el usuario
pueda leer qué se aprueba. Tras la resolución, lo borra.
"""
import json
import sys
import time

sys.path.insert(0, "/home/<agent>/workspace/scripts/lib")
from <agent>_common import read_hook_input, is_main_context, APPROVAL_PENDING

try:
    if not is_main_context():
        sys.exit(0)
    data = read_hook_input()
    message = (data.get("message") or "").lower()

    if "permission" in message or "waiting for your input" in message or "approval" in message:
        try:
            APPROVAL_PENDING.write_text(json.dumps({"message": data.get("message"), "ts": time.time()}))
        except Exception:
            pass
    else:
        # Notificación de resolución / otra: descongela el feed.
        try:
            APPROVAL_PENDING.unlink(missing_ok=True)
        except Exception:
            pass
except Exception:
    pass
sys.exit(0)

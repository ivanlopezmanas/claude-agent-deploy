#!/usr/bin/env python3
# chmod +x /home/<agent>/workspace/scripts/hooks/<agent>-posttooluse-hook.py
"""<agent>-posttooluse-hook.py — PostToolUse (feedback puro).

Nunca bloquea. FAIL-OPEN. Solo actúa en contexto main; en otros sale en silencio
(sin feedback en subagentes, §4.5).
"""
import sys

sys.path.insert(0, "/home/<agent>/workspace/scripts/lib")
from <agent>_common import read_hook_input, is_main_context, update_ticker_state, log_event

try:
    if not is_main_context():
        sys.exit(0)
    data = read_hook_input()
    update_ticker_state(tool=data.get("tool_name"), result=data.get("tool_response"))
    log_event(data)
except Exception:
    pass
sys.exit(0)

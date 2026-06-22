#!/usr/bin/env python3
# chmod +x /home/<agent>/workspace/scripts/hooks/<agent>-precompact-hook.py
"""<agent>-precompact-hook.py — PreCompact (guardrail blando).

Antes de compactar, escribe una señal a /tmp/<agent>-precompact-flag que el flujo de
memoria (§6) consume, para que ningún cierre por compactación pierda estado.
En F3 es solo la señal; la persistencia real es §6. Exit 0 siempre. FAIL-OPEN.
"""
import json
import sys
import time

sys.path.insert(0, "/home/<agent>/workspace/scripts/lib")
from <agent>_common import read_hook_input, _tmp

PRECOMPACT_FLAG = _tmp("<agent>-precompact-flag")

try:
    data = read_hook_input()
    PRECOMPACT_FLAG.write_text(json.dumps({
        "ts": time.time(),
        "session": data.get("session_id"),
        "trigger": data.get("trigger"),
    }, ensure_ascii=False))
except Exception:
    pass
sys.exit(0)

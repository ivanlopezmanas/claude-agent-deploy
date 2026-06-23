# tests/test_stop.py
import json
import time

import pytest

SCRIPT = "<agent>-stop-hook.py"
TURN_FLAG = "<agent>-telegram-turn"
REWAKE = "<agent>-stop-rewake-counter"


def _write_flag(tmp_path, ts):
    (tmp_path / TURN_FLAG).write_text(json.dumps({"ts": ts, "session": "s1"}))


def _transcript_with_reply(tmp_path, with_reply):
    p = tmp_path / "transcript.jsonl"
    content = "mcp__plugin_telegram_telegram__reply" if with_reply else "no reply here"
    p.write_text(json.dumps({"type": "assistant", "content": content}) + "\n")
    return str(p)


def _env(tmp_path, ctx="main"):
    return {"<AGENT>_TMP_OVERRIDE": str(tmp_path), "<AGENT>_CONTEXT": ctx}


class TestStopGuards:
    def test_stop_hook_active_exits(self, run_hook, tmp_path):
        rc, out, _ = run_hook(SCRIPT, {"stop_hook_active": True}, env=_env(tmp_path))
        assert rc == 0
        assert out is None

    def test_non_main_context_exits(self, run_hook, tmp_path):
        _write_flag(tmp_path, time.time())
        rc, out, _ = run_hook(SCRIPT, {"stop_hook_active": False}, env=_env(tmp_path, "cron"))
        assert rc == 0
        assert out is None

    def test_no_flag_exits(self, run_hook, tmp_path):
        rc, out, _ = run_hook(SCRIPT, {"stop_hook_active": False}, env=_env(tmp_path))
        assert rc == 0
        assert out is None

    def test_expired_flag_exits(self, run_hook, tmp_path):
        _write_flag(tmp_path, time.time() - 700)  # > 600s
        rc, out, _ = run_hook(SCRIPT, {"stop_hook_active": False, "transcript_path": ""},
                              env=_env(tmp_path))
        assert rc == 0
        assert out is None

    def test_reply_present_exits_and_clears(self, run_hook, tmp_path):
        _write_flag(tmp_path, time.time())
        tp = _transcript_with_reply(tmp_path, True)
        rc, out, _ = run_hook(SCRIPT, {"stop_hook_active": False, "transcript_path": tp},
                              env=_env(tmp_path))
        assert rc == 0
        assert out is None
        # La bandera de origen se borró.
        assert not (tmp_path / TURN_FLAG).exists()

    def test_no_reply_blocks(self, run_hook, tmp_path):
        _write_flag(tmp_path, time.time())
        tp = _transcript_with_reply(tmp_path, False)
        rc, out, _ = run_hook(SCRIPT, {"stop_hook_active": False, "transcript_path": tp},
                              env=_env(tmp_path))
        assert rc == 0
        assert isinstance(out, dict)
        assert out.get("decision") == "block"

    def test_rewake_counter_force_release(self, run_hook, tmp_path):
        _write_flag(tmp_path, time.time())
        (tmp_path / REWAKE).write_text(json.dumps({"n": 4, "t0": time.time()}))
        tp = _transcript_with_reply(tmp_path, False)
        rc, out, _ = run_hook(SCRIPT, {"stop_hook_active": False, "transcript_path": tp},
                              env=_env(tmp_path))
        assert rc == 0
        # Force-release: sale sin bloquear.
        assert out is None

    def test_malformed_input_fails_open(self, run_hook, tmp_path):
        import subprocess, sys, os
        proc = subprocess.run(
            [sys.executable, "/home/<agent>/workspace/scripts/hooks/" + SCRIPT],
            input="NOT JSON", capture_output=True, text=True,
            env={**os.environ, **_env(tmp_path)}, timeout=5)
        # FAIL-OPEN: exit 0, no cuelga el cierre.
        assert proc.returncode == 0

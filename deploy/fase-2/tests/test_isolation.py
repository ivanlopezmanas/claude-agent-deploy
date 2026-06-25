# tests/test_isolation.py
"""Aislamiento por contexto (main/subagent/background/cron):
- Guardrails (PreToolUse) corren en TODOS los contextos.
- Hooks de feedback (PostToolUse) salen en silencio en contextos no-main.
- Stop hook hace rewake solo en main.
"""
import json
import time

import pytest

PRETOOL = "pretooluse-hook.py"
POSTTOOL = "posttooluse-hook.py"
STOP = "stop-hook.py"

CONTEXTS = ["main", "subagent", "background", "cron"]


def _decision(out):
    return out.get("hookSpecificOutput", {}).get("permissionDecision") if isinstance(out, dict) else None


@pytest.mark.parametrize("ctx", CONTEXTS)
def test_guardrail_runs_in_all_contexts(run_hook, tmp_path, ctx):
    env = {"<AGENT>_TMP_OVERRIDE": str(tmp_path), "<AGENT>_CONTEXT": ctx}
    rc, out, _ = run_hook(PRETOOL,
                          {"tool_name": "Write", "tool_input": {"file_path": "/etc/hosts", "content": "x"}},
                          env=env)
    assert _decision(out) == "deny", f"guardrail debe bloquear en contexto {ctx}"


@pytest.mark.parametrize("ctx", ["subagent", "background", "cron"])
def test_feedback_silent_in_non_main(run_hook, tmp_path, ctx):
    env = {"<AGENT>_TMP_OVERRIDE": str(tmp_path), "<AGENT>_CONTEXT": ctx}
    rc, out, _ = run_hook(POSTTOOL,
                          {"tool_name": "Read", "tool_response": "ok"},
                          env=env)
    assert rc == 0
    # No escribe el ticker en contextos no-main.
    assert not (tmp_path / "<agent>-ticker-state.json").exists()


def test_feedback_writes_in_main(run_hook, tmp_path):
    env = {"<AGENT>_TMP_OVERRIDE": str(tmp_path), "<AGENT>_CONTEXT": "main"}
    rc, out, _ = run_hook(POSTTOOL,
                          {"tool_name": "Read", "tool_response": "ok"},
                          env=env)
    assert rc == 0
    assert (tmp_path / "<agent>-ticker-state.json").exists()


@pytest.mark.parametrize("ctx", ["subagent", "background", "cron"])
def test_stop_no_rewake_in_non_main(run_hook, tmp_path, ctx):
    env = {"<AGENT>_TMP_OVERRIDE": str(tmp_path), "<AGENT>_CONTEXT": ctx}
    (tmp_path / "<agent>-telegram-turn").write_text(json.dumps({"ts": time.time(), "session": "s1"}))
    rc, out, _ = run_hook(STOP, {"stop_hook_active": False, "transcript_path": ""}, env=env)
    assert rc == 0
    assert out is None  # no rewake fuera de main


def test_stop_rewake_in_main(run_hook, tmp_path):
    env = {"<AGENT>_TMP_OVERRIDE": str(tmp_path), "<AGENT>_CONTEXT": "main"}
    (tmp_path / "<agent>-telegram-turn").write_text(json.dumps({"ts": time.time(), "session": "s1"}))
    tp = tmp_path / "transcript.jsonl"
    tp.write_text(json.dumps({"type": "assistant", "content": "no reply"}) + "\n")
    rc, out, _ = run_hook(STOP, {"stop_hook_active": False, "transcript_path": str(tp)}, env=env)
    assert rc == 0
    assert isinstance(out, dict)
    assert out.get("decision") == "block"

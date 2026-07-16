# tests/test_sessionend.py
import json

SCRIPT = "sessionend-hook.py"


def test_non_main_context_exits_clean(run_hook, tmp_path):
    env = {"<AGENT>_TMP_OVERRIDE": str(tmp_path), "<AGENT>_CONTEXT": "subagent"}
    rc, out, err = run_hook(SCRIPT, {"session_id": "abc-123"}, env=env)
    assert rc == 0


def test_sdk_cli_entrypoint_exits_clean(run_hook, tmp_path):
    env = {"<AGENT>_TMP_OVERRIDE": str(tmp_path), "<AGENT>_CONTEXT": "main",
           "CLAUDE_CODE_ENTRYPOINT": "sdk-cli"}
    rc, out, err = run_hook(SCRIPT, {"session_id": "abc-123"}, env=env)
    assert rc == 0


def test_reentry_guard_exits_clean(run_hook, tmp_path):
    env = {"<AGENT>_TMP_OVERRIDE": str(tmp_path), "<AGENT>_CONTEXT": "main",
           "<AGENT>_HOOK_RUNNING": "1"}
    rc, out, err = run_hook(SCRIPT, {"session_id": "abc-123"}, env=env)
    assert rc == 0


def test_main_context_no_session_id_exits_clean(run_hook, tmp_path):
    env = {"<AGENT>_TMP_OVERRIDE": str(tmp_path), "<AGENT>_CONTEXT": "main"}
    rc, out, err = run_hook(SCRIPT, {}, env=env)
    assert rc == 0


def test_main_context_without_telegram_env_does_not_crash(run_hook, tmp_path):
    # Sin CHRONICLER real ni TELEGRAM_BOT_TOKEN en el entorno de test: ambos
    # pasos fallan en silencio (fail-open), el hook debe salir limpio igual.
    env = {"<AGENT>_TMP_OVERRIDE": str(tmp_path), "<AGENT>_CONTEXT": "main"}
    rc, out, err = run_hook(SCRIPT, {"session_id": "abc-123", "transcript_path": ""}, env=env)
    assert rc == 0


def test_malformed_stdin_exits_clean(run_hook, tmp_path):
    env = {"<AGENT>_TMP_OVERRIDE": str(tmp_path), "<AGENT>_CONTEXT": "main"}
    rc, out, err = run_hook(SCRIPT, "not json but run_hook dumps it as json string", env=env)
    assert rc == 0

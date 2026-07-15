# tests/test_stop.py
import json
import time

import pytest

SCRIPT = "stop-hook.py"
TURN_FLAG = "<agent>-telegram-turn"
REWAKE = "<agent>-stop-rewake-counter"


def _write_flag(tmp_path, ts):
    (tmp_path / TURN_FLAG).write_text(json.dumps({"ts": ts, "session": "s1"}))


def _tg_msg(text, msg_id="1"):
    return {
        "type": "user",
        "message": {
            "content": f'<channel source="plugin:telegram:telegram" chat_id="123" message_id="{msg_id}">{text}</channel>'
        }
    }


def _assistant_reply(tool_id="t1", text="respuesta"):
    return {
        "type": "assistant",
        "message": {
            "content": [
                {"type": "text", "text": text},
                {"type": "tool_use", "id": tool_id,
                 "name": "mcp__plugin_telegram_telegram__reply",
                 "input": {"chat_id": "123", "text": text}}
            ]
        }
    }


def _assistant_text(text):
    return {
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": text}]}
    }


def _tool_result(tool_id="t1", is_error=False):
    return {
        "type": "user",
        "message": {
            "content": [{"type": "tool_result", "tool_use_id": tool_id, "is_error": is_error}]
        }
    }


def _write_transcript(tmp_path, messages, name="transcript.jsonl"):
    p = tmp_path / name
    p.write_text("\n".join(json.dumps(m) for m in messages) + "\n")
    return str(p)


def _env(tmp_path, ctx="main"):
    # TELEGRAM_* vacíos a propósito: el rescate llama a _tg_send, y sin esto
    # los tests heredarían credenciales reales del entorno y mandarían
    # mensajes de verdad al chat de producción.
    return {
        "<AGENT>_TMP_OVERRIDE": str(tmp_path),
        "<AGENT>_CONTEXT": ctx,
        "TELEGRAM_BOT_TOKEN": "",
        "TELEGRAM_CHAT_ID": "",
    }


class TestStopGuards:
    def test_non_main_context_exits(self, run_hook, tmp_path):
        _write_flag(tmp_path, time.time())
        rc, out, _ = run_hook(SCRIPT, {"stop_hook_active": False}, env=_env(tmp_path, "cron"))
        assert rc == 0
        assert out is None

    def test_no_flag_exits(self, run_hook, tmp_path):
        rc, out, _ = run_hook(SCRIPT, {"stop_hook_active": False}, env=_env(tmp_path))
        assert rc == 0
        assert out is None

    def test_expired_flag_exits_and_clears_counter(self, run_hook, tmp_path):
        _write_flag(tmp_path, time.time() - 700)  # > 600s
        (tmp_path / REWAKE).write_text(json.dumps({"n": 2}))
        rc, out, _ = run_hook(SCRIPT, {"stop_hook_active": False, "transcript_path": ""},
                              env=_env(tmp_path))
        assert rc == 0
        assert out is None
        assert not (tmp_path / REWAKE).exists()

    def test_stop_hook_active_true_does_not_shortcut_block(self, run_hook, tmp_path):
        # Bug de la doc oficial: stop_hook_active es true en TODAS las llamadas
        # a Stop posteriores a un bloqueo previo, no solo en la primera. Si el
        # hook lo usara como salida incondicional, jamás llegaría a un segundo
        # intento. El contador es ahora la única fuente de verdad.
        _write_flag(tmp_path, time.time())
        tp = _write_transcript(tmp_path, [_tg_msg("pregunta"), _assistant_text("sin reply")])
        rc, out, _ = run_hook(SCRIPT, {"stop_hook_active": True, "transcript_path": tp},
                              env=_env(tmp_path))
        assert rc == 0
        assert isinstance(out, dict)
        assert out.get("decision") == "block"

    def test_reply_present_exits_and_clears(self, run_hook, tmp_path):
        _write_flag(tmp_path, time.time())
        (tmp_path / REWAKE).write_text(json.dumps({"n": 2}))
        tp = _write_transcript(tmp_path, [
            _tg_msg("pregunta"),
            _assistant_reply("t1", "respuesta ok"),
            _tool_result("t1", is_error=False),
        ])
        rc, out, _ = run_hook(SCRIPT, {"stop_hook_active": False, "transcript_path": tp},
                              env=_env(tmp_path))
        assert rc == 0
        assert out is None
        assert not (tmp_path / TURN_FLAG).exists()
        assert not (tmp_path / REWAKE).exists()

    def test_reply_tool_result_error_still_blocks(self, run_hook, tmp_path):
        # El reply se llamó pero falló (tool_result con error) — no debe contar como éxito.
        _write_flag(tmp_path, time.time())
        tp = _write_transcript(tmp_path, [
            _tg_msg("pregunta"),
            _assistant_reply("t1", "respuesta que fallo"),
            _tool_result("t1", is_error=True),
        ])
        rc, out, _ = run_hook(SCRIPT, {"stop_hook_active": False, "transcript_path": tp},
                              env=_env(tmp_path))
        assert rc == 0
        assert isinstance(out, dict)
        assert out.get("decision") == "block"

    def test_only_last_tg_message_matters(self, run_hook, tmp_path):
        # Un reply exitoso a un mensaje ANTERIOR de Telegram no debe contar
        # para el mensaje actual (ese era el bug de scoping de reply_in_transcript).
        _write_flag(tmp_path, time.time())
        tp = _write_transcript(tmp_path, [
            _tg_msg("primer mensaje", msg_id="1"),
            _assistant_reply("t1", "primera respuesta"),
            _tool_result("t1", is_error=False),
            _tg_msg("segundo mensaje", msg_id="2"),
            _assistant_text("segunda respuesta sin reply"),
        ])
        rc, out, _ = run_hook(SCRIPT, {"stop_hook_active": False, "transcript_path": tp},
                              env=_env(tmp_path))
        assert rc == 0
        assert isinstance(out, dict)
        assert out.get("decision") == "block"

    def test_no_reply_blocks_and_counts_attempts(self, run_hook, tmp_path):
        _write_flag(tmp_path, time.time())
        tp = _write_transcript(tmp_path, [_tg_msg("pregunta"), _assistant_text("sin reply")])

        for expected_attempt in (1, 2, 3):
            rc, out, _ = run_hook(SCRIPT, {"stop_hook_active": False, "transcript_path": tp},
                                  env=_env(tmp_path))
            assert rc == 0
            assert out.get("decision") == "block"
            assert f"Intento {expected_attempt} de 3" in out.get("reason", "")

    def test_rewake_exhausted_rescues_with_text(self, run_hook, tmp_path):
        _write_flag(tmp_path, time.time())
        (tmp_path / REWAKE).write_text(json.dumps({"n": 3}))
        tp = _write_transcript(tmp_path, [_tg_msg("pregunta"), _assistant_text("texto sin enviar")])
        rc, out, _ = run_hook(SCRIPT, {"stop_hook_active": False, "transcript_path": tp},
                              env=_env(tmp_path))
        assert rc == 0
        # Rescate, no bloqueo: el turno se cierra.
        assert out is None
        assert not (tmp_path / TURN_FLAG).exists()
        assert not (tmp_path / REWAKE).exists()

    def test_rewake_exhausted_rescues_without_text(self, run_hook, tmp_path):
        _write_flag(tmp_path, time.time())
        (tmp_path / REWAKE).write_text(json.dumps({"n": 3}))
        tp = _write_transcript(tmp_path, [_tg_msg("pregunta")])
        rc, out, _ = run_hook(SCRIPT, {"stop_hook_active": False, "transcript_path": tp},
                              env=_env(tmp_path))
        assert rc == 0
        assert out is None
        assert not (tmp_path / REWAKE).exists()

    def test_malformed_input_fails_open(self, run_hook, tmp_path):
        import subprocess, sys, os
        proc = subprocess.run(
            [sys.executable, "/home/<agent>/workspace/scripts/hooks/" + SCRIPT],
            input="NOT JSON", capture_output=True, text=True,
            env={**os.environ, **_env(tmp_path)}, timeout=5)
        # FAIL-OPEN: exit 0, no cuelga el cierre.
        assert proc.returncode == 0

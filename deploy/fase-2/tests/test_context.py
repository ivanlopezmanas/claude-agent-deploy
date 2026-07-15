# tests/test_context.py
"""context.py corre en background sin que nadie lea su stdout/stderr — la única
forma de saber qué hizo es el log compartido (log_permission). Estos tests
verifican que cada camino (sin transcript, sin uso, por debajo del umbral,
enviado, fallo de envío) deja rastro."""
import importlib.util
import json
import sys

import pytest

sys.path.insert(0, "/home/<agent>/workspace/scripts/lib")
import common as nc

SCRIPT_PATH = "/home/<agent>/workspace/scripts/lib/context.py"


def _run_context(monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", ["context.py"] + argv)
    spec = importlib.util.spec_from_file_location("context_under_test", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _read_log(log_path):
    if not log_path.exists():
        return []
    return [json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]


def _usage_line(model="claude-sonnet-4-6", cache_read=0, input_tokens=0, cache_creation=0):
    return {
        "type": "assistant",
        "message": {
            "model": model,
            "usage": {
                "input_tokens": input_tokens,
                "cache_creation_input_tokens": cache_creation,
                "cache_read_input_tokens": cache_read,
            },
        },
    }


def _write_transcript(tmp_path, lines):
    p = tmp_path / "transcript.jsonl"
    p.write_text("\n".join(json.dumps(l) for l in lines) + "\n")
    return str(p)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(nc, "LOG_PATH", tmp_path / "log.jsonl")
    # Sin credenciales reales: cualquier intento de envío debe fallar limpio,
    # nunca golpear la API de Telegram de verdad durante los tests.
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "")
    yield


class TestContextLogging:
    def test_missing_transcript_logs_no_transcript(self, monkeypatch, tmp_path):
        _run_context(monkeypatch, ["--mode", "hook", "--transcript", "/nonexistent/path.jsonl"])
        entries = _read_log(tmp_path / "log.jsonl")
        assert any(e["tool"] == "context" and e["decision"] == "no-transcript" for e in entries)

    def test_no_usage_data_logs_no_usage(self, monkeypatch, tmp_path):
        tp = _write_transcript(tmp_path, [{"type": "system", "message": {"content": "boot"}}])
        _run_context(monkeypatch, ["--mode", "hook", "--transcript", tp])
        entries = _read_log(tmp_path / "log.jsonl")
        assert any(e["tool"] == "context" and e["decision"] == "no-usage" for e in entries)

    def test_below_threshold_hook_mode_skips_and_logs(self, monkeypatch, tmp_path):
        tp = _write_transcript(tmp_path, [_usage_line(cache_read=1000)])  # pct bajo
        _run_context(monkeypatch, ["--mode", "hook", "--transcript", tp])
        entries = _read_log(tmp_path / "log.jsonl")
        assert any(e["tool"] == "context" and e["decision"] == "skip" for e in entries)
        assert not any(e["decision"] in ("sent", "send-failed") for e in entries)

    def test_above_threshold_logs_send_failed_without_credentials(self, monkeypatch, tmp_path):
        tp = _write_transcript(tmp_path, [_usage_line(cache_read=70_000)])  # pct > 30
        _run_context(monkeypatch, ["--mode", "hook", "--transcript", tp])
        entries = _read_log(tmp_path / "log.jsonl")
        failed = [e for e in entries if e["tool"] == "context" and e["decision"] == "send-failed"]
        assert len(failed) == 1
        assert "pct=" in failed[0]["reason"]

    def test_command_mode_always_attempts_send_even_below_threshold(self, monkeypatch, tmp_path):
        tp = _write_transcript(tmp_path, [_usage_line(cache_read=1000)])  # pct bajo
        _run_context(monkeypatch, ["--mode", "command", "--transcript", tp])
        entries = _read_log(tmp_path / "log.jsonl")
        # En modo command no hay guarda de umbral: intenta enviar (y falla, sin credenciales).
        assert any(e["tool"] == "context" and e["decision"] == "send-failed" for e in entries)

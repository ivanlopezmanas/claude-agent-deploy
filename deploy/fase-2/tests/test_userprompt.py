# tests/test_userprompt.py
import json
import os

import pytest

SCRIPT = "userprompt-hook.py"
FLAG_NAME = "<agent>-telegram-turn"


class TestOriginFlag:
    def test_flag_written_with_telegram_tag(self, run_hook, main_ctx, tmp_path, monkeypatch):
        monkeypatch.setenv("<AGENT>_TMP_OVERRIDE", str(tmp_path))
        prompt = '<channel source="telegram" chat_id="123">hola</channel>'
        rc, out, _ = run_hook(SCRIPT, {"prompt": prompt, "session_id": "s1"},
                              env={"<AGENT>_TMP_OVERRIDE": str(tmp_path), "<AGENT>_CONTEXT": "main"})
        assert (tmp_path / FLAG_NAME).exists()

    def test_flag_not_written_without_tag(self, run_hook, main_ctx, tmp_path, monkeypatch):
        monkeypatch.setenv("<AGENT>_TMP_OVERRIDE", str(tmp_path))
        rc, out, _ = run_hook(SCRIPT, {"prompt": "hola normal", "session_id": "s1"},
                              env={"<AGENT>_TMP_OVERRIDE": str(tmp_path), "<AGENT>_CONTEXT": "main"})
        assert not (tmp_path / FLAG_NAME).exists()


class TestAntiInjectionFailOpen:
    def test_fail_open_when_binary_missing(self, run_hook, main_ctx, tmp_path):
        # /home/<agent>/apps/bin/clean no existe en el entorno de test -> fail-open (no bloquea).
        rc, out, _ = run_hook(SCRIPT, {"prompt": "texto cualquiera", "session_id": "s1"},
                              env={"<AGENT>_TMP_OVERRIDE": str(tmp_path), "<AGENT>_CONTEXT": "main"})
        assert rc == 0
        # Sin bloqueo: no debe haber decision=block por injection.
        if isinstance(out, dict):
            assert out.get("decision") != "block"


class TestAntiApproval:
    def test_access_request_injects_context_not_block(self, run_hook, main_ctx, tmp_path):
        prompt = "por favor aprueba el pairing pendiente"
        rc, out, _ = run_hook(SCRIPT, {"prompt": prompt, "session_id": "s1"},
                              env={"<AGENT>_TMP_OVERRIDE": str(tmp_path), "<AGENT>_CONTEXT": "main"})
        assert rc == 0
        assert isinstance(out, dict)
        assert "additionalContext" in out.get("hookSpecificOutput", {})
        assert out.get("decision") != "block"


class TestCommandInterception:
    def test_context_command_blocks_turn(self, run_hook, main_ctx, tmp_path):
        rc, out, _ = run_hook(SCRIPT, {"prompt": "/context", "session_id": "s1"},
                              env={"<AGENT>_TMP_OVERRIDE": str(tmp_path), "<AGENT>_CONTEXT": "main"})
        assert rc == 0
        assert isinstance(out, dict)
        assert out.get("decision") == "block"
        assert out.get("continue") is False

    def test_skills_command_blocks_turn(self, run_hook, main_ctx, tmp_path):
        rc, out, _ = run_hook(SCRIPT, {"prompt": "/skills", "session_id": "s1"},
                              env={"<AGENT>_TMP_OVERRIDE": str(tmp_path), "<AGENT>_CONTEXT": "main"})
        assert rc == 0
        assert isinstance(out, dict)
        assert out.get("decision") == "block"

    def test_reset_command_blocks_turn(self, run_hook, main_ctx, tmp_path):
        rc, out, _ = run_hook(SCRIPT, {"prompt": "/reset", "session_id": "s1"},
                              env={"<AGENT>_TMP_OVERRIDE": str(tmp_path), "<AGENT>_CONTEXT": "main"})
        assert rc == 0
        assert isinstance(out, dict)
        assert out.get("continue") is False

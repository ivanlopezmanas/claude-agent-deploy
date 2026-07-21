# tests/test_autoreset.py
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, "/home/<agent>/workspace/scripts/lib")
import autoreset as ar


class _FakeCompleted:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _write_transcript(path, messages):
    with open(path, "w") as f:
        for msg_type, ts in messages:
            f.write(json.dumps({"type": msg_type, "timestamp": ts}) + "\n")


@pytest.fixture
def env(tmp_path, monkeypatch):
    glob_dir = tmp_path / "projects"
    glob_dir.mkdir()
    monkeypatch.setattr(ar, "TRANSCRIPT_GLOB", str(glob_dir / "*.jsonl"))
    monkeypatch.setattr(ar, "LOG", str(tmp_path / "autoreset.log"))
    return {"dir": glob_dir}


def _idle_ts(seconds):
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()


# ----------------------------------------------------------------- sin transcript
class TestNoTranscript:
    def test_no_transcript_files_is_a_silent_noop(self, env):
        assert ar.main() == {"ok": True, "notify": None}

    def test_empty_transcript_is_a_silent_noop(self, env):
        (env["dir"] / "s1.jsonl").write_text("")
        assert ar.main() == {"ok": True, "notify": None}


# ----------------------------------------------------------------- sesión idle -> reinicio
class TestIdleSession:
    def _seed(self, env, idle_seconds):
        _write_transcript(env["dir"] / "s1.jsonl", [("user", _idle_ts(idle_seconds))])

    def test_restarts_silently_on_success(self, env, monkeypatch):
        self._seed(env, ar.IDLE_THRESHOLD_SECONDS + 60)
        monkeypatch.setattr(ar.subprocess, "run", lambda *a, **k: _FakeCompleted(0))

        assert ar.main() == {"ok": True, "notify": None}

    def test_restart_failure_escalates_as_high(self, env, monkeypatch):
        self._seed(env, ar.IDLE_THRESHOLD_SECONDS + 60)
        monkeypatch.setattr(
            ar.subprocess, "run",
            lambda *a, **k: _FakeCompleted(1, stderr="unit not found"),
        )

        result = ar.main()
        assert result["ok"] is False
        assert result["notify"]["severity"] == "high"
        assert "unit not found" in result["notify"]["context"]

    def test_calls_systemctl_restart_with_service_name(self, env, monkeypatch):
        self._seed(env, ar.IDLE_THRESHOLD_SECONDS + 60)
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return _FakeCompleted(0)

        monkeypatch.setattr(ar.subprocess, "run", fake_run)
        ar.main()

        assert captured["cmd"] == ["sudo", "systemctl", "restart", ar.SERVICE]


# ----------------------------------------------------------------- sesión activa -> reintento
class TestActiveSession:
    def _seed(self, env, idle_seconds):
        _write_transcript(env["dir"] / "s1.jsonl", [("assistant", _idle_ts(idle_seconds))])

    def test_reschedules_silently_on_success(self, env, monkeypatch):
        self._seed(env, ar.IDLE_THRESHOLD_SECONDS - 60)
        monkeypatch.setattr(ar.subprocess, "run", lambda *a, **k: _FakeCompleted(0))

        assert ar.main() == {"ok": True, "notify": None}

    def test_reschedule_failure_escalates_as_high(self, env, monkeypatch):
        self._seed(env, ar.IDLE_THRESHOLD_SECONDS - 60)
        monkeypatch.setattr(
            ar.subprocess, "run",
            lambda *a, **k: _FakeCompleted(1, stderr="systemd-run: command not found"),
        )

        result = ar.main()
        assert result["ok"] is False
        assert result["notify"]["severity"] == "high"
        assert "systemd-run" in result["notify"]["context"]

    def test_calls_systemd_run_with_on_active_1h(self, env, monkeypatch):
        self._seed(env, ar.IDLE_THRESHOLD_SECONDS - 60)
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return _FakeCompleted(0)

        monkeypatch.setattr(ar.subprocess, "run", fake_run)
        ar.main()

        assert captured["cmd"][0] == "systemd-run"
        assert "--on-active=3600" in captured["cmd"]


class TestLatestTranscriptSelection:
    def test_picks_the_most_recently_modified_transcript(self, env, monkeypatch):
        old_path = env["dir"] / "old.jsonl"
        new_path = env["dir"] / "new.jsonl"
        _write_transcript(old_path, [("user", _idle_ts(ar.IDLE_THRESHOLD_SECONDS + 60))])
        _write_transcript(new_path, [("user", _idle_ts(10))])

        now = datetime.now().timestamp()
        os.utime(old_path, (now - 200, now - 200))
        os.utime(new_path, (now, now))

        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return _FakeCompleted(0)

        monkeypatch.setattr(ar.subprocess, "run", fake_run)
        ar.main()

        # el transcript más reciente está activo -> reintento, no reinicio
        assert captured["cmd"][0] == "systemd-run"


# ----------------------------------------------------------------- contrato de salida
class TestContractShape:
    def test_result_is_always_a_valid_ok_notify_dict(self, env, monkeypatch):
        monkeypatch.setattr(ar.subprocess, "run", lambda *a, **k: _FakeCompleted(0))
        for idle_seconds in (0, ar.IDLE_THRESHOLD_SECONDS + 60):
            _write_transcript(env["dir"] / "s1.jsonl", [("user", _idle_ts(idle_seconds))])
            result = ar.main()
            assert set(result.keys()) == {"ok", "notify"}
            assert isinstance(result["ok"], bool)
            if result["notify"] is not None:
                assert set(result["notify"].keys()) == {"severity", "message", "context"}
                assert result["notify"]["severity"] in ("critical", "high", "medium", "low")

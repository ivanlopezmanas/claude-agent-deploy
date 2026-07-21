# tests/test_self_improve.py
import json
import sys

import pytest

sys.path.insert(0, "/home/<agent>/workspace/scripts/lib")
import self_improve as si


class _FakeCompleted:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


# ----------------------------------------------------------------- gather_territory()
class TestGatherTerritory:
    def test_lists_entries_for_existing_dirs(self, tmp_path, monkeypatch):
        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir()
        (scripts_dir / "a.py").write_text("x")
        (scripts_dir / "b.py").write_text("x")
        monkeypatch.setattr(si, "TERRITORY_DIRS", {"scripts": str(scripts_dir)})
        monkeypatch.setattr(si, "SYSTEM_BIN_DIR", str(tmp_path / "nonexistent-bin"))

        territory = si.gather_territory()

        assert territory["scripts"]["entries"] == ["a.py", "b.py"]

    def test_missing_dir_has_none_entries_not_an_exception(self, tmp_path, monkeypatch):
        monkeypatch.setattr(si, "TERRITORY_DIRS", {"agents": str(tmp_path / "no-existe")})
        monkeypatch.setattr(si, "SYSTEM_BIN_DIR", str(tmp_path / "nonexistent-bin"))

        territory = si.gather_territory()

        assert territory["agents"]["entries"] is None

    def test_system_bin_filters_by_agent_prefix(self, tmp_path, monkeypatch):
        bin_dir = tmp_path / "usr-local-bin"
        bin_dir.mkdir()
        (bin_dir / "<agent>-autoreset").write_text("x")
        (bin_dir / "other-tool").write_text("x")
        monkeypatch.setattr(si, "TERRITORY_DIRS", {})
        monkeypatch.setattr(si, "SYSTEM_BIN_DIR", str(bin_dir))
        monkeypatch.setattr(si, "SYSTEM_BIN_PREFIX", "<agent>-")

        territory = si.gather_territory()

        assert territory["system_bin"]["entries"] == ["<agent>-autoreset"]


# ----------------------------------------------------------------- gather_tests()
class TestGatherTests:
    def test_captures_last_lines_of_stdout_and_returncode(self, monkeypatch):
        stdout = "\n".join(f"line {i}" for i in range(10))
        monkeypatch.setattr(si.subprocess, "run", lambda *a, **k: _FakeCompleted(1, stdout))

        result = si.gather_tests()

        assert result["returncode"] == 1
        assert result["summary"] == "\n".join(f"line {i}" for i in range(5, 10))

    def test_timeout_reports_error_without_raising(self, monkeypatch):
        import subprocess as sp

        def _raise(*a, **k):
            raise sp.TimeoutExpired(cmd="pytest", timeout=si.TEST_TIMEOUT_SECONDS)

        monkeypatch.setattr(si.subprocess, "run", _raise)

        result = si.gather_tests()

        assert "timeout" in result["error"]


# ----------------------------------------------------------------- gather_settings_check()
class TestGatherSettingsCheck:
    def test_valid_settings_no_missing_hooks(self, tmp_path, monkeypatch):
        hook_script = tmp_path / "hook.py"
        hook_script.write_text("#!/usr/bin/env python3\n")
        settings = {"hooks": {"PreToolUse": [{"hooks": [
            {"type": "command", "command": str(hook_script)}
        ]}]}}
        settings_path = tmp_path / "settings.json"
        settings_path.write_text(json.dumps(settings))
        monkeypatch.setattr(si, "SETTINGS_PATH", str(settings_path))

        result = si.gather_settings_check()

        assert result == {"valid_json": True, "missing_hooks": []}

    def test_hook_declared_but_missing_on_disk(self, tmp_path, monkeypatch):
        settings = {"hooks": {"PreToolUse": [{"hooks": [
            {"type": "command", "command": "/no/existe/hook.py"}
        ]}]}}
        settings_path = tmp_path / "settings.json"
        settings_path.write_text(json.dumps(settings))
        monkeypatch.setattr(si, "SETTINGS_PATH", str(settings_path))

        result = si.gather_settings_check()

        assert result["valid_json"] is True
        assert result["missing_hooks"] == ["/no/existe/hook.py"]

    def test_invalid_json_reports_error(self, tmp_path, monkeypatch):
        settings_path = tmp_path / "settings.json"
        settings_path.write_text("{not json")
        monkeypatch.setattr(si, "SETTINGS_PATH", str(settings_path))

        result = si.gather_settings_check()

        assert result["valid_json"] is False
        assert "error" in result

    def test_missing_settings_file_reports_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(si, "SETTINGS_PATH", str(tmp_path / "no-existe.json"))

        result = si.gather_settings_check()

        assert result["valid_json"] is False


# ----------------------------------------------------------------- gather_permissions_log_tail()
class TestGatherPermissionsLogTail:
    def test_returns_last_n_lines(self, tmp_path, monkeypatch):
        log_path = tmp_path / "perms.log"
        log_path.write_text("\n".join(f"line {i}" for i in range(100)) + "\n")
        monkeypatch.setattr(si, "PERMISSIONS_LOG", str(log_path))
        monkeypatch.setattr(si, "PERMISSIONS_LOG_TAIL_LINES", 3)

        result = si.gather_permissions_log_tail()

        assert result["tail"] == "line 97\nline 98\nline 99\n"

    def test_missing_log_is_not_an_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(si, "PERMISSIONS_LOG", str(tmp_path / "no-existe.log"))

        result = si.gather_permissions_log_tail()

        assert result["tail"] is None
        assert "note" in result


# ----------------------------------------------------------------- gather_memory()
class _FakeCursor:
    def __init__(self, results):
        self._results = list(results)
        self.queries = []

    def execute(self, query, params=None):
        self.queries.append((query, params))

    def fetchall(self):
        return self._results.pop(0)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConn:
    def __init__(self, results):
        self._cursor = _FakeCursor(results)
        self.closed = False

    def cursor(self, cursor_factory=None):
        return self._cursor

    def close(self):
        self.closed = True


class TestGatherMemory:
    def test_missing_dsn_reports_error_without_connecting(self, monkeypatch):
        monkeypatch.setattr(si, "DB_DSN", "")
        monkeypatch.setattr(si.psycopg2, "connect", lambda dsn: pytest.fail("no debería conectar"))

        result = si.gather_memory()

        assert "error" in result

    def test_success_returns_recent_and_chronic_keys(self, monkeypatch):
        monkeypatch.setattr(si, "DB_DSN", "postgresql://fake")
        recent = [{"id": 1, "category": "feedback", "content": "x"}]
        chronic = [{"k": "heartbeat", "occurrences": 5}]
        fake_conn = _FakeConn([recent, chronic])
        monkeypatch.setattr(si.psycopg2, "connect", lambda dsn: fake_conn)

        result = si.gather_memory()

        assert result["recent_7d"] == recent
        assert result["chronic_patterns_30d"] == chronic
        assert fake_conn.closed is True

    def test_query_does_not_filter_by_agent_id(self, monkeypatch):
        # El esquema del template no tiene columna agent_id (DB aislada por agente).
        monkeypatch.setattr(si, "DB_DSN", "postgresql://fake")
        fake_conn = _FakeConn([[], []])
        monkeypatch.setattr(si.psycopg2, "connect", lambda dsn: fake_conn)

        si.gather_memory()

        first_query = fake_conn._cursor.queries[0][0]
        assert "agent_id" not in first_query

    def test_connection_error_reports_error(self, monkeypatch):
        monkeypatch.setattr(si, "DB_DSN", "postgresql://fake")

        def _raise(dsn):
            raise Exception("connection refused")

        monkeypatch.setattr(si.psycopg2, "connect", _raise)

        result = si.gather_memory()

        assert "connection refused" in result["error"]


# ----------------------------------------------------------------- latest_previous_report()
class TestLatestPreviousReport:
    def test_returns_none_when_no_reports(self, tmp_path, monkeypatch):
        monkeypatch.setattr(si, "IMPROVEMENTS_DIR", str(tmp_path))
        assert si.latest_previous_report() is None

    def test_returns_most_recently_modified_report(self, tmp_path, monkeypatch):
        import os
        monkeypatch.setattr(si, "IMPROVEMENTS_DIR", str(tmp_path))
        old = tmp_path / "2026-01-01.md"
        new = tmp_path / "2026-02-01.md"
        old.write_text("x")
        new.write_text("x")
        now = __import__("time").time()
        os.utime(old, (now - 100, now - 100))
        os.utime(new, (now, now))

        assert si.latest_previous_report() == str(new)


# ----------------------------------------------------------------- main() / contrato de salida
class TestMain:
    def _stub_all_gatherers(self, monkeypatch):
        monkeypatch.setattr(si, "gather_territory", lambda: {"scripts": {"entries": []}})
        monkeypatch.setattr(si, "gather_tests", lambda: {"summary": "5 passed", "returncode": 0})
        monkeypatch.setattr(si, "gather_settings_check", lambda: {"valid_json": True, "missing_hooks": []})
        monkeypatch.setattr(si, "gather_permissions_log_tail", lambda: {"tail": None})
        monkeypatch.setattr(si, "gather_memory", lambda: {"recent_7d": [], "chronic_patterns_30d": []})
        monkeypatch.setattr(si, "latest_previous_report", lambda: None)

    def test_never_resolves_silently_always_hands_off_to_model(self, monkeypatch, tmp_path):
        self._stub_all_gatherers(monkeypatch)
        monkeypatch.setattr(si, "TAREAS_PENDIENTES", str(tmp_path / "no-existe.md"))

        result = si.main()

        assert result["ok"] is True
        assert result["notify"] is not None
        assert result["notify"]["message"] is None

    def test_context_bundles_all_evidence_sections(self, monkeypatch, tmp_path):
        self._stub_all_gatherers(monkeypatch)
        monkeypatch.setattr(si, "TAREAS_PENDIENTES", str(tmp_path / "no-existe.md"))

        result = si.main()
        context = result["notify"]["context"]

        assert set(context.keys()) == {
            "territory", "tests", "settings_check", "permissions_log_tail",
            "memory", "tareas_pendientes_path", "previous_report_path",
        }

    def test_result_matches_heartbeat_contract_shape(self, monkeypatch, tmp_path):
        self._stub_all_gatherers(monkeypatch)
        monkeypatch.setattr(si, "TAREAS_PENDIENTES", str(tmp_path / "no-existe.md"))

        result = si.main()

        assert set(result.keys()) == {"ok", "notify"}
        assert isinstance(result["ok"], bool)
        assert set(result["notify"].keys()) == {"severity", "message", "context"}
        assert result["notify"]["severity"] in ("critical", "high", "medium", "low")

    def test_output_is_json_serializable_even_with_datetimes(self, monkeypatch, tmp_path, capsys):
        from datetime import datetime
        self._stub_all_gatherers(monkeypatch)
        monkeypatch.setattr(si, "gather_memory", lambda: {
            "recent_7d": [{"fecha": datetime(2026, 7, 18)}],
            "chronic_patterns_30d": [],
        })
        monkeypatch.setattr(si, "TAREAS_PENDIENTES", str(tmp_path / "no-existe.md"))

        outcome = si.main()
        printed = json.dumps(outcome, default=str)  # no debe lanzar TypeError

        assert "2026-07-18" in printed

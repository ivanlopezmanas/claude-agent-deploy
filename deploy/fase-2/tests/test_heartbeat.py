# tests/test_heartbeat.py
import json
import subprocess
import sys

import pytest

sys.path.insert(0, "/home/<agent>/workspace/scripts/lib")
import heartbeat as hb


def _row(id="row-1", event_type="alert", payload=None, **extra):
    base = {
        "id": id, "source": "test", "event_type": event_type,
        "payload": payload if payload is not None else {}, "severity": "medium",
        "agent": None, "dedupe_key": None, "scheduled_task_id": None,
        "target_task_id": None, "created_at": "2026-07-18T00:00:00",
        "process_after": "2026-07-18T00:00:00",
    }
    base.update(extra)
    return base


class _FakeCursor:
    def __init__(self, fetchall_result=None):
        self._fetchall_result = fetchall_result or []
        self.queries = []

    def execute(self, query, params=None):
        self.queries.append((query, params))

    def fetchall(self):
        return self._fetchall_result

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConn:
    def __init__(self, fetchall_result=None):
        self._cursor = _FakeCursor(fetchall_result)
        self.committed = False
        self.rolled_back = False
        self.closed = False
        self.autocommit = True

    def cursor(self, cursor_factory=None):
        return self._cursor

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


# ----------------------------------------------------------------- claim_pending()
class TestClaimPending:
    def test_returns_claimed_rows(self):
        cur = _FakeCursor([_row("a"), _row("b")])
        rows = hb.claim_pending(cur)
        assert [r["id"] for r in rows] == ["a", "b"]

    def test_single_update_returning_no_select_then_update(self):
        # El bug que arrastraba heartbeat.md: nunca SELECT seguido de UPDATE.
        cur = _FakeCursor([])
        hb.claim_pending(cur)
        assert len(cur.queries) == 1
        query = cur.queries[0][0]
        assert "UPDATE agent_inbox" in query
        assert "RETURNING" in query
        assert "claimed_at = now()" in query


# ----------------------------------------------------------------- resolve_script_path()
class TestResolveScriptPath:
    def test_absolute_path_kept_as_is(self):
        assert hb.resolve_script_path("/opt/scripts/check.py") == "/opt/scripts/check.py"

    def test_relative_path_joined_with_agent_home(self):
        result = hb.resolve_script_path("workspace/scripts/lib/check.py")
        assert result == f"{hb.AGENT_HOME}/workspace/scripts/lib/check.py"


# ----------------------------------------------------------------- run_task_script()
class _FakeCompleted:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _contract_stdout(ok, notify=None):
    return json.dumps({"ok": ok, "notify": notify})


class TestRunTaskScript:
    def test_ok_true_notify_null_is_resolved(self, monkeypatch):
        stdout = _contract_stdout(True, None)
        monkeypatch.setattr(hb.subprocess, "run", lambda *a, **k: _FakeCompleted(0, stdout))
        outcome = hb.run_task_script(_row(payload={"script_path": "/x.py"}))
        assert outcome == {"resolved": True, "ok": True, "notify": None, "error": None}

    def test_exit_code_is_irrelevant_when_contract_is_respected(self, monkeypatch):
        # La fuente de verdad es el JSON, no el exit code -- un script que
        # cumple el contrato pero sale con !=0 por convención de shell no
        # debe tratarse distinto.
        stdout = _contract_stdout(True, None)
        monkeypatch.setattr(hb.subprocess, "run", lambda *a, **k: _FakeCompleted(1, stdout))
        outcome = hb.run_task_script(_row(payload={"script_path": "/x.py"}))
        assert outcome["resolved"] is True

    def test_ok_true_with_ready_message_is_not_resolved(self, monkeypatch):
        stdout = _contract_stdout(True, {"severity": "high", "message": "algo pasó", "context": None})
        monkeypatch.setattr(hb.subprocess, "run", lambda *a, **k: _FakeCompleted(0, stdout))
        outcome = hb.run_task_script(_row(payload={"script_path": "/x.py"}))
        assert outcome["resolved"] is False
        assert outcome["ok"] is True
        assert outcome["notify"]["message"] == "algo pasó"

    def test_ok_true_with_context_only_is_not_resolved(self, monkeypatch):
        stdout = _contract_stdout(True, {"severity": "medium", "message": None, "context": {"n": 3}})
        monkeypatch.setattr(hb.subprocess, "run", lambda *a, **k: _FakeCompleted(0, stdout))
        outcome = hb.run_task_script(_row(payload={"script_path": "/x.py"}))
        assert outcome["resolved"] is False
        assert outcome["notify"]["context"] == {"n": 3}

    def test_ok_false_is_not_resolved(self, monkeypatch):
        stdout = _contract_stdout(False, {"severity": "low", "message": None, "context": "disco al 95%"})
        monkeypatch.setattr(hb.subprocess, "run", lambda *a, **k: _FakeCompleted(0, stdout))
        outcome = hb.run_task_script(_row(payload={"script_path": "/x.py"}))
        assert outcome["resolved"] is False
        assert outcome["ok"] is False
        assert outcome["error"] == "la tarea falló (ok=false)"

    def test_ok_false_without_notify_is_still_valid_contract(self, monkeypatch):
        stdout = _contract_stdout(False, None)
        monkeypatch.setattr(hb.subprocess, "run", lambda *a, **k: _FakeCompleted(0, stdout))
        outcome = hb.run_task_script(_row(payload={"script_path": "/x.py"}))
        assert outcome["resolved"] is False
        assert outcome["ok"] is False

    def test_missing_ok_key_is_contract_violation(self, monkeypatch):
        stdout = json.dumps({"notify": None})
        monkeypatch.setattr(hb.subprocess, "run", lambda *a, **k: _FakeCompleted(0, stdout))
        outcome = hb.run_task_script(_row(payload={"script_path": "/x.py"}))
        assert outcome["ok"] is None
        assert outcome["resolved"] is False
        assert "incumplimiento de contrato" in outcome["error"]

    def test_ok_as_non_boolean_is_contract_violation(self, monkeypatch):
        stdout = json.dumps({"ok": "true", "notify": None})
        monkeypatch.setattr(hb.subprocess, "run", lambda *a, **k: _FakeCompleted(0, stdout))
        outcome = hb.run_task_script(_row(payload={"script_path": "/x.py"}))
        assert outcome["ok"] is None
        assert "incumplimiento de contrato" in outcome["error"]

    def test_empty_stdout_is_contract_violation(self, monkeypatch):
        monkeypatch.setattr(hb.subprocess, "run", lambda *a, **k: _FakeCompleted(0, ""))
        outcome = hb.run_task_script(_row(payload={"script_path": "/x.py"}))
        assert outcome["ok"] is None
        assert outcome["resolved"] is False

    def test_non_json_stdout_is_contract_violation(self, monkeypatch):
        monkeypatch.setattr(hb.subprocess, "run",
                             lambda *a, **k: _FakeCompleted(0, "esto no es json"))
        outcome = hb.run_task_script(_row(payload={"script_path": "/x.py"}))
        assert outcome["ok"] is None
        assert outcome["resolved"] is False

    def test_timeout_is_contract_violation(self, monkeypatch):
        def _raise(*a, **k):
            raise subprocess.TimeoutExpired(cmd="x", timeout=hb.SCRIPT_TIMEOUT_SECONDS)
        monkeypatch.setattr(hb.subprocess, "run", _raise)
        outcome = hb.run_task_script(_row(payload={"script_path": "/x.py"}))
        assert outcome["ok"] is None
        assert "timeout" in outcome["error"]

    def test_missing_script_is_contract_violation(self, monkeypatch):
        def _raise(*a, **k):
            raise FileNotFoundError("no such file")
        monkeypatch.setattr(hb.subprocess, "run", _raise)
        outcome = hb.run_task_script(_row(payload={"script_path": "/missing.py"}))
        assert outcome["ok"] is None
        assert outcome["resolved"] is False


# ----------------------------------------------------------------- is_task_with_script()
class TestIsTaskWithScript:
    def test_task_with_script_path(self):
        assert hb.is_task_with_script(_row(event_type="task", payload={"script_path": "/x.py"}))

    def test_task_without_script_path(self):
        assert not hb.is_task_with_script(_row(event_type="task", payload={}))

    def test_non_task_event_type(self):
        assert not hb.is_task_with_script(_row(event_type="alert", payload={"script_path": "/x.py"}))


# ----------------------------------------------------------------- classify_and_dispatch()
class TestClassifyAndDispatch:
    def test_resolved_task_closed_without_reaching_model(self, monkeypatch):
        monkeypatch.setattr(hb.subprocess, "run",
                             lambda *a, **k: _FakeCompleted(0, _contract_stdout(True, None)))
        cur = _FakeCursor()
        rows = [_row("t1", event_type="task", payload={"script_path": "/x.py"})]

        needs_model = hb.classify_and_dispatch(cur, rows)

        assert needs_model == []
        update_queries = [q for q, p in cur.queries if "UPDATE agent_inbox" in q]
        assert len(update_queries) == 1
        assert cur.queries[0][1] == ("dropped", "t1")

    def test_resolved_task_updates_core_task_when_named(self, monkeypatch):
        monkeypatch.setattr(hb.subprocess, "run",
                             lambda *a, **k: _FakeCompleted(0, _contract_stdout(True, None)))
        cur = _FakeCursor()
        rows = [_row("t1", event_type="task",
                      payload={"script_path": "/x.py", "core_task": "backup-diario"})]

        hb.classify_and_dispatch(cur, rows)

        core_task_queries = [(q, p) for q, p in cur.queries if "core_task" in q]
        assert core_task_queries[0][1] == ("backup-diario",)

    def test_failed_task_script_goes_to_model_with_outcome_attached(self, monkeypatch):
        stdout = _contract_stdout(False, {"severity": "high", "message": None, "context": "disco lleno"})
        monkeypatch.setattr(hb.subprocess, "run", lambda *a, **k: _FakeCompleted(0, stdout))
        cur = _FakeCursor()
        rows = [_row("t1", event_type="task", payload={"script_path": "/x.py"})]

        needs_model = hb.classify_and_dispatch(cur, rows)

        assert len(needs_model) == 1
        assert needs_model[0]["_script_outcome"]["ok"] is False
        # No se cierra en Python -- el modelo decide con el motivo del fallo.
        assert cur.queries == []

    def test_contract_violation_goes_to_model_with_outcome_attached(self, monkeypatch):
        monkeypatch.setattr(hb.subprocess, "run",
                             lambda *a, **k: _FakeCompleted(1, "esto no es json"))
        cur = _FakeCursor()
        rows = [_row("t1", event_type="task", payload={"script_path": "/x.py"})]

        needs_model = hb.classify_and_dispatch(cur, rows)

        assert len(needs_model) == 1
        assert needs_model[0]["_script_outcome"]["ok"] is None
        assert cur.queries == []

    def test_non_deterministic_row_passes_through_untouched(self):
        cur = _FakeCursor()
        rows = [_row("a1", event_type="alert", payload={})]

        needs_model = hb.classify_and_dispatch(cur, rows)

        assert needs_model == rows
        assert cur.queries == []

    def test_mixed_batch_only_unresolved_reach_model(self, monkeypatch):
        monkeypatch.setattr(hb.subprocess, "run",
                             lambda *a, **k: _FakeCompleted(0, _contract_stdout(True, None)))
        cur = _FakeCursor()
        rows = [
            _row("t1", event_type="task", payload={"script_path": "/x.py"}),
            _row("a1", event_type="alert", payload={}),
        ]

        needs_model = hb.classify_and_dispatch(cur, rows)

        assert [r["id"] for r in needs_model] == ["a1"]


# ----------------------------------------------------------------- build_model_prompt()
class TestBuildModelPrompt:
    def test_includes_base_prompt_and_rows_json(self, tmp_path, monkeypatch):
        prompt_file = tmp_path / "heartbeat.md"
        prompt_file.write_text("INSTRUCCIONES BASE")
        monkeypatch.setattr(hb, "PROMPT_FILE", str(prompt_file))
        rows = [_row("a1")]

        text = hb.build_model_prompt(rows)

        assert "INSTRUCCIONES BASE" in text
        assert '"id": "a1"' in text

    def test_serializes_non_json_native_values(self, tmp_path, monkeypatch):
        import uuid
        from datetime import datetime
        prompt_file = tmp_path / "heartbeat.md"
        prompt_file.write_text("x")
        monkeypatch.setattr(hb, "PROMPT_FILE", str(prompt_file))
        rows = [_row(id=uuid.UUID("12345678-1234-5678-1234-567812345678"),
                      created_at=datetime(2026, 7, 18))]

        text = hb.build_model_prompt(rows)  # no debe lanzar TypeError

        assert "12345678-1234-5678-1234-567812345678" in text


# ----------------------------------------------------------------- run_model()
class TestRunModel:
    def test_invokes_claude_with_prompt_as_input(self, monkeypatch, tmp_path):
        prompt_file = tmp_path / "heartbeat.md"
        prompt_file.write_text("base")
        monkeypatch.setattr(hb, "PROMPT_FILE", str(prompt_file))
        captured = {}

        def fake_run(cmd, input, capture_output, timeout, text):
            captured["cmd"] = cmd
            captured["input"] = input
            return _FakeCompleted(0)

        monkeypatch.setattr(hb.subprocess, "run", fake_run)
        result = hb.run_model([_row("a1")])

        assert result == 0
        assert captured["cmd"] == [hb.CLAUDE_BIN, "--print", "--strict-mcp-config"]
        assert "base" in captured["input"]

    def test_model_failure_propagates_returncode(self, monkeypatch, tmp_path):
        prompt_file = tmp_path / "heartbeat.md"
        prompt_file.write_text("base")
        monkeypatch.setattr(hb, "PROMPT_FILE", str(prompt_file))
        monkeypatch.setattr(hb.subprocess, "run",
                             lambda *a, **k: _FakeCompleted(1, stderr="boom"))

        assert hb.run_model([_row("a1")]) == 1

    def test_timeout_returns_one(self, monkeypatch, tmp_path):
        prompt_file = tmp_path / "heartbeat.md"
        prompt_file.write_text("base")
        monkeypatch.setattr(hb, "PROMPT_FILE", str(prompt_file))

        def _raise(*a, **k):
            raise subprocess.TimeoutExpired(cmd="claude", timeout=hb.MODEL_TIMEOUT_SECONDS)

        monkeypatch.setattr(hb.subprocess, "run", _raise)

        assert hb.run_model([_row("a1")]) == 1


# ----------------------------------------------------------------- main()
class TestMain:
    def test_missing_dsn_returns_one_without_connecting(self, monkeypatch):
        monkeypatch.setattr(hb, "DB_DSN", "")
        monkeypatch.setattr(hb.psycopg2, "connect",
                             lambda dsn: pytest.fail("no debería conectar"))

        assert hb.main() == 1

    def test_connection_error_returns_one(self, monkeypatch):
        monkeypatch.setattr(hb, "DB_DSN", "postgresql://fake")

        def _raise(dsn):
            raise Exception("connection refused")

        monkeypatch.setattr(hb.psycopg2, "connect", _raise)

        assert hb.main() == 1

    def test_nothing_claimed_commits_and_returns_zero(self, monkeypatch):
        monkeypatch.setattr(hb, "DB_DSN", "postgresql://fake")
        fake_conn = _FakeConn(fetchall_result=[])
        monkeypatch.setattr(hb.psycopg2, "connect", lambda dsn: fake_conn)
        monkeypatch.setattr(hb, "run_model", lambda rows: pytest.fail("no debería invocar el modelo"))

        assert hb.main() == 0
        assert fake_conn.committed is True

    def test_all_rows_resolved_by_script_does_not_invoke_model(self, monkeypatch):
        monkeypatch.setattr(hb, "DB_DSN", "postgresql://fake")
        fake_conn = _FakeConn(fetchall_result=[_row("t1", event_type="task",
                                                      payload={"script_path": "/x.py"})])
        monkeypatch.setattr(hb.psycopg2, "connect", lambda dsn: fake_conn)
        monkeypatch.setattr(hb.subprocess, "run",
                             lambda *a, **k: _FakeCompleted(0, _contract_stdout(True, None)))
        monkeypatch.setattr(hb, "run_model", lambda rows: pytest.fail("no debería invocar el modelo"))

        assert hb.main() == 0
        assert fake_conn.committed is True

    def test_unresolved_rows_invoke_model(self, monkeypatch):
        monkeypatch.setattr(hb, "DB_DSN", "postgresql://fake")
        fake_conn = _FakeConn(fetchall_result=[_row("a1", event_type="alert")])
        monkeypatch.setattr(hb.psycopg2, "connect", lambda dsn: fake_conn)
        captured = {}

        def fake_run_model(rows):
            captured["rows"] = rows
            return 0

        monkeypatch.setattr(hb, "run_model", fake_run_model)

        assert hb.main() == 0
        assert [r["id"] for r in captured["rows"]] == ["a1"]

    def test_model_failure_return_code_propagates(self, monkeypatch):
        monkeypatch.setattr(hb, "DB_DSN", "postgresql://fake")
        fake_conn = _FakeConn(fetchall_result=[_row("a1", event_type="alert")])
        monkeypatch.setattr(hb.psycopg2, "connect", lambda dsn: fake_conn)
        monkeypatch.setattr(hb, "run_model", lambda rows: 1)

        assert hb.main() == 1

    def test_dispatch_error_rolls_back_and_returns_one(self, monkeypatch):
        monkeypatch.setattr(hb, "DB_DSN", "postgresql://fake")
        fake_conn = _FakeConn(fetchall_result=[_row("a1", event_type="alert")])
        monkeypatch.setattr(hb.psycopg2, "connect", lambda dsn: fake_conn)

        def _raise(cur, rows):
            raise Exception("boom")

        monkeypatch.setattr(hb, "classify_and_dispatch", _raise)

        assert hb.main() == 1
        assert fake_conn.rolled_back is True
        assert fake_conn.closed is True

# tests/test_midnight.py
import json
import sys
from datetime import date, time as dtime

import pytest

sys.path.insert(0, "/home/<agent>/workspace/scripts/lib")
import midnight as mn


def _row(**over):
    base = {
        "id": 1, "kind": "slot", "time_from": dtime(9, 0), "time_to": dtime(10, 0),
        "slot_name": "morning", "is_modifier": False,
        "critical_limit": None, "high_limit": 5, "medium_limit": 10, "low_limit": 20,
        "task_id": None, "task_name": None, "task_kind": None,
        "script_path": None, "prompt_file": None, "task_severity": None,
    }
    base.update(over)
    return base


class _FakeCursor:
    def __init__(self, fetchall_results=None, fetchone_results=None, rowcounts=None):
        self._fetchall_queue = list(fetchall_results or [])
        self._fetchone_queue = list(fetchone_results or [])
        self._rowcount_queue = list(rowcounts or [])
        self.rowcount = 1
        self.queries = []

    def execute(self, query, params=None):
        self.queries.append((query, params))
        if self._rowcount_queue:
            self.rowcount = self._rowcount_queue.pop(0)

    def fetchall(self):
        return self._fetchall_queue.pop(0) if self._fetchall_queue else []

    def fetchone(self):
        return self._fetchone_queue.pop(0) if self._fetchone_queue else None

    def close(self):
        pass


class _FakeConn:
    def __init__(self, fetchall_results=None):
        self._cursor = _FakeCursor(fetchall_results=fetchall_results)
        self.committed = False
        self.closed = False
        self.autocommit = True

    def cursor(self, cursor_factory=None):
        return self._cursor

    def commit(self):
        self.committed = True

    def close(self):
        self.closed = True


# ----------------------------------------------------------------- load_calendar_ids()
class TestLoadCalendarIds:
    def test_missing_file_returns_empty(self, monkeypatch, tmp_path):
        monkeypatch.setattr(mn, "CALENDARS_CONFIG_PATH", str(tmp_path / "no-existe.json"))
        assert mn.load_calendar_ids() == []

    def test_invalid_json_returns_empty(self, monkeypatch, tmp_path):
        cfg = tmp_path / "calendars.json"
        cfg.write_text("{no es json")
        monkeypatch.setattr(mn, "CALENDARS_CONFIG_PATH", str(cfg))
        assert mn.load_calendar_ids() == []

    def test_reads_ids_from_calendars_entries(self, monkeypatch, tmp_path):
        cfg = tmp_path / "calendars.json"
        cfg.write_text(json.dumps({"calendars": [
            {"calendario": "personal", "descripcion": "Personal", "id": "primary"},
            {"calendario": "silvia", "descripcion": "Silvia", "id": "xyz@group.calendar.google.com"},
        ]}))
        monkeypatch.setattr(mn, "CALENDARS_CONFIG_PATH", str(cfg))
        assert mn.load_calendar_ids() == ["primary", "xyz@group.calendar.google.com"]

    def test_skips_entries_without_id(self, monkeypatch, tmp_path):
        cfg = tmp_path / "calendars.json"
        cfg.write_text(json.dumps({"calendars": [{"calendario": "sin_id", "descripcion": "x"}]}))
        monkeypatch.setattr(mn, "CALENDARS_CONFIG_PATH", str(cfg))
        assert mn.load_calendar_ids() == []


# ----------------------------------------------------------------- resolve_calendar_day_type()
class _FakeHTTPResponse:
    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestResolveCalendarDayType:
    def _configure(self, monkeypatch, calendar_ids=("primary",)):
        monkeypatch.setattr(mn, "N8N_CALENDAR_WEBHOOK_URL", "https://n8n.example/webhook/calendar-events")
        monkeypatch.setattr(mn, "N8N_WEBHOOK_SECRET", "s3cr3t")
        monkeypatch.setattr(mn, "load_calendar_ids", lambda: list(calendar_ids))

    def test_returns_none_without_webhook_url(self, monkeypatch):
        monkeypatch.setattr(mn, "N8N_CALENDAR_WEBHOOK_URL", "")
        monkeypatch.setattr(mn, "N8N_WEBHOOK_SECRET", "s3cr3t")
        assert mn.resolve_calendar_day_type(date(2026, 7, 22)) is None

    def test_returns_none_without_secret(self, monkeypatch):
        monkeypatch.setattr(mn, "N8N_CALENDAR_WEBHOOK_URL", "https://n8n.example/webhook/calendar-events")
        monkeypatch.setattr(mn, "N8N_WEBHOOK_SECRET", "")
        assert mn.resolve_calendar_day_type(date(2026, 7, 22)) is None

    def test_returns_none_without_calendars_configured(self, monkeypatch):
        self._configure(monkeypatch, calendar_ids=[])
        assert mn.resolve_calendar_day_type(date(2026, 7, 22)) is None

    def test_returns_day_type_from_webhook_response(self, monkeypatch):
        self._configure(monkeypatch)
        monkeypatch.setattr(
            mn.urllib.request, "urlopen",
            lambda req, timeout=None: _FakeHTTPResponse({"date": "2026-07-22", "day_type": "H", "events": []})
        )
        assert mn.resolve_calendar_day_type(date(2026, 7, 22)) == "H"

    def test_sends_secret_header_and_calendar_ids(self, monkeypatch):
        self._configure(monkeypatch, calendar_ids=["primary", "otro@x.com"])
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["headers"] = req.headers
            captured["body"] = json.loads(req.data.decode("utf-8"))
            captured["timeout"] = timeout
            return _FakeHTTPResponse({"date": "2026-07-22", "day_type": None, "events": []})

        monkeypatch.setattr(mn.urllib.request, "urlopen", fake_urlopen)
        mn.resolve_calendar_day_type(date(2026, 7, 22))

        assert captured["headers"]["X-webhook-secret"] == "s3cr3t"
        assert captured["body"] == {"date": "2026-07-22", "calendar_ids": ["primary", "otro@x.com"]}
        assert captured["timeout"] == mn.CALENDAR_WEBHOOK_TIMEOUT

    def test_returns_none_on_network_error(self, monkeypatch):
        self._configure(monkeypatch)

        def raise_error(req, timeout=None):
            raise mn.urllib.error.URLError("boom")

        monkeypatch.setattr(mn.urllib.request, "urlopen", raise_error)
        assert mn.resolve_calendar_day_type(date(2026, 7, 22)) is None

    def test_returns_none_on_invalid_json_response(self, monkeypatch):
        self._configure(monkeypatch)

        class _BadResponse(_FakeHTTPResponse):
            def read(self):
                return b"not json"

        monkeypatch.setattr(mn.urllib.request, "urlopen", lambda req, timeout=None: _BadResponse({}))
        assert mn.resolve_calendar_day_type(date(2026, 7, 22)) is None

    def test_returns_none_for_unexpected_day_type_value(self, monkeypatch):
        self._configure(monkeypatch)
        monkeypatch.setattr(
            mn.urllib.request, "urlopen",
            lambda req, timeout=None: _FakeHTTPResponse({"date": "2026-07-22", "day_type": "X", "events": []})
        )
        assert mn.resolve_calendar_day_type(date(2026, 7, 22)) is None


# ----------------------------------------------------------------- reconcile_day()
class TestReconcileDay:
    def test_queries_iso_weekday_and_wildcard(self, monkeypatch):
        monkeypatch.setattr(mn, "resolve_calendar_day_type", lambda t: None)
        cur = _FakeCursor(fetchall_results=[[]])
        target = date(2026, 7, 22)  # miércoles -> ISO weekday 3

        mn.reconcile_day(cur, target)

        query, params = cur.queries[0]
        assert set(params[0]) == {"3", "*"}
        assert params[1] == target

    def test_adds_calendar_day_type_when_resolved(self, monkeypatch):
        monkeypatch.setattr(mn, "resolve_calendar_day_type", lambda t: "H")
        cur = _FakeCursor(fetchall_results=[[]])

        mn.reconcile_day(cur, date(2026, 7, 22))

        params = cur.queries[0][1]
        assert set(params[0]) == {"3", "*", "H"}

    def test_dispatches_slot_rows_to_materialize_slot(self, monkeypatch):
        monkeypatch.setattr(mn, "resolve_calendar_day_type", lambda t: None)
        called = []
        monkeypatch.setattr(mn, "materialize_slot",
                             lambda cur, target, r: called.append(r) or True)
        cur = _FakeCursor(fetchall_results=[[_row(kind="slot")]])

        result = mn.reconcile_day(cur, date(2026, 7, 22))

        assert len(called) == 1
        assert result == {"materialized": 1, "enqueued": 0}

    def test_dispatches_task_rows_to_enqueue_scheduled_task(self, monkeypatch):
        monkeypatch.setattr(mn, "resolve_calendar_day_type", lambda t: None)
        called = []
        monkeypatch.setattr(mn, "enqueue_scheduled_task",
                             lambda cur, target, r: called.append(r) or True)
        cur = _FakeCursor(fetchall_results=[[_row(kind="task", task_kind="core")]])

        result = mn.reconcile_day(cur, date(2026, 7, 22))

        assert len(called) == 1
        assert result == {"materialized": 0, "enqueued": 1}

    def test_false_from_handler_not_counted(self, monkeypatch):
        monkeypatch.setattr(mn, "resolve_calendar_day_type", lambda t: None)
        monkeypatch.setattr(mn, "materialize_slot", lambda cur, target, r: False)
        cur = _FakeCursor(fetchall_results=[[_row(kind="slot")]])

        result = mn.reconcile_day(cur, date(2026, 7, 22))

        assert result == {"materialized": 0, "enqueued": 0}

    def test_mixed_batch_counts_each_kind_separately(self, monkeypatch):
        monkeypatch.setattr(mn, "resolve_calendar_day_type", lambda t: None)
        monkeypatch.setattr(mn, "materialize_slot", lambda cur, target, r: True)
        monkeypatch.setattr(mn, "enqueue_scheduled_task", lambda cur, target, r: True)
        cur = _FakeCursor(fetchall_results=[[_row(kind="slot"), _row(kind="task")]])

        result = mn.reconcile_day(cur, date(2026, 7, 22))

        assert result == {"materialized": 1, "enqueued": 1}


# ----------------------------------------------------------------- materialize_slot()
class TestMaterializeSlot:
    def test_valid_window_inserts_and_returns_true(self):
        cur = _FakeCursor(rowcounts=[1])
        r = _row(kind="slot", time_from=dtime(9, 0), time_to=dtime(10, 0))

        assert mn.materialize_slot(cur, date(2026, 7, 22), r) is True
        assert "INSERT INTO daily_schedule" in cur.queries[0][0]

    def test_invalid_window_skipped_without_query(self):
        cur = _FakeCursor()
        r = _row(time_from=dtime(10, 0), time_to=dtime(9, 0))

        assert mn.materialize_slot(cur, date(2026, 7, 22), r) is False
        assert cur.queries == []

    def test_conflict_do_nothing_returns_false(self):
        cur = _FakeCursor(rowcounts=[0])
        r = _row()

        assert mn.materialize_slot(cur, date(2026, 7, 22), r) is False

    def test_db_error_is_caught_and_returns_false(self, monkeypatch):
        cur = _FakeCursor()

        def _raise(*a, **k):
            raise Exception("boom")

        monkeypatch.setattr(cur, "execute", _raise)
        r = _row()

        assert mn.materialize_slot(cur, date(2026, 7, 22), r) is False


# ----------------------------------------------------------------- enqueue_scheduled_task()
class TestEnqueueScheduledTask:
    def _core_row(self, **over):
        base = dict(kind="task", task_id=5, task_name="autoreset", task_kind="core",
                    script_path="workspace/scripts/lib/autoreset.py",
                    time_from=dtime(4, 0), task_severity="low")
        base.update(over)
        return _row(**base)

    def test_skips_if_already_pending(self):
        cur = _FakeCursor(fetchone_results=[(1,)])

        result = mn.enqueue_scheduled_task(cur, date(2026, 7, 22), self._core_row())

        assert result is False
        assert len(cur.queries) == 1  # solo el SELECT de comprobación, sin INSERT

    def test_core_kind_enqueues_task_event_with_script_path(self):
        cur = _FakeCursor(fetchone_results=[None])

        result = mn.enqueue_scheduled_task(cur, date(2026, 7, 22), self._core_row())

        assert result is True
        insert_query, params = cur.queries[1]
        assert "INSERT INTO agent_inbox" in insert_query
        assert params[1] == "task"
        payload = json.loads(params[2])
        assert payload == {"core_task": "autoreset",
                            "script_path": "workspace/scripts/lib/autoreset.py"}
        assert params[5] == 5  # scheduled_task_id

    def test_briefing_kind_enqueues_scheduled_task_event_with_prompt_file(self):
        cur = _FakeCursor(fetchone_results=[None])
        r = _row(kind="task", task_id=8, task_name="briefing-manana", task_kind="briefing",
                 prompt_file="prompts/briefing.md", time_from=dtime(7, 30),
                 task_severity="medium")

        result = mn.enqueue_scheduled_task(cur, date(2026, 7, 22), r)

        assert result is True
        insert_query, params = cur.queries[1]
        assert params[1] == "scheduled_task"
        payload = json.loads(params[2])
        assert payload == {"scheduled_task": "briefing-manana",
                            "prompt_file": "prompts/briefing.md"}

    def test_dedupe_key_includes_task_name_and_date(self):
        cur = _FakeCursor(fetchone_results=[None])

        mn.enqueue_scheduled_task(cur, date(2026, 7, 22), self._core_row())

        params = cur.queries[1][1]
        assert params[4] == "scheduled_task:autoreset:2026-07-22"

    def test_source_prefix_used_for_pending_check(self):
        cur = _FakeCursor(fetchone_results=[None])

        mn.enqueue_scheduled_task(cur, date(2026, 7, 22), self._core_row())

        select_query, select_params = cur.queries[0]
        assert select_params[0] == "scheduled_task:autoreset"


# ----------------------------------------------------------------- main()
class TestMain:
    def test_missing_dsn_exits_1(self, monkeypatch):
        monkeypatch.setattr(mn, "DB_DSN", "")

        with pytest.raises(SystemExit) as exc:
            mn.main()
        assert exc.value.code == 1

    def test_connection_error_exits_1(self, monkeypatch):
        monkeypatch.setattr(mn, "DB_DSN", "postgresql://fake")

        def _raise(dsn):
            raise Exception("connection refused")

        monkeypatch.setattr(mn.psycopg2, "connect", _raise)

        with pytest.raises(SystemExit) as exc:
            mn.main()
        assert exc.value.code == 1

    def test_success_commits_and_closes(self, monkeypatch):
        monkeypatch.setattr(mn, "DB_DSN", "postgresql://fake")
        fake_conn = _FakeConn(fetchall_results=[[]])
        monkeypatch.setattr(mn.psycopg2, "connect", lambda dsn: fake_conn)

        mn.main()  # no debe lanzar SystemExit

        assert fake_conn.committed is True
        assert fake_conn.closed is True

    def test_reconcile_error_exits_1(self, monkeypatch):
        monkeypatch.setattr(mn, "DB_DSN", "postgresql://fake")
        fake_conn = _FakeConn(fetchall_results=[[]])
        monkeypatch.setattr(mn.psycopg2, "connect", lambda dsn: fake_conn)

        def _raise(cur, target):
            raise Exception("boom")

        monkeypatch.setattr(mn, "reconcile_day", _raise)

        with pytest.raises(SystemExit) as exc:
            mn.main()
        assert exc.value.code == 1

# tests/test_inbox_api.py
import http.client
import json
import sys
import threading

import pytest

sys.path.insert(0, "/home/<agent>/workspace/scripts/lib")
import inbox_api as api


@pytest.fixture(autouse=True)
def _isolate_log(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "LOG", str(tmp_path / "inbox_api.log"))


# ----------------------------------------------------------------- load_secrets()
class TestLoadSecrets:
    def test_multi_secret_json(self, monkeypatch):
        monkeypatch.setenv("INBOX_SECRETS", json.dumps({"n8n": "s1", "home_assistant": "s2"}))
        monkeypatch.delenv("INBOX_SECRET", raising=False)
        assert api.load_secrets() == {"n8n": "s1", "home_assistant": "s2"}

    def test_single_secret_fallback(self, monkeypatch):
        monkeypatch.delenv("INBOX_SECRETS", raising=False)
        monkeypatch.setenv("INBOX_SECRET", "unico")
        assert api.load_secrets() == {"webhook": "unico"}

    def test_invalid_json_falls_back_to_single(self, monkeypatch):
        monkeypatch.setenv("INBOX_SECRETS", "{no es json")
        monkeypatch.setenv("INBOX_SECRET", "unico")
        assert api.load_secrets() == {"webhook": "unico"}

    def test_nothing_configured_returns_empty(self, monkeypatch):
        monkeypatch.delenv("INBOX_SECRETS", raising=False)
        monkeypatch.delenv("INBOX_SECRET", raising=False)
        assert api.load_secrets() == {}


# ----------------------------------------------------------------- authenticate()
class TestAuthenticate:
    def test_matches_correct_secret(self):
        assert api.authenticate({"n8n": "s1", "ha": "s2"}, "s2") == "ha"

    def test_no_match_returns_none(self):
        assert api.authenticate({"n8n": "s1"}, "wrong") is None

    def test_empty_presented_returns_none(self):
        assert api.authenticate({"n8n": "s1"}, "") is None

    def test_empty_secrets_table_returns_none(self):
        assert api.authenticate({}, "anything") is None


# ----------------------------------------------------------------- validate_request_body()
class TestValidateRequestBody:
    def test_valid_minimal_body(self):
        clean = api.validate_request_body({"event_type": "alert", "message": "batería baja"})
        assert clean == {"event_type": "alert", "message": "batería baja",
                          "context": {}, "severity": "medium"}

    def test_non_dict_body_rejected(self):
        with pytest.raises(api.ValidationError):
            api.validate_request_body(["no", "es", "un", "objeto"])

    def test_task_event_type_rejected(self):
        with pytest.raises(api.ValidationError):
            api.validate_request_body({"event_type": "task", "message": "x"})

    def test_scheduled_task_event_type_rejected(self):
        with pytest.raises(api.ValidationError):
            api.validate_request_body({"event_type": "scheduled_task", "message": "x"})

    def test_missing_message_rejected(self):
        with pytest.raises(api.ValidationError):
            api.validate_request_body({"event_type": "info"})

    def test_blank_message_rejected(self):
        with pytest.raises(api.ValidationError):
            api.validate_request_body({"event_type": "info", "message": "   "})

    def test_non_dict_context_rejected(self):
        with pytest.raises(api.ValidationError):
            api.validate_request_body({"event_type": "info", "message": "x", "context": "no-dict"})

    def test_invalid_severity_rejected(self):
        with pytest.raises(api.ValidationError):
            api.validate_request_body({"event_type": "info", "message": "x", "severity": "nuclear"})

    @pytest.mark.parametrize("field", ["source", "agent", "dedupe_key",
                                        "process_after", "scheduled_task_id", "script_path"])
    def test_forbidden_fields_rejected(self, field):
        with pytest.raises(api.ValidationError):
            api.validate_request_body({"event_type": "info", "message": "x", field: "lo-que-sea"})

    def test_custom_severity_and_context_pass_through(self):
        clean = api.validate_request_body({
            "event_type": "alert", "message": "x", "severity": "critical",
            "context": {"entity": "sensor.bateria"},
        })
        assert clean["severity"] == "critical"
        assert clean["context"] == {"entity": "sensor.bateria"}


# ----------------------------------------------------------------- servidor end-to-end
class _FakeCursor:
    def __init__(self):
        self.queries = []

    def execute(self, query, params=None):
        self.queries.append((query, params))

    def fetchone(self):
        return None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConn:
    def __init__(self):
        self.cur = _FakeCursor()
        self.committed = False
        self.closed = False
        self.autocommit = True

    def cursor(self, cursor_factory=None):
        return self.cur

    def commit(self):
        self.committed = True

    def close(self):
        self.closed = True


@pytest.fixture
def running_server(monkeypatch):
    monkeypatch.setattr(api, "DB_DSN", "postgresql://fake")
    fake_conn = _FakeConn()
    monkeypatch.setattr(api.psycopg2, "connect", lambda dsn: fake_conn)

    server = api.IdleShutdownServer(("127.0.0.1", 0), api.InboxHandler)
    server.secrets = {"n8n": "topsecret"}
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield port, fake_conn
    finally:
        server.shutdown()
        thread.join(timeout=2)


def _post(port, body_dict, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    body = json.dumps(body_dict).encode("utf-8") if body_dict is not None else b""
    conn.request("POST", "/inbox", body=body, headers=headers or {})
    resp = conn.getresponse()
    data = json.loads(resp.read().decode("utf-8"))
    conn.close()
    return resp.status, data


class TestEndToEnd:
    def test_missing_secret_rejected(self, running_server):
        port, _ = running_server
        status, data = _post(port, {"event_type": "alert", "message": "x"})
        assert status == 401

    def test_wrong_secret_rejected(self, running_server):
        port, _ = running_server
        status, data = _post(port, {"event_type": "alert", "message": "x"},
                              headers={api.SECRET_HEADER: "wrong"})
        assert status == 401

    def test_valid_request_accepted_and_inserted(self, running_server):
        port, fake_conn = running_server
        status, data = _post(
            port, {"event_type": "alert", "message": "batería baja"},
            headers={api.SECRET_HEADER: "topsecret", "Content-Type": "application/json"},
        )
        assert status == 201
        assert data == {"status": "accepted"}
        assert fake_conn.committed is True
        insert_query, params = fake_conn.cur.queries[0]
        assert "INSERT INTO agent_inbox" in insert_query
        assert params[0] == "external:n8n"       # source derivado del label del secreto
        assert params[4] == "any"                # agent forzado

    def test_invalid_payload_rejected_before_touching_db(self, running_server):
        port, fake_conn = running_server
        status, data = _post(port, {"event_type": "task", "message": "x"},
                              headers={api.SECRET_HEADER: "topsecret"})
        assert status == 400
        assert fake_conn.cur.queries == []

    def test_forbidden_field_rejected(self, running_server):
        port, fake_conn = running_server
        status, data = _post(
            port, {"event_type": "alert", "message": "x", "agent": "opus"},
            headers={api.SECRET_HEADER: "topsecret"},
        )
        assert status == 400
        assert fake_conn.cur.queries == []

    def test_unknown_path_returns_404(self, running_server):
        port, _ = running_server
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("POST", "/otra-cosa", body=b"{}",
                     headers={api.SECRET_HEADER: "topsecret"})
        resp = conn.getresponse()
        assert resp.status == 404
        conn.close()

    def test_get_never_exposes_data(self, running_server):
        port, _ = running_server
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/inbox")
        resp = conn.getresponse()
        assert resp.status == 404
        conn.close()

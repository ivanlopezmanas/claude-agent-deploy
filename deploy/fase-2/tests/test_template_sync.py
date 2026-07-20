# tests/test_template_sync.py
import subprocess
import sys

import pytest

sys.path.insert(0, "/home/<agent>/workspace/scripts/lib")
import template_sync as ts


def _git(cwd, *args):
    return subprocess.run(
        ["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True,
    )


def _seed_repo(origin, work):
    subprocess.run(["git", "clone", "-q", str(origin), str(work)], check=True)
    _git(work, "config", "user.email", "t@t.com")
    _git(work, "config", "user.name", "t")
    _git(work, "checkout", "-q", "-b", "main")
    (work / "f.txt").write_text("v1")
    _git(work, "add", "f.txt")
    _git(work, "commit", "-q", "-m", "v1")
    _git(work, "push", "-q", "origin", "main")


def _push_change(seed, message):
    (seed / "f.txt").write_text(message)
    _git(seed, "add", "f.txt")
    _git(seed, "commit", "-q", "-m", message)
    _git(seed, "push", "-q", "origin", "main")


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Origin + template/ clonado, con las rutas de template_sync redirigidas a tmp_path."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    seed = tmp_path / "seed"
    _seed_repo(origin, seed)

    template_dir = tmp_path / "agent_home" / "template"
    subprocess.run(["git", "clone", "-q", str(origin), str(template_dir)], check=True)
    _git(template_dir, "checkout", "-q", "main")

    state_dir = tmp_path / "agent_home" / "workspace" / "state"
    monkeypatch.setattr(ts, "TEMPLATE_DIR", str(template_dir))
    monkeypatch.setattr(ts, "STATE_DIR", str(state_dir))
    monkeypatch.setattr(ts, "STATE_FILE", str(state_dir / "template-sync.json"))
    monkeypatch.setattr(ts, "LOCK_FILE", str(state_dir / "template-sync.lock"))
    monkeypatch.setattr(ts, "LOG", str(tmp_path / "template-sync.log"))

    return {"origin": origin, "seed": seed, "template_dir": template_dir}


# ----------------------------------------------------------------- baseline / sin novedades
class TestBaseline:
    def test_first_run_sets_baseline_without_notify(self, env):
        result = ts.main()
        assert result == {"ok": True, "notify": None}
        state = ts.load_state()
        assert state["last_notified_sha"] is not None

    def test_second_run_no_changes_stays_silent(self, env):
        ts.main()
        assert ts.main() == {"ok": True, "notify": None}


# ----------------------------------------------------------------- contenido nuevo
class TestNewContent:
    def test_new_commit_upstream_triggers_notify_with_raw_log(self, env):
        ts.main()  # baseline
        _push_change(env["seed"], "v2: cambio real")

        result = ts.main()
        assert result["ok"] is True
        assert result["notify"]["severity"] == "low"
        assert result["notify"]["message"] is None
        assert "v2: cambio real" in result["notify"]["context"]

    def test_repeat_after_notify_stays_silent(self, env):
        ts.main()
        _push_change(env["seed"], "v2")
        ts.main()
        assert ts.main() == {"ok": True, "notify": None}

    def test_last_notified_sha_advances_to_origin_head(self, env):
        ts.main()
        _push_change(env["seed"], "v2")
        ts.main()
        state = ts.load_state()
        head = _git(env["seed"], "rev-parse", "HEAD").stdout.strip()
        assert state["last_notified_sha"] == head


# ----------------------------------------------------------------- árbol sucio
class TestDirtyTree:
    def test_dirty_tree_notifies_once_then_silent(self, env):
        ts.main()  # baseline
        (env["template_dir"] / "f.txt").write_text("cambio local sin commitear")

        first = ts.main()
        assert first["ok"] is True
        assert first["notify"]["severity"] == "medium"

        second = ts.main()
        assert second == {"ok": True, "notify": None}

    def test_dirty_tree_never_fetches_or_merges(self, env):
        ts.main()
        (env["template_dir"] / "f.txt").write_text("cambio local")
        _push_change(env["seed"], "no debería adoptarse")

        ts.main()
        state = ts.load_state()
        head = _git(env["seed"], "rev-parse", "HEAD").stdout.strip()
        assert state["last_notified_sha"] != head

    def test_recovers_silently_once_tree_is_clean_again(self, env):
        ts.main()
        (env["template_dir"] / "f.txt").write_text("cambio local")
        ts.main()  # notifica una vez
        _git(env["template_dir"], "checkout", "--", "f.txt")  # limpiar a mano

        result = ts.main()
        assert result == {"ok": True, "notify": None}
        state = ts.load_state()
        assert state["needs_manual_review"] is False
        assert state["manual_review_reason"] is None


# ----------------------------------------------------------------- fast-forward fallido
class TestFastForwardFailure:
    def test_diverged_local_commit_notifies_once_then_silent(self, env):
        ts.main()  # baseline
        _git(env["template_dir"], "config", "user.email", "t@t.com")
        _git(env["template_dir"], "config", "user.name", "t")
        (env["template_dir"] / "f.txt").write_text("commit local divergente")
        _git(env["template_dir"], "add", "f.txt")
        _git(env["template_dir"], "commit", "-q", "-m", "local divergente")
        _push_change(env["seed"], "v3 upstream")

        first = ts.main()
        assert first["ok"] is True
        assert first["notify"]["severity"] == "medium"
        assert "fast-forward" in first["notify"]["context"]

        second = ts.main()
        assert second == {"ok": True, "notify": None}


# ----------------------------------------------------------------- fetch fallando
class TestFetchFailure:
    def _break_remote(self, env):
        broken = env["template_dir"].parent / "nonexistent.git"
        _git(env["template_dir"], "remote", "set-url", "origin", str(broken))

    def test_silent_for_first_days_then_escalates(self, env):
        ts.main()  # baseline
        self._break_remote(env)

        for _ in range(ts.FAILURE_SILENCE_THRESHOLD - 1):
            assert ts.main() == {"ok": True, "notify": None}

        escalated = ts.main()
        assert escalated["ok"] is False
        assert escalated["notify"]["severity"] == "high"

    def test_keeps_escalating_ok_false_while_broken(self, env):
        ts.main()
        self._break_remote(env)
        for _ in range(ts.FAILURE_SILENCE_THRESHOLD):
            ts.main()
        assert ts.main()["ok"] is False

    def test_recovers_silently_once_fetch_succeeds(self, env):
        ts.main()
        self._break_remote(env)
        for _ in range(ts.FAILURE_SILENCE_THRESHOLD):
            ts.main()
        _git(env["template_dir"], "remote", "set-url", "origin", str(env["origin"]))

        result = ts.main()
        assert result == {"ok": True, "notify": None}
        state = ts.load_state()
        assert state["consecutive_fetch_failures"] == 0


# ----------------------------------------------------------------- lock no bloqueante
class TestLock:
    def test_lock_held_returns_silent_noop_without_touching_git(self, env):
        import fcntl
        import os

        os.makedirs(ts.STATE_DIR, exist_ok=True)
        fd = open(ts.LOCK_FILE, "w")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            result = ts.main()
            assert result == {"ok": True, "notify": None}
            # sin baseline: el lock impidió tocar nada, ni siquiera fijar el SHA
            assert ts.load_state()["last_notified_sha"] is None
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            fd.close()


# ----------------------------------------------------------------- ~/template ausente
class TestMissingTemplate:
    def test_missing_template_dir_fails_loudly(self, tmp_path, monkeypatch):
        state_dir = tmp_path / "state"
        monkeypatch.setattr(ts, "TEMPLATE_DIR", str(tmp_path / "no-existe"))
        monkeypatch.setattr(ts, "STATE_DIR", str(state_dir))
        monkeypatch.setattr(ts, "STATE_FILE", str(state_dir / "template-sync.json"))
        monkeypatch.setattr(ts, "LOCK_FILE", str(state_dir / "template-sync.lock"))
        monkeypatch.setattr(ts, "LOG", str(tmp_path / "log"))

        result = ts.main()
        assert result["ok"] is False
        assert result["notify"]["severity"] == "high"


# ----------------------------------------------------------------- contrato de salida
class TestContractShape:
    def test_result_is_always_a_valid_ok_notify_dict(self, env):
        for _ in range(3):
            result = ts.main()
            assert set(result.keys()) == {"ok", "notify"}
            assert isinstance(result["ok"], bool)
            if result["notify"] is not None:
                assert set(result["notify"].keys()) == {"severity", "message", "context"}
                assert result["notify"]["severity"] in ("critical", "high", "medium", "low")

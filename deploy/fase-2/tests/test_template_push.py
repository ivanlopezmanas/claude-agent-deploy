# tests/test_template_push.py
import json
import subprocess
import sys

import pytest

sys.path.insert(0, "/home/<agent>/workspace/scripts/lib")
import template_guard as guard
import template_push as tp
import template_reverse as reverse
import template_sync as ts

PH = tr_ph = reverse.KEY_TO_PLACEHOLDER

IDENTITY = {
    "agent": "orion",
    "Agent": "Orion",
    "AGENT": "ORION",
    "vmid": "142",
    "ip_address": "192.168.1.50",
    "hostname": "ClaudeAgentOrion",
    "owner_name": "Iván López Mañas",
    "profession": "",
    "family": "",
    "tech_level": "",
    "use_cases": "",
    "tone_style": "",
    "language_preference": "",
}

MANIFEST = {
    "rules": [
        {"match": "etc/" + PH["agent"] + "/secrets.env", "classification": "never"},
        {"match": "workspace/state/", "classification": "never"},
        {"match": "claude/CLAUDE.md", "classification": "mixed", "repo_path": "CLAUDE.md"},
        {"match": "workspace/scripts/lib/known.py", "classification": "propagable", "repo_path": "known/known.py"},
        {"match": "workspace/scripts/lib/", "classification": "propagable", "repo_dir": "generic/"},
    ]
}


def _git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True)


@pytest.fixture
def env(tmp_path, monkeypatch):
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)

    template_dir = tmp_path / "agent_home" / "template"
    subprocess.run(["git", "clone", "-q", str(origin), str(template_dir)], check=True)
    _git(template_dir, "config", "user.email", "t@t.com")
    _git(template_dir, "config", "user.name", "t")
    _git(template_dir, "checkout", "-q", "-b", "main")
    (template_dir / "propagation-manifest.json").write_text(json.dumps(MANIFEST))
    _git(template_dir, "add", "-A")
    _git(template_dir, "commit", "-q", "-m", "seed")
    _git(template_dir, "push", "-q", "origin", "main")

    agent_home = tmp_path / "agent_home"
    state_dir = agent_home / "workspace" / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    identity_path = state_dir / "instance-identity.json"
    identity_path.write_text(json.dumps(IDENTITY))

    # Funciones reutilizadas de template_sync.py -- sus constantes viven en
    # su propio módulo, hay que parchearlas ahí (no en el nombre re-importado).
    monkeypatch.setattr(ts, "TEMPLATE_DIR", str(template_dir))
    monkeypatch.setattr(ts, "STATE_DIR", str(state_dir))
    monkeypatch.setattr(ts, "STATE_FILE", str(state_dir / "template-sync.json"))
    monkeypatch.setattr(ts, "LOCK_FILE", str(state_dir / "template-sync.lock"))
    monkeypatch.setattr(ts, "LOG", str(tmp_path / "template-sync.log"))

    # Constantes propias de template_push, ligadas en su propio import.
    monkeypatch.setattr(tp, "AGENT_HOME", str(agent_home))
    monkeypatch.setattr(tp, "TEMPLATE_DIR", str(template_dir))
    monkeypatch.setattr(tp, "MANIFEST_PATH", str(template_dir / "propagation-manifest.json"))
    monkeypatch.setattr(tp, "IDENTITY_PATH", str(identity_path))
    monkeypatch.setattr(tp, "LOG", str(tmp_path / "template-push.log"))

    monkeypatch.setenv("GITHUB_TOKEN", "fake-token-for-tests")

    return {"origin": origin, "template_dir": template_dir, "agent_home": agent_home}


def _write_production_file(env, relpath, content):
    path = env["agent_home"] / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return str(path)


def _fake_remote_push_ops(monkeypatch, env):
    """Redirige _push_branch/_push_main_direct al origin local (bare) en
    vez de a github.com -- sin red, comportamiento real de git."""
    def fake_push_branch(branch, token):
        return subprocess.run(
            ["git", "-C", str(env["template_dir"]), "push", "origin", f"{branch}:{branch}"],
            capture_output=True, text=True,
        )

    def fake_push_main_direct(token):
        return subprocess.run(
            ["git", "-C", str(env["template_dir"]), "push", "origin", "HEAD:main"],
            capture_output=True, text=True,
        )

    def fake_create_pr(branch, title, token):
        return {"pr_url": f"https://example.invalid/pr/{branch}", "compare_url": f"https://example.invalid/compare/{branch}"}

    monkeypatch.setattr(tp, "_push_branch", fake_push_branch)
    monkeypatch.setattr(tp, "_push_main_direct", fake_push_main_direct)
    monkeypatch.setattr(tp, "_create_pr", fake_create_pr)


# ----------------------------------------------------------------- relative_to_home
class TestRelativeToHome:
    def test_absolute_path_under_home(self, env):
        path = str(env["agent_home"] / "workspace" / "scripts" / "lib" / "x.py")
        assert tp.relative_to_home(path) == "workspace/scripts/lib/x.py"

    def test_already_relative_path_passthrough(self, env):
        assert tp.relative_to_home("workspace/scripts/lib/x.py") == "workspace/scripts/lib/x.py"


# ----------------------------------------------------------------- classify()
class TestClassify:
    def test_never_rule_resolves_agent_placeholder(self):
        rule = tp.classify("etc/orion/secrets.env", MANIFEST, IDENTITY)
        assert rule["classification"] == "never"

    def test_mixed_rule_matches_claude_md(self):
        rule = tp.classify("claude/CLAUDE.md", MANIFEST, IDENTITY)
        assert rule["classification"] == "mixed"
        assert rule["repo_path"] == "CLAUDE.md"

    def test_explicit_override_wins_over_generic_fallback(self):
        rule = tp.classify("workspace/scripts/lib/known.py", MANIFEST, IDENTITY)
        assert rule["repo_path"] == "known/known.py"

    def test_generic_fallback_used_for_unknown_script(self):
        rule = tp.classify("workspace/scripts/lib/nuevo.py", MANIFEST, IDENTITY)
        assert rule["classification"] == "propagable"
        assert rule["repo_dir"] == "generic/"

    def test_uncovered_when_nothing_matches(self):
        rule = tp.classify("workspace/random/thing.txt", MANIFEST, IDENTITY)
        assert rule["classification"] == "uncovered"


# ----------------------------------------------------------------- resolve_repo_destination()
class TestResolveRepoDestination:
    def test_repo_path_rule_used_as_is(self):
        rule = {"repo_path": "known/known.py"}
        assert tp.resolve_repo_destination("x", rule, IDENTITY) == "known/known.py"

    def test_repo_dir_rule_reverses_filename(self):
        rule = {"repo_dir": "generic/"}
        dest = tp.resolve_repo_destination("workspace/scripts/lib/orion-thing.py", rule, IDENTITY)
        assert dest == "generic/AGENT-thing.py"


# ----------------------------------------------------------------- build_proposal()
class TestBuildProposal:
    def test_never_path_is_rejected(self, env):
        with pytest.raises(tp.RejectedPath):
            tp.build_proposal(str(env["agent_home"] / "etc/orion/secrets.env"), MANIFEST, IDENTITY)

    def test_uncovered_path_is_rejected(self, env):
        path = _write_production_file(env, "workspace/random/thing.txt", "contenido")
        with pytest.raises(tp.RejectedPath):
            tp.build_proposal(path, MANIFEST, IDENTITY)

    def test_missing_file_is_rejected(self, env):
        path = str(env["agent_home"] / "workspace/scripts/lib/no-existe.py")
        with pytest.raises(tp.RejectedPath):
            tp.build_proposal(path, MANIFEST, IDENTITY)

    def test_propagable_without_existing_template_copy(self, env):
        path = _write_production_file(env, "workspace/scripts/lib/known.py", "Soy Orion.")
        proposal = tp.build_proposal(path, MANIFEST, IDENTITY)
        assert proposal["classification"] == "propagable"
        assert proposal["old_content"] == ""
        assert proposal["changed"] is True
        assert proposal["new_content"] == f"Soy {PH['Agent']}."

    def test_propagable_unchanged_when_identical_to_template(self, env):
        (env["template_dir"] / "known").mkdir(parents=True, exist_ok=True)
        (env["template_dir"] / "known" / "known.py").write_text(f"Soy {PH['Agent']}.")
        path = _write_production_file(env, "workspace/scripts/lib/known.py", "Soy Orion.")
        proposal = tp.build_proposal(path, MANIFEST, IDENTITY)
        assert proposal["changed"] is False

    def test_guard_leak_propagates_as_exception(self, env):
        path = _write_production_file(env, "workspace/scripts/lib/known.py", "token=sk-ant-api03-abcdefghijklmnop")
        with pytest.raises(guard.LeakFound):
            tp.build_proposal(path, MANIFEST, IDENTITY)

    def test_mixed_without_markers_is_rejected(self, env):
        path = _write_production_file(env, "claude/CLAUDE.md", "sin marcadores aquí")
        with pytest.raises(tp.RejectedPath):
            tp.build_proposal(path, MANIFEST, IDENTITY)

    def test_mixed_without_existing_template_copy_is_rejected(self, env):
        path = _write_production_file(
            env, "claude/CLAUDE.md",
            "<!-- TEMPLATE:BEGIN -->\ncontenido\n<!-- TEMPLATE:END -->\n",
        )
        with pytest.raises(tp.RejectedPath):
            tp.build_proposal(path, MANIFEST, IDENTITY)

    def test_mixed_splices_only_marked_sections(self, env):
        (env["template_dir"] / "CLAUDE.md").write_text(
            "cabecera\n<!-- TEMPLATE:BEGIN -->\nviejo\n<!-- TEMPLATE:END -->\nidentidad intacta\n"
        )
        path = _write_production_file(
            env, "claude/CLAUDE.md",
            "cabecera real\n<!-- TEMPLATE:BEGIN -->\nnuevo de Orion\n<!-- TEMPLATE:END -->\nIván López Mañas\n",
        )
        proposal = tp.build_proposal(path, MANIFEST, IDENTITY)
        assert "identidad intacta" in proposal["new_content"]
        assert "Iván López Mañas" not in proposal["new_content"]
        assert PH["Agent"] in proposal["new_content"]


# ----------------------------------------------------------------- cmd_preview()
class TestCmdPreview:
    def test_reports_ok_and_rejected_entries_without_writing_anything(self, env):
        good_path = _write_production_file(env, "workspace/scripts/lib/known.py", "Soy Orion.")
        bad_path = str(env["agent_home"] / "etc/orion/secrets.env")

        result = tp.cmd_preview([good_path, bad_path])
        by_path = {r["path"]: r for r in result["results"]}

        assert by_path[good_path]["ok"] is True
        assert by_path[bad_path]["ok"] is False
        assert not (env["template_dir"] / "known").exists()


# ----------------------------------------------------------------- cmd_apply()
class TestCmdApplyValidation:
    def test_missing_github_token_fails_clearly(self, env, monkeypatch):
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        path = _write_production_file(env, "workspace/scripts/lib/known.py", "Soy Orion.")
        result = tp.cmd_apply([path])
        assert result["ok"] is False
        assert "GITHUB_TOKEN" in result["reason"]

    def test_no_changes_returns_ok_without_pushing(self, env):
        (env["template_dir"] / "known").mkdir(parents=True, exist_ok=True)
        (env["template_dir"] / "known" / "known.py").write_text(f"Soy {PH['Agent']}.")
        path = _write_production_file(env, "workspace/scripts/lib/known.py", "Soy Orion.")
        result = tp.cmd_apply([path])
        assert result == {"ok": True, "pushed": False, "reason": "nada que propagar (sin cambios respecto al template actual)"}

    def test_dirty_template_tree_blocks_apply(self, env):
        (env["template_dir"] / "leftover.txt").write_text("cambio sin commitear")
        path = _write_production_file(env, "workspace/scripts/lib/known.py", "Soy Orion.")
        result = tp.cmd_apply([path])
        assert result["ok"] is False
        assert "sucio" in result["reason"]


class TestCmdApplyPrFlow:
    def test_creates_branch_commits_pushes_and_opens_pr(self, env, monkeypatch):
        _fake_remote_push_ops(monkeypatch, env)
        path = _write_production_file(env, "workspace/scripts/lib/known.py", "Soy Orion.")

        result = tp.cmd_apply([path], use_pr=True)

        assert result["ok"] is True
        assert result["pushed"] is True
        assert result["branch"].startswith("propagate-")
        assert result["pr_url"] is not None
        assert "known/known.py" in result["files"]
        # el working tree vuelve a main al terminar (ver el siguiente test);
        # el contenido escrito solo vive en la rama, se lee con git show.
        show = subprocess.run(
            ["git", "-C", str(env["template_dir"]), "show", f"{result['branch']}:known/known.py"],
            capture_output=True, text=True,
        )
        assert show.stdout == f"Soy {PH['Agent']}."

    def test_returns_to_main_branch_after_pr(self, env, monkeypatch):
        _fake_remote_push_ops(monkeypatch, env)
        path = _write_production_file(env, "workspace/scripts/lib/known.py", "Soy Orion.")
        tp.cmd_apply([path], use_pr=True)
        current_branch = subprocess.run(
            ["git", "-C", str(env["template_dir"]), "branch", "--show-current"],
            capture_output=True, text=True,
        ).stdout.strip()
        assert current_branch == "main"

    def test_pr_flow_never_advances_last_notified_sha(self, env, monkeypatch):
        _fake_remote_push_ops(monkeypatch, env)
        path = _write_production_file(env, "workspace/scripts/lib/known.py", "Soy Orion.")
        tp.cmd_apply([path], use_pr=True)
        state = ts.load_state()
        assert state["last_notified_sha"] is None

    def test_branch_reaches_origin(self, env, monkeypatch):
        _fake_remote_push_ops(monkeypatch, env)
        path = _write_production_file(env, "workspace/scripts/lib/known.py", "Soy Orion.")
        result = tp.cmd_apply([path], use_pr=True)
        branches = subprocess.run(
            ["git", "-C", str(env["origin"]), "branch", "--list", result["branch"]],
            capture_output=True, text=True,
        ).stdout
        assert result["branch"] in branches


class TestCmdApplyDirectFlow:
    def test_pushes_directly_to_main_and_advances_last_notified_sha(self, env, monkeypatch):
        _fake_remote_push_ops(monkeypatch, env)
        path = _write_production_file(env, "workspace/scripts/lib/known.py", "Soy Orion.")

        result = tp.cmd_apply([path], use_pr=False)

        assert result["ok"] is True
        assert result["branch"] is None
        state = ts.load_state()
        assert state["last_notified_sha"] == result["commit"]

    def test_direct_push_lands_on_main_at_origin(self, env, monkeypatch):
        _fake_remote_push_ops(monkeypatch, env)
        path = _write_production_file(env, "workspace/scripts/lib/known.py", "Soy Orion.")
        result = tp.cmd_apply([path], use_pr=False)
        origin_main_sha = subprocess.run(
            ["git", "-C", str(env["origin"]), "rev-parse", "main"],
            capture_output=True, text=True,
        ).stdout.strip()
        assert origin_main_sha == result["commit"]


class TestCmdApplyGuardSecondPass:
    def test_leak_in_staged_diff_aborts_and_cleans_up_branch(self, env, monkeypatch):
        _fake_remote_push_ops(monkeypatch, env)
        # El contenido reversado en sí no tiene fuga, pero simulamos que el
        # guard de la segunda pasada (sobre el diff en staging) sí encuentra
        # algo -- p.ej. algo que quedó mal reversado en otro fichero
        # tocado a la vez. Forzamos el escenario monkeypacheando guard.check
        # para que falle solo en la segunda llamada (diff en vez de contenido).
        calls = {"n": 0}
        real_check = guard.check

        def flaky_check(text, identity):
            calls["n"] += 1
            if calls["n"] > 1:  # primera pasada ok, segunda (diff staged) falla
                raise guard.LeakFound(["fuga simulada en el diff"])
            return real_check(text, identity)

        monkeypatch.setattr(tp.guard, "check", flaky_check)
        path = _write_production_file(env, "workspace/scripts/lib/known.py", "Soy Orion.")

        result = tp.cmd_apply([path], use_pr=True)

        assert result["ok"] is False
        assert "fuga simulada" in result["reason"]
        branches = subprocess.run(
            ["git", "-C", str(env["template_dir"]), "branch", "--list", "propagate-*"],
            capture_output=True, text=True,
        ).stdout
        assert branches.strip() == ""
        current_branch = subprocess.run(
            ["git", "-C", str(env["template_dir"]), "branch", "--show-current"],
            capture_output=True, text=True,
        ).stdout.strip()
        assert current_branch == "main"


class TestCmdApplyLock:
    def test_lock_held_blocks_apply_without_touching_anything(self, env, monkeypatch):
        import fcntl
        import os as os_module

        os_module.makedirs(ts.STATE_DIR, exist_ok=True)
        fd = open(ts.LOCK_FILE, "w")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            path = _write_production_file(env, "workspace/scripts/lib/known.py", "Soy Orion.")
            result = tp.cmd_apply([path])
            assert result["ok"] is False
            assert "lock" in result["reason"]
            assert not (env["template_dir"] / "known").exists()
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            fd.close()

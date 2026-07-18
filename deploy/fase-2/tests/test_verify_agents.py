# tests/test_verify_agents.py
import json
import sys

import pytest

sys.path.insert(0, "/home/<agent>/workspace/scripts/lib")
import verify_agents as va


AGENT_MD = """---
name: {name}
description: >
  Agente de prueba.
model: claude-sonnet-5
tools:
{tools}
---

# {name}
"""


def _write_agent(agents_dir, name, tools, extra_body=""):
    bullets = "\n".join(f"  - {t}" for t in tools)
    (agents_dir / f"{name}.md").write_text(AGENT_MD.format(name=name, tools=bullets) + extra_body)


def _write_skill(skills_dir, slug, body):
    skill_dir = skills_dir / slug
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(body)


@pytest.fixture
def wired(tmp_path, monkeypatch):
    """Arbol agents/skills/permissions vacío, listo para poblar por test, con los
    paths del módulo redirigidos vía monkeypatch (nunca toca el filesystem real)."""
    agents_dir = tmp_path / "agents"
    skills_dir = tmp_path / "skills"
    agents_dir.mkdir()
    skills_dir.mkdir()
    permissions_file = tmp_path / "agent-permissions.json"

    monkeypatch.setattr(va, "AGENTS_DIR", agents_dir)
    monkeypatch.setattr(va, "SKILLS_DIR", skills_dir)
    monkeypatch.setattr(va, "PERMISSIONS_FILE", permissions_file)

    def _permissions(agents=None):
        permissions_file.write_text(json.dumps({"defaults": {"allow": []}, "agents": agents or {}}))

    return {"agents_dir": agents_dir, "skills_dir": skills_dir, "permissions_file": permissions_file,
            "set_permissions": _permissions}


class TestCleanState:
    def test_agent_with_matching_entry_and_covered_tools(self, wired, capsys):
        _write_agent(wired["agents_dir"], "the-scribe", ["Read", "mcp__postgres__query_data"])
        wired["set_permissions"]({"the-scribe": {"allow": ["Read", "mcp__postgres__query_data"]}})

        assert va.main() == 0
        out = capsys.readouterr().out
        assert "Todo consistente" in out

    def test_no_agents_no_skills_is_clean(self, wired):
        wired["set_permissions"]()
        assert va.main() == 0


class TestMissingPermissionsEntry:
    def test_agent_without_entry_is_error(self, wired, capsys):
        _write_agent(wired["agents_dir"], "council-warden", ["Read"])
        wired["set_permissions"]()  # sin 'council-warden'

        assert va.main() == 1
        out = capsys.readouterr().out
        assert "council-warden" in out
        assert "sin entrada en agent-permissions.json" in out


class TestSubagentTypeReferences:
    def test_reference_to_nonexistent_agent_file_is_error(self, wired, capsys):
        _write_agent(wired["agents_dir"], "orchestrator", ["Agent"],
                      extra_body='\nsubagent_type="ghost-worker"\n')
        wired["set_permissions"]({"orchestrator": {"allow": ["Agent"]}})

        assert va.main() == 1
        out = capsys.readouterr().out
        assert "ghost-worker" in out
        assert "no existe agents/ghost-worker.md" in out

    def test_reference_to_agent_without_permissions_entry_is_error(self, wired, capsys):
        # Reproduce el near-miss real: council-of-elders referencia council-warden,
        # pero a council-warden se le olvida la entrada en agent-permissions.json.
        _write_agent(wired["agents_dir"], "council-of-elders", ["Agent"],
                      extra_body='\nsubagent_type="council-warden"\n')
        _write_agent(wired["agents_dir"], "council-warden", ["Read"])
        wired["set_permissions"]({"council-of-elders": {"allow": ["Agent"]}})  # sin 'council-warden'

        assert va.main() == 1
        out = capsys.readouterr().out
        assert "council-warden" in out and "no tiene entrada en agent-permissions.json" in out

    def test_reference_from_skill_file_is_checked(self, wired, capsys):
        _write_agent(wired["agents_dir"], "the-scribe", ["Read"])
        _write_skill(wired["skills_dir"], "the-scribe", 'subagent_type="the-scribe",')
        wired["set_permissions"]()  # sin 'the-scribe'

        assert va.main() == 1
        out = capsys.readouterr().out
        assert "the-scribe" in out


class TestUncoveredTools:
    def test_tool_without_matching_rule_is_warning_not_error(self, wired, capsys):
        _write_agent(wired["agents_dir"], "the-seeker", ["Agent", "Read", "Write"])
        wired["set_permissions"]({"the-seeker": {"allow": ["Agent", "Read"]}})  # falta Write

        assert va.main() == 0  # aviso, no error -- no bloquea
        out = capsys.readouterr().out
        assert "aviso" in out.lower()
        assert "Write" in out

    def test_pattern_rules_match_by_bare_tool_name(self, wired, capsys):
        _write_agent(wired["agents_dir"], "the-chronicler", ["Write"])
        wired["set_permissions"]({"the-chronicler": {"allow": ["Write(/tmp/<agent>-informe-*)"]}})

        assert va.main() == 0
        out = capsys.readouterr().out
        assert "aviso" not in out.lower()


class TestOrphanedPermissionsEntry:
    def test_entry_without_agent_file_is_warning(self, wired, capsys):
        wired["set_permissions"]({"ghost-agent": {"allow": ["Read"]}})

        assert va.main() == 0
        out = capsys.readouterr().out
        assert "ghost-agent" in out and "aviso" in out.lower()


class TestMalformedPermissionsFile:
    def test_invalid_json_is_error(self, wired, capsys):
        wired["permissions_file"].write_text("{not valid json")

        assert va.main() == 1
        out = capsys.readouterr().out
        assert "no es JSON válido" in out

    def test_missing_file_is_error(self, wired, capsys):
        wired["permissions_file"].unlink(missing_ok=True) if wired["permissions_file"].exists() else None

        assert va.main() == 1
        out = capsys.readouterr().out
        assert "No existe" in out

    def test_missing_required_keys_is_error(self, wired, capsys):
        wired["permissions_file"].write_text(json.dumps({"agents": {}}))  # falta 'defaults'

        assert va.main() == 1
        out = capsys.readouterr().out
        assert "faltan las claves" in out

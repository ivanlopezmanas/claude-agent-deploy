# tests/test_retrofit_template.py
import os
import subprocess
from pathlib import Path

import pytest

SCRIPT_SRC = "/home/<agent>/workspace/scripts/lib/retrofit-template.sh"


@pytest.fixture
def script(tmp_path):
    """Copia retrofit-template.sh apuntando REPO_URL a un origin local, sin depender de red."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)

    dst = tmp_path / "retrofit-template.sh"
    content = Path(SCRIPT_SRC).read_text()
    content = content.replace(
        "https://github.com/ivanlopezmanas/claude-agent-deploy.git", str(origin)
    )
    dst.write_text(content)
    dst.chmod(0o755)
    return dst, origin


def run(script_path, agent_home):
    return subprocess.run(
        ["bash", str(script_path), str(agent_home)], capture_output=True, text=True,
    )


class TestRetrofitTemplate:
    def test_clones_when_missing(self, script, tmp_path):
        script_path, _origin = script
        agent_home = tmp_path / "agent_home"

        result = run(script_path, agent_home)

        assert result.returncode == 0
        assert (agent_home / "template" / ".git").is_dir()

    def test_idempotent_second_run_skips_without_error(self, script, tmp_path):
        script_path, _origin = script
        agent_home = tmp_path / "agent_home"
        run(script_path, agent_home)

        second = run(script_path, agent_home)

        assert second.returncode == 0
        assert "SKIP" in second.stdout

    def test_non_git_directory_fails_without_touching_it(self, script, tmp_path):
        script_path, _origin = script
        agent_home = tmp_path / "agent_home"
        template = agent_home / "template"
        template.mkdir(parents=True)
        (template / "archivo.txt").write_text("contenido preexistente")

        result = run(script_path, agent_home)

        assert result.returncode == 1
        assert (template / "archivo.txt").read_text() == "contenido preexistente"

    def test_defaults_to_home_when_no_argument_given(self, script, tmp_path):
        script_path, _origin = script
        agent_home = tmp_path / "agent_home"
        agent_home.mkdir()

        result = subprocess.run(
            ["bash", str(script_path)],
            capture_output=True, text=True,
            env={**os.environ, "HOME": str(agent_home)},
        )

        assert result.returncode == 0
        assert (agent_home / "template" / ".git").is_dir()

import json, subprocess, sys, os
import pytest

HOOK_DIR = "/home/<agent>/workspace/scripts/hooks"   # los scripts de hook viven aquí (ver tabla §3)


@pytest.fixture
def run_hook():
    """Ejecuta un hook como subproceso, inyecta JSON por stdin, devuelve (exit, stdout_parsed)."""
    def _run(script, payload, env=None):
        full_env = {**os.environ, **(env or {})}
        proc = subprocess.run(
            [sys.executable, f"{HOOK_DIR}/{script}"],
            input=json.dumps(payload), capture_output=True, text=True, env=full_env, timeout=5,
        )
        out = None
        if proc.stdout.strip():
            try:
                out = json.loads(proc.stdout)
            except json.JSONDecodeError:
                out = proc.stdout
        return proc.returncode, out, proc.stderr
    return _run


@pytest.fixture(autouse=True)
def isolate_tmp(tmp_path, monkeypatch):
    """Redirige los ficheros /tmp/<agent>-* a un tmp aislado por test."""
    monkeypatch.setenv("<AGENT>_TMP_OVERRIDE", str(tmp_path))   # <agent>_common lee este override en tests
    yield


@pytest.fixture
def main_ctx(monkeypatch):
    monkeypatch.setenv("<AGENT>_CONTEXT", "main")


@pytest.fixture
def subagent_ctx(monkeypatch):
    monkeypatch.setenv("<AGENT>_CONTEXT", "subagent")

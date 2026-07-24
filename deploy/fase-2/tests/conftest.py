import json, subprocess, sys, os
import pytest

HOOK_DIR = "/home/<agent>/workspace/scripts/hooks"   # los scripts de hook viven aquí (ver tabla §3)

_SHIM = "#!/bin/sh\necho \"[TEST-SHIM] $0 $*\" >> \"${TEST_SHIM_LOG:-/dev/null}\"\nexit 0\n"
_GUARDED_BINS = ("systemctl", "sudo", "systemd-run")


@pytest.fixture(autouse=True)
def guard_service_restarts(tmp_path, monkeypatch):
    """Ningún test puede tocar systemctl/sudo/systemd-run de verdad.

    Los hooks se ejecutan como subproceso real (ver run_hook) y heredan os.environ,
    así que un hook sin mockear podría reiniciar el servicio de producción de
    verdad (ya ha pasado). Los shims van delante en el PATH y no hacen nada.
    """
    fake_bin = tmp_path / "fake_bin"
    fake_bin.mkdir()
    for name in _GUARDED_BINS:
        shim = fake_bin / name
        shim.write_text(_SHIM)
        shim.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ.get('PATH', '')}")
    monkeypatch.setenv("TEST_SHIM_LOG", str(tmp_path / "shim.log"))
    # Sin credenciales, _tg_send() devuelve None sin hacer red — ningún hook
    # puede mandar mensajes reales a Telegram durante un test.
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    yield


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

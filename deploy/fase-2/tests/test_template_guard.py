# tests/test_template_guard.py
import sys

import pytest

sys.path.insert(0, "/home/<agent>/workspace/scripts/lib")
import template_guard as tg
import template_reverse as tr

# Ver el mismo aviso en test_template_reverse.py: nunca escribir el
# placeholder literal entre corchetes angulares en este fichero.
PH = tr.KEY_TO_PLACEHOLDER

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


class TestScanIdentityValues:
    def test_detects_raw_agent_name(self):
        findings = tg.scan_identity_values("hola desde orion", IDENTITY)
        assert any("agent" in f for f in findings)

    def test_detects_owner_name_with_accents(self):
        findings = tg.scan_identity_values("propietario: Iván López Mañas", IDENTITY)
        assert any("owner_name" in f for f in findings)

    def test_detects_path_with_real_agent_name(self):
        findings = tg.scan_identity_values("ruta: /home/orion/workspace", IDENTITY)
        assert findings

    def test_ignores_empty_identity_values(self):
        findings = tg.scan_identity_values("texto sin nada relevante", IDENTITY)
        assert findings == []

    def test_clean_agnostic_text_returns_no_findings(self):
        text = f"Vivo en {PH['hostname']}, soy {PH['Agent']}."
        findings = tg.scan_identity_values(text, IDENTITY)
        assert findings == []

    def test_multiple_identity_values_each_reported(self):
        findings = tg.scan_identity_values("orion en vmid 142", IDENTITY)
        joined = " ".join(findings)
        assert "agent" in joined and "vmid" in joined


class TestScanSecretPatterns:
    def test_detects_anthropic_api_key(self):
        findings = tg.scan_secret_patterns("token=sk-ant-api03-abcdefghijklmnopqrstuvwxyz")
        assert findings

    def test_detects_classic_github_token(self):
        findings = tg.scan_secret_patterns("GITHUB_TOKEN=ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
        assert findings

    def test_detects_fine_grained_github_token(self):
        findings = tg.scan_secret_patterns("token: github_pat_" + "a" * 30)
        assert findings

    def test_detects_postgres_connection_string_with_credentials(self):
        findings = tg.scan_secret_patterns("postgresql://orion:hunter2@localhost:5432/agents")
        assert findings

    def test_detects_private_key_header(self):
        findings = tg.scan_secret_patterns("-----BEGIN RSA PRIVATE KEY-----\nMIIEow...")
        assert findings

    def test_detects_postgres_connection_string_env_assignment(self):
        findings = tg.scan_secret_patterns("POSTGRES_CONNECTION_STRING=postgresql://x:y@localhost/agents")
        assert findings

    def test_detects_telegram_bot_token(self):
        findings = tg.scan_secret_patterns("123456789:AAHk9xL3mZpQvR2wYtN8sJcFbGdEiKoLmPq")
        assert findings

    def test_clean_text_returns_no_findings(self):
        findings = tg.scan_secret_patterns("nada sensible en este texto, solo prosa normal")
        assert findings == []

    def test_agnostic_placeholder_is_not_a_false_positive(self):
        findings = tg.scan_secret_patterns("POSTGRES_CONNECTION_STRING=<postgres_dsn>")
        # sigue siendo un match de forma (tiene valor tras el =) -- deliberado,
        # cualquier valor tras el = se trata como posible secreto sin más contexto
        assert findings


class TestScanCombined:
    def test_combines_both_categories(self):
        text = "agente orion, token=sk-ant-api03-abcdefghijklmnop"
        findings = tg.scan(text, IDENTITY)
        joined = " ".join(findings)
        assert "identidad" in joined
        assert "secreto" in joined

    def test_clean_text_returns_empty_list(self):
        assert tg.scan("todo limpio, sin nada raro", IDENTITY) == []


class TestCheck:
    def test_raises_leak_found_with_findings(self):
        with pytest.raises(tg.LeakFound) as exc_info:
            tg.check("hola desde orion", IDENTITY)
        assert exc_info.value.findings

    def test_does_not_raise_on_clean_text(self):
        tg.check("nada que ver aquí", IDENTITY)  # no debe lanzar

    def test_realistic_leak_scenario_multiple_findings(self):
        text = (
            "# configuración de orion\n"
            "ruta: /home/orion/workspace\n"
            "POSTGRES_CONNECTION_STRING=postgresql://orion:hunter2@localhost:5432/agents\n"
        )
        with pytest.raises(tg.LeakFound) as exc_info:
            tg.check(text, IDENTITY)
        assert len(exc_info.value.findings) >= 2

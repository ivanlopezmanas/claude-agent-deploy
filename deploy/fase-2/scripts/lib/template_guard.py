#!/usr/bin/env python3
# chmod +x /home/<agent>/workspace/scripts/lib/template_guard.py
"""template_guard.py — escáner anti-fuga determinista (fase 2, push).

Se invoca DOS veces desde el orquestador de push (`template_push.py`):
1. Sobre la salida ya reversada, antes de escribirla en `~/template/`.
2. Sobre `git diff --staged`, justo antes del `git push`.

Bloqueo duro, no warning: si algo coincide, el orquestador para ahí, no
sigue "avisando pero continuando". Nunca hay medias tintas con secretos.

Dos categorías de patrones:
- Valores de `instance-identity.json` en crudo: el guard no adivina qué es
  sensible, lee el mapa de identidad de la instancia y prohíbe que
  cualquiera de sus valores no vacíos aparezca literal en el texto. Es la
  red que caza lo que `template_reverse.py` debió revertir y no revirtió.
- Patrones de secretos/tokens por FORMA, independientes de la identidad de
  esta instancia concreta (claves de API, cadenas de conexión, claves
  privadas, tokens de bot de Telegram).

Tradeoff deliberado: los valores de identidad se buscan como substring
literal, sin umbral de longitud mínima. Un `vmid` de pocos dígitos puede
dar algún falso positivo sobre un número no relacionado -- se acepta a
propósito: en un guardia anti-fuga, sobre-bloquear (revisar a mano una vez
más) es mucho más barato que dejar pasar una fuga real.

Deliberadamente fuera de esta versión (ver revisión de Opus): heurística de
entropía alta como red final. Añade ruido/falsos positivos (hashes de
tests, etc.) sin un caso concreto que lo justifique todavía.

Script standalone, stdlib only.
"""
import re


class LeakFound(Exception):
    def __init__(self, findings):
        self.findings = list(findings)
        super().__init__("; ".join(self.findings))


_SECRET_PATTERNS = [
    ("clave de API de Anthropic", re.compile(r"sk-ant-[A-Za-z0-9_-]{10,}")),
    ("token clásico de GitHub", re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}")),
    ("token de GitHub de grano fino", re.compile(r"github_pat_[A-Za-z0-9_]{20,}")),
    ("cadena de conexión con credenciales", re.compile(r"postgresql(\+\w+)?://[^:\s]+:[^@\s]+@\S+")),
    ("clave privada", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("POSTGRES_CONNECTION_STRING con valor", re.compile(r"POSTGRES_CONNECTION_STRING\s*=\s*\S+")),
    ("bot token de Telegram", re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b")),
]


def scan_identity_values(text: str, identity: dict) -> list:
    """Valores no vacíos de instance-identity.json presentes tal cual en
    el texto. No distingue mayúsculas/minúsculas más allá de lo que ya
    haga el propio valor -- coincidencia exacta, como en template_reverse."""
    findings = []
    for key, value in identity.items():
        if value and value in text:
            findings.append(f"valor de identidad '{key}' presente en el texto")
    return findings


def scan_secret_patterns(text: str) -> list:
    """Patrones de secretos por forma, sin depender de esta instancia."""
    findings = []
    for name, pattern in _SECRET_PATTERNS:
        if pattern.search(text):
            findings.append(f"patrón de secreto detectado: {name}")
    return findings


def scan(text: str, identity: dict) -> list:
    """Todos los hallazgos, vacío si el texto está limpio. No lanza --
    para eso está check()."""
    return scan_identity_values(text, identity) + scan_secret_patterns(text)


def check(text: str, identity: dict) -> None:
    """Como scan(), pero lanza LeakFound si hay algo. Uso normal desde el
    orquestador: quiere fallar duro, no decidir caso a caso en cada
    llamada."""
    findings = scan(text, identity)
    if findings:
        raise LeakFound(findings)

#!/usr/bin/env python3
# chmod +x /home/<agent>/workspace/scripts/lib/template_reverse.py
"""template_reverse.py — motor puro de traducción real -> agnóstico (fase 2, push).

Sin modelo de por medio: dado el contenido de un fichero de producción y el
mapa de identidad de la instancia (`instance-identity.json`), reconstruye la
forma agnóstica con los placeholders del template (agent/Agent/AGENT/vmid/...
entre corchetes angulares -- ver KEY_TO_PLACEHOLDER). Determinista y
auditable -- la traducción es una función pura, el resultado es siempre el
mismo diff para la misma entrada. El modelo solo decide QUÉ propagar y
revisa el diff resultante; nunca reescribe contenido.

Reglas (ver revisión de Opus, sesión de diseño de fase 2):
- Sustituir por LONGITUD DE VALOR DESCENDENTE, nunca por orden de
  declaración del placeholder: un valor corto que es substring de uno largo
  (p.ej. "Orion" dentro del hostname "ClaudeAgentOrion") tiene que
  reemplazarse DESPUÉS del largo, o corrompe la coincidencia larga a medias
  (dejaría "ClaudeAgent" + placeholder-de-Agent en vez del placeholder de
  hostname completo).
- Case-sensitive, exacto. Nunca case-insensitive (colisionaría Orion/orion/
  ORION, que mapean a placeholders distintos).
- Contenido y nombre de fichero usan reglas DISTINTAS: en contenido el
  placeholder de agent lleva corchetes angulares; en nombres de fichero el
  instalador usa el literal "AGENT" (sin corchetes, para sobrevivir al
  rename en GitHub) -- la reversa del nombre de fichero tiene que hacer lo
  mismo.
- Ficheros "mixtos" (identidad y contenido agnóstico entremezclados en el
  mismo fichero, como CLAUDE.md) NUNCA se reversan enteros. Solo lo que
  está explícitamente delimitado por los marcadores de SECTION_BEGIN/
  SECTION_END se considera propagable; el resto ni se toca ni se lee para
  este propósito.

Nota para quien edite este fichero: los placeholders con corchetes
angulares (el de agent, Agent, AGENT, vmid...) no pueden escribirse tal
cual en ningún comentario o docstring de este módulo -- ver el porqué
justo antes de KEY_TO_PLACEHOLDER.

Script standalone, stdlib only.
"""
import json
import re

# IMPORTANTE: los valores de este mapa NO pueden escribirse como el
# placeholder literal (corchete-angular + "agent" + corchete-angular) en
# este fichero. install-agent.sh (prepare_deploy_tmp) hace un sed GLOBAL de
# esa cadena exacta sobre todo el árbol al desplegar -- si el placeholder
# apareciera aquí tal cual, se sustituiría por el nombre real del agente al
# desplegar este mismo script, y el motor de reversa dejaría de reconocer
# sus propios placeholders en cualquier instancia ya desplegada. Se
# construyen con chr()/concatenación para que esa cadena contigua nunca
# exista en el texto del fichero.
_LT, _GT = chr(60), chr(62)


def _placeholder(key: str) -> str:
    return _LT + key + _GT


# El orden de declaración no importa -- build_substitution_pairs reordena
# por longitud de valor antes de aplicar nada.
KEY_TO_PLACEHOLDER = {
    key: _placeholder(key)
    for key in (
        "agent", "Agent", "AGENT", "vmid", "ip_address", "hostname",
        "owner_name", "profession", "family", "tech_level", "use_cases",
        "tone_style", "language_preference",
    )
}

SECTION_BEGIN = "<!-- TEMPLATE:BEGIN -->"
SECTION_END = "<!-- TEMPLATE:END -->"
_SECTION_RE = re.compile(re.escape(SECTION_BEGIN) + r"(.*?)" + re.escape(SECTION_END), re.DOTALL)


class IdentityLeakError(ValueError):
    """La reversa dejó un valor de identidad presente en el resultado."""


class MarkerMismatchError(ValueError):
    """El número de marcadores no coincide entre plantilla y secciones nuevas."""


def load_identity(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def build_substitution_pairs(identity: dict) -> list:
    """[(valor, placeholder), ...] ordenados por longitud de valor
    descendente. Valores vacíos (placeholders que el onboarding aún no ha
    rellenado) se omiten -- sustituir '' rompe str.replace (inserta el
    placeholder entre cada carácter del texto)."""
    pairs = []
    for key, placeholder in KEY_TO_PLACEHOLDER.items():
        value = identity.get(key, "")
        if value:
            pairs.append((value, placeholder))
    pairs.sort(key=lambda p: len(p[0]), reverse=True)
    return pairs


def reverse_content(text: str, identity: dict) -> str:
    """Sustituye cada valor real por su placeholder, exacto y
    case-sensitive, en orden de longitud de valor descendente."""
    for value, placeholder in build_substitution_pairs(identity):
        text = text.replace(value, placeholder)
    return text


def reverse_filename(name: str, identity: dict) -> str:
    """Los nombres de fichero usan el literal 'AGENT' (sin corchetes), no
    el placeholder con corchetes de contenido -- es la convención del
    instalador para sobrevivir al rename de GitHub. Solo aplica al valor
    de 'agent' (nombre real, minúsculas)."""
    agent_value = identity.get("agent", "")
    if agent_value:
        name = name.replace(agent_value, "AGENT")
    return name


def has_marked_sections(text: str) -> bool:
    return bool(_SECTION_RE.search(text))


def extract_marked_sections(text: str) -> list:
    """Contenido interior (sin los propios marcadores) de cada bloque
    TEMPLATE:BEGIN/END, en orden de aparición."""
    return [m.group(1) for m in _SECTION_RE.finditer(text)]


def splice_marked_sections(template_text: str, new_sections: list) -> str:
    """Sustituye el contenido interior de cada bloque marcado en
    template_text, en orden, por new_sections. El número de marcadores en
    template_text y de secciones nuevas debe coincidir -- si no, alguien
    añadió o quitó un marcador en un lado sin el otro, y eso se resuelve a
    mano, nunca a ciegas."""
    existing = list(_SECTION_RE.finditer(template_text))
    if len(existing) != len(new_sections):
        raise MarkerMismatchError(
            f"desajuste de marcadores: la plantilla tiene {len(existing)}, "
            f"se aportan {len(new_sections)} -- revisar a mano"
        )
    result = []
    cursor = 0
    for match, new_section in zip(existing, new_sections):
        result.append(template_text[cursor:match.start(1)])
        result.append(new_section)
        cursor = match.end(1)
    result.append(template_text[cursor:])
    return "".join(result)


def reverse_marked_sections(production_text: str, identity: dict) -> list:
    """Extrae las secciones marcadas del fichero de producción (mixto) y
    las revierte una a una. Es lo único que se propaga de un fichero
    mixto; el resto del fichero nunca se lee para este propósito."""
    return [reverse_content(section, identity) for section in extract_marked_sections(production_text)]


def assert_no_leftover_identity(text: str, identity: dict, exclude_keys=()) -> None:
    """Comprobación de cordura tras reversar: ningún valor de identidad no
    vacío debería seguir apareciendo en el resultado. Es una red adicional
    a template_guard.py -- si el motor puede detectarlo pronto, mejor
    fallar alto aquí que dejarlo solo en manos del guard."""
    for key, value in identity.items():
        if key in exclude_keys or not value:
            continue
        if value in text:
            raise IdentityLeakError(f"el valor de '{key}' sigue presente tras la reversa")

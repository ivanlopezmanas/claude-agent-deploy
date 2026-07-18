#!/usr/bin/env python3
"""verify_agents.py — consistencia entre agents/*.md, agent-permissions.json y las
referencias subagent_type="..." desde agentes/skills que lanzan subagentes.

Uso: python3 verify_agents.py

Comprueba, sin dependencias externas (stdlib only):
1. agent-permissions.json es JSON válido con las claves 'agents' y 'defaults'.
2. Todo fichero agents/*.md tiene entrada en agent-permissions.json — sin ella
   el agente queda bloqueado por completo al primer tool call (modelo allow-only
   default-deny, sin fallback "ask"). Este es el fallo que casi se cuela al
   rediseñar council-of-elders.
3. Toda herramienta listada en el frontmatter `tools:` de un agente tiene al
   menos una regla que la cubra (por nombre de tool) en su entrada de
   agent-permissions.json.
4. Todo subagent_type="X" referenciado desde agents/*.md o skills/**/SKILL.md
   apunta a un agente que existe como fichero Y tiene entrada en
   agent-permissions.json.
5. Entradas de agent-permissions.json sin fichero de agente correspondiente
   (agente eliminado, entrada huérfana) — aviso, no error.

Sale con código 1 si hay algún ERROR; 0 si como mucho hay AVISOS.
"""
import json
import re
import sys
from pathlib import Path

AGENTS_DIR = Path("/home/<agent>/claude/.claude/agents")
SKILLS_DIR = Path("/home/<agent>/claude/.claude/skills")
PERMISSIONS_FILE = Path("/home/<agent>/workspace/scripts/lib/agent-permissions.json")

SUBAGENT_TYPE_RE = re.compile(r"subagent_type\s*=\s*[\"']([\w-]+)[\"']")
# El "+" tras el guion exige al menos un espacio entre "-" y el nombre, para no
# confundir una viñeta ("- Agent") con la línea delimitadora del frontmatter ("---").
FRONTMATTER_TOOLS_RE = re.compile(r"^tools:\s*\n((?:^[ \t]*-[ \t]+\S+[ \t]*\n?)+)", re.MULTILINE)
TOOLS_BULLET_RE = re.compile(r"^[ \t]*-[ \t]+(\S+)", re.MULTILINE)


def _load_permissions_table() -> dict:
    return json.loads(PERMISSIONS_FILE.read_text())


def _agent_tools(md_text: str) -> list:
    """Extrae la lista de `tools:` del frontmatter YAML de un fichero de agente."""
    match = FRONTMATTER_TOOLS_RE.search(md_text)
    if not match:
        return []
    return TOOLS_BULLET_RE.findall(match.group(1))


def _rule_tool_names(rules: list) -> set:
    return {rule.split("(")[0] for rule in rules}


def _find_subagent_refs(files: list) -> list:
    """[(fichero, subagent_type_referenciado), ...] sobre una lista de ficheros .md."""
    refs = []
    for f in files:
        for ref in SUBAGENT_TYPE_RE.findall(f.read_text()):
            refs.append((f, ref))
    return refs


def main() -> int:
    errors = []
    warnings = []

    if not PERMISSIONS_FILE.exists():
        errors.append(f"No existe {PERMISSIONS_FILE}")
        _report(errors, warnings)
        return 1

    try:
        table = _load_permissions_table()
    except Exception as e:
        errors.append(f"{PERMISSIONS_FILE} no es JSON válido: {e}")
        _report(errors, warnings)
        return 1

    if not isinstance(table, dict) or "agents" not in table or "defaults" not in table:
        errors.append(f"{PERMISSIONS_FILE}: faltan las claves 'agents' o 'defaults'")
        _report(errors, warnings)
        return 1

    permission_agents = table["agents"]
    agent_files = sorted(AGENTS_DIR.glob("*.md")) if AGENTS_DIR.exists() else []
    agent_names = {f.stem for f in agent_files}

    # 2 y 3: todo agente de agents/*.md tiene entrada, y sus tools están cubiertas.
    for f in agent_files:
        name = f.stem
        if name not in permission_agents:
            errors.append(f"agents/{f.name}: sin entrada en agent-permissions.json — quedará bloqueado sin permisos")
            continue
        covered = _rule_tool_names(permission_agents[name].get("allow", []))
        for tool in _agent_tools(f.read_text()):
            if tool not in covered:
                warnings.append(f"agents/{f.name}: usa '{tool}' en su frontmatter pero agent-permissions.json no tiene ninguna regla que lo cubra")

    # 4: subagent_type referenciados desde agentes orquestadores y desde skills.
    skill_files = sorted(SKILLS_DIR.glob("**/SKILL.md")) if SKILLS_DIR.exists() else []
    for source, ref in _find_subagent_refs(agent_files) + _find_subagent_refs(skill_files):
        label = source.relative_to(source.parents[1])
        if ref not in agent_names:
            errors.append(f"{label}: referencia subagent_type='{ref}', pero no existe agents/{ref}.md")
        elif ref not in permission_agents:
            errors.append(f"{label}: referencia subagent_type='{ref}', que no tiene entrada en agent-permissions.json")

    # 5: entradas huérfanas en agent-permissions.json.
    for name in permission_agents:
        if name not in agent_names:
            warnings.append(f"agent-permissions.json: entrada '{name}' sin fichero agents/{name}.md correspondiente")

    _report(errors, warnings)
    return 1 if errors else 0


def _report(errors: list, warnings: list) -> None:
    if errors:
        print(f"{len(errors)} error(es):")
        for e in errors:
            print(f"  - {e}")
    if warnings:
        print(f"{len(warnings)} aviso(s):")
        for w in warnings:
            print(f"  - {w}")
    if not errors and not warnings:
        print("Todo consistente: agentes, permisos y referencias subagent_type cuadran.")


if __name__ == "__main__":
    sys.exit(main())

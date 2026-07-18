---
name: add-agent
description: >
  Crea, modifica o elimina subagentes de este agente desplegado (agents/*.md,
  agent-permissions.json, workspace.json, la skill que lo invoca, tests). Usar
  ante frases como "quiero crear un agente nuevo para X", "necesito un subagente
  que haga Y", "monta un agente que...", "modifica el agente Z", "elimina el
  agente W". Termina siempre con una verificación automática de que agentes,
  permisos y referencias subagent_type están correctamente enlazados — evita
  que un agente quede sin permisos y bloqueado en silencio.
version: 1.0.0
---

# Skill /add-agent — Crear, modificar o eliminar subagentes

Tipo A (ejecuta directamente, no lanza subagente). El propio agente principal
hace la entrevista, escribe los ficheros y corre la verificación.

---

## 1. Detección

Activar ante:
- "quiero crear un agente nuevo para..."
- "necesito un subagente que..."
- "monta un agente que haga..."
- "modifica el agente `<nombre>` para que..."
- "elimina el agente `<nombre>`"

## 2. Entrevista

No construir nada a ciegas. Preguntas mínimas (ajustar según lo que ya esté claro):

- ¿Qué hace este agente y quién lo invoca — una skill, otro agente orquestador, o ambos?
- ¿Qué herramientas necesita (`Read`, `Write`, `Bash`, `WebSearch`, `mcp__...`, `Agent` si orquesta a su vez)?
- Si escribe a disco: ¿en qué paths exactos? (necesario para `workspace.json` y
  para el patrón de la regla `Write(...)`/`Edit(...)` en `agent-permissions.json`)
- ¿Qué modelo — Haiku (barato, tareas acotadas), Sonnet (por defecto), u Opus
  (requiere permiso explícito del usuario, coste alto)?
- ¿Necesita Telegram o acceso a `agent_memory`? Por defecto, no — solo si se justifica.

## 3. Construcción

Checklist completo — saltarse un punto es exactamente el fallo que casi se
cuela al rediseñar `council-of-elders`: el orquestador estuvo a punto de
quedarse sin `Agent` en su allow-list, y `council-warden` a punto de quedar
bloqueado por completo por no tener entrada en `agent-permissions.json`.

1. **`agents/<nombre>.md`** — frontmatter con `name`, `description` (qué hace,
   quién lo invoca, si NO debe invocarse directamente), `model`, `tools:`
   (lista exacta de herramientas, una por línea). Cuerpo: reglas de operación,
   workflow, formato de output si aplica, bloque `loop_config`
   (`max_tool_calls`, `max_consecutive_failures`, `on_stall`).
2. **Entrada en `agent-permissions.json`** (`workspace/scripts/lib/`) — bajo
   `agents.<nombre>.allow`, una regla por cada tool del frontmatter. Los tools
   con path (`Write`, `Edit`) necesitan patrón, no el nombre desnudo:
   `Write(/home/<agent>/ruta/*)`.
3. **`workspace.json`** — si el agente escribe a disco, confirmar que el path
   está cubierto por una entrada de tier (o añadir una nueva) en
   `workspace/scripts/lib/workspace.json`.
4. **La `SKILL.md` que lo invoca** (si aplica) — nueva o modificada, con el
   bloque `Agent(subagent_type="<nombre>", ...)`. Si el agente es un
   orquestador que a su vez lanza otros subagentes, esas referencias
   `subagent_type="..."` viven en su propio `agents/<nombre>.md`, no en la skill.
5. **Tests en `fase-2/tests/`** — al menos que la entrada de permisos exista y
   cubra las tools esperadas (`test_verify_agents.py` sirve de referencia de estilo).
6. **Documentar el cambio** en el doc de tareas o de arquitectura correspondiente.

## 4. Verificación final — SIEMPRE, aunque solo se haya tocado un punto

No dar la tarea por terminada solo porque los ficheros se han escrito. Correr:

```bash
python3 /home/<agent>/workspace/scripts/lib/verify_agents.py
```

Este script (stdlib only, sin dependencias externas) comprueba automáticamente:

- Que `agent-permissions.json` es JSON válido y tiene la forma esperada.
- Que todo fichero `agents/*.md` tiene entrada en `agent-permissions.json` —
  si falta, ese agente queda bloqueado por completo al primer tool call (fail
  silencioso: exactamente el bug que casi se cuela con `council-warden`).
- Que las tools del frontmatter de cada agente están cubiertas por al menos
  una regla en su entrada de `agent-permissions.json` (aviso si no).
- Que toda referencia `subagent_type=...` desde `agents/*.md` o
  `skills/**/SKILL.md` apunta a un agente que existe Y tiene entrada en
  `agent-permissions.json`.
- Entradas huérfanas en `agent-permissions.json` sin fichero de agente
  correspondiente (aviso).

Sale con código 1 si hay algún error — en ese caso, arreglar antes de dar la
tarea por cerrada. Después, correr también la suite completa:

```bash
python3 -m pytest /home/<agent>/workspace/tests/ -q
```

Si algo fallara y no fuera evidente cómo arreglarlo, no forzar un parche —
explicar qué comprobación falló y por qué antes de tocar más código.

## 5. Cierre

Confirmar en una frase: qué se creó/modificó/eliminó, y que la verificación
pasó limpia (o qué avisos quedaron pendientes y por qué se consideran
aceptables).

---

## completion_criteria

```yaml
completion_criteria: "ficheros del agente creados/modificados/eliminados + verify_agents.py sin errores + pytest sin fallos nuevos"
max_iterations: 15
```

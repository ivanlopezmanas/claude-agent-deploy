---
name: resume
description: >
  Retoma una sesión anterior. Úsala cuando el usuario escriba /resume_{session_id} o /resume o pida
  continuar donde lo dejó en una sesión previa. Si incluye {session_id}, va directo al
  transcript. Si no, consulta la BD para mostrar sesiones recientes.
version: 1.1.0
---

# Skill /resume — Continuidad de sesiones

Flujo en dos fases:
- Si no tiene {session_id} Fase 1. busqueda de sesiones recientes. Si ya tiene {session_id} salta a la Fase 2.
- Fase 2 - busqueda del archivo de la sesión y resumen de sesión, delegado al subagente `session-continuity`.
---

## FASE 1 — Presentar sesiones recientes

Solo si no hay session_id en el comando.

Toma el `user_id` (chat_id) del mensaje de Telegram que disparó este comando —
**no lo hardcodees ni lo tomes de configuración**: cada usuario debe ver solo
sus propias sesiones, para que esto escale cuando haya varios usuarios
(family/guest en `agent_user_roles`).

Ejecuta esta query via MCP de Postgres (`mcp__postgres__query_data`), sustituyendo
`{user_id}` por ese valor:

```sql
SELECT
    session_id::text,
    MIN(fecha) AT TIME ZONE 'Europe/Madrid' AS inicio,
    MAX(fecha) AT TIME ZONE 'Europe/Madrid' AS fin,
    array_agg(category ORDER BY fecha) AS categories,
    array_agg(content ORDER BY fecha)  AS contents
FROM agent_memory
WHERE user_id = {user_id}
  AND session_id IS NOT NULL
GROUP BY session_id
ORDER BY MAX(fecha) DESC
LIMIT 8;
```

Para cada sesión, determina:
- **Fecha**: usa `fin` formateada como `DD/MM HH:MM`
- **Resumen**: 2 - 3 frases de qué se trabajó (basado en `contents`)
- **Tarea inacabada**: detecta si algún texto menciona trabajo en curso, "pendiente",
  "falta", "implementar", "continuar", o hay entradas `project`/`infrastructure` con
  trabajo técnico activo que no concluye explícitamente

Envía la lista por Telegram (reply, format: markdownv2):
- Sesiones numeradas del 1 al N
- Fecha + resumen breve
- Si hay tarea inacabada: `⚠️ *Tarea sin cerrar:* descripción en negrita`
- Al final, pregunta cuál quiere retomar

Espera la respuesta antes de continuar a FASE 2.

---

## FASE 2 — Leer transcript y devolver estado

Si el comando incluye un argumento con formato UUID (p.ej. `/resume_84b6c23c-99f7-4159-a1fe-bb6e64aa48f7`),
**ejecutar esta fase directamente**. No consultar la BD ni mostrar lista.

El path del transcript es: `/home/<agent>/claude/.claude/projects/-home-<agent>-claude/{session_id}.jsonl`

Lanza el subagente `session-continuity` (aislado del canal — §7.2: sin Telegram, sin
Session*/Notification/PostToolUse):

```python
Agent(
    description="Leer estado de sesión anterior",
    subagent_type="session-continuity",
    prompt=f"""
Session ID: {session_id}
Transcript path: /home/<agent>/claude/.claude/projects/-home-<agent>-claude/{session_id}.jsonl

Lee el transcript y devuelve el resumen estructurado (fecha_fin, tarea_en_curso,
completado, siguiente_paso, detalles_tecnicos) según tu workflow.
""",
    env={"<AGENT>_CONTEXT": "subagent"},
)
```

```yaml
completion_criteria: "resumen estructurado devuelto con los 5 campos (fecha_fin, tarea_en_curso, completado, siguiente_paso, detalles_tecnicos)"
max_iterations: 1
```

Una vez que el subagente devuelva el resumen, formatearlo y enviarlo por Telegram
(reply, format: markdownv2):

```
*Estado de la sesión — {fecha_fin}*

*Tarea en curso:*
{tarea_en_curso en negrita}

*Completado:*
{completado}

*Siguiente paso:*
{siguiente_paso}

*Detalles técnicos:*
{detalles_tecnicos}
```

No leas el transcript en el contexto principal — eso lo hace el subagente.

---

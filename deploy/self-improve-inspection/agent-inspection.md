# Auditoría de agentes

> Checklist de requisitos para todo agente del catálogo (§6.4 de `personal-agent-design.md`).
> No contiene los valores concretos de cada agente — esos viven en §6.4.
> Este documento define qué debe estar presente y en qué orden de prioridad.
>
> **Cuándo ejecutar:** mensual, junto con `skill-inspection.md` y el checklist del kernel (§1.7).
> **Quién lo ejecuta:** agente `self-improve` o el usuario manualmente.

---

## Requisitos por prioridad

### P1 — Nombre e identidad

- [ ] El agente tiene un nombre único y memorable
- [ ] Tiene una descripción de una sola frase que explica qué hace (sin ambigüedad con otros agentes del catálogo)


*Por qué es P1:* sin nombre e identidad clara, el agente principal no sabe cuándo invocarlo y la auditoría no puede referenciarlo.

---

### P2 — Allowlist explícita

- [ ] Tiene una `allowlist` declarada con las tools concretas que necesita
- [ ] No contiene tools que la tarea no requiere (principio de mínimo privilegio)
- [ ] Está declarado si tiene acceso de escritura o solo lectura
- [ ] Está declarado si tiene acceso a `agent_memory` o no
- [ ] Está declarado si tiene acceso a Telegram (ningún subagente debería tenerlo)
- [ ] El agente ha pedido algún otro permiso que no estuviera en su allowlist? debería de tenerlo?

*Por qué es P2:* una allowlist incompleta produce errores en ejecución; una allowlist demasiado amplia es un riesgo de seguridad. Es el segundo campo más crítico después de la identidad.

---

### P3 — Modelo asignado

- [ ] Tiene un modelo por defecto declarado (`haiku` / `sonnet` / `opus`)
- [ ] Si el modelo es `opus`, está documentado por qué (no puede ser el default sin justificación)
- [ ] Si admite override de modelo, las condiciones del override están documentadas

*Por qué es P3:* el modelo determina coste y calidad. Sin modelo explícito, el agente usa el default del proceso padre, que puede no ser el adecuado.

---

### P4 — Skill o trigger que lo invoca

- [ ] Está declarado qué skill lo lanza (o "cron + nombre del timer" si es automatizado)
- [ ] Si es invocado por el usuario, el trigger (qué dice el usuario) está documentado en la skill asociada

*Por qué es P4:* un agente sin skill conocida es huérfano — no hay forma de invocarlo de forma consistente ni de auditarlo.

---

### P5 — Tipo: iterativo o no iterativo

- [ ] Está declarado explícitamente si el agente ejecuta bucles internos o no
- [ ] Si es **no iterativo**: verificar que no se haya añadido lógica de bucle sin actualizar esta clasificación
- [ ] Si es **iterativo**: continuar con P6

*Por qué es P5:* la clasificación determina si aplican los requisitos de P6. Un agente mal clasificado puede tener un bucle sin condiciones de parada sin que la auditoría lo detecte.

---

### P6 — `loop_config` (solo para agentes iterativos)

- [ ] `max_tool_calls` declarado con valor numérico explícito
- [ ] `max_consecutive_failures` declarado con valor numérico explícito
- [ ] `on_stall` declarado: `write_and_stop` o `escalate`
- [ ] Existe al menos un test que verifica que el agente para al alcanzar `max_tool_calls`
- [ ] Existe al menos un test que verifica que el agente escribe `stall: true` al alcanzar `max_consecutive_failures`

*Por qué es P6 y no P1:* solo aplica a agentes iterativos. Para los no iterativos, este bloque no existe.

---

### P7 — Formato de output

- [ ] Está documentado qué escribe el agente al buzón (`/tmp/nox-subagent-<task_id>.json`) o qué devuelve directamente
- [ ] El formato es estructurado (JSON preferido) o Markdown con estructura definida
- [ ] Está documentado qué campos contiene el output en caso de éxito
- [ ] Está documentado qué contiene el output en caso de stall o error

*Por qué es P7:* el agente principal necesita saber qué esperar del buzón para procesarlo. Un formato indefinido produce code que asume y falla.

---

## Criterio de éxito

La auditoría está limpia cuando ningún agente del catálogo tiene campos obligatorios pendientes: todos tienen nombre, allowlist, modelo, skill asociada y clasificación de tipo. Los iterativos tienen `loop_config` completo y tests de parada. No hay referencias rotas entre agentes y skills.

---

## Formato de reporte cuando hay problemas

Si `self-improve` encuentra incidencias al ejecutar esta auditoría, las reporta al usuario con este formato:

```
Auditoría de agentes — [fecha]

Problemas encontrados: N

1. [nombre-agente] — P[prioridad]: [descripción del campo que falta o está incompleto]
   → Acción sugerida: [qué hay que añadir o corregir en §6.4]

2. [nombre-agente] — P[prioridad]: ...
   → Acción sugerida: ...

Agentes sin incidencias: [lista]
```

Si no hay incidencias: "Auditoría de agentes limpia — todos los campos obligatorios presentes."

---

## Histórico de cambios

| Fecha | Cambio | Autor |
|-------|--------|-------|
| 2026-06-16 | Creación como `agent-loop-audit.md` con foco en loops | owner + <Agent> |
| 2026-06-16 | Refactorizado a `agent-inspection.md`: esquema completo ordenado por prioridad. Valores concretos movidos a §6.4 del design doc. | owner + <Agent> |

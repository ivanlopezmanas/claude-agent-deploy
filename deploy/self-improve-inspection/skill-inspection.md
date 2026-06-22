# Auditoría de skills

> Checklist de requisitos para toda skill del agente.
> No contiene los valores concretos de cada skill — esos viven en la definición de la skill.
> Este documento define qué debe estar presente y en qué orden de prioridad.
>
> **Cuándo ejecutar:** mensual, junto con `agent-inspection.md` y el checklist del kernel (§1.7).
> **Quién lo ejecuta:** agente `self-improve` o el usuario manualmente.
> **Referencia:** `personal-agent-design.md` §6.1, §6.5 y §6.6.

---

## Requisitos por prioridad

### P1 — Nombre e identidad

- [ ] La skill tiene un nombre único
- [ ] Tiene una descripción de trigger clara: qué dice o hace el usuario para invocarla
- [ ] La descripción es suficientemente específica para que el modelo la distinga de otras skills

*Por qué es P1:* si el trigger es ambiguo, el modelo no invoca la skill cuando debería, o la invoca cuando no debería.

---

### P2 — Tipo declarado

Toda skill debe estar clasificada en uno o más de estos tipos:

| Tipo | Descripción |
|------|-------------|
| **A** | Produce output de alto riesgo (email en nombre del usuario, cifra financiera, confirmación de fecha) |
| **B** | Lanza uno o más agentes iterativos |
| **C** | Ninguno de los anteriores |

- [ ] El tipo está declarado en la definición de la skill
- [ ] Si el tipo es C, verificar que no se haya añadido output de alto riesgo o agentes iterativos sin actualizar la clasificación

*Por qué es P2:* el tipo determina qué requisitos adicionales aplican (P5 y P6). Una skill mal clasificada puede tener output de alto riesgo sin revisor o un agente iterativo sin condiciones de parada.

---

### P3 — Workflow declarado

- [ ] Los pasos del workflow están declarados en orden
- [ ] Cada paso indica qué hace (herramienta, lógica, decisión)
- [ ] Está claro qué entradas necesita la skill para ejecutarse
- [ ] Está claro qué devuelve o presenta al usuario al terminar

*Por qué es P3:* un workflow implícito es inauditable. Si no está escrito, no se puede verificar que esté bien.

---

### P4 — Agente(s) que invoca

- [ ] Si la skill lanza subagentes, están identificados por nombre del catálogo (§6.4)
- [ ] Si no lanza subagentes, está declarado que resuelve en la sesión principal
- [ ] El agente invocado existe en el catálogo (no hay referencias rotas)

*Por qué es P4:* una skill que referencia un agente que no existe o que no está en el catálogo produce un fallo silencioso.

---

### P5 — Verificación adversarial (solo tipo A)

Para skills que producen output de alto riesgo:

- [ ] El workflow incluye explícitamente un paso de verificación adversarial (santa-method, §6.6)
- [ ] El paso de verificación ocurre **antes** del reply final al usuario
- [ ] El revisor usa modelo `opus`
- [ ] El briefing del revisor **no incluye** el contexto de generación (anti-anchoring)
- [ ] Existe un test que verifica que el paso de verificación no puede saltarse

*Por qué es P5 y no P1:* solo aplica a skills tipo A. Para el resto, este bloque no existe.

---

### P6 — `loop_invocation` (solo tipo B)

Para skills que lanzan agentes iterativos:

- [ ] La skill pasa `completion_criteria` al construir el prompt del subagente
- [ ] El `completion_criteria` es observable y verificable (no genérico como "haz lo que puedas")
- [ ] La skill pasa `max_iterations` al construir el prompt del subagente
- [ ] Los valores son coherentes con la complejidad esperada de la tarea
- [ ] Si los valores son dinámicos (calculados en función de parámetros), la lógica de cálculo está documentada

*Por qué es P6 y no P1:* solo aplica a skills tipo B.

---

## Criterio de éxito

La auditoría está limpia cuando todas las skills del agente tienen tipo declarado, workflow documentado y skill-agente sin referencias rotas. Las de tipo A tienen el paso de verificación adversarial con revisor Opus. Las de tipo B pasan `loop_invocation` completo con criterios observables.

---

## Formato de reporte cuando hay problemas

Si `self-improve` encuentra incidencias al ejecutar esta auditoría, las reporta al usuario con este formato:

```
Auditoría de skills — [fecha]

Problemas encontrados: N

1. [nombre-skill] — P[prioridad]: [descripción del campo que falta o está incompleto]
   → Acción sugerida: [qué hay que añadir o corregir en la definición de la skill]

2. [nombre-skill] — P[prioridad]: ...
   → Acción sugerida: ...

Skills sin incidencias: [lista]
```

Si no hay incidencias: "Auditoría de skills limpia — todos los campos obligatorios presentes."

---

## Histórico de cambios

| Fecha | Cambio | Autor |
|-------|--------|-------|
| 2026-06-16 | Creación. Tipos A, B y C. Requisitos P1-P6 ordenados por prioridad. | owner + <Agent> |

---
name: council-of-elders
description: >
  Deliberación estructurada para decisiones complejas. Usar ante frases como "ayúdame
  a decidir entre X e Y", "necesito pensar bien esto", "¿qué harías tú con [decisión]?",
  "dame perspectivas distintas sobre...", "¿estoy tomando la decisión correcta?".
  REQUIERE confirmación del usuario antes de lanzar — usa modelos Opus.
version: 1.0.0
---

# Skill /council-of-elders — Deliberación para decisiones complejas

Tipo B (lanza subagentes). Tipo A parcial (output de alta relevancia — no es ejecutable
pero informa decisiones importantes). Confirmar con el usuario antes de lanzar: usa Opus,
coste elevado.

---

## Flujo

1. Identificar las opciones en juego y extraer el criterio de decisión del usuario.
2. Pedir confirmación al usuario antes de lanzar el council (Opus × 3 subagentes).
3. Definir 2-3 roles según la decisión (defensor A, defensor B, abogado del diablo).
4. Lanzar cada rol como subagente independiente **en paralelo** con el mismo briefing
   de contexto pero prompt de rol diferente. Sin acceso entre ellos — anti-anchoring.
5. Recopilar los tres outputs del buzón.
6. Sintetizar y presentar al usuario: argumentos ordenados por rol, sin colapsar
   a una sola recomendación. La decisión es del usuario.

---

## Lanzamiento de subagentes (en paralelo)

```python
roles = [
    ("defensor-a", f"Defiende con rigor la opción A: {opcion_a}. Argumenta sus ventajas."),
    ("defensor-b", f"Defiende con rigor la opción B: {opcion_b}. Argumenta sus ventajas."),
    ("abogado-del-diablo", "Cuestiona ambas opciones. Busca los riesgos que los defensores ignoran."),
]

# Lanzar en paralelo — mismo briefing de contexto, prompt de rol diferente
for rol, instruccion_rol in roles:
    Agent(
        description=f"council-of-elders — {rol}",
        subagent_type="council-of-elders",
        prompt=f"""
ROL: {rol}
{instruccion_rol}

CONTEXTO DE LA DECISIÓN:
{contexto_decision}

Escribe tu análisis en /tmp/<agent>-council-{rol}-{task_id}.json
""",
        run_in_background=True
    )
```

---

## completion_criteria

```yaml
completion_criteria: "los tres roles han entregado su análisis al buzón"
max_iterations: 15  # por subagente
```

---
name: council-of-elders
description: >
  Deliberación estructurada para decisiones complejas. Usar ante frases como "ayúdame
  a decidir entre X e Y", "necesito pensar bien esto", "¿qué harías tú con [decisión]?",
  "dame perspectivas distintas sobre...", "¿estoy tomando la decisión correcta?".
  La skill entrevista para fijar bien el objetivo antes de lanzar — no construye
  los roles, eso lo decide el agente council-of-elders. REQUIERE confirmación
  explícita del usuario antes de lanzar — usa Opus, coste elevado.
version: 2.0.0
---

# Skill /council-of-elders — Deliberación para decisiones complejas

Tipo B (lanza subagente). Tipo A parcial (output de alta relevancia — informa
decisiones importantes, no es ejecutable). La skill NO decide los roles del
panel — eso es la Fase 0 del agente `council-of-elders`. El trabajo de la skill
es interpretar, confirmar y entrevistar.

---

## Flujo

### 1. Detección

Activar ante señales de que el usuario está debatiendo una decisión, no
pidiendo un dato puntual: "ayúdame a decidir...", "no sé si A o B", "qué
opciones tengo para...", "¿estoy tomando la decisión correcta?".

### 2. Confirmación explícita

Antes de nada, preguntar directamente: *"¿Lanzamos el council para esto? Usa
Opus y tarda un poco, pero da un análisis más a fondo."* No continuar sin un sí
explícito del usuario.

### 3. Entrevista

Si confirma, no pasar la frase del usuario en crudo al agente. Hacer las
preguntas necesarias para fijar el objetivo con precisión — normalmente:

- ¿Qué opciones concretas hay en juego? (si no están claras)
- ¿Qué sabe ya, qué ha descartado y por qué?
- ¿Hay restricciones fijas (presupuesto, plazo, personas afectadas)?
- ¿Qué tipo de decisión es — técnica, con partes interesadas distintas, o
  depende de conocer a alguien en concreto?

No alargar la entrevista más de lo necesario — 2-4 preguntas suele bastar. El
objetivo es que el agente reciba un encargo bien definido, no interrogar por
interrogar.

### 4. Lanzamiento (una sola llamada)

```python
Agent(
    description="council-of-elders — deliberación",
    subagent_type="council-of-elders",
    prompt=f"""
OBJETIVO DE LA DECISIÓN (ya depurado en entrevista):
{objetivo_depurado}

OPCIONES EN JUEGO:
{opciones}

CONTEXTO ADICIONAL:
{restricciones_y_contexto}

task_id: {task_id}
""",
    run_in_background=True
)
```

La skill no decide roles ni número de wardens — eso es responsabilidad exclusiva
de la Fase 0 del agente.

### 5. Presentación del resultado

Al recibir la respuesta del agente (resumen + roles usados + disenso +
ruta del informe):

- Si `falta_contexto` es `true`, no insistir en lanzar el panel — pedir al
  usuario la información concreta que el agente ha señalado como necesaria.
- Si `disenso_relevante` es `true`, dejar claro que el panel no llegó a
  consenso y que la decisión final es del usuario — presentar los lados en
  desacuerdo sin colapsarlos en una recomendación única.
- Indicar siempre qué roles se usaron y por qué (transparencia del proceso).
- Ofrecer el informe completo (`informe_completo`) si el usuario quiere más
  detalle que el resumen.

---

## completion_criteria

```yaml
completion_criteria: "el agente council-of-elders ha devuelto resumen + roles_utilizados + ruta del informe"
max_iterations: 20
```

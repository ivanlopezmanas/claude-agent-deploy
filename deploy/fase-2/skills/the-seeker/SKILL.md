---
name: the-seeker
description: >
  Búsqueda e investigación. Usar ante frases como "busca X", "investiga Y", "dame un
  informe sobre Z", "compara opciones de...", "¿qué hay sobre...?", "necesito saber...".
  Queries simples (una fuente) se resuelven directamente. Investigaciones complejas
  lanzan the-seeker (Opus), que orquesta scouts Haiku en paralelo con refinamiento
  gap-driven y produce un informe en /home/<agent>/workspace/docs/informes/.
version: 2.0.0
---

# Skill /the-seeker — Búsqueda e investigación

---

## Lógica de decisión

- **Query simple** (una fuente esperada, respuesta directa): resolver en la sesión
  principal con WebSearch + WebFetch. Sin subagente.
- **Query compleja** (múltiples ángulos, informe estructurado, comparativa, >3 búsquedas
  estimadas): lanzar subagente `the-seeker` en background.

---

## Modo directo (query simple)

Ejecutar WebSearch y/o WebFetch directamente. Presentar respuesta al usuario.

---

## Modo subagente (investigación compleja)

```python
Agent(
    description="the-seeker — investigación",
    subagent_type="the-seeker",
    prompt=f"""
Objetivo de investigación: {descripcion_investigacion}
Resultado esperado: {que_debe_responder_o_producir}
Fecha: {datetime.now().isoformat()}

Workflow:
1. Descomponer en sub-queries independientes (2-3 para queries estrechas, 4-5 para amplias).
2. Lanzar un the-seeker-scout (Haiku) por sub-query en paralelo.
   Cada scout busca, selecciona hasta 5 URLs y las lee. Para antes si tiene cobertura.
3. Reflexión gap-driven: evaluar huecos y lanzar scouts de refinamiento si es necesario.
   Hasta 3 rondas extra con parada por suficiencia.
4. Sintetizar en informe Markdown.
5. Guardar informe en /home/<agent>/workspace/docs/informes/{{fecha}}-{{slug}}.md
""",
    run_in_background=True
)
```

Al recibir el resultado, indicar al usuario la ruta del informe y un resumen ejecutivo.

---

## completion_criteria

```yaml
completion_criteria: "informe escrito en /home/<agent>/workspace/docs/informes/ con hallazgos de todos los scouts"
max_iterations: 20
```

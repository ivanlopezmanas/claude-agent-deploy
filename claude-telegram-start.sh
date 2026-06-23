---
name: the-scribe
description: >
  Gestión de correo electrónico. Usar ante frases como "revisa mi correo", "¿qué tengo
  sin leer?", "dame un resumen del inbox", "responde a X", "redacta un email a...",
  "busca el email de...", "marca como leído...".
version: 1.0.0
---

# Skill /the-scribe — Gestión de correo

---

## Lógica de decisión

Lanza siempre el subagente `the-scribe` — el contexto de email no debe contaminar
la sesión principal.

Detecta el modo según la petición:
- **Triaje**: "revisa mi correo", "¿qué tengo pendiente?", "dame un resumen del inbox".
- **Acción específica**: buscar un email, redactar, responder, marcar como leído.

---

## Lanzamiento del subagente

```python
Agent(
    description="the-scribe — gestión de correo",
    subagent_type="the-scribe",
    prompt=f"""
Modo: {modo}  # 'triage' o 'action'
Petición del usuario: {mensaje_usuario}
Fecha y hora actual: {datetime.now().isoformat()}

{instrucciones_especificas_del_modo}

Devuelve tu output como respuesta final (JSON). No escribas a disco.
""",
    run_in_background=True
)
```

Al recibir el resultado, presentarlo al usuario. No ejecutar acciones irreversibles
(enviar emails) sin confirmación explícita del usuario.

---

## completion_criteria

```yaml
completion_criteria: "briefing de correo entregado con items clasificados y borradores redactados"
max_iterations: 30
```

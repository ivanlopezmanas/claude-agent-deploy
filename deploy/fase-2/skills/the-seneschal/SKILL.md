---
name: the-seneschal
description: >
  Gestión de calendario y agenda. Usar ante frases como "¿qué tengo hoy/esta semana?",
  "¿tengo hueco el jueves a las X?", "¿hay conflictos en mi agenda?", "añade una reunión",
  "mueve el evento de...", "¿cuándo tengo libre para...?", "revisa mi calendario".
version: 1.0.0
---

# Skill /the-seneschal — Gestión de calendario

---

## Lógica de decisión

Lanza siempre el subagente `the-seneschal` — el contexto de calendarios no debe
contaminar la sesión principal.

Detecta el modo según la petición:
- **Consulta**: ver agenda, buscar huecos, detectar conflictos, próximos eventos.
- **Gestión**: crear, modificar o eliminar eventos (siempre con confirmación previa).

---

## Lanzamiento del subagente

```python
Agent(
    description="the-seneschal — gestión de calendario",
    subagent_type="the-seneschal",
    prompt=f"""
Modo: {modo}  # 'query' o 'manage'
Petición del usuario: {mensaje_usuario}
Fecha y hora actual: {datetime.now().isoformat()}

{instrucciones_especificas_del_modo}

Devuelve tu output como respuesta final (JSON). No escribas a disco.
No crees, modifiques ni elimines eventos — solo prepara la propuesta para confirmación.
""",
    run_in_background=True
)
```

Al recibir el resultado, presentarlo al usuario. Para acciones de gestión (crear/modificar/
eliminar eventos), pedir confirmación explícita antes de relanzar el agente en modo ejecución.

---

## completion_criteria

```yaml
completion_criteria: "agenda consultada o propuesta de gestión preparada para confirmación"
max_iterations: 30
```

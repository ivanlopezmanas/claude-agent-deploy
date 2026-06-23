---
name: recall-memory
description: >
  Recupera memorias persistentes del agente desde PostgreSQL. Usar al inicio de sesión,
  cuando el usuario haga referencia explícita a algo pasado ("¿recuerdas?", "sigamos con
  esto"), o antes de proponer cambios de configuración. Soporta búsqueda por recencia,
  keyword, texto libre y fecha.
version: 1.0.0
---

# Skill /recall-memory — Recuperar contexto de sesiones anteriores

Consulta la tabla `agent_memory` en PostgreSQL via MCP (`mcp__postgres__query_data`).

---

## Modos de búsqueda

### Memorias recientes (uso por defecto al inicio de sesión)

```sql
SELECT category, keywords, text, fecha
FROM agent_memory
ORDER BY fecha DESC
LIMIT 10;
```

### Por keyword (cuando el usuario menciona un tema concreto)

```sql
SELECT category, keywords, text, fecha
FROM agent_memory
WHERE keywords && ARRAY['tema']
ORDER BY fecha DESC
LIMIT 10;
```

### Búsqueda fulltext (cuando hay una frase o concepto específico)

```sql
SELECT category, keywords, text, fecha
FROM agent_memory
WHERE to_tsvector('spanish', text) @@ plainto_tsquery('spanish', 'búsqueda')
ORDER BY fecha DESC
LIMIT 10;
```

### Por fecha

```sql
SELECT category, keywords, text, fecha
FROM agent_memory
WHERE DATE(fecha) = 'YYYY-MM-DD'
ORDER BY fecha DESC;
```

---

## Presentación de resultados

- Resumir en lenguaje natural, no volcar la tabla cruda.
- Destacar categorías `project` y `feedback` — suelen ser lo más accionable.
- Si no hay resultados relevantes, decirlo sin dramatizar.

---
name: artifacts
description: >
  Detecta cuándo el contenido de una respuesta merece publicarse como Artifact
  (página HTML/Markdown autocontenida con URL propia) en vez de responder con
  texto plano de Telegram, y cómo construirlo bien. Consultar SIEMPRE antes de
  responder con: tabla comparativa multi-columna, dashboard, diagrama, o
  informe largo con secciones (más de 30-50 líneas). También ante frases
  explícitas del usuario como "hazme una comparativa/dashboard", "publícalo
  como artifact", "como la última vez que hiciste [tabla/comparativa]".
version: 1.0.0
---

# Skill /artifacts — Cuándo y cómo publicar un Artifact

Tipo A (ejecuta directamente, no lanza subagente). Se consulta ANTES de
escribir la respuesta, no después — la decisión cambia qué se construye.

Basado en investigación de patrones de la industria (informe
`workspace/docs/informes/` si se ha copiado, o el original en
`the-seeker`): Anthropic (Claude Artifacts), OpenAI (Apps SDK / retirada de
ChatGPT Canvas) y guías de accesibilidad/dark-mode de la comunidad.

---

## 1. Detección — regla explícita, no intuición

ChatGPT Canvas se retiró en mayo 2026: su trigger "entrenado" (el modelo
decidía solo cuándo abrir el lienzo) acertaba en torno al 83% de las veces y
era inconsistente. Esta skill usa reglas explícitas, no "lo que parezca en
el momento".

**Publicar como artifact si se cumple al menos una señal, Y el contenido no
cabe cómodo como mensaje de Telegram:**

- Tabla comparativa multi-columna o matriz.
- Dashboard o datos con layout visual (varias métricas, agrupaciones).
- Diagrama (Mermaid, flujo, arquitectura).
- Informe largo con secciones — umbral: más de 30-50 líneas. Más
  conservador que las ~15 líneas de Claude.ai web: en Telegram, abrir una URL
  cuesta más atención que desplegar un panel lateral, así que el artifact
  tiene que ganárselo.
- El usuario probablemente querrá guardarlo, releerlo o reenviarlo — el valor
  está precisamente en que tiene URL propia.

**Test rápido:** ¿pagarías en Upwork por construir o editar esto? Si la
respuesta es sí, es artifact.

**NO publicar como artifact — sigue siendo texto (antipatrón nº1: sobreuso):**

- Confirmaciones y respuestas cortas (1-3 líneas).
- Snippets de código sueltos, aclaraciones puntuales, una ecuación.
- Cualquier cosa que el usuario claramente solo quiere leer una vez y seguir
  la conversación, no guardar ni reutilizar.

Ante la duda, no publicar. El texto plano de Telegram es el default; el
artifact es la excepción, y tiene que justificarse.

## 2. Invocación correcta

1. **Cargar la guía de diseño de artifacts** antes de escribir la primera
   línea — cubre tema claro/oscuro, responsive, accesibilidad. Esta skill no
   repite esas reglas, solo decide cuándo aplicarlas.
2. **HTML por defecto**, no Markdown, para contenido visual o compartible
   (tablas, comparativas, dashboards). Markdown solo para borradores cortos o
   contenido pensado para acabar en un repositorio git.
3. **Self-contained, sin excepciones** — CSP bloquea toda petición externa:
   scripts, CSS, fuentes, imágenes, fetch/XHR/WebSocket. Todo CSS/JS inline;
   imágenes como data-URI. Cap de 16 MiB — cuidado con base64 pesado.
4. **Es una foto fija** — sin backend, sin guardar formularios, sin lógica de
   servidor. Si la petición necesita estado o datos en vivo compartidos entre
   sesiones, no es un artifact estático — revisar si hay capacidades runtime
   disponibles antes de forzarlo en un artifact plano.
5. **Favicon obligatorio** (un emoji, 1-2 caracteres) — elegir uno que tenga
   sentido con el contenido y mantenerlo estable entre actualizaciones del
   mismo artifact (cambiarlo se lee como "página distinta").
6. **Actualizar, no crear de cero** — si el usuario pide "cambia X" o "añade
   Y" sobre un artifact ya publicado en la sesión (o uno reciente que
   referencia), redeployar sobre el mismo artifact en vez de publicar uno
   nuevo. Solo crear uno distinto si el tema es conceptualmente otro.

## 3. Cierre

Al publicar, decir en una frase qué es y por qué se hizo artifact — no dar
por hecho que el motivo es obvio. Si el contenido es largo, ofrecer también
un resumen en texto, no solo el enlace.

---

## Notas abiertas (sin confirmar en la investigación de origen)

- La mecánica exacta de favicon en artifacts fuera del propio Claude Code no
  está documentada en fuentes públicas — se sigue el criterio ya establecido
  para artifacts.
- No se encontraron antipatrones específicos de mensajería (Telegram/Discord)
  en la industria — las guías consultadas son todas de contexto web.

---

## completion_criteria

```yaml
completion_criteria: "artifact publicado (o decisión explícita de no publicar) + favicon + tema claro/oscuro + self-contained verificado"
max_iterations: 10
```

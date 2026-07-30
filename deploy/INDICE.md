# Índice de entregables de implementación — <Agent>

Plantilla genérica para desplegar un agente Claude Code. Todos los placeholders
(`<agent>`, `<Agent>`, `<AGENT>`, `<vmid>`, `<ip_address>`, `<hostname>`,
`<owner_name>`, `<profession>`, `<family>`, `<tech_level>`, `<use_cases>`,
`<tone_style>`, `<language_preference>`, `<owner_profile_description>`)
requieren sustitución antes del despliegue — la mayoría los rellena
`install-agent.sh` a partir de las respuestas del onboarding.

Convención de despliegue: cada fichero indica en su cabecera la ruta final dentro del
LXC (`/home/<agent>/...`, `/etc/...`, `/etc/systemd/system/...`) y el `chmod +x` cuando aplica.
Ningún fichero de `scripts/hooks/` ni `scripts/lib/` lleva ya prefijo `<agent>-` en el
nombre — el namespacing vive en la ruta (`/home/<agent>/...`), no en el filename.

---

## Fase 0 — Núcleo que responde (LXC + systemd + Claude Code + plugin + kernel)

| Fichero | Ruta destino en el LXC | Propósito |
|---------|------------------------|-----------|
| `fase-0/CLAUDE.md` | `/home/<agent>/claude/CLAUDE.md` | Kernel del agente: identidad, reglas inviolables, perfil del usuario, memoria, infraestructura |
| `fase-0/init-db.sql` | ejecutar una vez contra Postgres (`sudo -u postgres psql -f init-db.sql`) | Crea usuario, BD `agents`, las tablas + índices, RLS y las 2 tareas `core` semilla (`template-sync`, `self-improve`) |
| `fase-0/systemd/claude-telegram.service` | `/etc/systemd/system/claude-telegram.service` | Servicio principal (`User=<agent>`, arranca vía `claude-telegram-start.sh`) |
| `fase-0/systemd/heartbeat.timer` / `heartbeat.service` | `/etc/systemd/system/` | Timer monotónico cada 5 min; sesión efímera que procesa `agent_inbox` vía `heartbeat.py` |
| `fase-0/systemd/midnight.timer` / `midnight.service` | `/etc/systemd/system/` | Timer de calendario 00:00 (Persistent); job que rehace el día vía `midnight.py` |
| `fase-0/etc/sudoers.d/agent` | `/etc/sudoers.d/<agent>` | Sudo NOPASSWD restringido a `systemctl` de las unidades fijas de arriba |
| `fase-0/scripts/lib/claude-telegram-start.sh` | `/home/<agent>/workspace/scripts/lib/claude-telegram-start.sh` | Wrapper pty del servicio; fija `--model sonnet` y marca `NOX_SERVICE=telegram` |
| `fase-0/scripts/lib/patch-telegram-plugin.sh` | `/home/<agent>/workspace/scripts/lib/patch-telegram-plugin.sh` | Aplica el `server.ts` modificado al plugin de Telegram antes de arrancar (resistente a updates del plugin) |
| `fase-0/scripts/lib/telegram-server.ts` | copia de referencia del `server.ts` parcheado (cache del plugin) | Origen del parche que aplica `patch-telegram-plugin.sh` |
| `fase-0/scripts/lib/register-commands.py` | `/home/<agent>/workspace/scripts/lib/register-commands.py` | One-shot: registra los comandos del bot en el menú de Telegram (`setMyCommands`) |
| `fase-0/scripts/lib/retrofit-template.sh` | `/home/<agent>/workspace/scripts/lib/retrofit-template.sh` | Clona `~/template/` si no existe; idempotente |
| `fase-0/scripts/lib/heartbeat.py` | `/home/<agent>/workspace/scripts/lib/heartbeat.py` | Consumidor determinista de `agent_inbox` — reclama filas atómicamente, no invoca al modelo si no hay nada |
| `fase-0/scripts/lib/ticker.py` | `/home/<agent>/workspace/scripts/lib/ticker.py` | Anima "🔄 trabajando…" en Telegram mientras dura el turno; lo lanza `userprompt-hook.py`, lo mata `stop-hook.py` |
| `fase-0/scripts/lib/prompts/heartbeat.md` | `/home/<agent>/workspace/scripts/lib/prompts/heartbeat.md` | Prompt del heartbeat (reclama inbox, escala alertas críticas) |

## Fase 1 — Seguridad fundacional (guardrails, settings, tabla de rutas, AppArmor)

| Fichero | Ruta destino en el LXC | Propósito |
|---------|------------------------|-----------|
| `fase-1/settings.json` | `/home/<agent>/claude/.claude/settings.json` | Plano de control de la sesión principal: allow/deny (incluye `AskUserQuestion` en `deny`) + los 7 hooks |
| `fase-1/settings-background.json` | `/home/<agent>/claude/.claude/settings-background.json` | Subagentes/cron aislados vía `call_isolated_agent()`: sin plugin de Telegram, solo guardrails (PreToolUse, Stop) |
| `fase-1/scripts/lib/common.py` | `/home/<agent>/workspace/scripts/lib/common.py` | Módulo compartido: contexto (main/subagent/background/cron), logging, `call_isolated_agent`, scoring, detectores |
| `fase-1/scripts/lib/context.py` | `/home/<agent>/workspace/scripts/lib/context.py` | Catálogo de ventanas de contexto por modelo (usa `/context`) |
| `fase-1/scripts/lib/workspace.json` | `/home/<agent>/workspace/scripts/lib/workspace.json` | Tabla de rutas: tier (`never`/T1/T2) y política por directorio |
| `fase-1/scripts/lib/agent-permissions.json` | `/home/<agent>/workspace/scripts/lib/agent-permissions.json` | Permisos y patrones `Bash(...)` por `agent_type` de subagente |
| `fase-1/scripts/lib/greeting.py` | `/home/<agent>/workspace/scripts/lib/greeting.py` | Mensaje de bienvenida generado por Haiku al primer turno; aislado del canal |
| `fase-1/scripts/hooks/pretooluse-hook.py` | `/home/<agent>/workspace/scripts/hooks/pretooluse-hook.py` | Guardrail principal (FAIL-CLOSED): deny-list, reglas inviolables (incluye bloqueo duro de `AskUserQuestion`), scoring |
| `fase-1/scripts/hooks/userprompt-hook.py` | `/home/<agent>/workspace/scripts/hooks/userprompt-hook.py` | Bandera de origen, anti-injection (fail-open), intercepción `/context` `/skills` `/agents` `/reset` (lanza `reset_sequence.py`) |
| `fase-1/scripts/hooks/stop-hook.py` | `/home/<agent>/workspace/scripts/hooks/stop-hook.py` | Garantía de respuesta (FAIL-OPEN): guardas anti-deadlock, rewake, guarda de propiedad de sesión (`session_id`) |
| `fase-1/apparmor/apparmor-profile` | `/etc/apparmor.d/home.<agent>.claude` | Perfil AppArmor completo (red inet stream/dgram, binarios ix, denies) |

## Fase 2 — Harness completo, observabilidad y mecanismo de push (feedback + tests + skills + agents)

| Fichero | Ruta destino en el LXC | Propósito |
|---------|------------------------|-----------|
| `fase-2/scripts/hooks/sessionstart-hook.py` | `/home/<agent>/workspace/scripts/hooks/sessionstart-hook.py` | Limpia banderas huérfanas, log de inicio (FAIL-OPEN); si `NOX_SERVICE=telegram`, vuelca `session_id`/`transcript_path` a `data/telegram-session.json` |
| `fase-2/scripts/hooks/sessionend-hook.py` | `/home/<agent>/workspace/scripts/hooks/sessionend-hook.py` | Cierre de sesión: envía el código `/resume_...` a Telegram. **No** llama al chronicler aquí (incidente 2026-07-28: `KillMode=control-group` mata el subproceso a medias) |
| `fase-2/scripts/hooks/posttooluse-hook.py` | `/home/<agent>/workspace/scripts/hooks/posttooluse-hook.py` | Feedback (FAIL-OPEN): ticker + telemetría; silencio fuera de main |
| `fase-2/scripts/hooks/notification-hook.py` | `/home/<agent>/workspace/scripts/hooks/notification-hook.py` | Feedback: congela el feed en aprobación pendiente |
| `fase-2/scripts/hooks/precompact-hook.py` | `/home/<agent>/workspace/scripts/hooks/precompact-hook.py` | Señal de pre-compactación a `/tmp/<agent>-precompact-flag` |
| `fase-2/scripts/hooks/mcp-postgres-only.json` | `/home/<agent>/workspace/scripts/hooks/mcp-postgres-only.json` | MCP config para subagentes aislados (solo Postgres) |
| `fase-2/scripts/lib/template_push.py` | `/home/<agent>/workspace/scripts/lib/template_push.py` | Orquestador de push instancia→template: `preview`/`apply` (rama + PR en borrador por defecto) |
| `fase-2/scripts/lib/template_reverse.py` | `/home/<agent>/workspace/scripts/lib/template_reverse.py` | Motor puro de traducción real→agnóstico (`KEY_TO_PLACEHOLDER`, ficheros mixtos con marcadores `TEMPLATE:BEGIN/END`) |
| `fase-2/scripts/lib/template_guard.py` | `/home/<agent>/workspace/scripts/lib/template_guard.py` | Escáner anti-fuga determinista, se invoca 2 veces desde `template_push.py` |
| `fase-2/scripts/lib/verify_agents.py` | `/home/<agent>/workspace/scripts/lib/verify_agents.py` | Consistencia entre `agents/*.md`, `agent-permissions.json` y las referencias `subagent_type="..."` |
| `fase-2/tests/conftest.py` | `/home/<agent>/workspace/tests/conftest.py` | Fixtures pytest (runner de hooks, tmp aislado, contextos) |
| `fase-2/tests/test_common.py` | `/home/<agent>/workspace/tests/test_common.py` | Tests del módulo compartido: contexto, `call_isolated_agent` + logging, territorio (`agents/`/`skills/`/`inbox/`) |
| `fase-2/tests/test_context.py` | `/home/<agent>/workspace/tests/test_context.py` | Tests del catálogo de ventanas de contexto |
| `fase-2/tests/test_pretooluse.py` | `/home/<agent>/workspace/tests/test_pretooluse.py` | Deny-list, reglas inviolables (incluye `AskUserQuestion`), scoring, fail-closed, aislamiento |
| `fase-2/tests/test_userprompt.py` | `/home/<agent>/workspace/tests/test_userprompt.py` | Bandera de origen, anti-injection fail-open, intercepción, disparo de `reset_sequence.py` |
| `fase-2/tests/test_stop.py` | `/home/<agent>/workspace/tests/test_stop.py` | Guardas del Stop hook, incluida la guarda de propiedad de sesión |
| `fase-2/tests/test_sessionend.py` | `/home/<agent>/workspace/tests/test_sessionend.py` | Tests del SessionEnd hook (envío del código `/resume_`) |
| `fase-2/tests/test_isolation.py` | `/home/<agent>/workspace/tests/test_isolation.py` | Aislamiento por los 4 contextos (main/subagent/background/cron) |
| `fase-2/tests/test_retrofit_template.py` | `/home/<agent>/workspace/tests/test_retrofit_template.py` | Tests de `retrofit-template.sh` |
| `fase-2/tests/test_heartbeat.py` | `/home/<agent>/workspace/tests/test_heartbeat.py` | Tests de `heartbeat.py` (log aislado a `tmp_path`, nunca al log real) |
| `fase-2/tests/test_self_improve.py` | `/home/<agent>/workspace/tests/test_self_improve.py` | Tests de `self_improve.py` (incluye encoding UTF-8 de la conexión) |
| `fase-2/tests/test_midnight.py` | `/home/<agent>/workspace/tests/test_midnight.py` | Tests de `midnight.py` |
| `fase-2/tests/test_verify_agents.py` | `/home/<agent>/workspace/tests/test_verify_agents.py` | Tests de `verify_agents.py` |
| `fase-2/tests/test_template_push.py` / `test_template_reverse.py` / `test_template_guard.py` / `test_template_sync.py` | `/home/<agent>/workspace/tests/` | Tests del mecanismo de push/pull fase 2 |
| `fase-2/skills/recall-memory/SKILL.md` | `/home/<agent>/claude/.claude/skills/recall-memory/` | Recuperación de memorias desde PostgreSQL (reciente, keyword, fulltext, fecha) |
| `fase-2/skills/the-scribe/SKILL.md` | `/home/<agent>/claude/.claude/skills/the-scribe/` | Gestión de correo: triaje, búsqueda, redacción de borradores |
| `fase-2/skills/the-seneschal/SKILL.md` | `/home/<agent>/claude/.claude/skills/the-seneschal/` | Gestión de calendario: consulta, detección de conflictos, gestión de eventos |
| `fase-2/skills/council-of-elders/SKILL.md` | `/home/<agent>/claude/.claude/skills/council-of-elders/` | Deliberación estructurada para decisiones complejas (lanza `council-warden` × N) |
| `fase-2/skills/the-seeker/SKILL.md` | `/home/<agent>/claude/.claude/skills/the-seeker/` | Búsqueda e investigación (modo directo o subagente según complejidad) |
| `fase-2/skills/add-agent/SKILL.md` | `/home/<agent>/claude/.claude/skills/add-agent/` | Crea/modifica/elimina subagentes desplegados (agents/*.md, agent-permissions.json, workspace.json) |
| `fase-2/skills/artifacts/SKILL.md` | `/home/<agent>/claude/.claude/skills/artifacts/` | Detecta cuándo publicar un Artifact en vez de responder en texto plano por el canal |
| `fase-2/skills/propagate-to-template/SKILL.md` | `/home/<agent>/claude/.claude/skills/propagate-to-template/` | Orquesta `template_push.py`; nunca push directo a `main`, siempre rama + PR revisado por el owner |
| `fase-2/skills/resume/SKILL.md` | `/home/<agent>/claude/.claude/skills/resume/` | Continuidad de sesiones vía `/resume_{session_id}`, delega en el subagente `session-continuity` |
| `fase-2/agents/the-scribe.md` | `/home/<agent>/claude/.claude/agents/the-scribe.md` | Agente Sonnet: gestión de correo |
| `fase-2/agents/the-seneschal.md` | `/home/<agent>/claude/.claude/agents/the-seneschal.md` | Agente Sonnet: gestión de calendario |
| `fase-2/agents/the-chronicler.md` | `/home/<agent>/claude/.claude/agents/the-chronicler.md` | Agente Sonnet: extrae memorias de transcripts, inserta en `agent_memory` |
| `fase-2/agents/council-of-elders.md` | `/home/<agent>/claude/.claude/agents/council-of-elders.md` | Agente Opus: orquestador de deliberación, lanza `council-warden` en paralelo |
| `fase-2/agents/council-warden.md` | `/home/<agent>/claude/.claude/agents/council-warden.md` | Agente Sonnet: evaluador individual de una decisión bajo un rol/criterio concreto |
| `fase-2/agents/the-seeker.md` | `/home/<agent>/claude/.claude/agents/the-seeker.md` | Agente Opus: orquesta scouts Haiku en paralelo con refinamiento gap-driven, sintetiza informe |
| `fase-2/agents/the-seeker-scout.md` | `/home/<agent>/claude/.claude/agents/the-seeker-scout.md` | Agente Haiku: búsqueda + lectura en profundidad para una sub-query |
| `fase-2/agents/session-continuity.md` | `/home/<agent>/claude/.claude/agents/session-continuity.md` | Agente aislado: lee un transcript anterior y devuelve el estado de la tarea en curso |
| `fase-2/agents/self-improve.md` | `/home/<agent>/claude/.claude/agents/self-improve.md` | Agente semanal de automejora: audita hooks/tests/config/memorias con evidencia de `self_improve.py` |

## Fase futura — Scripts de fases 3-7

| Fichero | Ruta destino en el LXC | Propósito |
|---------|------------------------|-----------|
| `fase-futura/scripts/lib/chronicler.py` | `/home/<agent>/workspace/scripts/lib/chronicler.py` | SessionEnd (invocado desde `reset_sequence.py`, no desde el hook): distila, llama al agente `the-chronicler`, inserta memorias, genera material de self-improve |
| `fase-futura/scripts/lib/distill-transcript.py` | `/home/<agent>/workspace/scripts/lib/distill-transcript.py` | Reduce el transcript `.jsonl` a diálogo limpio `USER`/`<AGENT>` |
| `fase-futura/scripts/lib/reset_sequence.py` | `/home/<agent>/workspace/scripts/lib/reset_sequence.py` | `/reset`: guarda memoria (chronicler) y SOLO DESPUÉS reinicia `claude-telegram.service` — el reinicio ocurre siempre, con tope de espera |
| `fase-futura/scripts/lib/manual_reset.py` | `/home/<agent>/workspace/scripts/lib/manual_reset.py` | Atajo manual para reiniciar la sesión de Telegram colgada; sustituye a `autoreset.py` (retirado) |
| `fase-futura/scripts/lib/midnight.py` | `/home/<agent>/workspace/scripts/lib/midnight.py` | Job de medianoche: recorre `schedule_config` y rehace el día — `daily_schedule` (slots) + `agent_inbox` (`scheduled_task`, incluye mantenimiento `kind='core'`) |
| `fase-futura/scripts/lib/self_improve.py` | `/home/<agent>/workspace/scripts/lib/self_improve.py` | Recopilación determinista de evidencia mecánica para el agente `self-improve` |
| `fase-futura/scripts/lib/template_sync.py` | `/home/<agent>/workspace/scripts/lib/template_sync.py` | Pull diario de `~/template/` desde `claude-agent-deploy` (fase 1: solo lectura) |
| `fase-futura/config/calendars.json.example` | plantilla para `/home/<agent>/workspace/config/calendars.json` | Configuración de calendarios (copiar sin `.example`, rellenar IDs reales) |

---

## Raíz del repo

| Fichero | Propósito |
|---------|-----------|
| `install-agent.sh` | Instalador único: aplica los placeholders, despliega fase 0-2, arranca el servicio |
| `POST-INSTALL.md` | Checklist manual tras `install-agent.sh` (secrets, AppArmor enforce, onboarding) |
| `propagation-manifest.json` | Qué es propagable de vuelta al template y adónde aterriza (lo usa `template_push.py`) |
| `self-improve-inspection/` | **Huérfano** — 3 ficheros (`agent-inspection.md`, `CLAUDE-inspeccion.md`, `skill-inspection.md`) sin referencia desde `install-agent.sh`, `agents/self-improve.md` ni este índice; apuntan a una sección de un documento de diseño que no existe en el repo. Pendiente de decisión: enganchar al prompt de `self-improve` o retirar. |

---

## Notas de despliegue

- **Password de PostgreSQL:** en `init-db.sql` sustituir `<SUSTITUIR_PASSWORD>` por el
  generado con `openssl rand -base64 24`. Debe coincidir con `POSTGRES_CONNECTION_STRING`
  de `/etc/<agent>/secrets.env`.
- **Permisos de ejecución:** todos los scripts Python de `workspace/scripts/hooks/`
  y los `.sh`/`.py` de `workspace/scripts/lib/` requieren `chmod +x` (ver cabecera de cada uno).
- **AppArmor:** cargar primero con `aa-complain`, ejercitar, validar denegaciones y solo
  entonces `aa-enforce`.
- **GITHUB_TOKEN:** no lo provisiona el instalador por defecto. Sin él, `template_push.py apply`
  falla con un mensaje claro — el `preview` (solo lectura) funciona igual sin él.
- **`workspace/state/`:** debe quedar con permisos de escritura para el usuario `<agent>`
  (lo usan `template_sync.py`/`template_push.py` para el lock y `instance-identity.json`).
- **the-chronicler / self-improve:** los agentes referenciados deben existir en
  `/home/<agent>/claude/.claude/agents/` antes de activar `chronicler.py` y `self_improve.py`.
- **Sustitución de placeholders:** antes de desplegar, reemplazar en TODOS los ficheros los
  placeholders listados arriba. La mayoría los resuelve `install-agent.sh`; `<owner_profile_description>`
  y el resto del perfil de usuario los rellena el onboarding conversacional, no el instalador.

# Índice de entregables de implementación — <Agent>

Plantilla genérica para desplegar un agente Claude Code. Todos los placeholders
(`<agent>`, `<AGENT>`, `<Agent>`, `<owner_chat_id>`, `<owner_name>`) 
requieren sustitución antes del despliegue.

Convención de despliegue: cada fichero indica en su cabecera la ruta final dentro del
LXC (`/home/<agent>/...`, `/etc/...`, `/etc/systemd/system/...`) y el `chmod +x` cuando aplica.

---

## Fase 0 — Núcleo que responde (LXC + systemd + Claude Code + plugin + kernel)

| Fichero | Ruta destino en el LXC | Propósito |
|---------|------------------------|-----------|
| `fase-0/CLAUDE.md` | `/home/<agent>/claude/CLAUDE.md` | Kernel del agente: identidad, reglas inviolables, perfil del usuario, memoria, infraestructura, comandos especiales |
| `fase-0/init-db.sql` | `/tmp/init-db.sql` (ejecutar; vive en `workspace/scripts/lib/`) | Crea usuario, BD, las 12 tablas + vista + índices, RLS y dato semilla del owner |
| `fase-0/systemd/claude-telegram.service` | `/etc/systemd/system/claude-telegram.service` | Servicio principal (User=<agent>, <AGENT>_CONTEXT=main, hooks vía ExecStartPre) |
| `fase-0/systemd/heartbeat.timer` | `/etc/systemd/system/heartbeat.timer` | Timer monotónico cada 5 min (proactividad) |
| `fase-0/systemd/heartbeat.service` | `/etc/systemd/system/heartbeat.service` | Sesión efímera `claude --print` que procesa el inbox |
| `fase-0/systemd/midnight.timer` | `/etc/systemd/system/midnight.timer` | Timer de calendario 00:00 (Persistent) |
| `fase-0/systemd/midnight.service` | `/etc/systemd/system/midnight.service` | Job de medianoche (planning + reconciliación) |
| `fase-0/etc/sudoers.d/<agent>` | `/etc/sudoers.d/<agent>` | Sudo NOPASSWD restringido a systemctl de unidades fijas |
| `fase-0/scripts/lib/claude-telegram-start.sh` | `/home/<agent>/workspace/scripts/lib/claude-telegram-start.sh` | Wrapper pty; `--settings` por ruta (no JSON inline) |
| `fase-0/scripts/lib/<agent>-patch-telegram-plugin.sh` | `/home/<agent>/workspace/scripts/lib/<agent>-patch-telegram-plugin.sh` | Sobreescribe `server.ts` del plugin |
| `fase-0/scripts/lib/<agent>-register-commands.py` | `/home/<agent>/workspace/scripts/lib/<agent>-register-commands.py` | Registra comandos del bot (scope chat del owner) |
| `fase-0/scripts/lib/<agent>-telegram-server.ts` | `(caché del plugin)` | Copia literal del `server.ts` modificado |
| `fase-0/scripts/lib/prompts/heartbeat.md` | `/home/<agent>/workspace/scripts/lib/prompts/heartbeat.md` | Prompt del heartbeat (reclama inbox, alertas críticas) |

## Fase 1 — Seguridad fundacional (guardrails, settings, tabla de rutas, AppArmor)

| Fichero | Ruta destino en el LXC | Propósito |
|---------|------------------------|-----------|
| `fase-1/settings.json` | `/home/<agent>/claude/.claude/settings.json` | Plano de control sesión principal: allow/deny + 7 hooks |
| `fase-1/settings-background.json` | `/home/<agent>/claude/.claude/settings-background.json` | Subagentes/cron: sin Telegram, solo guardrails (PreToolUse, Stop) |
| `fase-1/scripts/lib/<agent>_common.py` | `/home/<agent>/workspace/scripts/lib/<agent>_common.py` | Módulo compartido: contexto, logging, salidas, scoring, detectores |
| `fase-1/scripts/lib/<agent>_workspace.json` | `/home/<agent>/workspace/scripts/lib/<agent>_workspace.json` | Tabla de rutas: tier y política por directorio |
| `fase-1/scripts/hooks/<agent>-pretooluse-hook.py` | `/home/<agent>/workspace/scripts/hooks/<agent>-pretooluse-hook.py` | Guardrail principal (FAIL-CLOSED): deny-list, reglas inviolables, scoring |
| `fase-1/scripts/hooks/<agent>-userprompt-hook.py` | `/home/<agent>/workspace/scripts/hooks/<agent>-userprompt-hook.py` | Bandera de origen, anti-injection (fail-open), intercepción `/context` `/skills` `/agents` `/reset` |
| `fase-1/scripts/hooks/<agent>-stop-hook.py` | `/home/<agent>/workspace/scripts/hooks/<agent>-stop-hook.py` | Garantía de respuesta (FAIL-OPEN): guardas anti-deadlock + rewake |
| `fase-1/apparmor/home.<agent>.claude` | `/etc/apparmor.d/home.<agent>.claude` | Perfil AppArmor completo (red inet stream/dgram, binarios ix, denies) |

## Fase 2 — Harness completo y observabilidad (feedback + tests + skills)

| Fichero | Ruta destino en el LXC | Propósito |
|---------|------------------------|-----------|
| `fase-2/scripts/hooks/<agent>-posttooluse-hook.py` | `/home/<agent>/workspace/scripts/hooks/<agent>-posttooluse-hook.py` | Feedback (FAIL-OPEN): ticker + telemetría; silencio fuera de main |
| `fase-2/scripts/hooks/<agent>-notification-hook.py` | `/home/<agent>/workspace/scripts/hooks/<agent>-notification-hook.py` | Feedback: congela el feed en aprobación pendiente |
| `fase-2/scripts/hooks/<agent>-precompact-hook.py` | `/home/<agent>/workspace/scripts/hooks/<agent>-precompact-hook.py` | Señal de pre-compactación a `/tmp/<agent>-precompact-flag` |
| `fase-2/scripts/hooks/<agent>-sessionstart-hook.py` | `/home/<agent>/workspace/scripts/hooks/<agent>-sessionstart-hook.py` | Limpia banderas huérfanas + log de inicio (FAIL-OPEN) |
| `fase-2/scripts/hooks/<agent>-mcp-postgres-only.json` | `/home/<agent>/workspace/scripts/hooks/<agent>-mcp-postgres-only.json` | MCP config para subagentes (solo Postgres, vía env var) |
| `fase-2/tests/conftest.py` | `/home/<agent>/workspace/tests/conftest.py` | Fixtures pytest (runner de hooks, tmp aislado, contextos) |
| `fase-2/tests/test_<agent>_common.py` | `/home/<agent>/workspace/tests/test_<agent>_common.py` | Tests del módulo compartido (context, lookup_tier, scoring, detectores, salidas) |
| `fase-2/tests/test_pretooluse.py` | `/home/<agent>/workspace/tests/test_pretooluse.py` | Deny-list, reglas inviolables, scoring, fail-closed, aislamiento |
| `fase-2/tests/test_userprompt.py` | `/home/<agent>/workspace/tests/test_userprompt.py` | Bandera de origen, anti-injection fail-open, anti-aprobación, intercepción |
| `fase-2/tests/test_stop.py` | `/home/<agent>/workspace/tests/test_stop.py` | Guardas del Stop hook (todos los casos §9.1) |
| `fase-2/tests/test_isolation.py` | `/home/<agent>/workspace/tests/test_isolation.py` | Aislamiento por los 4 contextos (main/subagent/background/cron) |
| `fase-2/skills/recall-memory/SKILL.md` | `/home/<agent>/claude/.claude/skills/recall-memory/SKILL.md` | Skill de recuperación de memorias desde PostgreSQL (reciente, keyword, fulltext, fecha) |
| `fase-2/skills/the-scribe/SKILL.md` | `/home/<agent>/claude/.claude/skills/the-scribe/SKILL.md` | Skill de gestión de correo: triaje, búsqueda, redacción de borradores |
| `fase-2/skills/the-seneschal/SKILL.md` | `/home/<agent>/claude/.claude/skills/the-seneschal/SKILL.md` | Skill de gestión de calendario: consulta, detección de conflictos, gestión de eventos |
| `fase-2/skills/council-of-elders/SKILL.md` | `/home/<agent>/claude/.claude/skills/council-of-elders/SKILL.md` | Skill de deliberación estructurada para decisiones complejas (lanza Opus × 3) |
| `fase-2/skills/the-seeker/SKILL.md` | `/home/<agent>/claude/.claude/skills/the-seeker/SKILL.md` | Skill de búsqueda e investigación (modo directo o subagente según complejidad) |
| `fase-2/agents/the-scribe.md` | `/home/<agent>/claude/.claude/agents/the-scribe.md` | Agente Sonnet: gestión de correo — triaje, clasificación y redacción de borradores |
| `fase-2/agents/the-seneschal.md` | `/home/<agent>/claude/.claude/agents/the-seneschal.md` | Agente Sonnet: gestión de calendario — consulta, detección de conflictos y gestión de eventos |
| `fase-2/agents/the-chronicler.md` | `/home/<agent>/claude/.claude/agents/the-chronicler.md` | Agente Sonnet: extrae memorias de transcripts e inserta en agent_memory |
| `fase-2/agents/council-of-elders.md` | `/home/<agent>/claude/.claude/agents/council-of-elders.md` | Agente Opus: rol único en un council de 3 instancias paralelas |
| `fase-2/agents/the-seeker.md` | `/home/<agent>/claude/.claude/agents/the-seeker.md` | Agente Opus: orquesta scouts Haiku en paralelo con refinamiento gap-driven (hasta 3 rondas), sintetiza y escribe informe en workspace/docs/informes/ |
| `fase-2/agents/the-seeker-scout.md` | `/home/<agent>/claude/.claude/agents/the-seeker-scout.md` | Agente Haiku: búsqueda + lectura en profundidad (hasta 5 URLs con parada por suficiencia) para una sub-query; devuelve JSON con hallazgos y gaps al orquestador (invocado por the-seeker) |

## Fase futura — Scripts de fases 3-7

| Fichero | Ruta destino en el LXC | Propósito |
|---------|------------------------|-----------|
| `fase-futura/scripts/lib/chronicler.py` | `/home/<agent>/workspace/scripts/lib/chronicler.py` | SessionEnd: distila, llama a the-chronicler, inserta memorias, genera material self-improve, notifica |
| `fase-futura/scripts/lib/distill-transcript.py` | `/home/<agent>/workspace/scripts/lib/distill-transcript.py` | Reduce el transcript a líneas relevantes |
| `fase-futura/scripts/lib/<agent>-autoreset.py` | `/home/<agent>/workspace/scripts/lib/<agent>-autoreset.py` | Reinicio nocturno (idle>1h → restart; activo → reintento) |
| `fase-futura/scripts/lib/self-improve.sh` | `/home/<agent>/workspace/scripts/lib/self-improve.sh` | Lanza el agente self-improve (<AGENT>_CONTEXT=cron, settings-background) |
| `fase-futura/scripts/lib/midnight.py` | `/home/<agent>/workspace/scripts/lib/midnight.py` | Job de medianoche: recorre schedule_config y rehace el día — daily_schedule (slots) + agent_inbox (scheduled_task, incluye mantenimiento kind='core') |

---

## Notas de despliegue

- **Password de PostgreSQL:** en `init-db.sql` sustituir `<SUSTITUIR_PASSWORD>` por el
  generado con `openssl rand -base64 24`. Debe coincidir con `POSTGRES_CONNECTION_STRING`
  de `/etc/<agent>/secrets.env`.
- **Permisos de ejecución:** todos los scripts Python de hooks (`workspace/scripts/hooks/<agent>-*.py`)
  y los `.sh`/`.py` de `workspace/scripts/lib/` requieren `chmod +x` (ver cabecera de cada uno).
- **AppArmor:** cargar primero con `aa-complain`, ejercitar, validar denegaciones y solo
  entonces `aa-enforce`.
- **Filtro anti-injection (`clean`):** el `<agent>-userprompt-hook.py` arranca con stub fail-open
  hasta que `/home/<agent>/apps/bin/clean` exista (instalación con permiso del usuario).
- **the-chronicler / self-improve:** los agentes referenciados deben existir en
  `/home/<agent>/claude/.claude/agents/` antes de activar `chronicler.py` y `self-improve.sh`.
- **Sustitución de placeholders:** antes de desplegar, reemplazar en TODOS los ficheros:
  - `<agent>` → nombre del agente en minúsculas
  - `<Agent>` → nombre del agente con mayúscula inicial
  - `<AGENT>` → nombre del agente en mayúsculas
  - `<owner_chat_id>` → Telegram chat_id del propietario
  - `<owner_name>` → nombre del propietario

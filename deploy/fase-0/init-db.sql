-- init-db.sql — inicialización completa de la base de datos de <Agent>
-- Ejecutar como: sudo -u postgres psql -f init-db.sql
--
-- IMPORTANTE: sustituir '<SUSTITUIR_PASSWORD>' por el password real antes de ejecutar.
--   Generar con: openssl rand -base64 24
--   Este password DEBE coincidir con el de POSTGRES_CONNECTION_STRING en
--   /etc/<agent>/secrets.env (paso 15 del checklist §1.8).
--
-- Crea: usuario <agent>, base de datos agents, las 12 tablas + índices,
-- Row Level Security sobre agent_memory y el dato semilla del owner.
-- El orden respeta dependencias FK y es ejecutable de arriba a abajo.

-- ============================================================================
-- 1. Usuario y base de datos
-- ============================================================================

CREATE USER <agent> WITH PASSWORD '<SUSTITUIR_PASSWORD>';
CREATE DATABASE agents OWNER <agent>;

\c agents

-- ============================================================================
-- 2. Schema completo (§1.2) — 12 tablas + índices
--    Orden FK-seguro:
--      agent_domains, pending_prompts, agent_memory, slot_type, scheduled_task,
--      agent_inbox, core_task, schedule_config, daily_schedule, agent_telemetry,
--      agent_backup_log, agent_user_roles
-- ============================================================================

-- ---- §2 Kernel: agent_domains -----------------------------------------------
CREATE TABLE agent_domains (
  id       SERIAL PRIMARY KEY,
  name     VARCHAR(50) UNIQUE NOT NULL,
  keywords TEXT[],
  backend  VARCHAR(20),
  config   JSONB,
  active   BOOLEAN DEFAULT true
);

-- ---- §3 Telegram: pending_prompts ------------------------------------------
CREATE TABLE pending_prompts (
  request_id       TEXT PRIMARY KEY,          -- ID corto (<=8 chars, parte del callback_data)
  chat_id          BIGINT NOT NULL,
  context          JSONB,                     -- datos que el agente necesita al retomar
  options          JSONB,                     -- opciones enviadas
  created_at       TIMESTAMPTZ DEFAULT NOW(),
  expires_at       TIMESTAMPTZ,               -- NOW() + '10 minutes' por defecto
  status           TEXT DEFAULT 'pending',    -- 'pending' | 'answered' | 'expired'
  answer           TEXT,                      -- valor elegido por el usuario
  requested_by     BIGINT,                    -- user_id del solicitante
  approve_by       BIGINT,                    -- user_id del owner que debe aprobar
  notify_requester BOOLEAN DEFAULT true
);

CREATE INDEX idx_pending_approve ON pending_prompts (approve_by, status)
  WHERE status = 'pending';

-- ---- §6 Memoria: agent_memory ----------------------------------------------
CREATE TABLE agent_memory (
  -- Identidad
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id       BIGINT DEFAULT NULL,             -- NULL = memoria del agente; valor = usuario específico

  -- Contenido
  content       TEXT NOT NULL,                   -- el hecho en lenguaje natural
  category      TEXT NOT NULL,                   -- user|feedback|project|reference
  keywords      TEXT[],                          -- búsqueda léxica exacta (GIN)
  entities      TEXT[],                          -- personas/proyectos mencionados (GIN)
  importance    SMALLINT DEFAULT 3,              -- 1(efímero) a 5(permanente)
  metadata      JSONB DEFAULT '{}',              -- contexto flexible sin migraciones

  -- Vigencia
  fecha         TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at    TIMESTAMPTZ,                     -- NULL = permanente; valor = TTL explícito
  superseded_by UUID REFERENCES agent_memory(id), -- si el hecho fue reemplazado

  -- Recuperación
  last_accessed TIMESTAMPTZ,                     -- para decay y LRU
  access_count  INT DEFAULT 0,                   -- frecuencia de recuperación
  session_id    UUID                             -- sesión de origen
  -- embedding vector(384)                       -- NULL hasta activar pgvector (fase 2)
);

CREATE INDEX ON agent_memory (category);
CREATE INDEX ON agent_memory USING GIN (keywords);
CREATE INDEX ON agent_memory USING GIN (entities);
CREATE INDEX ON agent_memory (expires_at) WHERE superseded_by IS NULL;
CREATE INDEX idx_agent_memory_user ON agent_memory (user_id);

-- ---- §8 Proactividad: slot_type --------------------------------------------
CREATE TABLE slot_type (
  id             SERIAL PRIMARY KEY,
  name           TEXT NOT NULL UNIQUE,
  label          TEXT NOT NULL,
  is_modifier    BOOLEAN NOT NULL DEFAULT false,
  critical_limit INT,
  high_limit     INT NOT NULL,
  medium_limit   INT NOT NULL,
  low_limit      INT NOT NULL,
  CONSTRAINT chk_limits_nonneg CHECK (
    (critical_limit IS NULL OR critical_limit >= 0)
    AND high_limit >= 0 AND medium_limit >= 0 AND low_limit >= 0
  )
);

-- ---- §8 Proactividad: scheduled_task ---------------------------------------
CREATE TABLE scheduled_task (
  id          SERIAL PRIMARY KEY,
  name        TEXT NOT NULL UNIQUE,
  label       TEXT NOT NULL,
  kind        TEXT NOT NULL CHECK (kind IN ('briefing','monitor')),
  prompt_file TEXT NOT NULL,
  severity    TEXT NOT NULL DEFAULT 'medium'
                CHECK (severity IN ('critical','high','medium','low')),
  enabled     BOOLEAN NOT NULL DEFAULT true
);

-- ---- §8 Proactividad: agent_inbox ------------------------------------------
CREATE TABLE agent_inbox (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  source            TEXT NOT NULL CHECK (source <> ''),
  event_type        TEXT NOT NULL
                      CHECK (event_type IN ('alert','reminder','info','task',
                                            'scheduled_task','follow_up')),
  payload           JSONB NOT NULL,
  severity          TEXT NOT NULL DEFAULT 'medium'
                      CHECK (severity IN ('critical','high','medium','low')),
  agent             TEXT
                      CHECK (agent IS NULL OR agent IN
                        ('any','opus','self-improve','session-summarizer')),
  dedupe_key        TEXT,
  scheduled_task_id INT REFERENCES scheduled_task(id), -- identidad: esta fila ES la ejecución de este monitor/briefing
  target_task_id    INT REFERENCES scheduled_task(id), -- routing: item derivado que solo puede recoger este monitor/briefing
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  process_after     TIMESTAMPTZ NOT NULL DEFAULT now(),
  claimed_at        TIMESTAMPTZ,                         -- reserva atómica: el heartbeat lo rellena al reclamar
  processed_at      TIMESTAMPTZ,
  decision          TEXT
                      CHECK (decision IS NULL OR decision IN
                        ('sent','sent_in_briefing','queued_briefing',
                         'deferred','delegated','dropped')),

  CONSTRAINT chk_terminal_state CHECK (
    (processed_at IS NULL
       AND (decision IS NULL OR decision IN ('queued_briefing','deferred','delegated')))
    OR
    (processed_at IS NOT NULL
       AND decision IN ('sent','sent_in_briefing','dropped'))
  )
);

CREATE INDEX inbox_pending
  ON agent_inbox (process_after)
  WHERE processed_at IS NULL;

CREATE UNIQUE INDEX inbox_dedupe
  ON agent_inbox (source, event_type, dedupe_key)
  WHERE processed_at IS NULL AND dedupe_key IS NOT NULL;

-- ---- §8 Proactividad: core_task --------------------------------------------
CREATE TABLE core_task (
  id               SERIAL PRIMARY KEY,
  name             TEXT NOT NULL UNIQUE,
  description      TEXT,
  schedule_cron    TEXT NOT NULL,           -- expresión cron estándar: '0 4 * * *'
  script_path      TEXT NOT NULL,           -- ruta dentro del home del agente
  enabled          BOOLEAN NOT NULL DEFAULT true,
  last_enqueued_at TIMESTAMPTZ,             -- cuándo midnight generó la última entrada en inbox
  last_executed_at TIMESTAMPTZ              -- cuándo el heartbeat procesó la última ejecución
);

-- Filas iniciales. Solo se siembra aquí lo que ya cumple el contrato de salida
-- de heartbeat.py ({"ok","notify"} por stdout) -- self-improve/autoreset/
-- permission-audit siguen documentados en el diseño pero todavía no lo
-- cumplen (ver tareas-pendientes.md), sembrarlos generaría fallos silenciosos
-- cada vez que corrieran.
INSERT INTO core_task (name, description, schedule_cron, script_path) VALUES
  ('template-sync', 'Pull diario de ~/template/ desde claude-agent-deploy (fase 1: solo lectura)',
   '0 5 * * *', 'workspace/scripts/lib/template_sync.py')
ON CONFLICT (name) DO NOTHING;

-- ---- §8 Proactividad: schedule_config --------------------------------------
CREATE TABLE schedule_config (
  id                  SERIAL PRIMARY KEY,
  day_type            TEXT NOT NULL,
  date_from           DATE,
  date_to             DATE,
  kind                TEXT NOT NULL CHECK (kind IN ('slot','task')),
  slot_type_id        INT REFERENCES slot_type(id),
  scheduled_task_id   INT REFERENCES scheduled_task(id),
  time_from           TIME NOT NULL,
  time_to             TIME,
  enabled             BOOLEAN NOT NULL DEFAULT true,

  CONSTRAINT chk_kind CHECK (
    (kind = 'slot'
       AND slot_type_id IS NOT NULL
       AND scheduled_task_id IS NULL
       AND time_to IS NOT NULL)
    OR
    (kind = 'task'
       AND scheduled_task_id IS NOT NULL
       AND slot_type_id IS NULL
       AND time_to IS NULL)
  ),
  CONSTRAINT chk_specific_dates CHECK (
    (day_type = 'S' AND date_from IS NOT NULL)
    OR
    (day_type <> 'S' AND date_from IS NULL AND date_to IS NULL)
  ),
  CONSTRAINT chk_date_order CHECK (
    date_to IS NULL OR date_from IS NULL OR date_to >= date_from
  ),
  CONSTRAINT chk_day_type CHECK (
    day_type IN ('1','2','3','4','5','6','7','H','T','S')
  )
);

CREATE INDEX ON schedule_config (day_type, kind) WHERE enabled = true;

-- ---- §8 Proactividad: daily_schedule ---------------------------------------
CREATE TABLE daily_schedule (
  id                SERIAL PRIMARY KEY,
  date              DATE NOT NULL,
  slot_type_name    TEXT NOT NULL,
  is_modifier       BOOLEAN NOT NULL DEFAULT false,
  start_ts          TIMESTAMPTZ NOT NULL,
  end_ts            TIMESTAMPTZ NOT NULL,
  priority          INT NOT NULL DEFAULT 1,

  critical_limit    INT,
  high_limit        INT NOT NULL,
  medium_limit      INT NOT NULL,
  low_limit         INT NOT NULL,

  critical_sent     INT NOT NULL DEFAULT 0,
  high_sent         INT NOT NULL DEFAULT 0,
  medium_sent       INT NOT NULL DEFAULT 0,
  low_sent          INT NOT NULL DEFAULT 0,

  calendar_event_id TEXT,

  CONSTRAINT chk_window CHECK (end_ts > start_ts)
);

CREATE UNIQUE INDEX daily_schedule_base
  ON daily_schedule (date, slot_type_name, start_ts)
  WHERE calendar_event_id IS NULL;

CREATE UNIQUE INDEX daily_schedule_modifier
  ON daily_schedule (date, calendar_event_id)
  WHERE calendar_event_id IS NOT NULL;

CREATE INDEX daily_schedule_active
  ON daily_schedule (date, start_ts, end_ts);

-- ---- §10 Observabilidad: agent_telemetry ------------------------------------
CREATE TABLE agent_telemetry (
  id          BIGSERIAL PRIMARY KEY,
  ts          TIMESTAMPTZ NOT NULL DEFAULT now(),
  session_id  TEXT,
  event_type  TEXT NOT NULL,   -- 'tool_call' | 'hook_decision' | 'session_start' | 'session_end'
  tool_name   TEXT,
  decision    TEXT,            -- 'allow' | 'block' | 'review' | 'require_confirmation'
  duration_ms INT,
  context     TEXT,            -- AGENT_CONTEXT: main | subagent | background | cron
  error       TEXT,
  metadata    JSONB DEFAULT '{}'
);

CREATE INDEX ON agent_telemetry (session_id);
CREATE INDEX ON agent_telemetry (ts);
CREATE INDEX ON agent_telemetry (tool_name, decision);

-- ---- §11 Resiliencia: agent_backup_log --------------------------------------
CREATE TABLE agent_backup_log (
  id          BIGSERIAL PRIMARY KEY,
  ts          TIMESTAMPTZ DEFAULT now(),
  type        TEXT,          -- 'full' | 'diff' | 'verify'
  status      TEXT,          -- 'ok' | 'failed'
  size_bytes  BIGINT,
  duration_s  INT,
  error       TEXT
);

-- ---- §13 Multiusuario: agent_user_roles -------------------------------------
CREATE TABLE agent_user_roles (
  id           BIGSERIAL PRIMARY KEY,
  user_id      BIGINT NOT NULL UNIQUE,   -- Telegram user_id (o equivalente según canal)
  name         TEXT NOT NULL,
  role         TEXT NOT NULL CHECK (role IN ('owner','family','guest')),
  tools_allow  TEXT[],                   -- allowlist explícita (NULL = defaults del rol)
  tools_deny   TEXT[],                   -- denylist explícita (prevalece sobre tools_allow)
  active       BOOLEAN DEFAULT true,
  created_at   TIMESTAMPTZ DEFAULT now(),
  updated_at   TIMESTAMPTZ DEFAULT now(),
  notes        TEXT
);

-- ============================================================================
-- 3. Permisos: el agente necesita acceso completo a todas las tablas
-- ============================================================================
GRANT USAGE ON SCHEMA public TO <agent>;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO <agent>;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO <agent>;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO <agent>;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO <agent>;

-- ============================================================================
-- 4. Dato semilla: owner del sistema
-- ============================================================================
INSERT INTO agent_user_roles (user_id, name, role) VALUES (<owner_chat_id>, '<owner_name>', 'owner');

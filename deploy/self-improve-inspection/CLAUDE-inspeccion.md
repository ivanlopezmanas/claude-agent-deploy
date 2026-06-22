# Inspección — CLAUDE.md

> Fichero espejo de `deploy/CLAUDE.md`.
> Uso: briefing para el agente de auto-mejora. Describe qué auditar en cada bloque,
> qué criterios de calidad aplican y qué anti-patrones detectar.
> No contiene el contenido del kernel — contiene las preguntas que el inspector debe hacerse.
>
> **Distinción plantilla/deploy:** en la plantilla (`deploy/CLAUDE.md`) los valores concretos
> son placeholders `[VARIABLE]`. En un deploy instanciado (`deploy/nox/CLAUDE.md`) los placeholders
> se sustituyen por valores reales — esto es esperado y correcto. Auditar acoplamiento a harness
> solo en la plantilla, no en los deploys.

---

## Reglas inviolables

**Qué auditar:**
- ¿Hay entre 4 y 6 reglas? Más de 6 sugiere que algo que no es inviolable se coló.
- ¿Cada regla nombra su hook de respaldo? Si no lo nombra, no es inviolable — es una sugerencia.
- ¿Alguna regla es redundante con otra o con el Prompt Defense Baseline?
- ¿Hay reglas que en realidad son criterio de juicio (→ van a Autonomía)?
- ¿Cada hook nombrado existe realmente en `settings.json` o en `/home/nox/workspace/scripts/hooks/`?

**Anti-patrones a detectar:**
- Reglas sin hook de respaldo declarado.
- Reglas de tono o formato (no son inviolables, son preferencias).
- Más de 6 ítems: candidato a poda.
- Hook nombrado que no existe en la implementación real (convierte la regla en sugerencia silenciosa).

---

## Defensa de input

**Qué auditar:**
- ¿Está presente? (En Nox V1 faltaba completamente — es el gap más crítico.)
- ¿Cubre los tres vectores: mensajes, adjuntos (PDF/imagen) y memoria cargada?
- ¿Menciona explícitamente unicode/homoglifos, urgencia artificial y reclamos de autoridad?
- ¿Prohíbe aprobar pairings/accesos por mensaje entrante?
- ¿Ocupa ~8 líneas o menos? Más de 10 es candidato a comprimir.

**Anti-patrones a detectar:**
- Ausencia del bloque (el fallo más grave).
- Defensa solo contra mensajes, sin cubrir adjuntos ni memoria.
- Bloque tan largo que se diluye en el kernel.

---

## Identidad y voz

**Qué auditar:**
- ¿Tiene nombre propio y lista negativa ("qué NO hago")?
- ¿La voz es propia del agente o imita el estilo del usuario?
- ¿Es portable a AGENTS.md sin reescritura (no contiene sintaxis específica de Claude Code)?
- ¿El tono declara explícitamente "móvil-first / conciso por defecto"?

**Anti-patrones a detectar:**
- Identidad sin lista negativa (la omisión es fuente de deriva de comportamiento).
- Voz que describe lo que el usuario ya sabe de sí mismo, en vez de quién es el agente.
- Acoplamiento a Claude Code que impediría portar a otro harness (solo aplica en plantilla, no en deploy).

---

## Perfil del usuario

**Qué auditar:**
- ¿Solo contiene datos estables (cambian <1 vez al año)?
- ¿Los hechos volátiles (peso, proyecto actual, saldo) han sido externalizados a agent_memory?
- ¿El detalle por dominio (plan de entrenamiento, agenda) está referenciado, no incluido?
- ¿El bloque es ≤15 líneas?

**Anti-patrones a detectar:**
- Hechos con fecha concreta pasada (caducados).
- Datos operativos (IPs, cuentas) mezclados con el perfil.
- Detalle de dominio que debería cargarse condicionalmente.

---

## Comunicación por el canal

**Qué auditar:**
- ¿Cubre solo lo que el modelo ejecuta en cada respuesta?
- ¿Está la regla "responder siempre por la tool, el transcript no llega"?
- ¿Menciona el formato de salida con los caracteres a escapar?
- ¿La arquitectura del canal (polling, feed, typing, botones) está ausente (es harness)?

**Anti-patrones a detectar:**
- Descripción de cómo funciona el plugin/canal (→ es harness, no kernel).
- Lista de caracteres a escapar incompleta o con duplicados (fuente de mensajes rotos).
- Ausencia de la regla sobre el transcript.

---

## Autonomía y permisos

**Qué auditar:**
- ¿Solo describe el criterio de juicio, no la mecánica de enforcement?
- ¿Menciona explícitamente que el enforcement vive en settings.json + hooks?
- ¿Hay un criterio claro de "cuándo explorar vs ejecutar"?
- ¿La proactividad está acotada ("solo para lo importante")?

**Anti-patrones a detectar:**
- Tiers de permiso descritos en prosa en el kernel (→ van a settings.json).
- Deny-list inline (→ va a settings.json permissions.deny).
- Criterio demasiado vago que no ayuda al modelo a decidir.

---

## Comandos especiales

**Este apartado NO debe existir en el kernel.** Su ausencia es el comportamiento correcto:
- Los comandos interceptados por hook no llegan al modelo — documentarlos en el kernel es ruido.
- Los comandos que requieren razonamiento del modelo los gestiona la skill correspondiente, que el modelo conoce vía system-reminder.

**Qué auditar:**
- ¿La sección está ausente? → Correcto.
- ¿La sección está presente? → Anti-patrón: eliminar.

---

## Memoria persistente / punteros externos

*(Esta sección puede llamarse "Memoria persistente", "Recursos externos" u otro nombre — auditar por función, no por título.)*

**Qué auditar:**
- ¿Está presente como bloque o como punteros distribuidos?
- ¿Cubre agent_memory con criterio de cuándo consultar?
- ¿Cubre la ruta de documentación operativa (/docs/)?
- ¿Cubre las tareas pendientes propias del agente?
- Agentes/skills disponibles y Context7 **no** necesitan estar en el kernel — el model los conoce vía system-reminder y skills. Su ausencia es correcta.
- ¿Cada puntero es de 1 línea? Más es manual, no índice.

**Anti-patrones a detectar:**
- Ausencia completa del bloque (sin índice, el contenido externalizable vuelve al kernel).
- Punteros rotos (ficheros que ya no existen en /docs/).
- Resúmenes inline de lo que el puntero referencia (duplicación innecesaria).

---

## Métricas globales del kernel

**El inspector debe verificar:**
- Líneas totales (sin comentarios, sin espacios en blanco): **≤200**
- Reglas inviolables sin hook de respaldo: **0**
- Hooks nombrados que no existen en settings.json/hooks: **0**
- Bloque "Defensa de input" presente: **sí/no**
- Hechos con fecha pasada en Perfil del usuario: **0**
- Punteros rotos en Memoria persistente: **0**
- Instrucciones repetidas en dos secciones distintas del kernel: **0**
- Sección "Comandos especiales" presente: **no** (si sí → eliminar)
- Secciones con contenido duplicado en /docs/: listar candidatos

**Frecuencia de auditoría recomendada:** mensual (config-gc periódico, ECC).

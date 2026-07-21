---
name: propagate-to-template
description: >
  Propaga una mejora ya hecha en esta instancia de vuelta al template
  (claude-agent-deploy), para que otras instancias desplegadas del mismo
  template puedan recibirla vía su pull diario. Invocación EXCLUSIVAMENTE
  deliberada — nunca heurística, nunca automática al terminar una tarea. Usar
  solo ante frases explícitas como "esto llévalo al template", "propaga esto
  al repo", "esto también en la plantilla", "generaliza este cambio para el
  template". No activar solo porque se acaba de tocar infraestructura — si el
  usuario no lo pide con estas palabras o equivalentes, no proponerlo.
version: 1.0.0
---

# Skill /propagate-to-template — Push de instancia a template (fase 2)

Tipo A (ejecuta directamente, no lanza subagente). Orquesta
`template_push.py`, que hace la parte determinista (clasificar, reversar,
guardia anti-fuga, git); la skill hace la parte que necesita criterio y
confirmación humana: qué ficheros exactos, y si el resultado se puede
empujar.

**Regla dura:** esta skill NUNCA push a `main` directamente por su cuenta,
y NUNCA mergea un PR. `template_push.py apply` empuja por defecto a una
rama + PR en borrador — el merge lo hace Iván, desde GitHub, cuando quiera.

---

## 1. Detección

Activar solo ante intención explícita y verbalizada: "esto al template",
"propágalo al repo", "llévalo a la plantilla", "generaliza este cambio",
"que las otras instancias también lo tengan". Si la señal es ambigua
("esto está bien, ¿no?" tras un cambio de infra) — NO activar, seguir
conversando normal. El usuario tiene que pedirlo con estas palabras o
equivalentes claros.

## 2. Confirmar el conjunto exacto de ficheros

Nunca asumir "todo lo de esta sesión". A partir del contexto de la
conversación (qué ficheros de producción se tocaron, en qué orden),
proponer una lista candidata concreta y preguntar:

> "¿Propagamos exactamente estos ficheros: [lista]? ¿Alguno de estos era
> específico de esta instancia y no debería ir al template?"

No seguir hasta tener el conjunto exacto confirmado por Iván. Un cambio en
un hook puede mezclar, en el mismo fichero, algo genérico (una regla nueva)
con algo específico de esta instancia (una IP, un path con el nombre real)
— eso lo separa el motor de reversa y el guard, no esta conversación, pero
el CONJUNTO de ficheros sí lo decide Iván aquí.

## 3. Preview — clasificar, reversar, guard (pasada 1)

Con el conjunto confirmado, ejecutar:

```bash
python3 /home/<agent>/workspace/scripts/lib/template_push.py preview <ruta1> <ruta2> ...
```

Leer el JSON de salida. Por cada ruta, `results[i]` trae `ok` y:

- `ok: false` con `reason` — el fichero se rechaza. Motivos posibles y qué
  decir a Iván en cada uno:
  - **"en la lista 'never' del manifiesto"** → nunca se propaga (secretos,
    memoria, identidad, estado local). No hay vuelta atrás aquí, no insistir.
  - **"no está en propagation-manifest.json"** → el fichero no tiene
    destino conocido en el repo. Se necesita añadir una regla a
    `propagation-manifest.json` (a mano, con permiso explícito, es un
    cambio al propio mecanismo) antes de poder propagar este fichero.
  - **"guard anti-fuga: ..."** → el motor de reversa dejó algo sin
    convertir (probablemente un valor de identidad que no está en
    `instance-identity.json` todavía, o texto libre que menciona algo
    sensible). Revisar el fichero de producción a mano antes de reintentar
    — nunca forzar el push ignorando esto.
  - **"fichero mixto sin marcadores"** → el fichero (típicamente
    `CLAUDE.md`) no tiene bloques `<!-- TEMPLATE:BEGIN -->`/`<!-- TEMPLATE:END -->`
    delimitando la parte agnóstica. Sin marcadores explícitos, no se
    propaga nada de ese fichero — nunca adivinar qué parte es agnóstica.
- `ok: true` con `old_content`/`new_content`/`changed` — la propuesta.

## 4. Presentar el diff y confirmar

Para cada `ok: true` con `changed: true`, enseñar a Iván qué va a cambiar
en el template (el `new_content` frente al `old_content` — un diff legible,
no el JSON crudo). Preguntar confirmación explícita:

> "¿Confirmas que empuje esto a una rama + PR en borrador contra
> `claude-agent-deploy`? Tú lo revisas y mergeas cuando quieras."

Si algún fichero salió rechazado en el preview, decirlo también aquí —
"estos N sí, estos M no por X motivo" — nunca aplicar solo los que
funcionaron sin que Iván sepa que otros se quedaron fuera.

Si `changed: false` en todos, informar que no hay nada nuevo que propagar
(el template ya tiene esa versión) y no seguir.

## 5. Apply — solo tras confirmación explícita

```bash
python3 /home/<agent>/workspace/scripts/lib/template_push.py apply <ruta1> <ruta2> ...
```

(Sin flags = rama + PR, el modo por defecto y recomendado. `--direct`
existe para push directo a `main` sin PR, pero solo se usa si Iván lo pide
explícitamente para este caso concreto — no es el flujo normal.)

Leer el JSON de salida:

- `ok: false` — no se pudo completar. Casos frecuentes:
  - **"GITHUB_TOKEN no está configurado"** — prerrequisito de fase 2 sin
    resolver en esta instancia. No hay nada que reintentar; hace falta que
    Iván decida cómo se provisiona el token (ver nota abajo) antes de que
    el push funcione aquí.
  - **"lock de template ocupado"** — alguien (persona o el pull diario)
    está tocando `~/template` ahora mismo. Reintentar en un momento, no
    hace falta ninguna otra acción.
  - **"~/template ya está sucio"** — hay cambios sin commitear en
    `~/template` de antes (trabajo manual a medias). Revisar a mano, no
    forzar.
  - **"guard anti-fuga (pasada 2, diff staged)"** — algo se coló hasta el
    `git diff` en staging. El script ya deshizo el commit y limpió la
    rama solo; informar a Iván y no reintentar sin revisar qué pasó.
- `ok: true, pushed: true` — éxito. Reportar `pr_url` si vino (o
  `compare_url` como alternativa si la creación automática del PR falló
  pero la rama sí se empujó) y la lista de `files` propagados. Dejar claro
  que el PR queda pendiente de que Iván lo revise y mergee — la skill no
  hace nada más después de esto.

## Nota — GITHUB_TOKEN pendiente de provisionar

A fecha de esta versión, ningún agente desplegado tiene `GITHUB_TOKEN` en
su `secrets.env` por defecto (el instalador nunca lo pide ni lo escribe).
Sin él, `apply` fallará siempre con el mensaje de arriba. Cómo se
provisiona ese token a cada instancia (¿PAT de grano fino por agente?
¿compartido? ¿GitHub App?) es una decisión de diseño pendiente, señalada
explícitamente en la revisión de Opus de esta fase (M3: la autenticación
es aparcable para el pull de solo-lectura, pero es justo la pieza que hace
falta resolver para el push). Esta skill no la resuelve ni la rodea —
si `apply` falla por esto, comunicarlo tal cual, no intentar workarounds.

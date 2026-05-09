# Inicio rápido

`sts2_autoplay` se utiliza para conectar a N.E.K.O el estado local de *Slay the Spire 2* expuesto por `STS2 AI Agent`. El plugin puede leer la situación, ejecutar acciones legales, jugar automáticamente según una estrategia, dejar que la chica gato elija una sola carta, enviar observaciones al frontend y permitir que la chica gato envíe orientaciones suaves desde tareas en segundo plano para influir en la siguiente ronda de decisiones.

## Tutorial de uso

### Obtener el MOD

Usar Git Clone:
```text
git clone https://gitclone.com/github.com/CharTyr/STS2-Agent.git
```

### Instalar el Mod del juego

En Steam, haz clic derecho sobre *Slay the Spire 2* y selecciona "Administrar -> Examinar archivos locales".

El directorio predeterminado del juego en Steam suele ser similar a:

```text
...\Steam\steamapps\common\Slay the Spire 2
```

Copia el mod `STS2 AI Agent` en la carpeta `mods/` del directorio de Slay the Spire 2.

Si no hay carpeta `mods` bajo el directorio de Slay the Spire 2, créala tú mismo.

```text
Usar mods puede causar la pérdida de partidas guardadas. Haz una copia de seguridad o usa la consola para recuperar (en el menú principal de Slay the Spire pulsa la tecla "~", escribe "unlock all", y desbloqueará todos los personajes y dificultades).
```

Después de la instalación, el directorio debería verse así:

```text
Slay the Spire 2/
  mods/
    STS2AIAgent.dll
    STS2AIAgent.pck
    mod_id.json
```

### Iniciar el juego y confirmar la interfaz

Primero inicia el juego normalmente, para que el Mod se cargue con el juego.

Después de cargar el mod, en NEKO, activa la pata de gato, enciende el plugin, entra al panel de plugins e inicia manualmente el plugin de Slay the Spire.

### Comandos disponibles

`juega carta` / `juego automático` / `completa la partida` / `cómo voy?` / `detente`
`juega una carta` / `juega esta carta` / `recomiéndame una carta` ... y similares...

## Resumen de funciones

- Conecta con el servicio HTTP local de `STS2 AI Agent` y lee el estado del juego.
- Soporta ejecutar manualmente un paso, juego semiautomático en segundo plano, pausar, reanudar y detener.
- Soporta tres modos de decisión: `full-program`, `half-program`, `full-model`.
- Soporta cargar documentos de estrategia por personaje; los archivos de estrategia están en `strategies/`.
- Soporta selección única de carta por la chica gato: solo elige una carta de las acciones `play_card` disponibles, primero envía la razón y luego ejecuta.
- Soporta orientación suave de la chica gato: el usuario o la chica gato pueden enviar orientación en lenguaje natural, que se considerará en la siguiente ronda de decisiones del LLM.
- Soporta informes de observación en segundo plano: envía al frontend el piso actual, el combate, la mano, las intenciones del enemigo, los razonamientos del LLM, etc.
- Soporta protección de seguridad: pausa con poca vida, ralentización ante jefes/ataques peligrosos, reanudación automática tras recuperar vida, estrategia de supervivencia desesperada, maximización de beneficio y puntuación de sinergia.

## Dependencias

Este plugin depende del servicio HTTP local proporcionado por el Mod aguas arriba `STS2 AI Agent`:

- Mod en el juego: `STS2AIAgent`
- Dirección de interfaz local predeterminada: `http://127.0.0.1:8080`
- Dirección de comprobación de salud: `http://127.0.0.1:8080/health`

Es decir, el plugin funciona bajo las siguientes premisas:

1. El Mod `STS2 AI Agent` está instalado en *Slay the Spire 2*.
2. Después de iniciar el juego, `http://127.0.0.1:8080/health` es accesible.
3. El plugin `sts2_autoplay` está habilitado en N.E.K.O.

## Configuración del plugin

Archivo de configuración: `plugin.toml`

### Configuración básica

| Opción | Predeterminado | Descripción |
| --- | --- | --- |
| `base_url` | `http://127.0.0.1:8080` | Dirección del Agent local de Spire. |
| `connect_timeout_seconds` | `5` | Tiempo de espera de conexión en segundos. |
| `request_timeout_seconds` | `15` | Tiempo de espera de la solicitud en segundos. |
| `poll_interval_idle_seconds` | `3` | Intervalo de sondeo en estado inactivo. |
| `poll_interval_active_seconds` | `1` | Intervalo de sondeo durante el juego automático. |
| `action_interval_seconds` | `1.5` | Intervalo extra entre cada acción. |
| `post_action_delay_seconds` | `0.5` | Intervalo de espera tras una acción para que se estabilice la situación. |
| `autoplay_on_start` | `false` | Si comenzar a jugar automáticamente tras iniciar el plugin. |
| `semi_auto_autoplay` | `true` | Si crear un contexto de tarea semiautomática al iniciar el juego automático. |
| `mode` | `half-program` | Modo de juego automático actual. |
| `character_strategy` | `defect` | Nombre de la estrategia del personaje, corresponde a `strategies/<name>.md`. |
| `max_consecutive_errors` | `3` | Número máximo de errores consecutivos; si se supera, se considera desconexión. |
| `push_notifications` | `true` | Campo histórico reservado. |
| `event_stream_enabled` | `false` | Campo reservado, actualmente no activado. |

### Modos de decisión

`mode` admite los siguientes valores, así como sus alias en chino:

| Modo | Alias chino | Descripción |
| --- | --- | --- |
| `full-program` | `全程序` | Heurística puramente programática, sin llamar al modelo. |
| `half-program` | `半程序` | Primero comprobaciones programáticas previas, luego una llamada al modelo para la decisión, con validación/reserva de legalidad. |
| `full-model` | `全模型` | Dos llamadas al modelo: primero reasoning, luego final action; comprobaciones programáticas en medio y validación final de legalidad. |

### Estrategias de personaje

`character_strategy` busca documentos de estrategia bajo `strategies/<name>.md`. Estrategias actualmente integradas:

- `defect`
- `ironclad`
- `silent_hunter`
- `necrobinder`
- `regent`

Puedes añadir archivos Markdown en `strategies/` para extender estrategias. Por ejemplo:

```text
strategies/my_strategy.md
```

Luego configura o establece el parámetro de entrada en:

```text
my_strategy
```

### Envío al frontend y observación de la chica gato

| Opción | Predeterminado | Descripción |
| --- | --- | --- |
| `llm_frontend_output_enabled` | `true` | Si enviar activamente las acciones/errores del juego automático al frontend. |
| `llm_frontend_output_probability` | `0.15` | Probabilidad de envío de acciones normales, el rango se acota a `0.0 ~ 1.0`. Los errores se envían forzosamente. |
| `neko_reporting_enabled` | `true` | Si enviar informes de observación de la chica gato. |
| `neko_report_interval_steps` | `1` | Cada cuántos pasos de juego automático enviar un informe de observación; mínimo `1`. |
| `neko_commentary_enabled` | `true` | Si generar comentarios en directo de la chica gato en los informes de observación. Si se desactiva, los informes estructurados aún se envían, pero `live_commentary.text` permanece vacío. |
| `neko_commentary_probability` | `0.65` | Probabilidad de activación de comentarios normales de baja prioridad, acotada a `0.0 ~ 1.0`; situaciones de alta prioridad como vida baja, remate o ataque alto pueden saltarse la probabilidad. |
| `neko_commentary_min_interval_seconds` | `4` | Intervalo mínimo en segundos para repetir comentarios en la misma situación de baja prioridad, para reducir spam y repetición. |
| `neko_critical_commentary_always` | `true` | Si los comentarios de urgencia `critical` / `high` siempre se reproducen, por ejemplo: agonía, remate, ataque enemigo alto. |
| `neko_guidance_max_queue` | `50` | Longitud máxima de la cola de orientaciones suaves de la chica gato. |

Los informes de observación de la chica gato llevan `report`, `neko_context`, `live_commentary`, `task` y otros metadata simplificados, ayudando al frontend o a la lógica de diálogo a reconocer esto como una "observación de proceso" y no una notificación de finalización de tarea. Para ahorrar tokens del usuario, el contenido enviado solo conserva la acción actual, la vida, la mano, los enemigos, el resumen táctico, la orientación consumida y el resumen de la tarea.

`live_commentary` proporciona al frontend/TTS campos cortos de locución: `text`, `scene`, `mood`, `urgency`, `priority`, `tts`, `interrupt`, `tone`, `character_strategy`. Los comentarios se eligen al azar de un grupo de plantillas por escena para reducir repetición; también se ajustan las inclinaciones según la estrategia del personaje, por ejemplo, `defect` tiende a lo racional, `ironclad` a lo estable. Actualmente cubre agonía, vida baja, remate, ataques enemigos entrantes, defensa, combate normal, recompensas, tienda, sitios de descanso, eventos, mapa, así como comentarios a nivel de evento como fin de combate, reliquias clave y finalización de elección de ruta.

### Protección de seguridad y acciones autónomas

| Opción | Predeterminado | Descripción |
| --- | --- | --- |
| `neko_auto_low_hp_threshold` | `0.3` | Cuando la proporción de vida actual cae por debajo de este valor, el juego automático en segundo plano se pausa autónomamente. |
| `neko_auto_safe_hp_threshold` | `0.5` | Tras recuperar la vida hasta esta proporción, se permite la reanudación automática. |
| `neko_auto_dangerous_attack_threshold` | `20` | Ralentización automática cuando el daño entrante del enemigo alcanza este valor y rompe la defensa. |
| `neko_auto_resume_after_low_hp` | `true` | Si se permite reanudación automática tras una pausa por vida baja al recuperarse la vida. |
| `neko_desperate_enabled` | `true` | Si activar la estrategia de supervivencia desesperada. |
| `neko_desperate_hp_threshold` | `0.2` | Proporción de vida que activa la estrategia de supervivencia desesperada. |
| `neko_maximize_enabled` | `true` | Si activar la selección de cartas con maximización de beneficio. |
| `neko_synergy_enabled` | `true` | Si activar la puntuación de sinergia/combinación. |

Las acciones autónomas actuales incluyen:

- `pause`: pausa con vida baja, esperando indicaciones del usuario o de la chica gato.
- `slow_down`: ralentiza temporalmente el intervalo de acciones durante peleas de jefe o ataques peligrosos.
- `resume`: reanuda tras cumplirse la condición de vida segura.

## Frases recomendadas para usuarios normales

Los usuarios normales no necesitan recordar las entradas de bajo nivel a continuación. Se prefiere pasar las palabras originales del usuario a `sts2_neko_command`, y el plugin decidirá internamente si consultar estado, dar consejos, jugar realmente una carta, ejecutar un paso, iniciar el juego automático, pausar, reanudar, detener, repasar la jugada reciente, responder preguntas sobre el juego automático, o usar la frase como orientación suave durante el juego automático.

Reglas de interacción recomendadas:

| Frase del usuario | Comportamiento del plugin |
| --- | --- |
| `está conectada la spire` / `cuál es la situación ahora` | Solo consultar conexión, estado o instantánea; no operar el juego. |
| `cómo jugar este turno` / `qué carta es mejor jugar` | Solo recomendar una carta jugable y explicar la razón; no jugar automáticamente. |
| `juega una carta por mí` / `elige una carta y juégala` | Tras autorización explícita, elegir solo una de las acciones `play_card` y jugarla. |
| `da un paso por mí` / `ejecuta un paso` | Tras autorización explícita, ejecutar una acción legal, que puede incluir terminar el turno, elegir recompensa o moverse en el mapa. |
| `pasa este piso por mí` / `juega un poco automáticamente` | Iniciar juego semiautomático; condición de parada predeterminada: completar el piso actual. |
| `defiende primero` / `no seas codicioso con el daño` | Mientras el juego automático está corriendo, esto se convierte en orientación suave para la siguiente ronda; si no está corriendo, pedir aclaración de forma conservadora, no actuar. |
| `cómo jugué antes` / `repasa esa carta` | Dar evaluación de juego basada en la última instantánea ligera; no operar el juego. |
| `por qué juegas así` / `qué estás haciendo` | Mientras el juego automático está corriendo, responder sobre la estrategia actual y el razonamiento de la situación; no realizar acciones extra. |
| `pausa un momento` / `continúa` / `vamos a parar` | Pausar, reanudar o detener el juego automático respectivamente. |

Predeterminados de seguridad: la consulta no opera, las frases vagas no ejecutan acciones peligrosas; solo cuando el usuario dice explícitamente "juega por mí", "ejecuta", "juega automáticamente" o "encárgate" se realizan acciones reales.

## Entradas del plugin

Las siguientes entradas están expuestas al host y pueden llamarse directamente en N.E.K.O. Para escenarios de usuarios normales, se recomienda llamar primero a `sts2_neko_command`; las demás entradas son principalmente interfaces de control preciso para desarrolladores.

### `sts2_neko_command`

Entrada maestra de lenguaje natural para Slay the Spire. Cuando el usuario no especifica explícitamente una herramienta de bajo nivel, se prefiere llamarla.

Parámetros:

- `command`: obligatorio, palabras originales del usuario. Ejemplos: `cómo jugar este turno`, `juega una carta por mí`, `defiende primero`, `pausa un momento`.
- `scope`: opcional, predeterminado `auto`. Valores posibles: `auto`, `status`, `advice`, `one_card`, `one_action`, `autoplay`, `control`, `guidance`, `review`, `question`, `chat`.
- `confirm`: opcional, predeterminado `false`. Usado para confirmar operaciones de alto riesgo como toma de control continua.

El retorno incluye `intent`, `action`, `executed`, `needs_confirmation`, `summary` y el `result` subyacente.

### `sts2_health_check`

Comprueba si el servicio local de Spire Agent está disponible.

### `sts2_refresh_state`

Fuerza una actualización del estado actual de Spire.

### `sts2_get_status`

Obtiene información sobre estado de conexión, estado del juego automático, modo actual, estrategia del personaje, tarea semiautomática, errores recientes, acciones recientes, etc.

### `sts2_get_snapshot`

Obtiene la instantánea del juego más recientemente cacheada y las acciones ejecutables actualmente.

### `sts2_step_once`

Ejecuta un paso según la estrategia actual.

### `sts2_play_one_card_by_neko`

Permite a la chica gato elegir y jugar una carta.

Parámetros:

- `objective`: opcional, objetivo de autorización del usuario. Ejemplo: `elige una carta y juégala por mí`.

Comportamiento:

1. Lee al jugador actual, la mano, los enemigos y las acciones legales.
2. Mantiene solo las acciones `play_card`.
3. Permite al modo/estrategia actual elegir una carta.
4. Primero envía al frontend "qué carta planea jugar y por qué".
5. Re-valida que la acción siga siendo legal.
6. Juega la carta y envía la observación de finalización.

Si actualmente no hay cartas jugables, devuelve `idle` y envía la razón del fallo.

### `sts2_start_autoplay`

Inicia el bucle de juego semiautomático en segundo plano.

Parámetros:

- `objective`: opcional, objetivo de autorización del usuario. Ejemplo: `pasa este piso por mí`.
- `stop_condition`: condición de parada, predeterminado `current_floor`.

`stop_condition` admite:

- `current_floor`: termina al completar el piso actual o entrar al siguiente.
- `current_combat` / `combat`: termina cuando, durante la tarea, se haya entrado en combate y luego se haya salido.
- `manual` / `none`: no termina automáticamente, requiere parada manual.

Tras iniciar, el plugin crea un contexto de tarea semiautomática y envía un evento de inicio de tarea al frontend. Al completarse la tarea se envía `semi_auto_task_completed`.

### `sts2_pause_autoplay`

Pausa el juego automático.

### `sts2_resume_autoplay`

Reanuda un juego automático pausado cuya tarea en segundo plano aún existe. Si la tarea en segundo plano ya no existe, devuelve `idle` de forma segura y no reinicia implícitamente el juego automático.

### `sts2_stop_autoplay`

Detiene el juego automático y limpia el contexto de la tarea semiautomática.

### `sts2_get_history`

Obtiene el historial reciente de acciones y estados.

Parámetros:

- `limit`: número de entradas a devolver, predeterminado `20`, rango limitado a `1 ~ 100`.

### `sts2_send_neko_guidance`

Envía orientación suave de la chica gato al juego automático en segundo plano. La orientación entra en la cola y se inyecta en el contexto en la siguiente ronda de decisiones del LLM.

Parámetros:

- `content`: obligatorio, contenido de orientación en lenguaje natural. Ejemplo: `defiende primero, no te apresures con el daño`.
- `step`: opcional, número de paso correspondiente.
- `type`: opcional, predeterminado `soft_guidance`.

### `sts2_set_mode`

Establece el modo de juego automático.

Parámetros:

- `mode`: admite `full-program` / `全程序`, `half-program` / `半程序`, `full-model` / `全模型`.

### `sts2_set_character_strategy`

Establece el nombre de la estrategia del personaje.

Parámetros:

- `character_strategy`: tras la normalización del nombre, se hace coincidir con `strategies/<name>.md`. Por ejemplo, `defect` coincide con `strategies/defect.md`.

### `sts2_set_speed`

Establece parámetros de velocidad y los escribe de vuelta en el `plugin.toml` local.

Parámetros:

- `action_interval_seconds`
- `post_action_delay_seconds`
- `poll_interval_active_seconds`

## Modo de uso típico

### Comprobar conexión

1. Inicia *Slay the Spire 2*.
2. Confirma que `http://127.0.0.1:8080/health` es accesible.
3. En N.E.K.O llama a `sts2_health_check`.

### Ejecutar manualmente un paso

Llamar:

```text
sts2_step_once
```

El plugin elegirá y ejecutará una acción legal según el `mode` y `character_strategy` actuales.

### Que la chica gato juegue una carta

El usuario puede decirle a la chica gato algo como:

```text
elige una carta y juégala por mí
```

El host debería llamar:

```text
sts2_play_one_card_by_neko
```

El plugin solo elige entre las cartas actualmente jugables y no elige terminar turno, mapa, recompensa u otras acciones.

### Que la chica gato ayude a pasar un piso

El usuario puede decir:

```text
pasa este piso por mí
```

El host debería llamar:

```text
sts2_start_autoplay
```

Parámetros recomendados:

```json
{
  "objective": "pasa este piso por mí",
  "stop_condition": "current_floor"
}
```

Durante la ejecución de la tarea, los eventos de observación son solo informes de progreso y no representan finalización. Solo al recibir el evento de finalización de la tarea semiautomática se debe decir al usuario que el piso está completado.

### Orientación durante la partida

Durante el juego automático, el usuario o la chica gato pueden enviar orientación:

```text
defiende primero, no recibas demasiado daño
```

Debería llamarse:

```text
sts2_send_neko_guidance
```

Parámetros recomendados:

```json
{
  "content": "defiende primero, no recibas demasiado daño",
  "type": "soft_guidance"
}
```

La orientación se considerará en la siguiente ronda de decisiones del LLM. El modo `full-program` no depende del modelo, por lo que el impacto de la orientación suave es limitado.

## Eventos enviados al frontend

El plugin envía las siguientes categorías de eventos a través del canal de mensajes del host. Excepto inicio/finalización de tarea, errores y avisos de carta única, las observaciones normales intentan usar texto corto y metadata simplificada para reducir el consumo de tokens del usuario.

| Tipo de evento | Descripción |
| --- | --- |
| `action` | Observación normal de acción del juego automático, controlada por probabilidad. |
| `error` | Error del juego automático, envío forzado. |
| `neko_report` | Informe completo de observación de la chica gato, incluyendo situación actual, mano, enemigos, resumen táctico y razonamiento del modelo. |
| `neko_card_task_planned` | La tarea de carta única de la chica gato planea jugar una carta determinada. |
| `neko_card_task_completed` | Tarea de carta única de la chica gato ejecutada. |
| `neko_card_task_failed` | La tarea de carta única de la chica gato no pudo ejecutarse. |
| `semi_auto_task_started` | Tarea semiautomática iniciada. |
| `semi_auto_task_completed` | Tarea semiautomática completada. |
| `neko_autonomous_action` | El sistema pausó, ralentizó o reanudó autónomamente. |

Nota: `neko_report` es una observación de proceso, no una notificación de finalización de tarea. El frontend o la lógica de diálogo no debe describir una acción de paso único, jugar carta, terminar turno o actualización de estado como "tarea completada", "jefe vencido", "combate terminado" o "partida pasada". Si la chica gato quiere influir en la siguiente ronda de decisiones, debe llamarse a `sts2_send_neko_guidance`; si quiere controlar el flujo con dureza, debe llamarse a las entradas de pausa, reanudación o detención.

## Resolución de problemas comunes

### Falla la conexión al llamar a una entrada del plugin

Comprueba primero:

- Si el juego ha sido iniciado.
- Si el Mod `STS2 AI Agent` se ha colocado correctamente en `mods/` del juego.
- Si `http://127.0.0.1:8080/health` es accesible.
- Si el `base_url` en `plugin.toml` es correcto.

### `http://127.0.0.1:8080/health` no se abre

Comprueba prioritariamente:

1. Si el juego realmente ha sido iniciado.
2. Si `STS2AIAgent.dll`, `STS2AIAgent.pck` y `mod_id.json` se han copiado todos al directorio `mods/` del juego.
3. Si los nombres de archivo han sido renombrados por el sistema, duplicados o colocados en el directorio incorrecto.
4. Si estás operando en el directorio del juego de Steam y no en el directorio del repositorio aguas arriba.
5. Si un firewall o software de seguridad está bloqueando el puerto local.

### El juego automático funciona, pero el frontend no recibe mensajes

Comprueba:

- Si `llm_frontend_output_enabled` es `true`.
- Si `llm_frontend_output_probability` es demasiado bajo.
- Si `neko_reporting_enabled` es `true`.
- Durante depuración puedes establecer primero `llm_frontend_output_probability` a `1`.
- Si el frontend del host está recibiendo los mensajes enviados por el plugin.

### La orientación de la chica gato durante la partida no tiene efecto evidente

Comprueba:

- Si el modo actual es `half-program` o `full-model`.
- Si `sts2_send_neko_guidance` devuelve `ok`.
- Si el contenido de la orientación es lo suficientemente concreto, por ejemplo: "prioriza defensa", "ataca primero al enemigo de menor vida", "guarda la poción".
- Si las acciones legales actuales realmente pueden satisfacer la orientación.

### La tarea semiautomática tarda en completarse

Comprueba `stop_condition`:

- Si es `manual` / `none`, la tarea no se completará automáticamente, hay que llamar a `sts2_stop_autoplay`.
- Si es `current_combat`, la tarea se completa cuando, durante la tarea, se haya entrado en combate y luego se haya salido.
- Si es `current_floor`, normalmente se completa al completar el piso actual o entrar al siguiente.

Puedes llamar a `sts2_get_status` para ver `autoplay.task`.

### Atascos en salas de evento, ventanas emergentes o estados de transición

La versión actual ya gestiona eventos, ventanas emergentes y estados de transición; las acciones prioritarias incluyen:

- `confirm_modal`
- `dismiss_modal`
- `choose_event_option`
- `proceed`

Si sigue atascado, primero usa `sts2_get_snapshot` para ver el `screen` actual y los `available_actions`.

### El juego automático se pausa de repente o va más lento

Puede haberse activado la protección de seguridad:

- Cuando la proporción de vida cae por debajo de `neko_auto_low_hp_threshold`, se pausa.
- Durante peleas de jefe o ataques peligrosos, se ralentiza.
- Si `neko_auto_resume_after_low_hp` es `true`, tras recuperar la vida hasta `neko_auto_safe_hp_threshold` puede reanudarse automáticamente.

Puedes llamar a `sts2_get_status` para ver el estado, o llamar a `sts2_resume_autoplay` / `sts2_stop_autoplay` para gestionarlo.

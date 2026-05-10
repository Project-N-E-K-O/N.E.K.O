# Inicio rápido

`sts2_autoplay` se utiliza para conectar a N.E.K.O el estado local de *Slay the Spire 2* expuesto por `STS2 AI Agent`. El plugin puede leer la situación actual, ejecutar acciones legales, jugar automáticamente según la estrategia, permitir que la chica gato elija una sola carta, enviar información de observación al frontend, y permitir que la chica gato envíe orientación suave en tareas en segundo plano para influir en la siguiente ronda de decisiones.

## Tutorial de uso

### Obtener el MOD

Usando Git:
```text
https://gitclone.com/github.com/CharTyr/STS2-Agent.git
```

### Instalar el Mod del juego

En Steam, haz clic derecho en *Slay the Spire 2* y elige Administrar -> Explorar archivos locales.

La carpeta predeterminada del juego en Steam suele ser similar a:

```text
...\Steam\steamapps\common\Slay the Spire 2
```

Copia el mod `STS2 AI Agent` dentro de la carpeta `mods/` del directorio del juego.

Si no existe una carpeta `mods` dentro del directorio de *Slay the Spire 2*, créala manualmente.

```text
Usar mods puede causar pérdida de guardados. Haz una copia de seguridad o usa la consola para compensarte (en el menú principal de Slay the Spire pulsa la tecla "~", introduce "unlock all" y se desbloquearán todos los personajes y dificultades).
```

Tras la instalación, la estructura debería verse así:

```text
Slay the Spire 2/
  mods/
    STS2AIAgent.dll
    STS2AIAgent.pck
    mod_id.json
```

### Iniciar el juego y confirmar la interfaz

Primero inicia el juego normalmente para que el Mod se cargue junto con él.

La primera vez que cambies al modo con mods puede cerrarse de forma inesperada una vez. Es normal; simplemente vuelve a iniciar el juego.

Después de cargar el mod, en N.E.K.O activa Cat Paw, habilita el plugin, entra en el panel del plugin y arranca manualmente el plugin de Slay the Spire.

### Comandos disponibles

【Jugar una carta】【Autojugar por mí】【Pasar un piso】【Qué tal jugué】【Detener】
【Jugar una sola carta】【Jugar cierta carta】【Recomendar una carta】... y expresiones similares.

## Contacto

Si tienes cualquier problema, envía por correo los registros de ejecución del juego y de N.E.K.O a zhaijiunknown@outlook.com.

Registros del juego:
```text
%AppData%\SlayTheSpire2\logs
```

Registros de N.E.K.O:
```text
Tu carpeta de usuario\AppData\Local\N.E.K.O\logs
```

## Resumen de funciones

- Se conecta al servicio HTTP local de `STS2 AI Agent` y lee el estado del juego.
- Soporta ejecución manual de un paso, juego semiautomático en segundo plano, pausa, reanudación y detención.
- Soporta tres modos de decisión: `full-program`, `half-program` y `full-model`.
- Soporta cargar documentos de estrategia por personaje; los archivos de estrategia están en `strategies/`.
- Soporta selección de una sola carta por la chica gato: elige únicamente una carta de entre las acciones `play_card` disponibles, primero envía la razón y luego la ejecuta.
- Soporta orientación suave de la chica gato: el usuario o la propia chica gato pueden enviar instrucciones en lenguaje natural, y la siguiente ronda de decisión del LLM las tendrá en cuenta.
- Soporta informes de observación en segundo plano: envía al frontend el piso actual, combate, mano, intención de los enemigos, razonamiento del LLM, etc.
- Soporta protecciones de seguridad: pausa con vida baja, reducción de velocidad en Boss/ataques peligrosos, reanudación automática tras recuperar vida, estrategia de supervivencia en vida crítica, maximización de beneficio y puntuación de sinergia.

## Configuración de este plugin

Archivo de configuración: `plugin.toml`

### Configuración básica

| Opción | Valor por defecto | Descripción |
| --- | --- | --- |
| `base_url` | `http://127.0.0.1:8080` | Dirección del Agent local de Slay the Spire. |
| `connect_timeout_seconds` | `5` | Segundos de espera para la conexión. |
| `request_timeout_seconds` | `15` | Segundos de espera para la petición. |
| `poll_interval_idle_seconds` | `3` | Intervalo de sondeo en estado inactivo. |
| `poll_interval_active_seconds` | `1` | Intervalo de sondeo mientras se ejecuta el autojuego. |
| `action_interval_seconds` | `1.5` | Intervalo adicional entre acciones. |
| `post_action_delay_seconds` | `0.5` | Tiempo de espera tras ejecutar una acción para dejar que la situación se estabilice. |
| `autoplay_on_start` | `false` | Si el plugin debe empezar a jugar automáticamente al iniciarse. |
| `semi_auto_autoplay` | `true` | Si al iniciar el autojuego se debe crear el contexto de tarea semiautomática. |
| `mode` | `half-program` | Modo actual de autojuego. |
| `character_strategy` | `defect` | Nombre de la estrategia del personaje, correspondiente a `strategies/<name>.md`. |
| `max_consecutive_errors` | `3` | Número máximo de errores consecutivos; superado ese número se considera desconectado. |
| `push_notifications` | `true` | Campo histórico conservado. |
| `event_stream_enabled` | `false` | Campo reservado; por ahora no está habilitado realmente. |

### Modos de decisión

`mode` soporta los siguientes valores, así como sus alias en chino:

| Modo | Alias chino | Descripción |
| --- | --- | --- |
| `full-program` | `全程序` | Heurística puramente programática; no llama al modelo. |
| `half-program` | `半程序` | Primero realiza comprobaciones programáticas previas, luego hace una decisión del modelo, y finalmente valida la legalidad o aplica fallback. |
| `full-model` | `全模型` | Dos llamadas al modelo: primero reasoning y luego final action; entre ambas se realizan comprobaciones programáticas, y al final se valida de nuevo la legalidad. |

### Estrategia de personaje

`character_strategy` busca el documento de estrategia en `strategies/<name>.md`. Estrategias integradas actualmente:

- `defect`
- `ironclad`
- `silent_hunter`
- `necrobinder`
- `regent`

Puedes añadir nuevos archivos Markdown dentro de `strategies/` para ampliar estrategias. Por ejemplo:

```text
strategies/my_strategy.md
```

Luego establece la configuración o el parámetro de entrada como:

```text
my_strategy
```

### Envíos al frontend y observación de la chica gato

| Opción | Valor por defecto | Descripción |
| --- | --- | --- |
| `llm_frontend_output_enabled` | `true` | Si los movimientos/errores del autojuego se envían activamente al frontend. |
| `llm_frontend_output_probability` | `0.15` | Probabilidad de envío para acciones normales; se ajusta al rango `0.0 ~ 1.0`. Los errores se envían siempre. |
| `neko_reporting_enabled` | `true` | Si se envían informes de observación de la chica gato. |
| `neko_report_interval_steps` | `1` | Cada cuántos pasos del autojuego se envía un informe de observación; mínimo `1`. |
| `neko_commentary_enabled` | `true` | Si se genera narración en tiempo real de la chica gato dentro del informe de observación. Si se desactiva, el informe estructurado sigue enviándose, pero `live_commentary.text` permanecerá vacío. |
| `neko_commentary_probability` | `0.65` | Probabilidad de activación para narraciones normales de baja prioridad; se ajusta al rango `0.0 ~ 1.0`. Escenarios de alta prioridad como vida baja, letal o grandes ataques pueden ignorar esta probabilidad. |
| `neko_commentary_min_interval_seconds` | `4` | Intervalo mínimo en segundos para repetir narración en el mismo escenario de baja prioridad, para reducir spam y líneas repetidas. |
| `neko_critical_commentary_always` | `true` | Si la narración de urgencia `critical` / `high` debe emitirse siempre, por ejemplo con vida crítica, letal o ataques enemigos muy altos. |
| `neko_guidance_max_queue` | `50` | Longitud máxima de la cola de orientación suave de la chica gato. |

Los informes de observación de la chica gato incluirán metadata simplificada como `report`, `neko_context`, `live_commentary` y `task`, para que el frontend o la lógica de diálogo puedan distinguir que esto es una “observación de proceso” y no una notificación de finalización de tarea. Para ahorrar tokens del usuario, el contenido enviado solo conserva la acción actual, vida, mano, enemigos, resumen táctico, orientación ya consumida y resumen de la tarea.

`live_commentary` proporciona al frontend/TTS campos de locución corta: `text`, `scene`, `mood`, `urgency`, `priority`, `tts`, `interrupt`, `tone` y `character_strategy`. La narración se elige aleatoriamente de un conjunto de plantillas por escena para reducir repeticiones; también se ajusta según la estrategia del personaje, por ejemplo `defect` es más racional y `ironclad` más estable. Actualmente cubre vida crítica, vida baja, letal, ataques entrantes, defensa, combate normal, recompensas, tienda, puntos de descanso, eventos, mapa, así como narraciones a nivel de evento como final de combate, reliquia clave o finalización de elección de ruta.

### Protecciones de seguridad y acciones autónomas

| Opción | Valor por defecto | Descripción |
| --- | --- | --- |
| `neko_auto_low_hp_threshold` | `0.3` | Si la proporción de vida actual cae por debajo de este valor, el autojuego en segundo plano se pausará autónomamente. |
| `neko_auto_safe_hp_threshold` | `0.5` | Cuando la vida se recupere hasta esta proporción, podrá reanudarse automáticamente. |
| `neko_auto_dangerous_attack_threshold` | `20` | Cuando el daño entrante del enemigo alcance este valor y rompa la defensa, se reducirá la velocidad automáticamente. |
| `neko_auto_resume_after_low_hp` | `true` | Si se permite reanudar automáticamente tras recuperar vida después de una pausa por vida baja. |
| `neko_desperate_enabled` | `true` | Si se activa la estrategia de supervivencia con vida crítica. |
| `neko_desperate_hp_threshold` | `0.2` | Proporción de vida que activa la estrategia de supervivencia desesperada. |
| `neko_maximize_enabled` | `true` | Si se activa la selección de cartas con maximización de beneficio. |
| `neko_synergy_enabled` | `true` | Si se activa la puntuación de sinergia/combinación. |

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
4. Primero envía al frontend "qué carta está a punto de jugar y por qué".
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

## Problemas comunes

### Fallo de conexión al llamar entradas del plugin

Comprueba primero:

- Si el juego ya está iniciado.
- Si el Mod `STS2 AI Agent` se ha colocado correctamente en `mods/` del juego.
- Si `http://127.0.0.1:8080/health` es accesible.
- Si `base_url` en `plugin.toml` es correcto.

### No se puede abrir `http://127.0.0.1:8080/health`

Prioriza estas comprobaciones:

1. Si el juego realmente está iniciado.
2. Si `STS2AIAgent.dll`, `STS2AIAgent.pck` y `mod_id.json` han sido copiados al directorio `mods/` del juego.
3. Si el sistema cambió el nombre de los archivos, si están duplicados o en un directorio incorrecto.
4. Si estás operando sobre el directorio del juego de Steam y no sobre el directorio del repositorio original.
5. Si algún cortafuegos o software de seguridad está bloqueando el puerto local.

### El autojuego funciona, pero el frontend no recibe mensajes

Comprueba:

- Si `llm_frontend_output_enabled` está en `true`.
- Si `llm_frontend_output_probability` es demasiado bajo.
- Si `neko_reporting_enabled` está en `true`.
- Durante pruebas de integración, puedes poner primero `llm_frontend_output_probability` en `1`.
- Si el frontend del host está recibiendo realmente los mensajes push del plugin.

### La orientación a mitad de partida no tiene efecto visible

Comprueba:

- Si el modo actual es `half-program` o `full-model`.
- Si `sts2_send_neko_guidance` devolvió `ok`.
- Si el contenido de la orientación es lo bastante específico, por ejemplo “prioriza defensa”, “ataca primero al enemigo con menos vida” o “guarda la poción”.
- Si las acciones legales actuales realmente pueden satisfacer la orientación.

### La tarea semiautomática nunca termina

Comprueba `stop_condition`:

- Si es `manual` / `none`, la tarea no se completará automáticamente; debes llamar a `sts2_stop_autoplay`.
- Si es `current_combat`, la tarea se completará después de haber entrado en combate durante la tarea y luego haber salido de él.
- Si es `current_floor`, normalmente se completará cuando el piso actual termine o al entrar al siguiente.

Puedes llamar a `sts2_get_status` para revisar `autoplay.task`.

### Se queda atascado en eventos, ventanas emergentes o estados de transición

La versión actual ya gestiona eventos, ventanas emergentes y estados de transición. Las acciones prioritarias incluyen:

- `confirm_modal`
- `dismiss_modal`
- `choose_event_option`
- `proceed`

Si sigue atascado, usa primero `sts2_get_snapshot` para revisar `screen` y `available_actions` actuales.

### El autojuego se pausa o se ralentiza de repente

Puede que se haya activado una protección de seguridad:

- Se pausará cuando la proporción de vida caiga por debajo de `neko_auto_low_hp_threshold`.
- Se ralentizará durante peleas de Boss o ataques peligrosos.
- Si `neko_auto_resume_after_low_hp` está en `true`, puede reanudarse automáticamente cuando la vida se recupere hasta `neko_auto_safe_hp_threshold`.

Puedes llamar a `sts2_get_status` para revisar el estado, o usar `sts2_resume_autoplay` / `sts2_stop_autoplay` para manejarlo.

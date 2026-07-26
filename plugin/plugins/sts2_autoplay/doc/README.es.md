# Inicio rápido

`sts2_autoplay` se utiliza para conectar a N.E.K.O el estado local de *Slay the Spire 2* expuesto por `STS2 AI Agent`. El plugin puede leer la situación actual, ejecutar acciones legales, jugar automáticamente según la estrategia, permitir que la chica gato elija una sola carta, enviar información de observación al frontend, y permitir que la chica gato envíe orientación suave en tareas en segundo plano para influir en la siguiente ronda de decisiones.

## Tutorial de uso

### Obtener el MOD

Usando Git:
```text
https://github.com/CharTyr/STS2-Agent/releases
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

### Configurar el puerto del mod

Por defecto, el mod escucha en el puerto `8080`. Es un puerto muy usado, así que puede estar ocupado por otro software.

Puedes abrir Variables de entorno y crear una nueva variable de sistema/global llamada `STS2_API_PORT`. Elige un puerto menos común, por ejemplo `18080` o `38080`.

Ejemplo: nombre de variable `STS2_API_PORT`, valor `18080`.

Nota: puedes abrir Variables de entorno rápidamente así:

- Pulsa Win + R para abrir el cuadro Ejecutar.

- Escribe el siguiente comando y pulsa Enter:

```text
rundll32 sysdm.cpl,EditEnvironmentVariables
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

- Se conecta al servicio HTTP local `STS2 AI Agent` y lee el estado actual de la partida.
- Permite consultar la situación actual de un solo vistazo: refresca una vez el estado y reúne el snapshot, el resumen de la situación y el paquete de sincronización de la neko.
- Permite controlar el autoplay en segundo plano: iniciar, pausar, reanudar, detener y también ejecutar directamente el siguiente paso sugerido.
- Incluye modo de acompañamiento, que puede observar la partida contigo y enviar comentarios, recordatorios y observaciones sin interrumpir el flujo principal.
- Permite ajustar la estrategia en lenguaje natural: una sola frase del usuario puede convertirse en una preferencia u override ligado al evento o enemigo actual.
- Permite revisar el siguiente movimiento recomendado antes de decidir si quieres ejecutarlo.
- Incluye protecciones de seguridad, como pausa con vida baja, desaceleración ante ataques peligrosos, recuperación de velocidad cuando el peligro pasa y, si procede, reanudación del autoplay.
- También soporta envíos pasivos al frontend: sincronización de estado, observaciones, pistas de acompañamiento y retroalimentación de control.

## Configuración de este plugin

Archivo de configuración: `plugin.toml`

### Configuración básica

| Opción | Valor por defecto | Descripción |
| --- | --- | --- |
| `base_url` | `http://127.0.0.1:8080` | Dirección del Agent local de Spire. |
| `connect_timeout_seconds` | `5` | Tiempo límite de conexión, en segundos. |
| `request_timeout_seconds` | `15` | Tiempo límite de la petición, en segundos. |
| `poll_interval_idle_seconds` | `3` | Intervalo de sondeo cuando el plugin está ocioso. |
| `poll_interval_active_seconds` | `1` | Intervalo de sondeo mientras el autoplay está en ejecución. |
| `action_interval_seconds` | `1.5` | Pausa extra entre acciones. |
| `post_action_delay_seconds` | `0.5` | Espera tras cada acción para dejar que la situación se estabilice. |
| `autoplay_on_start` | `false` | Si el plugin debe empezar a jugar automáticamente al iniciarse. |
| `character_strategy` | `defect` | Estrategia predeterminada; en ejecución se asocia al contexto de estrategia que mejor encaja con la situación actual. |
| `max_consecutive_errors` | `3` | Número máximo de errores consecutivos antes de considerar que la conexión está en mal estado. |

### Envíos al frontend y observación de acompañamiento

| Opción | Valor por defecto | Descripción |
| --- | --- | --- |
| `llm_frontend_output_enabled` | `true` | Si se permite enviar al frontend acciones y errores del autoplay. |
| `llm_frontend_output_probability` | `1.0` | Probabilidad de envío de mensajes de acción normales. Los errores y algunos mensajes de control importantes aún pueden forzarse. |
| `autoplay_push_probability` | `0.5` | Probabilidad de enviar sincronizaciones normales de la partida cuando el modo de acompañamiento no está activo. |
| `companion_push_probability` | `0.7` | Probabilidad de enviar sincronizaciones normales mientras el modo de acompañamiento está activo. |
| `neko_reporting_enabled` | `true` | Si se habilita la capacidad de observación de la neko. |
| `neko_report_interval_steps` | `1` | Cada cuántos pasos del autoplay se reorganiza el contenido de observación. |
| `neko_report_hud_enabled` | `true` | Si ese contenido de observación se envía de verdad al HUD o canal de mensajes del frontend. |
| `neko_commentary_enabled` | `true` | Si se pueden generar comentarios y recordatorios de acompañamiento. |
| `neko_commentary_probability` | `0.65` | Probabilidad de disparo para comentarios normales de baja prioridad. |
| `neko_commentary_min_interval_seconds` | `4` | Intervalo mínimo antes de repetir comentarios parecidos; sirve para reducir spam. |
| `neko_critical_commentary_always` | `true` | Si las alertas de prioridad alta deben anunciarse siempre. |
| `neko_guidance_max_queue` | `50` | Límite interno de la cola para contexto relacionado con guías y preferencias. |

### Protección automática y control del ritmo

| Opción | Valor por defecto | Descripción |
| --- | --- | --- |
| `neko_auto_low_hp_threshold` | `0.3` | Si la proporción de vida cae por debajo de este valor, el autoplay preferirá pausar. |
| `neko_auto_safe_hp_threshold` | `0.5` | Cuando la vida vuelve a este rango, la situación puede volver a considerarse segura. |
| `neko_auto_dangerous_attack_threshold` | `20` | Si la intención de daño enemiga alcanza este umbral, puede activarse la protección de desaceleración. |
| `neko_auto_resume_after_low_hp` | `true` | Si se permite reanudar automáticamente tras una pausa por vida baja cuando la situación vuelve a ser segura. |
| `neko_desperate_enabled` | `true` | Si se activa la postura de supervivencia en vida crítica. |
| `neko_desperate_hp_threshold` | `0.2` | Proporción de vida que dispara esa postura de supervivencia. |
| `neko_maximize_enabled` | `true` | Si se activa una inclinación más fuerte hacia jugadas de valor máximo. |

## Formas recomendadas para usuarios normales

Los usuarios normales no necesitan memorizar parámetros de bajo nivel. Lo más cómodo es pasar la frase original a las entradas de más alto nivel que siguen activas, y dejar que el plugin decida si se trata de consultar la situación, ajustar la estrategia o ejecutar el siguiente paso sugerido.

Interpretación recomendada:

| Lo que quiere decir el usuario | Capacidad más adecuada |
| --- | --- |
| `qué está pasando ahora` | `sts2_get_status` |
| `enséñame la situación actual` | `sts2_read_state` |
| `deja que ella siga jugando` / `pausa el autoplay por ahora` / `que siga otra vez` / `ya no hace falta que juegue sola` | entradas de control de autoplay |
| `ajusta la estrategia según esto: en este evento prefiero la ruta de menor coste` | `sts2_apply_user_override` |
| `muéstrame qué quiere hacer después` | `sts2_get_planned_operation` |
| `haz el paso sugerido` | `sts2_execute_planned_operation` |
| `activa el modo de acompañamiento` / `desactiva el modo de acompañamiento` | entradas de control del modo de acompañamiento |

Flujo recomendado:
1. Primero mira la situación actual.
2. Luego revisa qué quiere hacer a continuación.
3. Si quieres cambiar el criterio, ajusta la estrategia con una frase.
4. Por último decide si ejecutas ese paso o si dejas que el autoplay continúe.

## Entradas del plugin

Estas son las capacidades públicas que realmente siguen expuestas por el script principal. Los nombres visibles se han llevado a un tono más natural, pero los `entry id` internos se mantienen estables para no romper la integración del host.

### `sts2_health_check`

Comprueba si el servicio local del Agent de Spire está realmente accesible. Es una buena primera comprobación al arrancar, al integrar o al investigar errores.

### `sts2_get_status`

Muestra el estado general del runtime: si la conexión está bien, en qué pantalla estás, si el autoplay está corriendo, si estás en standby y cuál es el estado reciente de errores y modo.

### `sts2_read_state`

Refresca una vez la situación actual y devuelve tres capas juntas:
- el snapshot actual
- el resumen de la situación actual
- el paquete de sincronización actual de la neko

Es útil cuando quieres ver todo de una vez antes de decidir el siguiente movimiento.

### `sts2_set_standby`

Activa o desactiva el modo standby. En standby no se ejecutan acciones, pero se conserva la capacidad de organizar el estado y preparar sincronización.

### `sts2_start_autoplay`

Deja que ella siga jugando. Inicia el autoplay en segundo plano y permite que la situación avance sola.

### `sts2_pause_autoplay`

Pausa el autoplay por ahora. Es útil si quieres intervenir manualmente o cambiar la estrategia antes del siguiente movimiento.

### `sts2_resume_autoplay`

Hace que vuelva a seguir jugando desde el punto donde estaba pausado.

### `sts2_stop_autoplay`

Hace que deje de jugar sola. Detiene completamente el autoplay en segundo plano y te devuelve el control.

### `sts2_enable_companion_mode`

Activa el modo de acompañamiento. Cuando está activo, el plugin organiza la situación con más frecuencia y envía observaciones, comentarios y recordatorios cuando corresponde.

### `sts2_disable_companion_mode`

Desactiva el modo de acompañamiento. Apaga la capa de comentarios, pero mantiene la lectura básica del estado y el control del autoplay.

### `sts2_apply_user_override`

Ajusta la estrategia a partir de una sola nota del usuario. Interpreta tu frase en el contexto de la escena actual y la convierte en un override ligado al evento o enemigo correspondiente.

Esta entrada también aplica una protección adicional:
- si el autoplay está corriendo, **lo pausa primero**
- después de actualizar la estrategia, te indica que **si quieres continuar debes reanudar el autoplay manualmente**
- no seguirá avanzando por su cuenta hasta que tú lo decidas explícitamente

### `sts2_get_planned_operation`

Muestra qué quiere hacer después. Es la opción adecuada si quieres inspeccionar la siguiente jugada antes de ejecutarla.

### `sts2_execute_planned_operation`

Ejecuta directamente el siguiente paso recomendado.

## Eventos enviados al frontend

El plugin envía varios tipos de información pasiva a través del canal de mensajes del host, agrupados sobre todo en tres bloques:

1. **Sincronización de estado y situación**
   - resumen de la situación actual
   - resumen de la recomendación actual
   - información de sincronización mientras el modo de acompañamiento está activo

2. **Retroalimentación del control del autoplay**
   - autoplay iniciado
   - pausado / reanudado / detenido
   - aviso de que debes reanudar manualmente tras actualizar la estrategia

3. **Avisos de acompañamiento y protección**
   - comentarios de acompañamiento
   - recordatorios de riesgo
   - pausa por vida baja
   - desaceleración por ataque peligroso
   - recuperación de velocidad o reanudación del autoplay cuando el peligro ha pasado

Estos envíos usan semántica pasiva por defecto y no deberían interrumpir a la fuerza la conversación principal. Su frecuencia también depende de ajustes como:
- `autoplay_push_probability`
- `companion_push_probability`
- `neko_commentary_probability`
- `neko_report_hud_enabled`

## Resolución de problemas habituales

### Error de conexión al llamar una entrada del plugin

Primero revisa:

- si el juego ya está iniciado
- si el mod `STS2 AI Agent` está colocado correctamente en `mods/`
- si `http://127.0.0.1:8080/health` es accesible
- si `base_url` en `plugin.toml` es correcto

### No se puede abrir `http://127.0.0.1:8080/health`

Comprueba en este orden:

1. si el juego está realmente iniciado
2. si `STS2AIAgent.dll`, `STS2AIAgent.pck` y `mod_id.json` se copiaron todos a `mods/`
3. si los nombres de archivo fueron renombrados, duplicados o puestos en una carpeta equivocada
4. si estás operando en el directorio del juego de Steam y no en el repositorio upstream
5. si un firewall o software de seguridad está bloqueando el puerto local

### El autoplay funciona, pero el frontend no recibe mensajes

Revisa:

- si `llm_frontend_output_enabled` está en `true`
- si `llm_frontend_output_probability` no es demasiado bajo
- si `neko_reporting_enabled` está en `true`
- durante la integración, puedes subir temporalmente `llm_frontend_output_probability` a `1`
- si el frontend del host está recibiendo realmente los mensajes del plugin

### La guía a mitad de partida no parece tener efecto

Revisa:

- si ahora mismo el plugin no está en standby
- si `sts2_send_neko_guidance` devolvió `ok`
- si la guía es suficientemente concreta, por ejemplo `prioriza defensa`, `pega primero al enemigo con menos vida`, `guarda la poción`
- si las acciones legales actuales permiten cumplir realmente esa guía

### La tarea semiautomática no termina

Revisa `stop_condition`:

- si es `manual` / `none`, la tarea no termina sola y debes llamar a `sts2_stop_autoplay`
- si es `current_combat`, termina cuando durante la tarea se ha entrado en combate y luego se sale de él
- si es `current_floor`, normalmente termina al limpiar el piso actual o al entrar en el siguiente

Puedes usar `sts2_get_status` para revisar `autoplay.task`.

### Se queda atascado en eventos, ventanas emergentes o estados de transición

La versión actual ya maneja eventos, popups y estados de transición. Las acciones prioritarias incluyen:

- `confirm_modal`
- `dismiss_modal`
- `choose_event_option`
- `proceed`

Si aun así sigue atascado, primero usa `sts2_read_state` para revisar el `screen` y `available_actions` actuales.

### El autoplay se pausa o se vuelve más lento de repente

Puede que haya saltado una protección de seguridad:

- se pausa si la proporción de HP cae por debajo de `neko_auto_low_hp_threshold`
- se ralentiza en Boss o ante ataques peligrosos
- si `neko_auto_resume_after_low_hp` está en `true`, puede reanudarse cuando el HP vuelva a `neko_auto_safe_hp_threshold`

Puedes usar `sts2_get_status` para revisar el estado, o llamar a `sts2_resume_autoplay` / `sts2_stop_autoplay` para intervenir.

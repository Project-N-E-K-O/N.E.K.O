# Plugin de respuesta automática de QQ

Este plugin se conecta a QQ mediante el protocolo OneBot y ofrece respuestas automáticas inteligentes según el nivel de permisos. Soporta chats privados y grupos, e integra conversación con IA.

## Inicio y guía

1. Descarga una implementación de OneBot (se recomienda NapCat). Los ejemplos siguientes usan NapCat.
   Abre:
   ```text 
   https://github.com/NapNeko/NapCatQQ/releases
   ```
   Elige cualquier paquete para descargar (se recomienda `NapCat.Shell.zip`).
2. Inicia NapCat y abre la página de configuración de NapCat.
3. En la barra lateral izquierda, selecciona **Configuración de red**.
4. Agrega un **Servidor WS**, actívalo y guarda.
5. Inicia el plugin.
6. Vuelve una vez atrás.
7. Abre de nuevo el panel de este plugin.
8. Sigue la guía paso a paso.

Notas sobre el flujo actual:
- Durante `startup`, el plugin crea automáticamente el archivo de configuración de negocio si todavía no existe.
- NapCat se inicia primero desde el directorio de ejecución configurado; si está vacío, vuelve al directorio predeterminado.
- Cuando NapCat genera el código QR de inicio de sesión, este se copia al directorio estático del plugin; desde la UI puedes pulsar **Actualizar código QR** para sincronizar y mostrar la imagen más reciente.
- Desde la interfaz se puede gestionar directamente:
  - Dirección OneBot / Token / PATH
  - Inicio de NapCat en primer plano o en segundo plano
  - Usuarios de confianza / grupos de confianza
  - Inicio / parada de la respuesta automática

## Características

- Soporte del protocolo OneBot mediante NapCat
- Gestión multinivel de permisos de usuario: `admin`, `trusted`, `normal`
- Control multinivel de permisos de grupo: `trusted`, `open`, `normal`
- Integración de conversación con IA usando OmniOfflineClient
- Sincronización de memoria para conversaciones privadas del administrador
- Reenvío probabilístico de mensajes normales al administrador
- Modo de grupo abierto con respuesta sin `@`
- Gestión de apodos para usuarios de confianza
- Reconexión automática de WebSocket con retroceso exponencial

## Configuración

Lo recomendable es configurar todo desde la UI del plugin en **Configuración del servicio QQ OneBot**. En el primer arranque se crea automáticamente el archivo de configuración de negocio. Los campos más habituales son:

- `onebot_url`: dirección WebSocket de OneBot, por defecto `ws://127.0.0.1:3001`
- `token`: token de acceso de OneBot
- `napcat_directory`: directorio de ejecución de NapCat
- `show_napcat_window`: `true` para iniciar en primer plano con consola visible; `false` para iniciar en segundo plano
- `trusted_users`: lista de usuarios de confianza
- `trusted_groups`: lista de grupos de confianza
- `normal_relay_probability`: probabilidad de reenviar mensajes normales al dueño
- `truth_reply_probability`: probabilidad de responder activamente en grupos open

### Tabla de configuración

| Clave | Tipo | Descripción |
|------|------|-------------|
| `onebot_url` | string | Dirección WebSocket del servicio OneBot |
| `token` | string | Token de acceso del servicio OneBot (si el servidor lo requiere) |
| `trusted_users` | array | Lista de usuarios de confianza con número QQ, nivel de permiso y apodo |
| `trusted_groups` | array | Lista de grupos de confianza con número de grupo y nivel de permiso |
| `normal_relay_probability` | float | Probabilidad de reenviar mensajes normales privados/grupales al dueño |
| `truth_reply_probability` | float | Probabilidad de respuesta directa en grupos `open` sin `@` |

### Campos de probabilidad dentro de los grupos de confianza

Cada elemento de `trusted_groups` puede incluir dos campos de probabilidad opcionales además de `group_id` y `level`:

- `normal_relay_probability`: solo se aplica cuando `level = normal`. Cuando un mensaje del grupo entra en la ruta de “reenviar al dueño”, este valor sustituye la probabilidad global predeterminada de reenvío para ese grupo.
- `open_reply_probability`: solo se aplica cuando `level = open`. En grupos abiertos, cuando nadie hace `@` al bot, este valor sustituye la probabilidad global predeterminada de respuesta proactiva para ese grupo.

Notas:
- Ambos valores deben ser decimales entre `0` y `1`.
- Si se dejan vacíos, el plugin usa la configuración global predeterminada.
- Los grupos `trusted` no usan ninguno de estos dos campos, porque solo entran en el flujo de respuesta normal cuando el bot recibe un `@` explícito.

## Niveles de permiso

### Permisos de usuario

| Nivel | Significado | Comportamiento |
|------|-------------|----------------|
| `admin` | Administrador | Respuesta directa en privado, sincronización con memoria, tratamiento como "maestro" |
| `trusted` | Usuario de confianza | Respuesta directa en privado, sin sincronización de memoria, admite apodo |
| `normal` | Usuario normal | No responde directamente, puede reenviarse al administrador según probabilidad |
| `none` | No autorizado | Se ignora el mensaje |

### Permisos de grupo

| Nivel | Significado | Comportamiento |
|------|-------------|----------------|
| `trusted` | Grupo de confianza | Responde solo si el bot recibe `@` |
| `open` | Grupo abierto | Puede responder sin `@`, reutiliza contexto temporal y ficha del personaje, pero no escribe en memoria |
| `normal` | Grupo normal | No responde directamente, puede reenviar al administrador |
| `none` | No autorizado | Se ignora el mensaje |

## Información adicional

### 1. Envío proactivo de mensajes

Puedes invocar entradas del panel para que el bot primero genere un texto con estilo mediante IA y luego lo envíe de forma proactiva al usuario o grupo indicado.

Notas:
- `message` ahora representa una instrucción para la IA, no el texto final que se enviará sin cambios.
- Se reutilizan la personalidad del personaje y la configuración del modelo existentes.
- En mensajes privados proactivos puede leerse el contexto de memoria, pero este envío no se vuelve a escribir en la memoria.
- Los envíos proactivos a grupos no se escriben en memoria.
- La entrada privada usa `target`: puede ser un ID numérico de QQ o un apodo ya configurado en la lista de usuarios de confianza.
- `group_id` debe ser una cadena numérica.
- `message` no puede estar vacío.
- La respuesta automática debe estar ya iniciada y OneBot debe estar conectado; de lo contrario, la entrada fallará inmediatamente.

### 2. Detener el plugin

Al detener el plugin se realiza lo siguiente:
- Cierre de la conexión WebSocket
- Limpieza de los recursos en ejecución

## Preguntas frecuentes

### 1. No se puede conectar a OneBot

**Problema**: en el log aparece `Failed to connect to OneBot`

**Soluciones**:
- Comprueba que NapCat esté funcionando correctamente (puerto 3001)
- Confirma que `onebot_url` sea correcto
- Verifica que el `token` sea válido

### 2. El bot no responde

**Problema**: se envía un mensaje pero no hay respuesta

**Soluciones**:
- Comprueba que el remitente esté en `trusted_users`
- Revisa el nivel de permiso (`normal` no recibe respuesta directa)
- Mira el log para confirmar que el mensaje se haya recibido
- En grupos `trusted`, asegúrate de haber hecho `@` al bot
- En grupos `open`, no hace falta `@` si todo está configurado correctamente

### 3. Fallo en la sincronización de memoria

**Problema**: el log muestra un error de sincronización de memoria

**Soluciones**:
- Confirma que Memory Server está en ejecución
- Solo se sincronizan a memoria las conversaciones privadas del administrador
- Los grupos (incluidos los `open`) solo usan contexto temporal y no escriben en el almacenamiento de memoria

### 4. No funciona el reenvío de mensajes normales

**Problema**: los mensajes de usuarios normales no se reenvían al administrador

**Soluciones**:
- Comprueba que haya un administrador configurado (`level = "admin"`)
- Verifica el valor de `normal_relay_probability` (por defecto 0.1 = 10%)
- Revisa el log para ver si el reenvío llegó a activarse

## Contacto

Si tienes cualquier problema, abre un issue o envía un correo a zhaijiunknown@outlook.com.

## Licencia

Este plugin sigue la licencia del proyecto N.E.K.O.

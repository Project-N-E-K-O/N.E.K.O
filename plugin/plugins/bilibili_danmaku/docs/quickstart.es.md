# Escucha de danmaku de Bilibili - Inicio rápido

En unos pocos pasos puedes monitorizar en tiempo real los danmaku de una sala en directo de Bilibili, combinándolos con respuestas de AI, una LLM de fondo y herramientas de lectura/escritura de Bilibili.

---

## 1. Estado de la conexión

La tarjeta superior muestra en tiempo real el estado de ejecución del plugin:

- **Luz de estado** — Gris = sin conexión, verde = conectado, amarillo = conectando, rojo = error
- **Recibidos** — Total acumulado de danmaku / regalos / SC recibidos
- **Filtrados** — Número de mensajes bloqueados por las reglas de filtro
- **Búfer** — Danmaku a la espera de envío agregado
- **Popularidad** — Valor de popularidad actual de la sala en directo

---

## 2. Iniciar sesión en tu cuenta de Bilibili

Haz clic en la sección «Cuenta de Bilibili» para desplegar el panel de inicio de sesión:

- **Inicio de sesión por QR** (recomendado): pulsa «Inicio por QR» → escanea el código QR con la Bilibili App → espera la confirmación automática
- **Comprobar credenciales**: consulta el estado de sesión, el nombre de usuario, el UID y la fecha de caducidad
- **Recargar credenciales**: actualiza manualmente el estado de inicio de sesión
- **Cerrar sesión**: elimina las credenciales cifradas locales y cierra la sesión

> Se ha eliminado la entrada manual de Cookie para evitar fugas de información sensible. El modo invitado puede recibir danmaku, pero no enviarlos ni usar el filtrado avanzado.

---

## 3. Ajustes de la sala en directo

En la zona «Ajustes de la sala en directo»:

1. **Introduce el ID de la sala** — se obtiene de la URL de la sala, por ejemplo `22925943` en `https://live.bilibili.com/22925943`
2. **Pulsa «Cambiar sala»** — para aplicar el nuevo ID
3. **Pulsa «Iniciar escucha»** — conecta con el servidor de danmaku y empieza a recibirlos

**Enviar danmaku en directo**:

- Con «Que NEKO hable» desactivado: el contenido del campo se envía directamente a la sala
- Con «Que NEKO hable» activado: el contenido y el contexto de la sala pasan a NEKO, que genera la respuesta según su personaje antes de enviarla

---

## 4. Ajustes de envío al AI

Controla cómo se envían los danmaku al AI para procesarlos:

- **Intervalo de envío (segundos)** — Intervalo con el que los danmaku agregados se envían al AI. Se recomienda 10–30 segundos. Demasiado corto: el AI reacciona con demasiada frecuencia; demasiado largo: alta latencia en la respuesta
- **Longitud máxima del danmaku** — Bilibili limita los danmaku a 20 caracteres; lo que sobrepase la respuesta del AI se trunca automáticamente. Se recomienda mantener 20
- **Nombre del AI destino** — Indica qué AI recibe los danmaku. Si lo dejas en blanco, se enviarán al AI por defecto
- **UID / nombre de usuario del dueño en Bilibili** — Una vez configurada la cuenta del dueño, NEKO trata sus mensajes de forma especial (respuesta prioritaria, tono distinto, etc.)

---

## 5. Flujo de danmaku en tiempo real

Muestra en tiempo real los danmaku, regalos y SC recibidos:

- **Danmaku (Rosa)** — Danmaku de usuarios habituales; muestra nombre, nivel e insignia de fan
- **Regalo (Dorado)** — Registro de regalos enviados por usuarios
- **SC (Super Chat) (Verde)** — Mensaje destacado de pago

**Botones de control**:

- **Auto-scroll**: si está activo, los nuevos danmaku se desplazan automáticamente al área visible
- **Pausar / Reanudar**: detiene o reanuda la actualización del flujo de danmaku
- **Vaciar**: limpia el historial de danmaku mostrado actualmente

---

## 6. Herramientas de lectura de Bilibili

Leen datos públicos de Bilibili sin permisos de escritura, por lo que su uso es seguro. Rellena los campos «palabra clave / BV / UID / ID de favoritos» de arriba y pulsa el botón correspondiente:

- **Buscar vídeos** — Buscar vídeos por palabra clave. Requerido: Palabra clave
- **Vídeos populares** — Lista de vídeos populares de todo el sitio
- **Búsquedas en tendencia** — Ranking de búsquedas en tiempo real de Bilibili
- **Imprescindibles de la semana** — Selección semanal de imprescindibles
- **Ranking** — Ranking de una categoría específica. Requerido: Orden/categoría (`all`/`game`/`dance`, etc.)
- **Información del vídeo** — Obtener detalles del vídeo. Requerido: BV
- **Comentarios del vídeo** — Obtener la lista de comentarios del vídeo. Requerido: BV
- **Subtítulos del vídeo** — Obtener subtítulos generados por AI. Requerido: BV
- **Danmaku histórico** — Obtener el historial de danmaku del vídeo. Requerido: BV
- **Información del usuario** — Obtener el perfil del usuario. Requerido: UID
- **Subidas del usuario** — Obtener la lista de vídeos subidos por el usuario. Requerido: UID
- **Lista de favoritos** — Obtener la lista de carpetas de favoritos del usuario. Requerido: UID
- **Contenido de favoritos** — Obtener los vídeos dentro de una carpeta de favoritos. Requerido: media_id de favoritos

Los resultados se muestran de forma unificada en el área «Resultados de las herramientas de Bilibili».

---

## 7. Herramientas de escritura de Bilibili

Realizan operaciones de escritura en Bilibili. **Afectan a tu cuenta**, úsalas con precaución:

- **Publicar comentario/respuesta** — Comentar bajo un vídeo o responder a un comentario. Requerido: BV + contenido del comentario; las respuestas requieren además el rpid del comentario
- **Publicar publicación** — Publicar una nueva publicación (dinámica). Requerido: Texto de la publicación (admite imágenes)
- **Enviar mensaje privado** — Enviar un mensaje privado a un usuario. Requerido: UID del destinatario + contenido del mensaje

- **Que NEKO hable**: al activarlo, comentarios/publicaciones/mensajes privados se generan primero por NEKO según su personaje y luego se envían
- Los botones de las herramientas de escritura son rojos. Antes de invocarlos, confirma: cuenta iniciada, contenido correcto y destinatario adecuado

---

## 8. Ajustes de la LLM de fondo

Una vez activada, los danmaku se agregan y se envían a una LLM designada que genera mensajes de orientación, para que NEKO responda más naturalmente al ambiente de la sala.

**Configuración básica**:

- **Interruptor de activación** — Activa/desactiva la función de LLM de fondo
- **URL de la API** — Endpoint compatible con OpenAI, p. ej. `https://api.openai.com/v1/chat/completions`
- **Nombre del modelo** — P. ej. `gpt-4o-mini`, `deepseek-chat`
- **API Key** — Clave de la API (oculta por defecto al introducirla; pulsa para verla)
- **Ventana de agregación** — Cuántos danmaku recopilar antes de lanzar el resumen de la LLM. Se recomienda 10–20
- **Tamaño máx. de muestra** — Capacidad máxima del pool de muestras de danmaku; al superarla, se descartan los más antiguos por orden temporal

**Ajustes avanzados** (haz clic en «Ajustes avanzados» para desplegar):

- **Nombre de la catgirl** — Sustituye automáticamente el marcador `{name}` en los prompts
- **Contexto de la base de conocimiento** — Personalidad, frases recurrentes y memes habituales del personaje; admite el marcador `{name}`
- **Resumen del perfil del usuario** — Perfil básico del streamer/usuarios como referencia para la LLM
- **Plantilla del prompt** — System Prompt personalizado; admite los marcadores `{name}` y `{knowledge_context}`. Si lo dejas vacío, se usa la plantilla por defecto

> Tras configurarlo, pulsa «Guardar configuración» y luego activa el interruptor. Pulsa «Probar» para comprobar la conectividad de la API.

---

## Preguntas frecuentes

**Falla el inicio de sesión por QR?** Asegúrate de haber iniciado sesión en la App; el QR es válido 2 minutos — refréscalo y vuelve a escanear
**La escucha de danmaku no responde?** Comprueba que el ID de la sala sea correcto, que la red funcione y que la cuenta haya iniciado sesión
**El AI no responde a los danmaku?** Asegúrate de que el intervalo de envío esté configurado, la LLM de fondo esté activada y la API esté correctamente configurada
**Falla el envío de danmaku?** Confirma que has iniciado sesión y que tienes permisos para enviar danmaku en la sala (algunas restringen por nivel de cuenta)
**Errores en las llamadas a la API?** Verifica la URL de la API, el nombre del modelo y la API Key; pulsa «Probar» para diagnosticar


# Plugin Mijia · Guía rápida de uso (v1.1)

> **Consejo clave**: este plugin está diseñado en torno al **control por lenguaje natural**. En la mayoría de los casos no necesitas buscar el ID del dispositivo ni parámetros técnicos: basta con dar una orden coloquial para controlar tus dispositivos.

---

## 1. Tabla rápida de comandos principales

Solo tienes que decirle al AI Agent uno de los siguientes tipos de comandos y el plugin los analizará y ejecutará automáticamente:

| Tipo de acción | Comando de ejemplo (cópialo y úsalo) | Notas |
| :--- | :--- | :--- |
| **💡 Encender/apagar dispositivo** | `enciende la luz del dormitorio` <br> `apaga el enchufe del salón` | Admite verbos como «enciende / apaga / on / off» |
| **🏠 Control por zona** | `enciende la luz del salón` <br> `apaga el aire acondicionado del dormitorio principal` | **(Novedad clave)** Admite «nombre de habitación + nombre de dispositivo» |
| **🌡️ Ajustar temperatura** | `pon el aire a 26 grados` <br> `ajusta el aire a 24 grados` | Reconoce números y unidades automáticamente |
| **☀️ Ajustar brillo** | `pon la luz al 50%` <br> `brillo de la lámpara de mesa 30` | Admite porcentajes o números enteros |
| **🔄 Cambiar de modo** | `pon el aire en modo frío` <br> `pon el ventilador en automático` | Admite las palabras de modo más comunes |
| **🎬 Ejecutar escena** | `ejecuta la escena de llegada a casa` <br> `activa la escena de salir de casa` | Las escenas deben estar configuradas previamente en la Mijia App |

---

## 2. Truco avanzado: resolver el problema de «hay tantos dispositivos que no se encuentra el correcto»

Si en casa tienes varios dispositivos del mismo tipo (por ejemplo, varias «luces»), decir simplemente `enciende la luz` puede provocar un mensaje de desambiguación.

### 1. Ejemplo de mensaje de desambiguación
Cuando un comando es ambiguo, el plugin devuelve un mensaje similar al siguiente; **añade el nombre de la habitación** tal como te indique:

> Se han encontrado 3 dispositivos que coinciden con «luz»:
> 1. 🟢 Salón — plafón de techo
> 2. 🔴 Dormitorio — plafón de techo
> Especifica con precisión usando «nombre de habitación + nombre de dispositivo», por ejemplo «luz del dormitorio»

### 2. Formato de comando preciso recomendado
Para evitar confusiones, recomendamos adoptar el hábito de usar **`[nombre de habitación] + [nombre de dispositivo]`**:

*   ❌ Comando ambiguo: `enciende la luz`
*   ✅ Comando preciso: `enciende la luz del dormitorio`
*   ✅ Comando preciso: `enciende el plafón de techo del salón`

---

## 3. Puntos de entrada de la API de uso común (para desarrolladores / usuarios avanzados)

Si necesitas llamar directamente a las interfaces del plugin, estos son los puntos de entrada principales:

| ID de entrada | Función | Ejemplo de parámetros |
| :--- | :--- | :--- |
| `smart_control` | **Control unificado por lenguaje natural (recomendado)** | `{ "command": "enciende la luz del dormitorio" }` |
| `find_device_by_name` | Buscar un dispositivo | `{ "name": "luz del dormitorio" }` |
| `query_device_state` | Consultar el estado de un dispositivo | `{ "name": "aire acondicionado" }` |
| `execute_scene` | Ejecutar una escena | `{ "scene_id": "12345" }` |

---

## 4. Preguntas frecuentes (FAQ)

**Q: ¿Por qué aparece el mensaje «No has iniciado sesión»?**
A: Abre primero el plugin Mijia en el panel de plugins de NEKO, pulsa «Iniciar sesión con código QR» y completa la autorización.

**Q: ¿Por qué el dispositivo está sin conexión pero el plugin lo muestra como conectado?**
A: El plugin muestra el estado en la nube, que puede sufrir cierto retraso. Comprueba que el dispositivo realmente esté encendido y conectado a la Mijia App.

**Q: ¿Qué dispositivos son compatibles?**
A: En teoría, todos los dispositivos de la plataforma IoT de Mijia. Cualquier dispositivo que se pueda controlar en la Mijia App también se puede controlar con este plugin.

---
*Versión del documento: 2026-04-30*

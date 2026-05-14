# Mijia Plugin · Quick Operation Guide (v1.1)

> **Key tip**: This plugin is built around **natural-language control**. In most cases you don't need to look up device IDs or technical parameters — just use spoken commands to control your devices.

---

## 1. Core command cheat sheet

Just say one of the following kinds of commands to the AI Agent, and the plugin will parse and execute it automatically:

- **💡 Switch device** — `turn on the bedroom light` / `turn off the living room socket`  
  Supports verbs like "turn on / turn off / on / off"
- **🏠 Room control** — `turn on the light in the living room` / `turn off the master bedroom AC`  
  **(New focus)** Supports "room name + device name"
- **🌡️ Adjust temperature** — `set the AC to 26 degrees` / `set the AC to 24 degrees`  
  Automatically recognizes numbers and units
- **☀️ Adjust brightness** — `set the light to 50%` / `desk lamp brightness 30`  
  Supports percentages or integers
- **🔄 Switch mode** — `set the AC to cool mode` / `set the fan to auto`  
  Supports common mode words
- **🎬 Run a scene** — `run the come-home scene` / `trigger the leave-home scene`  
  Scenes must be pre-configured in the Mijia App

---

## 2. Advanced tip: solving the "too many devices to find" problem

If you have multiple devices of the same type at home (for example several "lights"), saying `turn on the light` directly may trigger a disambiguation prompt.

### 1. Disambiguation prompt example
When a command is ambiguous, the plugin will return a prompt similar to the following — please **add the room name** as suggested:

> Found 3 devices matching 'light':
> 1. 🟢 Living room ceiling light
> 2. 🔴 Bedroom ceiling light
> Please specify precisely with room name + device name, e.g. 'bedroom light'

### 2. Recommended precise command format
To avoid confusion, we recommend getting into the habit of using **`[room name] + [device name]`**:

*   ❌ Vague command: `turn on the light`
*   ✅ Precise command: `turn on the bedroom light`
*   ✅ Precise command: `turn on the living room ceiling light`

---

## 3. Common API entry points (for developers / advanced users)

If you need to call the plugin interface directly, here are the core entry points:

- **`smart_control`** — Unified natural-language control (recommended)  
  Params: `{ "command": "turn on the bedroom light" }`
- **`list_devices`** — Get device list  
  Params: `{ "home_id": "12345" }`
- **`query_device_state`** — Query device state  
  Params: `{ "name": "AC" }`
- **`list_scenes`** — List smart scenes  
  Params: `{ "home_id": "12345" }`

---

## 4. Frequently asked questions (FAQ)

**Q: Why does it say "Not logged in"?**
A: First open the Mijia plugin in the NEKO plugin panel, tap "Scan QR to log in" and complete the authorization.

**Q: Why is my device offline but the plugin shows it online?**
A: The plugin shows the cloud state, which may be delayed. Please check that the device is actually powered on and connected to the Mijia App.

**Q: Which devices are supported?**
A: In theory all devices on the Mijia IoT platform are supported. Any device that can be controlled in the Mijia App is supported by this plugin.

---
*Document version: 2026-04-30*

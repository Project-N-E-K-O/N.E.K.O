# Frontend Overview

N.E.K.O.'s frontend is built with **vanilla JavaScript** (no framework) with Jinja2 HTML templates. It renders Live2D models via Pixi.js and VRM models via Three.js.

## Technology stack

| Component | Technology |
|-----------|-----------|
| Templates | Jinja2 (server-rendered) |
| JavaScript | Vanilla ES6+ |
| Live2D rendering | Pixi.js + Live2D Cubism SDK |
| VRM rendering | Three.js + @pixiv/three-vrm |
| Styling | Custom CSS + dark mode support |
| i18n | JSON locale files with JS runtime |

## File structure

```
static/
├── app.js                    # Main application logic
├── theme-manager.js          # Dark/light mode toggle
├── css/
│   ├── index.css             # Main stylesheet
│   ├── dark-mode.css         # Dark mode overrides
│   ├── chara_manager.css     # Character manager styles
│   └── ...
├── js/
│   ├── api_key_settings.js   # API key settings page
│   ├── agent_ui_v2.js        # Agent interface
│   ├── steam_workshop_manager.js
│   └── ...
├── locales/
│   ├── en.json               # English
│   ├── zh-CN.json            # Simplified Chinese
│   ├── zh-TW.json            # Traditional Chinese
│   ├── ja.json               # Japanese
│   └── ko.json               # Korean
├── live2d-ui-*.js            # Live2D UI components
└── vrm-ui-*.js               # VRM UI components
```

## Key concepts

- **Pages** are server-rendered HTML templates that load JavaScript modules
- **WebSocket** is used for real-time audio/text chat (see [WebSocket Protocol](/api/websocket/protocol))
- **REST API** is used for all CRUD operations (see [API Reference](/api/))
- **Theme manager** handles dark/light mode with CSS variable overrides
- **i18n** is handled client-side by loading the appropriate locale JSON file

# Frontend Overview

N.E.K.O.'s frontend consists of three layers: traditional server-rendered pages, a React chat window component, and a Vue plugin manager dashboard.

## Architecture

| Layer | Technology | Location |
|-------|-----------|----------|
| Main UI pages | Vanilla JS + Jinja2 templates | `static/` + `templates/` |
| Chat window | React 18 + TypeScript | `frontend/react-neko-chat/` |
| Plugin manager | Vue 3 + Element Plus | `frontend/plugin-manager/` |
| Live2D rendering | Pixi.js + Live2D Cubism SDK | `static/` |
| VRM rendering | Three.js + @pixiv/three-vrm | `static/` |

## Traditional frontend (static/ + templates/)

The main UI is built with **vanilla JavaScript** and Jinja2 HTML templates.

```
static/
├── app.js                    # Main application logic
├── theme-manager.js          # Dark/light mode toggle
├── css/                      # Stylesheets
├── js/                       # Feature-specific JS modules
├── locales/                  # i18n JSON files (en, zh-CN, zh-TW, ja, ko)
├── live2d-ui-*.js            # Live2D UI components
├── vrm-ui-*.js               # VRM UI components
└── react/neko-chat/          # React chat window build output
```

## Chat window (React)

The chat window is built as an IIFE library and embedded in the main page.

- **Source**: `frontend/react-neko-chat/`
- **Build output**: `static/react/neko-chat/neko-chat-window.iife.js`
- **Global**: `window.NekoChatWindow`
- **Dev server**: `npm run dev` (port 5174)

The glue layer `static/app-react-chat-window.js` loads and mounts the React component into the DOM.

## Plugin manager (Vue)

A standalone dashboard for managing plugins, viewing logs, and monitoring metrics.

- **Source**: `frontend/plugin-manager/`
- **Build output**: `frontend/plugin-manager/dist/`
- **Served at**: `/ui/` by the plugin server (port 48916)
- **Dev server**: `npm run dev` (port 5173, proxies API to plugin server)

## Key concepts

- **Pages** are server-rendered HTML templates that load JavaScript modules
- **WebSocket** is used for real-time audio/text chat (see [WebSocket Protocol](/api/websocket/protocol))
- **REST API** is used for all CRUD operations (see [API Reference](/api/))
- **Theme manager** handles dark/light mode with CSS variable overrides
- **i18n** is handled client-side by loading the appropriate locale JSON file

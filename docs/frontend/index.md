# Frontend overview

N.E.K.O. serves its main interface from the FastAPI main server. The repository contains three frontend codebases with different build and runtime boundaries.

## Codebases

| Surface | Technology | Source | Runtime artifact |
| --- | --- | --- | --- |
| Main UI and auxiliary pages | Jinja2, vanilla JavaScript, CSS | `templates/`, `static/app/`, `static/live2d/`, `static/vrm/`, `static/mmd/` | Rendered by the main server, normally on port `48911` |
| Chat UI | React 18 and TypeScript | `frontend/react-neko-chat/` | `static/react/neko-chat/neko-chat-window.iife.js` and `.css` |
| Plugin manager | Vue 3 and TypeScript | `frontend/plugin-manager/` | `frontend/plugin-manager/dist/`, served by the plugin server |

The avatar renderers are part of the main UI: Live2D uses Pixi/Cubism, VRM and MMD use Three.js, and PNGTuber uses `static/pngtuber-core.js`. The Electron desktop pet is a host mode, not another avatar format.

## One chat implementation

`frontend/react-neko-chat/` is the only chat UI implementation. Its IIFE exposes `window.NekoChatWindow`, and scripts under `static/app/app-react-chat-window/` mount it into `#react-chat-window-root`.

Both `templates/index.html` and `templates/chat.html` provide that mount point. The former shows the chat as a floating/collapsible surface in the main page; the latter hosts compact or full standalone chat surfaces.

The old `#chat-container` DOM remains only as a compatibility shell for older scripts. Both templates hide it, and `static/app/app-chat-adapter.js` replaces legacy `appendMessage()` calls with calls to `window.reactChatWindowHost`. Do not add new UI or logic to the legacy container.

## Web and Electron hosts

In a browser, `/` is the single main page. `/chat`, `/chat_full`, and `/subtitle` can also be opened directly for development and testing.

The Electron distribution is a separate host application. It loads multiple routes into independent windows: the pet uses the main-page template, chat windows use `/chat` or `/chat_full`, and subtitles use `/subtitle`. Renderer code detects preload globals such as `window.nekoChatWindow` and `window.nekoSubtitle`; the host owns native window creation and IPC.

Cross-window web fallbacks live under `static/app/app-interpage/`. They use the `neko_page_channel` `BroadcastChannel`, with same-origin `postMessage` fallbacks. Changes to routes, asset URLs, initialization order, or window communication must be checked in both browser and Electron modes.

## Loading and asset rules

- Server-rendered pages use root-relative URLs such as `/static/...`; do not derive assets from the current route.
- User and Workshop models are exposed through dedicated mounts; never turn filesystem paths into browser URLs.
- Classic scripts under `static/` communicate through documented globals and DOM events, so template load order is part of the runtime contract.
- React chat changes belong in `frontend/react-neko-chat/`; rebuild the IIFE instead of editing generated files.
- Plugin-manager changes belong in `frontend/plugin-manager/`; its build and localization are separate from the main page.

See [Pages and templates](/frontend/pages), [Internationalization](/frontend/i18n), and the renderer-specific pages for the current entry points.

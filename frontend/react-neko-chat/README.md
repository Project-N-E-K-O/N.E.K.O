# React Neko Chat

This directory hosts the new React-based chat module for N.E.K.O.

## Current role

This is a new module, not a replacement of the existing vanilla frontend yet.

The old frontend code stays intact during this phase.

## Refactor rules

The following rules are part of this refactor and should be treated as project constraints:

1. Do not modify or delete the legacy chat implementation unless explicitly needed later.
2. Migrate logic selectively from the old implementation into this module.
3. Keep the React module small, readable, and easy to embed into the existing static pages.
4. The new UI is not a visual copy of the current floating chat box.
5. The new layout should move toward a QQ-like chat window experience.

## Three host contexts (any change must be verified in all three)

The same React bundle is mounted into three host contexts, and they behave
differently. Any change here must be checked in **all three** before shipping —
"works in the browser dev server" is **not** sufficient.

1. **`index.html` — desktop browser (wide viewport)**
   - Chat is a collapsible floating panel on top of the main page.
   - Vite dev server (`npm run dev`) approximates this path.
2. **`index.html` — mobile (narrow viewport)**
   - Same route, but the mobile-detection branch kicks in: collapsed bubble,
     drag handle, keyboard-aware scroll, touch-only behaviors.
   - Reproduce by resizing the browser window or via mobile emulation.
3. **`chat.html` — Electron standalone window**
   - The desktop distribution opens chat in its own BrowserWindow. Full-screen
     layout, **no Live2D / side panels**, and **mobile detection is bypassed**
     even if the window is dragged narrow.
   - Electron's Chromium fork is **not** equivalent to a regular browser.
     Known divergence: `scrollTo({ behavior: 'smooth' })` is a silent no-op in
     Electron 41 — so anything depending on smooth scroll must fall back to
     instant assignment (`scrollTop = scrollHeight`).

When editing scroll, sizing, animation, ResizeObserver, keyboard, or touch
logic, mentally walk all three paths first, then smoke-test all three before
declaring the change done.

## UI direction

The target interaction model is a QQ-style chat window, not a full QQ-like application system.

Expected layout direction:

- Main chat area: QQ-style message timeline
- Bottom area: richer composer and action row
- Top area: clearer title, session status, and lightweight actions
- Optional side sections are allowed only if they directly serve the single chat window

Visual goals:

- clearer information hierarchy
- stronger QQ-like desktop chat feel
- denser but more organized message layout
- more room for avatars, timestamps, status, attachments, and system notices

This refactor is expected to be a major chat-window UI redesign, not a rebuild into a full IM system.

## Integration direction

The preferred integration mode is:

- React chat is built as an embeddable module
- existing static pages provide the host container
- host-side bridge adapts websocket, IPC, and legacy page capabilities

The React layer should avoid directly depending on scattered `window.*` globals where possible.

## Suggested module boundaries

- `src/components/`: chat UI pieces
- `src/store/`: message and session state
- `src/bridge/`: host integration layer
- `src/mount.tsx`: mount and unmount entry for native pages

## Migration strategy

Phase 1:

- keep the scaffold running
- define mount API and host bridge
- lock down the new UI layout skeleton

Phase 2:

- migrate message rendering
- migrate input/composer behavior
- connect websocket events through the bridge

Phase 3:

- migrate attachments, status, and session states
- replace the old chat box in selected pages only after the new module is stable

## Notes for future work

- Preserve old code as reference while migrating.
- Favor deliberate UI redesign over one-to-one porting.
- When unsure, prefer architecture that supports a QQ-style chat window rather than the current floating-card layout.
- Avoid expanding scope into contact lists, multi-pane social features, or a full desktop IM shell unless explicitly requested.

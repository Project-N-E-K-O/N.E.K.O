
---
layout: home

hero:
  name: Project N.E.K.O.
  text: Developer Documentation
  tagline: Code-backed documentation for the local companion runtime, memory, Agent services, plugins, browser UI, and Electron routes.
  image:
    src: /logo.jpg
    alt: N.E.K.O. Logo
  actions:
    - theme: brand
      text: Get Started
      link: /guide/
    - theme: alt
      text: Run and Deploy
      link: /deployment/
    - theme: alt
      text: API Reference
      link: /api/
    - theme: alt
      text: View on GitHub
      link: https://github.com/Project-N-E-K-O/N.E.K.O

features:
  - icon: 🧭
    title: Choose the Right Runtime
    details: Source development serves the browser UI at /, while Electron distributions use separate routes and windows such as /chat and /subtitle.
    link: /guide/quick-start
    linkText: Start here
  - icon: 🎙️
    title: Conversation and Avatars
    details: Follow the current text, audio, vision, character, Live2D, VRM, MMD, PNGTuber, and desktop-pet ownership boundaries without duplicating the React chat UI.
    link: /frontend/
    linkText: Frontend architecture
  - icon: 🧠
    title: Persistent Memory
    details: Understand event persistence, projections, recall candidates, evidence and reflection, persona, maintenance queues, and optional local-vector retrieval as separate layers.
    link: /architecture/memory-system
    linkText: Memory architecture
  - icon: 🤖
    title: Agents and Plugins
    details: Trace task state, browser and computer automation, external Agent adapters, plugin routing, SDK contracts, hosted UI, and packaging from their implemented paths.
    link: /architecture/agent-system
    linkText: Agent architecture
  - icon: ▶️
    title: Start from Source
    details: Use Python 3.11 through uv, build the two frontend projects with the repository scripts, and start the supported suite with uv run python launcher.py.
    link: /guide/dev-setup
    linkText: Development setup
  - icon: 🔌
    title: Ports and Deployment
    details: Source defaults use 48911 for the main service and 48912 for memory. Docker maps host 48911/48912 to Nginx HTTP/HTTPS instead; do not mix the two port models.
    link: /deployment/
    linkText: Deployment choices
  - icon: 📡
    title: API Contracts
    details: Browse REST pages, WebSocket protocol, internal services, page routes, runtime tools, cloud-save staging, and capture bridge behavior verified against current routers.
    link: /api/
    linkText: Open API reference
  - icon: 🧰
    title: Configuration and Contribution
    details: Use the current schema and per-surface precedence rules, then follow uv, i18n, privacy, structural-symmetry, test, and packaging gates for changes.
    link: /contributing/
    linkText: Contribute safely
---

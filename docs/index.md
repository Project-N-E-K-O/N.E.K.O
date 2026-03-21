---
layout: home

hero:
  name: Project N.E.K.O.
  text: Developer Documentation
  tagline: A proactive, omni-modal AI companion featuring 24/7 ambient awareness, agent capability and an embodied emotional engine.
  image:
    src: /logo.jpg
    alt: N.E.K.O. Logo
  actions:
    - theme: brand
      text: Get Started
      link: /guide/
    - theme: alt
      text: API Reference
      link: /api/
    - theme: alt
      text: View on GitHub
      link: https://github.com/Project-N-E-K-O/N.E.K.O

features:
  - icon: 🏗️
    title: Microservice Architecture
    details: Three-server design (Main, Memory, Agent) with WebSocket real-time communication, ZeroMQ event bus, and hot-swappable LLM sessions.
    link: /architecture/
    linkText: Learn more
  - icon: 🔌
    title: Plugin SDK
    details: Extend N.E.K.O. with Python plugins. Decorator-based API, async support, lifecycle hooks, and persistent state management.
    link: /plugins/
    linkText: Build a plugin
  - icon: 🌐
    title: REST & WebSocket API
    details: Comprehensive API surface — 10 REST routers covering characters, models, memory, agents, and a streaming WebSocket protocol for real-time voice/text chat.
    link: /api/
    linkText: API reference
  - icon: 🧠
    title: Memory System
    details: Semantic recall via hybrid embedding-vector and BM25 indexing, time-indexed history, sliding-window compressed recent memory, and persistent user preferences.
    link: /architecture/memory-system
    linkText: How it works
  - icon: 🤖
    title: Agent Framework
    details: Background task execution via MCP, Computer Use, Browser Use, and Virtual Machine adapters. Automatic task planning, deduplication, and parallel capability assessment.
    link: /architecture/agent-system
    linkText: Explore agents
  - icon: 🎨
    title: Live2D & VRM
    details: Rich frontend with Live2D and VRM model rendering, emotion mapping, voice cloning, and internationalization across 5 languages.
    link: /frontend/
    linkText: Frontend guide
---

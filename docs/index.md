---
layout: home

hero:
  name: Project N.E.K.O.
  text: Developer Documentation
  tagline: Build the living AI companion metaverse — open source, extensible, multi-modal.
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
    details: Four-server design (Main, Memory, Agent, Monitor) with WebSocket real-time communication, ZeroMQ event bus, hot-swappable LLM sessions, and unified launcher.
    link: /architecture/
    linkText: Learn more
  - icon: 🔌
    title: Plugin SDK
    details: Extend N.E.K.O. with Python plugins. Decorator-based API, async support, lifecycle hooks, and persistent state management.
    link: /plugins/
    linkText: Build a plugin
  - icon: 🌐
    title: REST & WebSocket API
    details: Comprehensive API surface — 11 REST routers covering characters, models, memory, agents, workshop, and a streaming WebSocket protocol for real-time voice/text chat.
    link: /api/
    linkText: API reference
  - icon: 🧠
    title: Memory System
    details: Semantic recall via embeddings, time-indexed history, compressed recent memory with sliding window, persistent user preferences, and LLM-based settings extraction.
    link: /architecture/memory-system
    linkText: How it works
  - icon: 🤖
    title: Agent Framework
    details: Background task execution via MCP, Computer Use, Browser Use, and User Plugin adapters. Parallel capability assessment with priority-based execution.
    link: /architecture/agent-system
    linkText: Explore agents
  - icon: 🎨
    title: Live2D & VRM
    details: Rich frontend with Live2D and VRM model rendering, emotion mapping, voice cloning, and internationalization across 5 languages.
    link: /frontend/
    linkText: Frontend guide
---

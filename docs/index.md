---
layout: home
title: Project N.E.K.O. Developer Documentation
titleTemplate: false
description: Build, deploy, configure and extend Project N.E.K.O., an open-source AI companion with realtime voice, persistent memory, embodied avatars, agents, APIs and plugins.

hero:
  name: Project N.E.K.O.
  text: Developer Documentation
  tagline: A proactive, multimodal AI companion with optional screen-aware interaction, persistent memory, agent channels, and embodied avatars.
  image:
    src: /logo.jpg
    alt: N.E.K.O. Logo
  actions:
    - theme: brand
      text: Get Started
      link: /guide/
    - theme: brand
      text: Get on Steam
      link: https://store.steampowered.com/app/4099310/__NEKO/
    - theme: alt
      text: API Reference
      link: /api/
    - theme: alt
      text: View on GitHub
      link: https://github.com/Project-N-E-K-O/N.E.K.O

features:
  - icon: 🎮
    title: Steam Workshop & Community
    details: Available on Steam with Workshop support for sharing character cards, supported avatar assets, previews, and optional reference voice samples.
    link: https://store.steampowered.com/app/4099310/__NEKO/
    linkText: View on Steam
  - icon: 🎙️
    title: Omni-Modal Dialogue
    details: Voice, text, and vision in a unified conversation loop. Real-time speech with RNNoise neural denoising, AGC, and VAD for ultra-low-latency interaction.
    link: /architecture/
    linkText: Learn more
  - icon: 💬
    title: Proactive Chat
    details: Optional proactive interaction can use screen context, supported feeds, music, and memes when the corresponding features are enabled. Privacy mode can stop proactive screen viewing.
    link: /guide/
    linkText: Learn more
  - icon: 🧠
    title: Five-Dimensional Memory
    details: Per-character working, recent, fact, reflection, and persona layers. BM25 recall works without embeddings; optional local embeddings can improve semantic retrieval.
    link: /architecture/memory-system
    linkText: How it works
  - icon: 🤖
    title: Agent Framework
    details: Optional background tasks through enabled and ready Computer Use, Browser Use, user-plugin, OpenClaw, and OpenFang channels. Individual tasks and all active tasks can be cancelled.
    link: /architecture/agent-system
    linkText: Explore agents
  - icon: 🔌
    title: Plugin Ecosystem
    details: Plugin SDK and marketplace for custom extensions, with a decorator-based API, async lifecycle hooks, inter-plugin messaging, and Agent-facing entries when enabled.
    link: /plugins/
    linkText: Build a plugin
  - icon: 🎭
    title: Live2D, VRM, MMD & PNGTuber
    details: Four supported avatar formats can run in the main UI and desktop-pet host mode, with format-specific expressions, lip sync, animations, and interaction. Voice registration supports multiple cloud and local backends with provider-specific requirements.
    link: /frontend/
    linkText: Frontend guide
  - icon: 🌐
    title: Configurable AI Providers & i18n
    details: Multiple core, assist, speech, and related provider profiles are configurable. Provider availability changes by version and region; the product UI and prompts support 8 languages.
    link: /config/api-providers
    linkText: Provider list
---

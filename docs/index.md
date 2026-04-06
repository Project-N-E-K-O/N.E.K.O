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
      text: Get on Steam
      link: https://store.steampowered.com/app/3117010/NEKO/
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
  - icon: 🎮
    title: Steam Workshop & Community
    details: Available on Steam with full Workshop UGC support — share and discover characters, Live2D models, and plugins. Steam achievements, cloud saves, and one-click updates included.
    link: https://store.steampowered.com/app/3117010/NEKO/
    linkText: View on Steam
  - icon: 🎙️
    title: Omni-Modal Dialogue
    details: Voice, text, and vision in a unified conversation loop. Real-time speech with RNNoise neural denoising, AGC, and VAD — 13ms latency. Supports 14+ LLM providers out of the box.
    link: /architecture/
    linkText: Learn more
  - icon: 💬
    title: Proactive Chat
    details: 24/7 ambient awareness — she starts conversations based on screen content, trending topics, time of day, holidays, and personal interests. No prompt needed.
    link: /guide/
    linkText: Learn more
  - icon: 🧠
    title: Three-Tier Memory
    details: Semantic recall via hybrid embedding-vector and BM25 indexing. Facts, reflections, and persona layers with sliding-window compression and persistent user preferences.
    link: /architecture/memory-system
    linkText: How it works
  - icon: 🤖
    title: Agent Framework
    details: Background task execution via MCP tools, Computer Use, Browser Use, and OpenFang A2A adapters. Automatic task planning, deduplication, and parallel execution.
    link: /architecture/agent-system
    linkText: Explore agents
  - icon: 🔌
    title: Plugin Ecosystem
    details: Python plugin SDK v2 with marketplace, decorator-based API, async lifecycle hooks, and inter-plugin messaging. Built-in plugins for MCP, reminders, Bilibili danmaku, smart home, and more.
    link: /plugins/
    linkText: Build a plugin
  - icon: 🎭
    title: Live2D, VRM & Voice Clone
    details: Embodied avatars with emotion-mapped expressions, lip sync, and idle animations. Clone any voice from a 5-second sample via MiniMax or CosyVoice backends.
    link: /frontend/
    linkText: Frontend guide
  - icon: 🌐
    title: i18n & Multi-Provider
    details: Full UI and prompt localization across 7 languages (zh-CN, zh-TW, en, ja, ko, ru). Supports OpenAI, Anthropic, Google, Qwen, DeepSeek, Groq, Ollama, and more.
    link: /config/api-providers
    linkText: Provider list
---

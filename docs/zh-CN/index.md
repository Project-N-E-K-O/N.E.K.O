---
layout: home
title: Project N.E.K.O. 开发者文档
titleTemplate: false
description: Project N.E.K.O. 开源 AI 伙伴的开发者文档，涵盖安装部署、模型配置、长期记忆、具身 Avatar、智能体、API 与插件开发。

hero:
  name: Project N.E.K.O.
  text: 开发者文档
  tagline: 主动式全模态 AI 伙伴，具备 24/7 环境感知、智能体能力与具身情感引擎。
  image:
    src: /logo.jpg
    alt: N.E.K.O. Logo
  actions:
    - theme: brand
      text: 快速开始
      link: /zh-CN/guide/
    - theme: brand
      text: Steam 上获取
      link: https://store.steampowered.com/app/4099310/__NEKO/
    - theme: alt
      text: API 参考
      link: /zh-CN/api/
    - theme: alt
      text: 在 GitHub 上查看
      link: https://github.com/Project-N-E-K-O/N.E.K.O

features:
  - icon: 🎮
    title: Steam 创意工坊与社区
    details: 已上架 Steam，完整支持创意工坊 UGC —— 分享和发现社区创建的角色、模型、语音包与插件。
    link: https://store.steampowered.com/app/4099310/__NEKO/
    linkText: 在 Steam 上查看
  - icon: 🎙️
    title: 全模态对话
    details: 语音、文字、视觉统一在一个对话循环中。实时语音搭载 RNNoise 神经网络降噪、AGC 与 VAD，超低延迟交互。
    link: /zh-CN/architecture/
    linkText: 了解更多
  - icon: 💬
    title: 主动搭话
    details: 24/7 环境感知 —— 屏幕理解、社交媒体热点、个人动态、音乐与表情包。她会主动找你聊天。
    link: /zh-CN/guide/
    linkText: 了解更多
  - icon: 🧠
    title: 五维记忆系统
    details: 通过嵌入向量和 BM25 混合索引实现语义召回。工作记忆、近期记忆、事实记忆、反思记忆、人格记忆五维记忆，支持滑动窗口压缩和持久化用户偏好。
    link: /zh-CN/architecture/memory-system
    linkText: 工作原理
  - icon: 🤖
    title: 智能体框架
    details: 通过 MCP 工具、Computer Use、Browser Use 和 OpenFang A2A 适配器执行后台任务。自动任务规划、去重和并行执行。
    link: /zh-CN/architecture/agent-system
    linkText: 探索智能体
  - icon: 🔌
    title: 插件生态
    details: 完整插件 SDK 与市场，支持自定义扩展。装饰器 API、异步生命周期钩子和插件间通信。内置 MCP、提醒、直播弹幕、智能家居等插件。
    link: /zh-CN/plugins/
    linkText: 构建插件
  - icon: 🎭
    title: Live2D、VRM、MMD、PNGTuber、猫咪桌宠
    details: 五种具身化虚拟形象，支持情绪驱动表情、口型同步、待机动画、动作捕捉与全屏追踪。仅需 5 秒音频即可通过 MiniMax 或 CosyVoice 后端克隆任意声音。
    link: /zh-CN/frontend/
    linkText: 前端指南
  - icon: 🌐
    title: 14+ 大模型服务商与国际化
    details: OpenAI、Anthropic、Google、通义千问、DeepSeek、Groq、Ollama 等，开箱即用含免费模型。全量 UI 与 Prompt 本地化覆盖 8 种语言（简中、繁中、英、日、韩、俄、西、葡）。
    link: /zh-CN/config/api-providers
    linkText: 服务商列表
---

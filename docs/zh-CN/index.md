---
layout: home

hero:
  name: Project N.E.K.O.
  text: 开发者文档
  tagline: 构建有生命力的 AI 伙伴元宇宙 —— 开源、可扩展、多模态。
  image:
    src: /logo.jpg
    alt: N.E.K.O. Logo
  actions:
    - theme: brand
      text: 快速开始
      link: /zh-CN/guide/
    - theme: alt
      text: API 参考
      link: /zh-CN/api/
    - theme: alt
      text: 在 GitHub 上查看
      link: https://github.com/Project-N-E-K-O/N.E.K.O

features:
  - icon: "\U0001F3D7\uFE0F"
    title: 微服务架构
    details: 三服务器设计（主服务器、记忆服务器、智能体服务器），支持 WebSocket 实时通信、ZeroMQ 事件总线和 LLM 会话热切换。
    link: /zh-CN/architecture/
    linkText: 了解更多
  - icon: "\U0001F50C"
    title: 插件 SDK
    details: 使用 Python 插件扩展 N.E.K.O.。提供基于装饰器的 API、异步支持、生命周期钩子和持久化状态管理。
    link: /zh-CN/plugins/
    linkText: 构建插件
  - icon: "\U0001F310"
    title: REST 与 WebSocket API
    details: 全面的 API 接口 —— 10 个 REST 路由，覆盖角色、模型、记忆、智能体，以及用于实时语音/文字聊天的 WebSocket 流式协议。
    link: /zh-CN/api/
    linkText: API 参考
  - icon: "\U0001F9E0"
    title: 记忆系统
    details: 通过嵌入向量实现语义召回，支持时间索引历史、滑动窗口压缩近期记忆，以及持久化用户偏好。
    link: /zh-CN/architecture/memory-system
    linkText: 工作原理
  - icon: "\U0001F916"
    title: 智能体框架
    details: 通过 MCP、Computer Use 和 Browser Use 适配器执行后台任务。自动任务规划、去重和并行能力评估。
    link: /zh-CN/architecture/agent-system
    linkText: 探索智能体
  - icon: "\U0001F3A8"
    title: Live2D 与 VRM
    details: 丰富的前端体验，支持 Live2D 和 VRM 模型渲染、情绪映射、语音克隆，以及 5 种语言的国际化。
    link: /zh-CN/frontend/
    linkText: 前端指南
---

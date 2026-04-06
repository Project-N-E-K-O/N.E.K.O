---
layout: home

hero:
  name: Project N.E.K.O.
  text: 开发者文档
  tagline: 主动式全模态 AI 伙伴，具备 24/7 环境感知、智能体能力与具身情感引擎。
  image:
    src: /logo.jpg
    alt: N.E.K.O. Logo
  actions:
    - theme: brand
      text: Steam 上获取
      link: https://store.steampowered.com/app/3117010/NEKO/
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
  - icon: 🎮
    title: Steam 创意工坊与社区
    details: 已上架 Steam，完整支持创意工坊 UGC —— 分享和发现角色、Live2D 模型与插件。内置 Steam 成就、云存档与一键更新。
    link: https://store.steampowered.com/app/3117010/NEKO/
    linkText: 在 Steam 上查看
  - icon: 🎙️
    title: 全模态对话
    details: 语音、文字、视觉统一在一个对话循环中。实时语音搭载 RNNoise 神经网络降噪、AGC 和 VAD —— 13ms 延迟。开箱即用支持 14+ 大模型服务商。
    link: /zh-CN/architecture/
    linkText: 了解更多
  - icon: 💬
    title: 主动搭话
    details: 24/7 环境感知 —— 她会根据屏幕内容、热搜话题、时间段、节假日和个人兴趣主动发起对话，无需提示。
    link: /zh-CN/guide/
    linkText: 了解更多
  - icon: 🧠
    title: 三层记忆系统
    details: 通过嵌入向量和 BM25 混合索引实现语义召回。事实、反思、人设三层记忆，支持滑动窗口压缩和持久化用户偏好。
    link: /zh-CN/architecture/memory-system
    linkText: 工作原理
  - icon: 🤖
    title: 智能体框架
    details: 通过 MCP 工具、Computer Use、Browser Use 和 OpenFang A2A 适配器执行后台任务。自动任务规划、去重和并行执行。
    link: /zh-CN/architecture/agent-system
    linkText: 探索智能体
  - icon: 🔌
    title: 插件生态
    details: Python 插件 SDK v2，支持市场分发、装饰器 API、异步生命周期钩子和插件间通信。内置 MCP、提醒、B站弹幕、智能家居等插件。
    link: /zh-CN/plugins/
    linkText: 构建插件
  - icon: 🎭
    title: Live2D、VRM 与声音克隆
    details: 具身化虚拟形象，支持情绪驱动表情、口型同步与待机动画。仅需 5 秒音频即可通过 MiniMax 或 CosyVoice 后端克隆任意声音。
    link: /zh-CN/frontend/
    linkText: 前端指南
  - icon: 🌐
    title: 国际化与多服务商
    details: 全量 UI 与 Prompt 本地化覆盖 7 种语言（简中、繁中、英、日、韩、俄）。支持 OpenAI、Anthropic、Google、通义千问、DeepSeek、Groq、Ollama 等。
    link: /zh-CN/config/api-providers
    linkText: 服务商列表
---

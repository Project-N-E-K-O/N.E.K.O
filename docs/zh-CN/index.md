---
layout: home
title: Project N.E.K.O. 开发者文档
titleTemplate: false
description: Project N.E.K.O. 开源 AI 伙伴的开发者文档，涵盖安装部署、模型配置、长期记忆、具身 Avatar、智能体、API 与插件开发。

hero:
  name: Project N.E.K.O.
  text: 开发者文档
  tagline: 主动式多模态 AI 伙伴，提供可选的屏幕上下文交互、持久记忆、智能体通道与具身 Avatar。
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
    details: 已上架 Steam，创意工坊支持分享角色卡、受支持的 Avatar 资源、预览图与可选参考语音。
    link: https://store.steampowered.com/app/4099310/__NEKO/
    linkText: 在 Steam 上查看
  - icon: 🎙️
    title: 全模态对话
    details: 语音、文字、视觉统一在一个对话循环中。实时语音搭载 RNNoise 神经网络降噪、AGC 与 VAD，超低延迟交互。
    link: /zh-CN/architecture/
    linkText: 了解更多
  - icon: 💬
    title: 主动搭话
    details: 启用相应功能后，主动交互可使用屏幕上下文、受支持的信息源、音乐与表情包；隐私模式可停止主动屏幕查看。
    link: /zh-CN/guide/
    linkText: 了解更多
  - icon: 🧠
    title: 五维记忆系统
    details: 按角色维护工作上下文、近期记忆、事实、反思和 Persona 五个层次。没有向量时仍可使用 BM25，可选的本地 Embedding 能增强语义召回。
    link: /zh-CN/architecture/memory-system
    linkText: 工作原理
  - icon: 🤖
    title: 智能体框架
    details: 通过已启用且就绪的 Computer Use、Browser Use、用户插件、OpenClaw 与 OpenFang 通道执行可选后台任务；支持取消单个任务或结束全部活动任务。
    link: /zh-CN/architecture/agent-system
    linkText: 探索智能体
  - icon: 🔌
    title: 插件生态
    details: 插件 SDK 与市场支持自定义扩展，提供装饰器 API、异步生命周期钩子、插件间通信，以及启用后的智能体入口。
    link: /zh-CN/plugins/
    linkText: 构建插件
  - icon: 🎭
    title: Live2D、VRM、MMD 与 PNGTuber
    details: 四类受支持的 Avatar 格式可运行在主界面与桌宠宿主形态中，并按格式提供表情、口型、动画和交互能力。音色注册支持多种云端与本地后端，样本要求以所选服务为准。
    link: /zh-CN/frontend/
    linkText: 前端指南
  - icon: 🌐
    title: 可配置的 AI 服务商与国际化
    details: 可配置多个核心对话、辅助、语音及相关服务 Profile；可用 Provider 会随版本与地区变化，产品 UI 与 Prompt 支持 8 种语言。
    link: /zh-CN/config/api-providers
    linkText: 服务商列表
---

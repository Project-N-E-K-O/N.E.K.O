
---
layout: home

hero:
  name: Project N.E.K.O.
  text: 开发者文档
  tagline: 以当前代码为依据，说明本地伙伴运行时、记忆系统、Agent 服务、插件、浏览器 UI 与 Electron 路由。
  image:
    src: /logo.jpg
    alt: N.E.K.O. Logo
  actions:
    - theme: brand
      text: 快速开始
      link: /zh-CN/guide/
    - theme: alt
      text: 运行与部署
      link: /zh-CN/deployment/
    - theme: alt
      text: API 参考
      link: /zh-CN/api/
    - theme: alt
      text: 查看 GitHub
      link: https://github.com/Project-N-E-K-O/N.E.K.O

features:
  - icon: 🧭
    title: 先选对运行环境
    details: 源码开发在 / 提供浏览器 UI；Electron 分发则使用 /chat、/subtitle 等独立路由与窗口。
    link: /zh-CN/guide/quick-start
    linkText: 从这里开始
  - icon: 🎙️
    title: 对话与虚拟形象
    details: 按当前归属理解文字、音频、视觉、角色、Live2D、VRM、MMD、PNGTuber 与桌宠；不要复制另一套 React 聊天 UI。
    link: /zh-CN/frontend/
    linkText: 前端架构
  - icon: 🧠
    title: 持久化记忆
    details: 对话事件、投影、召回候选、证据与反思、人格、维护队列及可选本地向量检索是相互独立的层。
    link: /zh-CN/architecture/memory-system
    linkText: 记忆架构
  - icon: 🤖
    title: Agent 与插件
    details: 从实际代码路径追踪任务状态、浏览器与桌面自动化、外部 Agent 适配、插件路由、SDK、Hosted UI 与打包契约。
    link: /zh-CN/architecture/agent-system
    linkText: Agent 架构
  - icon: ▶️
    title: 从源码启动
    details: 使用 Python 3.11 和 uv，通过仓库脚本构建两个前端项目，再用 uv run python launcher.py 启动受支持的服务组。
    link: /zh-CN/guide/dev-setup
    linkText: 开发环境
  - icon: 🔌
    title: 端口与部署
    details: 源码默认主服务 48911、记忆服务 48912；Docker 的宿主机 48911/48912 则分别映射 Nginx HTTP/HTTPS，不要混用两套含义。
    link: /zh-CN/deployment/
    linkText: 选择部署方式
  - icon: 📡
    title: API 契约
    details: 查阅当前路由验证过的 REST、WebSocket、内部服务、Web 页面、运行时工具、云存档 staging 与截图桥。
    link: /zh-CN/api/
    linkText: 打开 API 参考
  - icon: 🧰
    title: 配置与贡献
    details: 使用当前配置 schema 与各入口自己的优先级，并遵循 uv、i18n、隐私、结构对偶、测试与打包门禁。
    link: /zh-CN/contributing/
    linkText: 安全参与贡献
---

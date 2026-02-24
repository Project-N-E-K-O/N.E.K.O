# 前端概述

N.E.K.O. 的前端使用**原生 JavaScript**（无框架）构建，配合 Jinja2 HTML 模板。通过 Pixi.js 渲染 Live2D 模型，通过 Three.js 渲染 VRM 模型。

## 技术栈

| 组件 | 技术 |
|------|------|
| 模板 | Jinja2（服务端渲染） |
| JavaScript | 原生 ES6+ |
| Live2D 渲染 | Pixi.js + Live2D Cubism SDK |
| VRM 渲染 | Three.js + @pixiv/three-vrm |
| 样式 | 自定义 CSS + 深色模式支持 |
| 国际化 | JSON 语言文件 + JS 运行时 |

## 文件结构

```
static/
├── app.js                    # 主应用逻辑
├── theme-manager.js          # 深色/浅色模式切换
├── css/
│   ├── index.css             # 主样式表
│   ├── dark-mode.css         # 深色模式覆盖样式
│   ├── chara_manager.css     # 角色管理器样式
│   └── ...
├── js/
│   ├── api_key_settings.js   # API 密钥设置页面
│   ├── agent_ui_v2.js        # Agent 界面
│   ├── steam_workshop_manager.js
│   └── ...
├── locales/
│   ├── en.json               # English
│   ├── zh-CN.json            # 简体中文
│   ├── zh-TW.json            # 繁体中文
│   ├── ja.json               # 日语
│   └── ko.json               # 韩语
├── live2d-ui-*.js            # Live2D UI 组件
└── vrm-ui-*.js               # VRM UI 组件
```

## 核心概念

- **页面**是服务端渲染的 HTML 模板，加载 JavaScript 模块
- **WebSocket** 用于实时音频/文本聊天（参见 [WebSocket 协议](/api/websocket/protocol)）
- **REST API** 用于所有 CRUD 操作（参见 [API 参考](/api/)）
- **主题管理器**通过 CSS 变量覆盖处理深色/浅色模式
- **国际化**在客户端通过加载对应的语言 JSON 文件实现

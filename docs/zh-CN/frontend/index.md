# 前端概述

N.E.K.O. 的前端由三层组成：传统的服务端渲染页面、React 聊天窗口组件和 Vue 插件管理面板。

## 架构

| 层级 | 技术 | 位置 |
|------|------|------|
| 主 UI 页面 | 原生 JS + Jinja2 模板 | `static/` + `templates/` |
| 聊天窗口 | React 18 + TypeScript | `frontend/react-neko-chat/` |
| 插件管理面板 | Vue 3 + Element Plus | `frontend/plugin-manager/` |
| Live2D 渲染 | Pixi.js + Live2D Cubism SDK | `static/` |
| VRM 渲染 | Three.js + @pixiv/three-vrm | `static/` |

## 传统前端（static/ + templates/）

主 UI 使用**原生 JavaScript** 和 Jinja2 HTML 模板构建。

```
static/
├── app.js                    # 主应用逻辑
├── theme-manager.js          # 深色/浅色模式切换
├── css/                      # 样式表
├── js/                       # 功能模块 JS
├── locales/                  # 国际化 JSON 文件（en, zh-CN, zh-TW, ja, ko）
├── live2d-ui-*.js            # Live2D UI 组件
├── vrm-ui-*.js               # VRM UI 组件
└── react/neko-chat/          # React 聊天窗口构建产物
```

## 聊天窗口（React）

聊天窗口以 IIFE 库的形式构建，嵌入到主页面中。

- **源码**: `frontend/react-neko-chat/`
- **构建产物**: `static/react/neko-chat/neko-chat-window.iife.js`
- **全局变量**: `window.NekoChatWindow`
- **开发服务器**: `npm run dev`（端口 5174）

胶水层 `static/app-react-chat-window.js` 负责加载 React 组件并挂载到 DOM。

## 插件管理面板（Vue）

用于管理插件、查看日志和监控指标的独立仪表板。

- **源码**: `frontend/plugin-manager/`
- **构建产物**: `frontend/plugin-manager/dist/`
- **服务路径**: 插件服务器（端口 48916）的 `/ui/`
- **开发服务器**: `npm run dev`（端口 5173，代理 API 到插件服务器）

## 核心概念

- **页面**是服务端渲染的 HTML 模板，加载 JavaScript 模块
- **WebSocket** 用于实时音频/文本聊天（参见 [WebSocket 协议](/zh-CN/api/websocket/protocol)）
- **REST API** 用于所有 CRUD 操作（参见 [API 参考](/zh-CN/api/)）
- **主题管理器**通过 CSS 变量覆盖处理深色/浅色模式
- **国际化**在客户端通过加载对应的语言 JSON 文件实现

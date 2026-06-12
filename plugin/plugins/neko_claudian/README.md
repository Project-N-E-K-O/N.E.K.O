# 🐱 猫娘 Claudian (Neko Claudian)

> 在 N.E.K.O 猫娘插件里调用 Claude Code 干活

[![Version](https://img.shields.io/badge/version-1.0.0-pink)]()
[![License](https://img.shields.io/badge/license-MIT-blue)]()

## ✨ 特性

- 🤖 **完整 Claude Code 集成** — 1:1 移植 Obsidian Claudian 插件的所有功能
- 🐱 **猫娘主题** — 清新明亮的粉蓝白渐变 + 毛玻璃效果
- 🌐 **多 Tab 支持** — 同时运行多个 Claude 会话
- 🔧 **工具渲染** — 10+ 种工具的可视化展示
- 🤝 **MCP 支持** — Model Context Protocol 服务器管理
- 📋 **计划模式** — Plan Mode 支持
- ⏪ **Rewind** — 对话回退功能
- 🌍 **10 语言 i18n** — 中文、英文、日文、韩文等
- 🐾 **猫娘 LLM 注入** — 5 个专用入口让猫娘控制 Claude

## 📦 安装

### 方式一：从插件市场安装（推荐）

1. 打开 N.E.K.O
2. 进入插件市场
3. 搜索 "猫娘 Claudian"
4. 点击安装

### 方式二：手动安装

1. 下载 `neko_claudian.neko-plugin` 文件
2. 复制到 `%LOCALAPPDATA%\N.E.K.O\plugins\`
3. 重启 N.E.K.O

## 🚀 快速开始

### 1. 启动插件

安装后，插件会自动启动。访问以下地址检查状态：

```
http://127.0.0.1:48916/plugin/neko_claudian/ui/
```

### 2. 开始对话

在输入框中输入消息，按 Enter 或点击发送按钮。

### 3. 使用斜杠命令

```
/clear    — 开始新对话
/help     — 查看帮助
/resume   — 恢复之前的对话
/fork     — 分叉对话
```

## 🏗️ 架构

```
neko_claudian/
├── __init__.py              # 插件主入口
├── plugin.toml              # 插件配置
├── core/                    # 核心模块
│   ├── runtime.py           # ChatRuntime 协议
│   ├── types/               # 类型定义
│   ├── controllers/         # 控制器
│   ├── state/               # 状态管理
│   ├── tabs/                # Tab 管理
│   ├── mcp/                 # MCP 支持
│   ├── security/            # 安全管理
│   ├── tools/               # 工具定义
│   ├── commands/            # 命令系统
│   ├── auxiliary/           # 辅助服务
│   ├── agents/              # Agent 管理
│   ├── prompts/             # 提示词模板
│   ├── storage/             # 存储服务
│   ├── settings/            # 设置管理
│   ├── utils/               # 工具函数
│   └── providers/           # Provider 实现
│       └── claude/          # Claude Provider
├── static/                  # 前端静态资源
│   ├── index.html           # 主页面
│   ├── js/                  # JavaScript
│   ├── css/                 # 样式表
│   └── i18n/                # 国际化
└── docs/                    # 文档
```

## 🐾 猫娘 LLM 注入

插件提供 5 个专用入口让猫娘 LLM 控制 Claude：

| 入口 | 说明 |
|------|------|
| `neko_inject_text` | 注入文本到输入框（不发送） |
| `neko_send_silently` | 静默发送消息给 Claude |
| `neko_click_send` | 模拟点击发送按钮 |
| `push_claude_reply` | 把 Claude 回复推给猫娘 |
| `neko_observe_stream` | 旁听 Claude 流式事件 |

### 示例

```python
# 跨插件调用
await plugin.call("neko_claudian", "neko_inject_text", {
    "text": "帮我写一个 Python 脚本"
})

# 静默发送
await plugin.call("neko_claudian", "neko_send_silently", {
    "text": "分析这段代码的性能问题"
})
```

## ⚙️ 配置

配置文件位于 `data/settings.json`：

```json
{
    "model": "claude-sonnet-4-20250514",
    "permissionMode": "normal",
    "maxTabs": 5,
    "enableAutoScroll": true,
    "enableAutoTitleGeneration": true
}
```

### 主要配置项

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `model` | claude-sonnet-4-20250514 | 使用的模型 |
| `permissionMode` | normal | 权限模式 |
| `maxTabs` | 5 | 最大 Tab 数 |
| `enableAutoScroll` | true | 自动滚动 |
| `enableAutoTitleGeneration` | true | 自动生成标题 |

## 🔧 API 接口

插件提供 REST API 和 SSE 接口：

### REST API

```
GET  /neko_claudian/api/health     — 健康检查
GET  /neko_claudian/api/status     — 状态信息
GET  /neko_claudian/api/tabs       — Tab 列表
POST /neko_claudian/api/send       — 发送消息
GET  /neko_claudian/api/settings   — 获取设置
POST /neko_claudian/api/settings   — 更新设置
```

### SSE 流

```
GET /neko_claudian/api/stream/*    — 全局事件流
GET /neko_claudian/api/stream/{tab_id} — Tab 事件流
```

## 📚 文档

- [TUTORIAL.md](TUTORIAL.md) — 30 分钟保姆级教程
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — 架构详解
- [docs/CLAUDIAN_PORT_NOTES.md](docs/CLAUDIAN_PORT_NOTES.md) — 移植笔记

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

## 📄 许可证

MIT License

## 🙏 致谢

- [Anthropic](https://anthropic.com) — Claude API
- [Claudian](https://github.com/claude-obsidian/claude-obsidian) — 原始 Obsidian 插件
- [N.E.K.O](https://github.com/neko) — 猫娘框架

---

Made with 🐱 by Neko Team

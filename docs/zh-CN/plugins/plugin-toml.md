# 插件配置 (plugin.toml)

每个插件的根目录下都有一个 `plugin.toml` 文件。本页说明所有可用字段。

## 最小示例

```toml
[plugin]
id = "my_plugin"
name = "My Plugin"
version = "0.1.0"
entry = "plugin.plugins.my_plugin:MyPlugin"

[plugin.sdk]
supported = ">=0.1.0,<0.3.0"

[plugin_runtime]
enabled = true
```

## 完整示例（真实插件）

```toml
[plugin]
id = "web_search"
name = "网络搜索"
description = "联网搜索。自动根据用户 IP 选择搜索引擎。"
short_description = "Web search via Baidu (CN) or DuckDuckGo (intl)."
keywords = ["搜索", "search", "百度", "duckduckgo", "google"]
version = "0.1.0"
entry = "plugin.plugins.web_search:WebSearchPlugin"

[plugin.author]
name = "N.E.K.O Team"

[plugin.sdk]
recommended = ">=0.1.0,<0.2.0"
supported = ">=0.1.0,<0.3.0"

[plugin.i18n]
default_locale = "zh-CN"
locales_dir = "i18n"

[plugin.store]
enabled = false

[plugin_runtime]
enabled = true
auto_start = true

# 自定义配置段 — 通过 self.config.dump() 读取
[search]
max_results = 8
timeout_seconds = 15
```

## 字段参考

### `[plugin]` — 插件身份

| 字段 | 必填 | 说明 |
|------|------|------|
| `id` | 是 | 唯一标识符。必须和文件夹名一致。 |
| `name` | 是 | 显示名称，在插件管理面板中展示。 |
| `entry` | 是 | Python 入口点：`plugin.plugins.<id>:<ClassName>` |
| `description` | 否 | 完整描述。 |
| `short_description` | 否 | 简短描述（< 300 字符），AI agent 用它来决定是否调用你的插件。 |
| `keywords` | 否 | 正则表达式列表。AI agent 用它来匹配用户意图。 |
| `version` | 否 | 语义化版本号。 |
| `type` | 否 | `"plugin"`（默认）、`"extension"` 或 `"adapter"`。 |
| `passive` | 否 | 设为 `true` 时，AI agent 不会主动分派任务给这个插件。用于监听类插件（如弹幕、QQ 自动回复）。 |

### `[plugin.author]`

| 字段 | 说明 |
|------|------|
| `name` | 作者名 |
| `email` | 作者邮箱（可选） |

### `[plugin.sdk]` — SDK 版本约束

| 字段 | 说明 |
|------|------|
| `recommended` | 推荐的 SDK 版本范围。 |
| `supported` | 最低支持范围。不满足时拒绝加载。 |
| `untested` | 允许加载但会显示警告。 |
| `conflicts` | 冲突的版本范围，拒绝加载。 |

### `[plugin_runtime]` — 运行时行为

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `enabled` | `true` | 是否允许加载。 |
| `auto_start` | `false` | N.E.K.O 启动时是否自动运行。 |

### `[plugin.i18n]` — 国际化

| 字段 | 说明 |
|------|------|
| `default_locale` | 回退语言（如 `"zh-CN"`、`"en"`）。 |
| `locales_dir` | 语言 JSON 文件目录，相对于插件根目录。 |

### `[plugin.store]` — 持久化存储

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `enabled` | `false` | 启用 `self.store` 键值持久化。 |

### `[plugin.database]` — SQLite 数据库

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `enabled` | `false` | 启用 `self.db` SQLite 访问。 |

### `[plugin_state]` — 状态持久化

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `backend` | `"off"` | `"off"`、`"memory"` 或 `"file"`。 |

### `[plugin.ui]` — Hosted UI 界面

```toml
[plugin.ui]
enabled = true

[[plugin.ui.panel]]
id = "main"
title = "My Plugin"
entry = "ui/panel.tsx"
context = "dashboard"
permissions = ["state:read", "action:call"]

[[plugin.ui.guide]]
id = "quickstart"
title = "Quickstart"
entry = "docs/quickstart.md"
permissions = ["state:read"]
```

详见 [Hosted UI](./hosted-ui)。

### 自定义配置段

框架不认识的段会被当作自定义配置。在插件中读取：

```python
cfg = await self.config.dump()
my_settings = cfg.get("search", {})
```

## 包含所有可选文件的目录结构

```
plugin/plugins/my_plugin/
├── plugin.toml          # 必须：插件配置
├── __init__.py          # 必须：插件代码
├── config.json          # 可选：额外配置
├── data/                # 可选：运行时数据 (self.data_path())
├── i18n/                # 可选：语言文件
│   ├── en.json
│   └── zh-CN.json
├── ui/                  # 可选：Hosted TSX 面板
│   └── panel.tsx
├── docs/                # 可选：Markdown/TSX 指南
│   └── quickstart.md
└── static/              # 可选：旧版 Web UI
    └── index.html
```

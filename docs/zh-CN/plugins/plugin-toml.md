# 插件配置 (plugin.toml)

每个插件的根目录下都有一个 `plugin.toml`。它告诉 N.E.K.O 你的插件是什么、怎么加载、有什么能力。

下面是一个虚构的"智能笔记"插件的完整配置。这个插件能搜索笔记、创建笔记，有自己的 UI 面板，支持中英文，还能被 AI 主动调用。

## 完整示例

```toml
[plugin]
id = "smart_notes"
name = "智能笔记"
description = "管理你的笔记：搜索、创建、整理，支持 AI 自动归类。"
short_description = "Note management with AI-powered organization."
keywords = ["笔记", "note", "记录", "备忘", "memo", "メモ"]
version = "1.2.0"
entry = "plugin.plugins.smart_notes:SmartNotesPlugin"

[plugin.author]
name = "Alice"

[plugin.sdk]
recommended = ">=0.1.0,<0.2.0"
supported = ">=0.1.0,<0.3.0"

[plugin.i18n]
default_locale = "zh-CN"
locales_dir = "i18n"

[plugin.store]
enabled = true

[plugin.ui]
enabled = true

[[plugin.ui.panel]]
id = "main"
title = "智能笔记"
entry = "ui/panel.tsx"
context = "dashboard"
permissions = ["state:read", "action:call"]

[[plugin.ui.guide]]
id = "quickstart"
title = "使用指南"
entry = "docs/guide.md"
permissions = ["state:read"]

[plugin_runtime]
enabled = true
auto_start = true

[notes]
max_per_page = 20
auto_classify = true
```

## 逐段解释

### `[plugin]` — 插件是谁

```toml
[plugin]
id = "smart_notes"
name = "智能笔记"
entry = "plugin.plugins.smart_notes:SmartNotesPlugin"
```

这三个字段是**必填**的。`id` 必须和文件夹名一致，`entry` 告诉系统去哪里找你的 Python 类。

```toml
description = "管理你的笔记：搜索、创建、整理，支持 AI 自动归类。"
short_description = "Note management with AI-powered organization."
keywords = ["笔记", "note", "记录", "备忘", "memo", "メモ"]
```

这三个字段决定了 **AI agent 能不能找到你的插件**：

- `short_description` — AI 用它来判断"这个插件能做什么"，要简洁准确
- `keywords` — AI 用它来匹配用户意图。用户说"帮我记一下"，如果你的 keywords 里有"记"，就会匹配上
- `description` — 给人看的完整描述，显示在插件管理面板

如果你的插件不需要被 AI 主动调用（比如纯监听类），可以不写这些，再加上 `passive = true`。

```toml
version = "1.2.0"
```

可选。用于版本管理和市场发布。

---

### `[plugin.author]` — 谁写的

```toml
[plugin.author]
name = "Alice"
```

可选。在插件管理面板中显示。

---

### `[plugin.sdk]` — 兼容哪个版本的 SDK

```toml
[plugin.sdk]
recommended = ">=0.1.0,<0.2.0"
supported = ">=0.1.0,<0.3.0"
```

告诉系统你的插件是为哪个 SDK 版本写的。如果用户的 N.E.K.O 版本太旧或太新，系统会警告或拒绝加载。

- `supported` — 低于这个范围就拒绝加载
- `recommended` — 在这个范围内体验最好
- `untested` — 允许但会提示"未经测试"
- `conflicts` — 明确不兼容的版本

---

### `[plugin_runtime]` — 怎么运行

```toml
[plugin_runtime]
enabled = true
auto_start = true
```

- `enabled` — 设为 `false` 可以临时禁用插件，不用删文件
- `auto_start` — 设为 `true` 时 N.E.K.O 启动就自动运行；否则需要在面板中手动启动

---

### `[plugin.i18n]` — 多语言支持

```toml
[plugin.i18n]
default_locale = "zh-CN"
locales_dir = "i18n"
```

如果你的插件需要支持多语言，在插件目录下创建 `i18n/` 文件夹，放入语言文件：

```text
i18n/
├── en.json
└── zh-CN.json
```

不需要多语言？不写这段就行。

---

### `[plugin.store]` — 持久化存储

```toml
[plugin.store]
enabled = true
```

启用后，你可以在代码中用 `self.store` 保存和读取数据（键值对形式），重启后数据还在。

不需要存数据？不写这段就行（默认关闭）。

---

### `[plugin.ui]` — 自定义界面

```toml
[plugin.ui]
enabled = true

[[plugin.ui.panel]]
id = "main"
title = "智能笔记"
entry = "ui/panel.tsx"
context = "dashboard"
permissions = ["state:read", "action:call"]

[[plugin.ui.guide]]
id = "quickstart"
title = "使用指南"
entry = "docs/guide.md"
permissions = ["state:read"]
```

如果你的插件需要在插件管理面板中显示自定义界面：

- `panel` — 交互面板（用 TSX 写，可以有按钮、表格、表单）
- `guide` — 只读文档（用 Markdown 写）

文件扩展名决定渲染方式：`.tsx` = 交互面板，`.md` = 文档。

不需要 UI？不写这段就行。详见 [Hosted UI](./hosted-ui)。

---

### `[plugin_runtime]` 之后的自定义段 — 你的业务配置

```toml
[notes]
max_per_page = 20
auto_classify = true
```

框架不认识的段会被当作你的业务配置。在代码中这样读取：

```python
cfg = await self.config.dump()
notes_cfg = cfg.get("notes", {})
max_per_page = notes_cfg.get("max_per_page", 20)
```

你可以定义任意多个自定义段，想叫什么名字都行。

---

## 这个插件的目录结构

```text
plugin/plugins/smart_notes/
├── plugin.toml              ← 就是上面这个文件
├── __init__.py              ← 插件代码
├── i18n/                    ← 语言文件（因为配了 [plugin.i18n]）
│   ├── en.json
│   └── zh-CN.json
├── ui/                      ← 交互面板（因为配了 [[plugin.ui.panel]]）
│   └── panel.tsx
├── docs/                    ← 使用指南（因为配了 [[plugin.ui.guide]]）
│   └── guide.md
└── data/                    ← 运行时数据（自动创建，self.data_path() 指向这里）
```

只有 `plugin.toml` 和 `__init__.py` 是必须的。其他目录按需创建。

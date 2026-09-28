# 插件配置 (plugin.toml)

每个插件的根目录下都有一个 `plugin.toml`。它告诉 N.E.K.O 这个包是什么、宿主应导入哪个 Python 类，以及它暴露哪些可选能力。

::: warning 两种不同的 entry
`[plugin].entry = "module.path:ClassName"` 是**宿主加载入口**，插件进程启动时用它导入一个 `NekoPluginBase` 类。`greet` 这类运行时入口 ID 来自 `@plugin_entry(id="greet")` 或 `register_dynamic_entry(...)`；插件加载成功后，Agent 才会选择这些 ID。
:::

下面是一个虚构的"智能笔记"插件的完整配置。这个插件能搜索笔记、创建笔记，有自己的 UI 面板，支持中英文，还能被 AI 主动调用。

## 完整示例

```toml
[plugin]
id = "smart_notes"
name = "智能笔记"
type = "plugin"
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
version = "1.2.0"
entry = "plugin.plugins.smart_notes:SmartNotesPlugin"
```

支持的检查与发布流程要求这四个字段**必填**。旧的源码发现路径仍可能加载部分清单不完整、目录名与 ID 不一致的插件，但这不代表它们是有效的发布包。`id` 必须符合 `^[A-Za-z0-9_-]+$` 且全局唯一。打包和生产安装要求声明 ID、归档目录、执行目标目录与 entry 包路径保持一致，也不会创建带数字后缀的可执行副本。`entry` 必须是 `module.path:ClassName`，并解析到 `NekoPluginBase` 子类；不能直接把 `PluginRouter` 当作启动类。

普通插件的 `type = "plugin"` 可省略，因为它是默认值。只有 Adapter 包才使用 `type = "adapter"`。已删除的 `extension` 类型和 `[plugin.host]` 表会被拒绝。

不同版本之间应保持 `id` 不变。升级、重新安装和降级只替换可执行代码，会保留运行时的 `config`、`data` 与 `cache`；修改 `id` 会创建另一个插件身份。可选的 `previous_ids` 只用于阻止新旧身份同时安装，不是运行时别名，也不会迁移或删除旧数据。任何替换操作都必须由用户明确确认。

```toml
description = "管理你的笔记：搜索、创建、整理，支持 AI 自动归类。"
short_description = "Note management with AI-powered organization."
keywords = ["笔记", "note", "记录", "备忘", "memo", "メモ"]
```

这些字段在宿主完成加载后参与 Agent 路由：

- `description` — 完整描述，同时用于插件元数据和 Agent 精筛。
- `short_description` — 粗筛使用的短描述；缺失时 Agent 可以根据 `description` 生成并缓存。
- `keywords` — 正则表达式模式。命中项会并入第一阶段候选集，但不会跳过第二阶段，也不保证执行。

纯监听/集成插件可设置 `passive = true`，使其完全不参与 Agent 分派。非 passive 插件还必须至少有一个 Agent 可见的运行时入口，才会成为候选。

Agent 第二阶段最终返回 `plugin_id` 和运行时 `entry_id`。两者都会严格对照本轮候选集校验；第一次不合法时只纠正重试一次，仍不合法就拒绝执行。

```toml
version = "1.2.0"
```

检查与发布流程必填，用于版本管理和市场发布。

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

告诉宿主这个包支持哪些插件 SDK 版本。值使用 Python packaging 的版本范围语法。

- `supported` — 正式支持范围
- `recommended` — 最充分测试的范围；超出时告警
- `untested` — 额外允许但会告警的范围
- `conflicts` — 明确拒绝的范围，即使同时命中其他范围也拒绝

如果声明了 `supported`，宿主版本必须落入 `supported` 或 `untested`，否则插件不加载；无效的版本范围也会被拒绝。

---

### `[plugin_runtime]` — 怎么运行

```toml
[plugin_runtime]
enabled = true
auto_start = true
priority = 0
timeout = 10
startup_failure = "warn"
```

- `enabled` — 设为 `false` 可以临时禁用插件，不用删文件
- `auto_start` — 设为 `true` 时 N.E.K.O 启动就自动运行；否则需要在面板中手动启动
- `priority` — 可选的整数运行时顺序提示
- `timeout` — 等待启动就绪的秒数，必须满足 `0 < timeout <= 300`；省略时使用系统默认值
- `startup_failure` — `startup` 钩子失败后的策略：`warn`（默认，保留进程并标记降级）、`fail`（终止启动）或 `ignore`（仅记录）

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

额外的顶层 section 会作为业务配置保留。在代码中这样读取：

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
└── docs/                    ← 使用指南（因为配了 [[plugin.ui.guide]]）
    └── guide.md
```

上面是插件源码。可写的配置、数据和缓存不放在源码目录，而是在用户数据目录中：

```text
<用户数据根目录>/plugins/smart_notes/
├── config/
│   └── plugin.toml          ← 这个用户实际使用的配置
├── data/                    ← 运行时数据，self.data_path() 指向这里
└── cache/                   ← 运行时缓存，self.cache_path() 指向这里
```

必需的是 `plugin.toml` 和 `[plugin].entry` 指向的可导入 Python 模块。模块不一定非得是 `__init__.py`，只是这种布局最常见。安装包中的代码与这些可写状态分开存放。

## 配置面板的 JSON Schema

在插件的 `plugin.toml` 同级放置可选的 `config.schema.json`，即可为通用「配置」页提供字段名称、说明和控件类型。不需要修改 `plugin.toml` 或提供自定义 UI。文件随插件源码/安装包发布；不要放进用户的运行时配置或 profile 目录。使用打包 include 白名单时，需要包含此文件。

例如，对应 `[notes]` 段的配置：

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "properties": {
    "notes": {
      "type": "object",
      "title": "笔记设置",
      "properties": {
        "max_per_page": {
          "type": "integer",
          "title": "每页笔记数量",
          "description": "每页最多显示多少条笔记。",
          "x-title-i18n": { "zh-CN": "每页笔记数量", "en": "Notes per page" },
          "x-description-i18n": { "zh-CN": "每页最多显示多少条笔记。", "en": "Maximum number of notes shown on a page." },
          "minimum": 1,
          "maximum": 100,
          "default": 20
        },
        "auto_classify": {
          "type": "boolean",
          "title": "自动分类",
          "description": "是否自动整理新建的笔记。"
        },
        "sort_order": {
          "type": "string",
          "title": "排序方式",
          "enum": ["newest", "oldest"]
        }
      }
    }
  }
}
```

支持的表单字段：

| 关键字 | 配置面板行为 |
| --- | --- |
| `properties` | 描述对象中的字段；结构与实际配置路径对应。未声明的已有字段仍可编辑。 |
| `title` / `description` | 显示名称和说明，按纯文本渲染；内部 key 保留为辅助信息。缺少标题时显示 key。 |
| `type` | 单一 `string`、`number`、`integer`、`boolean`、`object`、`array` 分别使用对应控件；缺失时根据当前值推断。 |
| `items` | 用一个子 schema 描述数组元素，支持嵌套对象和数组。 |
| `enum` | 字符串、数字、布尔值的非空列表显示为下拉框，保存时保留原类型。 |
| `minimum` / `maximum` | 数字控件的上下限；`integer` 控件只接受整数。 |
| `maxLength` | 文本输入的最大长度。 |
| `readOnly` | 禁用该字段及其子控件的编辑。 |
| `default` | 显式添加字段或数组元素时的初始值；不是运行时默认配置。 |
| `x-title-i18n` / `x-description-i18n` | 可选的 locale 到文本映射；标准 `title`、`description` 仍为字符串。 |

多语言文本复用面板的 locale 回退顺序：当前 locale、基础语言、`en-US`、`en`、映射中的首个非空值，最后回退到 `title` / `description`。示例仅展示两种语言，插件发布时应补齐自己支持的所有语言。

Schema 只提供表单展示和控件设置，**不是完整的 JSON Schema 校验器，也不是服务端权限或配置校验边界**。`required`、`pattern`、组合 schema、`$ref`、布尔 schema、类型联合及 `null` 控件不在当前表单支持范围内；不会请求 `$schema` 或 `$ref` 中的 URL。运行时合法性仍由插件校验，默认配置仍写在 `plugin.toml` / `config.example.toml` 中。

打开页面不会根据 `default` 填充配置或写入 profile。仅在 schema 中声明、但配置中尚不存在的字段也会显示，编辑后才写入；对象继续按原有规则合并，数组仍整体替换。顶层 `plugin` 段仍受保护，不参与 profile 编辑。

文件必须是 UTF-8 JSON、根节点为 `type: "object"`，大小不超过 256 KiB，`properties` / `items` 嵌套深度不超过 32 层。无文件时使用旧表单；文件损坏或所支持关键字的结构无效时，接口返回警告，页面提示并回退到通用编辑器。配置查询接口的 `config_schema` 字段单独返回该元数据，不会混入 `config` 或 profile。

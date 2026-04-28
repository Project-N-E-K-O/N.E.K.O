# 使用 Hosted TSX 构建插件界面

Hosted UI 让插件在插件管理器中渲染交互式面板或教程页。插件用 Python 提供状态和动作，用在线编译的 TSX 或简单 Markdown 描述界面。

当插件需要配置面板、工具/服务器管理界面、按钮动作、多语言文本或只读教程时，优先考虑 Hosted UI。

## 什么时候使用哪种 UI

| 需求 | 推荐模式 |
|------|----------|
| 交互式配置面板 | Hosted TSX |
| 工具/服务器管理界面 | Hosted TSX |
| 只读教程或说明文档 | Markdown |
| 完全自定义旧版页面 | Static UI |

新插件的交互式 UI 推荐使用 Hosted TSX。Static UI 仍作为兼容路径保留。

## 文件结构

```text
plugin/plugins/my_plugin/
  plugin.toml
  __init__.py
  ui/panel.tsx
  docs/quickstart.md
  i18n/en.json
  i18n/zh-CN.json
```

## 在 `plugin.toml` 声明界面

```toml
[plugin]
id = "my_plugin"
name = "My Plugin"
description = "A plugin with a hosted UI"
version = "0.1.0"
entry = "plugin.plugins.my_plugin:MyPlugin"

[plugin.i18n]
default_locale = "en"
locales_dir = "i18n"

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

### Surface 字段

| 字段 | 含义 |
|------|------|
| `panel` / `guide` / `docs` | 界面在插件管理器中的位置 |
| `id` | surface 标识，同一类型内唯一 |
| `title` | 显示标题 |
| `entry` | 相对插件目录的文件路径 |
| `context` | Python 侧 `@ui.context(id=...)` 的上下文 ID |
| `permissions` | surface 能力，例如 `state:read`、`action:call` |

模式会根据 `entry` 后缀自动推断：

| 后缀 | 模式 |
|------|------|
| `.tsx`, `.jsx` | `hosted-tsx` |
| `.md`, `.mdx` | `markdown` |
| `.html`, `.htm` | `static` |

## Python 侧提供状态和动作

```python
from plugin.sdk.plugin import (
    NekoPluginBase,
    neko_plugin,
    plugin_entry,
    ui,
    tr,
    Ok,
)


@neko_plugin
class MyPlugin(NekoPluginBase):
    @ui.context(id="dashboard")
    async def dashboard(self):
        return {
            "items": [
                {"id": "demo", "status": "ready"},
            ],
        }

    @ui.action(
        label=tr("actions.refresh.label", default="Refresh"),
        tone="primary",
        refresh_context=True,
    )
    @plugin_entry(
        id="refresh_item",
        name=tr("entries.refresh.name", default="Refresh Item"),
        description=tr("entries.refresh.description", default="Refresh an item."),
        input_schema={
            "type": "object",
            "properties": {
                "item_id": {
                    "type": "string",
                    "description": tr("fields.itemId", default="Item ID"),
                },
            },
            "required": ["item_id"],
        },
        llm_result_fields=["message"],
    )
    async def refresh_item(self, item_id: str, **_):
        return Ok({"message": f"Refreshed {item_id}"})
```

要点：

- `@ui.context(id="dashboard")` 的返回值会进入 TSX 的 `props.state`。
- `@ui.action(...)` 会把某个 entry 暴露给当前 surface。
- `@plugin_entry(...)` 仍然是后端可调用入口，也是 LLM 可见工具元数据。
- `tr(...)` 声明插件本地 i18n key，并提供英文默认值。
- `refresh_context=True` 表示动作成功后自动刷新上下文。

## 编写 TSX 面板

```tsx
import {
  Page,
  Card,
  Stack,
  Text,
  DataTable,
  ActionButton,
} from "@neko/plugin-ui"
import type { HostedAction, PluginSurfaceProps } from "@neko/plugin-ui"

type Item = {
  id: string
  status: string
}

type State = {
  items?: Item[]
}

export default function Panel(props: PluginSurfaceProps<State>) {
  const { t, state, actions } = props
  const refresh = actions.find((action) => action.id === "refresh_item") as HostedAction | undefined

  return (
    <Page title={props.plugin.name} subtitle={t("panel.subtitle")}>
      <Card title={t("panel.items")}>
        <Stack>
          <DataTable
            data={state.items || []}
            rowKey="id"
            columns={[
              { key: "id", label: t("fields.itemId") },
              { key: "status", label: t("fields.status") },
            ]}
          />

          {refresh ? (
            <ActionButton action={refresh} values={{ item_id: "demo" }}>
              {t("actions.refresh.label")}
            </ActionButton>
          ) : (
            <Text>{t("panel.noActions")}</Text>
          )}
        </Stack>
      </Card>
    </Page>
  )
}
```

Hosted TSX 会在线编译。TSX 文件不要从 npm 包导入依赖，只从 `@neko/plugin-ui` 导入组件和类型。

## 添加插件 i18n 文件

`i18n/en.json`：

```json
{
  "panel.subtitle": "Manage plugin items.",
  "panel.items": "Items",
  "panel.noActions": "No actions exposed.",
  "actions.refresh.label": "Refresh",
  "entries.refresh.name": "Refresh Item",
  "entries.refresh.description": "Refresh an item.",
  "fields.itemId": "Item ID",
  "fields.status": "Status"
}
```

`i18n/zh-CN.json`：

```json
{
  "panel.subtitle": "管理插件项目。",
  "panel.items": "项目",
  "panel.noActions": "没有暴露可用动作。",
  "actions.refresh.label": "刷新",
  "entries.refresh.name": "刷新项目",
  "entries.refresh.description": "刷新一个项目。",
  "fields.itemId": "项目 ID",
  "fields.status": "状态"
}
```

Python 和 TSX 共用同一套 key：

```python
tr("actions.refresh.label", default="Refresh")
self.i18n.t("messages.done", default="Done")
```

```tsx
props.t("panel.subtitle")
props.t("item.count", { count: 3 })
```

fallback 顺序：

1. 当前 locale
2. 基础 locale，例如 `zh-CN` 的 `zh`
3. 插件 `default_locale`
4. `default` 参数或 key 名

只有中文 locale 会回退到 `zh-CN`。非中文 locale 不会默认漏出中文文本。

## Markdown 教程页

只读文档可以使用 Markdown：

```toml
[[plugin.ui.guide]]
id = "quickstart"
title = "Quickstart"
entry = "docs/quickstart.md"
permissions = ["state:read"]
```

支持：

- 标题
- 段落
- 无序列表
- 引用
- fenced code block
- inline code
- `http` / `https` 链接

不支持：

- inline HTML
- 脚本
- MDX 组件

## `PluginSurfaceProps`

| Prop | 类型 | 说明 |
|------|------|------|
| `plugin` | `Record<string, any>` | 插件元数据 |
| `surface` | `Record<string, any>` | 当前 surface 元数据 |
| `state` | 泛型 `State` | Python context 返回的状态 |
| `stateSchema` | `JsonSchema \| null` | 可选状态 schema |
| `actions` | `HostedAction[]` | `@ui.action` 暴露的动作 |
| `entries` | `Record<string, any>[]` | 插件入口列表 |
| `config` | `{ schema, value, readonly }` | 插件配置快照 |
| `warnings` | `Array<{ path, code, message }>` | UI 声明告警 |
| `locale` | `string` | 当前 UI locale |
| `t` | `(key, params?) => string` | 插件本地翻译函数 |
| `api` | `HostedApi` | action/refresh bridge |
| `useLocalState` | hook | iframe 内本地状态，刷新 context 后仍保留 |

## `HostedApi`

```ts
type HostedApi = {
  call(actionId: string, args?: Record<string, any>): Promise<any>
  refresh(): Promise<any>
}
```

- `api.call()` 调用当前 surface 暴露的插件 entry。
- `api.refresh()` 重新拉取 context 并重新渲染。
- 如果 action 设置了 `refresh_context=false`，则不会自动刷新。

## UI Kit 快速参考

### 布局

| 组件 | 用途 |
|------|------|
| `Page` | 页面外壳 |
| `Card` | 卡片区块 |
| `Section` | 通用区块 |
| `Heading` | 标题 |
| `Stack` | 垂直布局 |
| `Grid` | 网格布局 |
| `Text` | 段落文本 |
| `Divider` | 分隔线 |

### 数据展示

| 组件 | 用途 |
|------|------|
| `StatusBadge` | 状态标签 |
| `StatCard` | 指标卡片 |
| `KeyValue` | 键值行 |
| `DataTable` | 表格 |
| `List` | 列表 |
| `JsonView` | JSON 预览 |
| `CodeBlock` | 代码块 |

### 表单和动作

| 组件 | 用途 |
|------|------|
| `Field` | label/help/error 包装 |
| `Input` | 单行输入 |
| `Textarea` | 多行输入 |
| `Select` | 下拉选择 |
| `Switch` | checkbox 开关 |
| `Form` | 表单包装 |
| `ActionForm` | 基于 schema 的 action 表单 |
| `ActionButton` | 调用 action 的按钮 |
| `RefreshButton` | 调用 `api.refresh()` 的按钮 |

### 反馈和弹层

| 组件 | 用途 |
|------|------|
| `Alert` | 行内消息 |
| `InlineError` | 错误块 |
| `EmptyState` | 空状态 |
| `Modal` | 弹窗 |
| `ConfirmDialog` | 确认弹窗 |
| `AsyncBlock` | 异步 loading/error/data 块 |
| `Tip` | 提示 |
| `Warning` | 警告 |

## Hooks 快速参考

| Hook | 用途 |
|------|------|
| `useLocalState` | surface 本地状态，context refresh 后保留 |
| `useAsync` | 异步数据，带 loading/error/reload |
| `useForm` | 表单状态辅助 |
| `useToast` | toast 通知 |
| `useConfirm` | Promise 风格确认框 |
| `useDebounce` | 防抖派生值 |
| `useDebouncedState` | state + 防抖 state |
| `useI18n` | 翻译函数和当前 locale |
| `useState`, `useEffect`, `useMemo`, `useCallback`, `useRef`, `useReducer` | 基础 runtime hooks |

示例：

```tsx
const tools = useAsync(() => props.api.call("list_tools"), [])

if (tools.loading) return <Text>Loading...</Text>
if (tools.error) return <InlineError error={tools.error} />

return <DataTable data={tools.data?.tools || []} />
```

## Runtime 能力边界

Hosted TSX 不是完整 React。它有意提供一个较小的运行时。

支持：

- function component
- Fragment
- keyed children
- controlled input/select/textarea/checkbox
- 上面列出的 hooks
- 插件本地 i18n
- action bridge

不支持：

- class component
- React Context
- portal API
- Suspense / concurrent rendering
- server component
- 从插件 TSX 里导入 npm 包
- `dangerouslySetInnerHTML`

`useLayoutEffect` 当前等同于 `useEffect`，不要依赖 React 的 pre-paint layout timing 语义。

## 测试

运行完整 hosted UI 检查：

```bash
scripts/check-hosted-ui.sh
```

常用单项：

```bash
cd frontend/plugin-manager
npm run check-hosted-tsx -- plugin/plugins/my_plugin
npm run test:hosted
npm run test:hosted:e2e
```

`check-hosted-tsx` 检查 TSX 语法和类型。hosted 测试覆盖 runtime、iframe 执行、i18n 覆盖和 MCP Adapter 面板 fixture。

## 完整示例

参考 MCP Adapter：

```text
plugin/plugins/mcp_adapter/
  __init__.py
  plugin.toml
  ui/panel.tsx
  docs/quickstart.tsx
  i18n/en.json
  i18n/zh-CN.json
```

它展示了：

- Python context 状态
- 暴露 actions
- 表格和表单 UI
- 批量 JSON 导入
- toast 和 confirm dialog
- 插件本地 i18n
- hosted TSX 测试

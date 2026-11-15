# hosted UI：宿主这一侧

插件作者怎么写 surface，看 `.agent/skills/neko-plugin/references/hosted-ui-authoring.md` 和 `hosted-ui-api.md`。这里讲改**打包器、桥、UI context** 时要知道的事。

## 1. 打包器只服务一份很窄的白名单

hosted TSX surface 跑在沙箱 iframe 的 `srcdoc` 里，自包含，没有模块加载器。surface 一旦 `import './sibling'`，框架就得提前把兄弟模块拼成一坨注入进去。

这件事有两处实现，**契约必须一致**：

| 位置 | 用途 | 做法 |
|---|---|---|
| `frontend/plugin-manager/src/components/plugin/hosted/hostedTsxModule.mjs` | 浏览器运行时（`tsxRuntime.ts` 调 `bundleHostedTsxSource`）和检查脚本（`scripts/check-hosted-tsx.mjs` 调 `findHostedRelativeImportSpecifiers`、`assertHostedExportContract`）共用 | 轻量的字符串扫描器。纯 ESM，配一份手写的 `.d.mts`，不依赖 Node 或 DOM |
| `plugin/server/application/plugins/ui_query_service.py` 里的 `_hosted_*` 函数 | 后端 `/plugin/{id}/hosted-ui/source` 端点走依赖图 | 手写的 Python 扫描器 |

支持的导出形态只有这些：`export const NAME =`（`export let` / `export var` 前后端的校验器都拒绝，要求改成 `const`）、`export function` / `export async function NAME`、`export class NAME`、只含类型的声明、入口文件的 `export default`。明确拒绝：re-export、`export { … }` 列表、`export *`、enum、generator、abstract class、解构导出、一条语句多个声明、`export namespace`、动态 `import()`、相对路径之外的裸模块（`@neko/plugin-ui` 除外）。

为什么这么窄：

- 浏览器里装不下整个 TypeScript 编译器，所以运行时只能用轻量扫描器。扫描器一旦要支持任意导出语法，就得正确处理注释、字符串、JSX、正则字面量、解构、多声明符，每个 TS 语法特性都是一个新的边界情况，没有底（#1775）。
- 实际的插件 surface 只用到很窄的形态。re-export 没有任何使用者，所以整套删掉了。
- 分工是：**检查脚本是严格门禁，运行时只服务白名单**。门禁保证发出去的代码符合契约，运行时才可以假定输入简单。运行时处理导出的方式是「剥掉 `export ` 前缀，记下声明的名字」，不解析初始化表达式。
- 检查脚本向运行时看齐，而不是反过来。检查脚本里 TypeScript 编译器只用来做类型诊断，以及用语法树判断动态 `import()`（字符串扫描器会被 JSX 文本和模板表达式骗到）。

⚠️ **契约校验不能只放在检查脚本里。** 安装到用户机器上的第三方插件走后端的 `/hosted-ui/source` 端点，根本不跑 `check-hosted-tsx`。所以后端那份扫描器必须自己守同一份契约，并报出对应的错误码（`PLUGIN_UI_EXPORT_UNSUPPORTED`、`PLUGIN_UI_BARE_IMPORT_UNSUPPORTED`、`PLUGIN_UI_DYNAMIC_IMPORT_UNSUPPORTED` 等）。前端补了一个洞，要问一句后端是不是也有。

两边已经对齐、改的时候别弄散的几个判断：

- 只含类型的导入要擦掉、不打包：`import type …`，以及绑定全是内联 `type` 的 `import { type Foo }`。
- 空的 `import {} from './x'` 和裸的 `import './x'` 不是只含类型，要进依赖图，为的是执行它的副作用。
- `import { type as kind }` 导入的是一个名字叫 `type` 的**值**。
- 依赖图的上限：32 个文件、512 KiB（`_HOSTED_TSX_DEPENDENCIES_MAX_FILES` / `_MAX_BYTES`）。

已经定性、**不逐条补扫描器**的尾巴：同一行以 `}` 结尾后紧跟的 export、同一行里的 `export default`、把 `from` 当名字的导入、逗号在行首的多声明符、ambient 导出。它们都在白名单之外，只有压缩过或生成的代码才会出现。评审里再报这类，按「白名单外，不支持」回复，不要给扫描器加分支。

改完要跑的（除了第一条，CI 都不跑）：

```bash
cd frontend/plugin-manager
npm run test:unit          # CI 会跑，含 hosted 目录下的 vitest
npm run test:hosted-script # 检查脚本自己的 node:test
npm run check-hosted-tsx -- plugin/plugins/<带 TSX surface 的插件>   # 路径相对仓库根，脚本自己解析
cd ../..                   # 回到仓库根再跑 Python 测试
uv run pytest plugin/tests/unit/server/test_plugin_ui_query_service.py -q
```

## 2. surface 调入口：只能走桥，只能调 UI action

surface 的 iframe 是 `sandbox="allow-scripts"`，没有 `allow-same-origin`，来源是不透明的。

- **直接 `fetch('/runs')` 在浏览器里一定失败。** 请求带的是 `Origin: null`，而插件服务的 CORS 只放行配置的来源和 localhost，并且带凭据（`plugin/server/http_app.py`）。桌面端窗口里能不能通，取决于壳的窗口安全设置，不要依赖它。仓库里 `plugin/plugins/game_agent_minecraft/surfaces/quickstart.tsx` 的注释记了这件事。
- **支持的路径是 `props.api.call(actionId, args)`。** 它经 `postMessage` 交给宿主页面，再打到 `POST /plugin/{id}/hosted-ui/action/{action_id}`。
- 桥的后端（`ui_query_service.call_surface_action`）有授权门 `_resolve_authorized_action_entry_id`：只放行出现在这个 surface 的 UI context `actions` 列表里的 action。而 `actions` 列表由 `host._collect_ui_actions()` 生成，它**只收带 `@ui.action` 元数据的入口**。普通的 `@plugin_entry` 不在里面，调它得到 403 `PLUGIN_UI_ACTION_FORBIDDEN`。

所以碰到「surface 想调一个普通入口」：

- 做法是给那个入口加 `@ui.action`，并在清单里给 surface `action:call` 权限。
- 不要为了绕过授权门，在 surface 里改回直接 `fetch`。那等于把这个面板做成只在某一种运行环境里能用。
- 「给沙箱来源放行 CORS」或「把所有入口都自动暴露成 UI action」是平台级的改动，不是在单个 surface 或单个插件的 PR 里顺手做的。

## 3. UI context 的 actions 和 provider 是解耦的

`plugin/core/host.py` 的 `_handle_ui_context`：

- `actions` 只从 `@ui.action` 装饰器的元数据算出来，不执行插件写的任何代码。
- 插件自己写的 `@ui.context` provider 缺失、抛错或超时，都**不能**把 `actions` 一起弄丢。这时返回 `actions` 加一个 `context_error` 字段，渲染路径把它变成一条 surface 警告。

为什么：`actions` 同时是 `api.call` 的授权白名单。以前两者绑在一起，provider 一出问题，面板上每个按钮都 500 `PLUGIN_UI_CONTEXT_QUERY_FAILED`，和真正的原因毫无关系。回归测试在 `plugin/tests/unit/core/test_host_ui_context_actions_decoupled.py`。

聊天卡片的按钮用的 context id 是 `__chat_card__`，它只要 action 列表，不调面板的 state provider。

## 4. 宿主往 handler 里注入的参数

宿主触发入口时会往参数里放一个 `_ctx`。`plugin/sdk/shared/core/entry_runtime.py` 的 `prepare_entry_kwargs` 负责在 handler 的签名接不住它时把它丢掉，包括没有声明 params 模型的入口、零参数入口、只写了 `input_schema` 的入口。

规则：**以后宿主再往 handler 里注入别的保留参数，也要在这里按签名过滤。** 不能指望插件作者都写了 `**kwargs`。文档里教的最简单写法 `async def greet(self, name: str)` 就没有 `**kwargs`，以前点执行直接 `TypeError`。

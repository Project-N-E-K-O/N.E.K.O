# N.E.K.O 前端

React 19 + Vite 7 的单页应用，使用 npm workspaces 管理组件库、通用工具与请求库。构建产物统一输出到仓库根的 `static/bundles`，供服务端模板直接引用。

## 技术栈

- **框架**: React 19.1.1
- **构建工具**: Vite 7.1.7
- **语言**: TypeScript 5.9.2
- **HTTP 客户端**: Axios 1.13.2
- **测试框架**: Vitest 2.1.3（含 coverage-v8）
- **包管理**: npm workspaces

## 项目结构

```
frontend/
├── src/
│   ├── web/              # SPA 应用入口
│   │   ├── main.tsx      # React 挂载点
│   │   ├── App.tsx       # 主应用组件
│   │   └── styles.css    # 全局样式
│   └── types/            # TypeScript 类型定义
│       └── shims.d.ts
├── packages/             # npm workspaces 子包
│   ├── components/       # UI 组件库
│   │   ├── src/
│   │   │   ├── Button.tsx
│   │   │   ├── Button.css
│   │   │   ├── StatusToast.tsx
│   │   │   ├── StatusToast.css
│   │   │   └── Modal/            # 模态框组件
│   │   │       ├── BaseModal.tsx
│   │   │       ├── AlertDialog.tsx
│   │   │       ├── ConfirmDialog.tsx
│   │   │       ├── PromptDialog.tsx
│   │   │       ├── Modal.css
│   │   │       └── index.tsx
│   │   ├── index.ts      # 组件导出入口
│   │   └── vite.config.ts
│   ├── request/          # HTTP 请求库（Axios 封装）
│   │   ├── __tests__/    # 单元测试
│   │   │   ├── requestClient.test.ts
│   │   │   ├── entrypoints.test.ts
│   │   │   └── nativeStorage.test.ts
│   │   ├── coverage/     # 测试覆盖率报告
│   │   ├── createClient.ts
│   │   ├── index.ts      # 通用入口
│   │   ├── index.web.ts  # Web 端入口（默认实例）
│   │   ├── index.native.ts # React Native 入口
│   │   └── src/
│   │       ├── request-client/  # 请求客户端核心
│   │       │   ├── requestQueue.ts  # 请求队列管理
│   │       │   ├── tokenStorage.ts  # Token 存储实现
│   │       │   └── types.ts         # 类型定义
│   │       └── storage/          # 存储抽象层
│   │           ├── index.ts          # 统一入口
│   │           ├── index.web.ts      # Web 入口
│   │           ├── index.native.ts   # Native 入口
│   │           ├── webStorage.ts     # localStorage 封装
│   │           ├── nativeStorage.ts  # AsyncStorage 封装
│   │           └── types.ts          # Storage 接口定义
│   ├── web-bridge/       # 桥接层（将组件与请求能力暴露到 window）
│   │   ├── src/
│   │   │   ├── index.ts
│   │   │   └── global.ts
│   │   └── vite.config.ts
│   └── common/           # 公共工具与类型
│       ├── index.ts
│       └── vite.config.ts
├── scripts/              # 构建辅助脚本
│   ├── clean-bundles.js  # 清空构建输出目录
│   └── copy-react-umd.js # 复制 React UMD 文件
├── vendor/               # 第三方库源文件
│   └── react/            # React/ReactDOM UMD 文件
├── index.html            # 开发环境 HTML 模板
├── vite.web.config.ts    # Web 应用 Vite 配置
├── tsconfig.json         # TypeScript 配置
└── package.json          # 工作区根配置
```

## 目录说明

- **`src/web/`**: SPA 应用入口，包含 `main.tsx`（React 挂载）和 `App.tsx`（主组件逻辑）
- **`packages/components/`**: UI 组件库，产出 ES/UMD 双格式，支持外部化 React/ReactDOM，包含 Button、StatusToast、Modal 等组件
- **`packages/request/`**: Axios 封装库，提供请求队列、Token 自动刷新等功能，支持 Web/React Native 双平台
- **`packages/request/__tests__/`**: 请求库单元测试，使用 Vitest 编写
- **`packages/web-bridge/`**: 桥接层，将组件和请求能力暴露到 `window` 对象，供非 React 代码使用
- **`packages/common/`**: 公共类型定义（如 `ApiResponse<T>`）和工具函数
- **`scripts/`**: 构建辅助脚本，用于清理输出目录和复制 React UMD 文件
- **`vendor/react/`**: React/ReactDOM 生产环境 UMD 源文件，构建时复制到 `static/bundles`
- **`static/bundles/`**: 构建输出目录（位于仓库根目录，由脚本自动创建/清理），存放组件库、请求库、web-bridge 等构建产物
- **`dist/webapp/`**: Web 应用构建输出目录，存放 `react_web.js`（SPA 入口）

## 环境要求

- **Node.js**: 推荐 22.x 或 20.x LTS；18.x 作为最低兼容基线，不建议再低
- **npm**: 推荐 11.x；10.x 作为最低兼容基线

## 环境变量示例

- 已提供示例文件：`frontend/.env.example`
- 使用方式：复制为 `.env`（或针对环境创建 `.env.local` 等），再按需修改

内容字段说明：
```
# API 服务器基础地址
VITE_API_BASE_URL=http://localhost:48911

# 静态资源服务器根路径（提供 /static），不填则回退到 VITE_API_BASE_URL
VITE_STATIC_SERVER_URL=http://localhost:48911

# WebSocket 基础地址，不填则回退到 VITE_API_BASE_URL
VITE_WEBSOCKET_URL=ws://localhost:48911
```

**注意**：请求日志的启用/禁用由构建模式自动控制：
- 开发构建 (`npm run build:dev`)：自动启用请求日志
- 生产构建 (`npm run build:prod`)：自动禁用请求日志

## 安装

```bash
cd frontend
npm install
```

### 命令行约定

- **macOS/Linux（bash/zsh）或 Windows cmd**: 使用 `cd frontend && npm run ...`
- **Windows PowerShell**: 使用 `cd frontend; npm run ...`（分号分隔）
- 若已进入 `frontend` 目录，可直接 `npm run ...`

## 开发

### 开发命令

以下命令默认在仓库根目录执行（按上方所述区分 shell）：

- **Web 应用开发**: `cd frontend && npm run dev:web`（PowerShell: `cd frontend; npm run dev:web`）
  - 启动 Vite 开发服务器，支持 HMR
  - 默认访问地址: `http://localhost:5173`
- **Common 包调试**: `cd frontend && npm run dev:common`（PowerShell: `cd frontend; npm run dev:common`）
  - 用于调试 `packages/common` 包

### 路径别名

项目配置了以下路径别名，可在代码中直接使用：

- `@project_neko/components` → `packages/components/index.ts`
- `@project_neko/common` → `packages/common/index.ts`
- `@project_neko/request` → `packages/request/index.ts`

这些别名在 `tsconfig.json` 和 `vite.web.config.ts` 中均有配置。

### 开发示例

示例页面（`src/web/App.tsx`）展示了以下功能：

1. **API 请求示例**：调用 `/api/config/page_config` 并打印返回结果
2. **StatusToast 使用**：通过 ref 调用 `show()` 方法显示提示消息
3. **Modal 使用**：展示 `AlertDialog`、`ConfirmDialog`、`PromptDialog` 的使用方式

示例代码读取可配置的 API / 静态资源基址（默认回退到 48911）：

```9:18:frontend/src/web/App.tsx
const API_BASE = trimTrailingSlash(
  (import.meta as any).env?.VITE_API_BASE_URL ||
    (typeof window !== "undefined" ? (window as any).API_BASE_URL : "") ||
    "http://localhost:48911"
);
const STATIC_BASE = trimTrailingSlash(
  (import.meta as any).env?.VITE_STATIC_SERVER_URL ||
    (typeof window !== "undefined" ? (window as any).STATIC_SERVER_URL : "") ||
    API_BASE
);
```

> StatusToast 会自动解析静态基址：`staticBaseUrl` prop（若传）→ `window.STATIC_SERVER_URL` → `window.API_BASE_URL` → `VITE_STATIC_SERVER_URL` → `VITE_API_BASE_URL` → 默认 `http://localhost:48911`。示例中通过 `staticBaseUrl={STATIC_BASE}` 显式传入。

### 请求客户端使用

在 `App.tsx` 中创建请求客户端实例（同样使用可配置基址，默认回退 48911）：

```21:29:frontend/src/web/App.tsx
const request = createRequestClient({
  baseURL: API_BASE,
  storage: new WebTokenStorage(),
  refreshApi: async () => {
    // 示例中不做刷新，实际可按需实现
    throw new Error("refreshApi not implemented");
  },
  returnDataOnly: true
});
```

也可以直接使用 `packages/request/index.web.ts` 导出的默认实例（已配置 Token 刷新）。

### 组件使用示例

**StatusToast**：
```typescript
const toastRef = useRef<StatusToastHandle | null>(null);
// 显示提示
toastRef.current?.show("接口调用成功", 2500);
```

**Modal**：
```typescript
const modalRef = useRef<ModalHandle | null>(null);
// Alert
await modalRef.current?.alert("这是一条 Alert 弹窗", "提示");
// Confirm
const ok = await modalRef.current?.confirm("确认要执行该操作吗？", "确认");
// Prompt
const name = await modalRef.current?.prompt("请输入昵称：", "Neko");
```

## 测试

### 统一运行

- `cd frontend && npm run test`  
  - 使用 Vitest `--pool=threads` 跑全部工作区用例（目前有少量 Modal 长耗时用例被标记 skip，避免 CI 超时）。

### 按包运行

- 请求库：`cd frontend && npm run test -w @project_neko/request`
- 组件库与通用工具：`cd frontend && npx vitest run --pool=threads packages/common/__tests__/index.test.ts packages/components/__tests__`

### 覆盖率

- 全量覆盖率：`cd frontend && npm run test:coverage`（同样使用 threads 池）
- 单包覆盖率（示例，请求库）：`cd frontend && npm run test -- -w @project_neko/request --coverage --pool=threads`
- 覆盖率报告输出到对应包的 `coverage/` 目录（如 `packages/request/coverage/index.html` 可用浏览器打开）。

### 测试文件说明

| 文件 | 描述 |
|------|------|
| `requestClient.test.ts` | 请求客户端核心功能测试（Token 刷新、请求队列、拦截器等） |
| `entrypoints.test.ts` | 入口文件导出测试（index.ts、index.web.ts、index.native.ts） |
| `nativeStorage.test.ts` | React Native 存储抽象测试 |

## 构建

### 构建模式

项目支持两种构建模式：

- **开发构建** (`build:dev`): 用于本地开发和调试
  - 启用请求日志 (`VITE_REQUEST_LOG_ENABLED=true`)
  - 生成 sourcemap 便于调试
  - 不压缩代码，便于阅读和调试
  
- **生产构建** (`build:prod`): 用于生产环境部署
  - 禁用请求日志 (`VITE_REQUEST_LOG_ENABLED=false`)
  - 不生成 sourcemap（减小体积）
  - 压缩代码（esbuild minify）

### 完整构建

**生产构建**（默认）：
```bash
cd frontend && npm run build
# 或
cd frontend && npm run build:prod
```

**开发构建**：
```bash
cd frontend && npm run build:dev
```

（PowerShell: `cd frontend; npm run build`）

构建流程依次执行：

1. **`clean:bundles`**: 清空仓库根的 `static/bundles` 目录
2. **`build:request`**: 构建请求库，产出 ES/UMD 双格式
3. **`build:common`**: 构建通用工具包，产出 ES/UMD 双格式
4. **`build:components`**: 构建组件库，产出 ES/UMD 双格式，外部化 `react`/`react-dom`，生成 `components.css`
5. **`build:web-bridge`**: 构建桥接层，产出 ES/UMD 双格式，将组件和请求能力暴露到 `window`
6. **`build:web`**: 构建 Web 应用入口，生成 `react_web.js`（ES 模块），输出到 `dist/webapp`
7. **`copy:react-umd`**: 复制 `vendor/react/*.js` 到 `static/bundles`

### 单独构建

可以单独构建某个包或入口：

**生产构建**：
```bash
# 构建组件库
cd frontend && npm run build:components:prod

# 构建请求库
cd frontend && npm run build:request:prod

# 构建通用工具
cd frontend && npm run build:common:prod

# 构建桥接层
cd frontend && npm run build:web-bridge:prod

# 构建 Web 应用
cd frontend && npm run build:web:prod
```

**开发构建**：
```bash
# 构建组件库
cd frontend && npm run build:components:dev

# 构建请求库
cd frontend && npm run build:request:dev

# 构建通用工具
cd frontend && npm run build:common:dev

# 构建桥接层
cd frontend && npm run build:web-bridge:dev

# 构建 Web 应用
cd frontend && npm run build:web:dev
```

**注意**：不带 `:dev` 或 `:prod` 后缀的命令默认使用生产模式。

### 构建产物

主要产物位于以下目录：

**`static/bundles/`**（仓库根目录）：
- **`components.js`** / **`components.es.js`**: 组件库（UMD/ES 格式）
- **`components.css`**: 组件库样式文件
- **`common.js`** / **`common.es.js`**: 通用工具（UMD/ES 格式）
- **`request.js`** / **`request.es.js`**: 请求库（UMD/ES 格式）
- **`web-bridge.js`** / **`web-bridge.es.js`**: 桥接层（UMD/ES 格式）
- **`react.production.min.js`**: React 生产环境 UMD（由脚本复制）
- **`react-dom.production.min.js`**: ReactDOM 生产环境 UMD（由脚本复制）

**`dist/webapp/`**（frontend 目录下）：
- **`react_web.js`**: SPA 入口（ESM 格式）
- **`frontend.css`**: Web 应用样式文件

所有构建产物均包含 source map 文件（`.map`）。

## 服务端集成

### HTML 模板引用

在服务端模板中按以下顺序引用构建产物：

**方式一：使用 SPA 应用（推荐）**

```html
<!-- 1. 引入 React/ReactDOM UMD（组件库依赖） -->
<script src="/static/bundles/react.production.min.js"></script>
<script src="/static/bundles/react-dom.production.min.js"></script>

<!-- 2. 引入组件库样式 -->
<link rel="stylesheet" href="/static/bundles/components.css" />

<!-- 3. 引入组件库 UMD（依赖全局 React/ReactDOM） -->
<script src="/static/bundles/components.js"></script>

<!-- 4. 引入 SPA 入口（ES 模块） -->
<!-- 注意：react_web.js 构建在 dist/webapp/，需要复制到 static/bundles/ 或配置静态服务指向 dist/webapp -->
<script type="module" src="/static/bundles/react_web.js"></script>
<link rel="stylesheet" href="/static/bundles/frontend.css" />
```

**方式二：仅使用桥接层（非 React 环境）**

```html
<!-- 1. 引入 React/ReactDOM UMD -->
<script src="/static/bundles/react.production.min.js"></script>
<script src="/static/bundles/react-dom.production.min.js"></script>

<!-- 2. 引入组件库样式 -->
<link rel="stylesheet" href="/static/bundles/components.css" />

<!-- 3. 引入组件库 UMD -->
<script src="/static/bundles/components.js"></script>

<!-- 4. 引入请求库 UMD（可选，web-bridge 会自动创建默认实例） -->
<script src="/static/bundles/request.js"></script>

<!-- 5. 引入桥接层 UMD（自动将组件和请求能力暴露到 window） -->
<script src="/static/bundles/web-bridge.js"></script>
```

### 说明

- 组件库 UMD 依赖全局 `React` 和 `ReactDOM`，因此需要先加载 React UMD
- SPA 入口以 ES 模块形式挂载到页面中的 `#root` 元素
- 确保页面中存在 `<div id="root"></div>` 作为挂载点（使用 SPA 时）
- 桥接层会自动将 `StatusToast`、`Modal` 和请求客户端绑定到 `window` 对象，供非 React 代码使用
- 使用桥接层时，可通过 `window.showStatusToast()`、`window.showAlert()`、`window.showConfirm()`、`window.showPrompt()` 和 `window.request` 等 API

## 其他脚本

### 类型检查

仅执行 TypeScript 类型检查，不生成文件：

```bash
cd frontend && npm run typecheck
```

（PowerShell: `cd frontend; npm run typecheck`）

### 清理构建产物

手动清理构建输出目录：

```bash
cd frontend && npm run clean:bundles
```

## 包说明

### `@project_neko/components`

UI 组件库，当前包含：

- **Button**: 基础按钮组件，支持多种变体（primary、secondary、success、danger）和尺寸
- **StatusToast**: 状态提示组件，用于显示成功、错误、警告等信息
- **Modal**: 模态框组件系统
  - **BaseModal**: 基础模态框组件
  - **AlertDialog**: 警告对话框
  - **ConfirmDialog**: 确认对话框
  - **PromptDialog**: 输入对话框

组件库使用经典 JSX 转换（`React.createElement`），确保 UMD 格式在浏览器中与 React UMD 兼容。

### `@project_neko/request`

HTTP 请求库，基于 Axios 封装，提供：

- **请求队列**: 自动管理并发请求，Token 刷新期间暂存新请求
- **Token 管理**: 自动存储和刷新访问令牌，支持 401 自动刷新
- **平台适配**: 支持 Web（localStorage）和 React Native（AsyncStorage）
- **错误处理**: 统一的错误处理机制，支持自定义错误处理器
- **请求日志**: 可配置的请求/响应日志，开发环境自动启用

#### 配置选项

`createRequestClient(options: RequestClientConfig)` 支持以下配置：

| 选项 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `baseURL` | `string` | ✅ | - | API 基础 URL |
| `storage` | `TokenStorage` | ✅ | - | Token 存储实现 |
| `refreshApi` | `TokenRefreshFn` | ✅ | - | Token 刷新函数 |
| `timeout` | `number` | - | `15000` | 请求超时时间（毫秒） |
| `requestInterceptor` | `Function` | - | - | 自定义请求拦截器 |
| `responseInterceptor` | `Object` | - | - | 自定义响应拦截器 |
| `returnDataOnly` | `boolean` | - | `true` | 是否只返回 `response.data` |
| `errorHandler` | `Function` | - | - | 自定义错误处理器 |
| `logEnabled` | `boolean` | - | auto | 是否启用请求日志 |

#### 日志控制

请求日志的启用优先级：
1. `config.logEnabled`（配置项覆盖）
2. `globalThis.NEKO_REQUEST_LOG_ENABLED`（全局变量）
3. `import.meta.env.MODE`（构建模式，development 时启用）
4. 默认关闭

#### 导出内容

```typescript
// 类型导出
export type { RequestClientConfig, TokenStorage, TokenRefreshFn, TokenRefreshResult, QueuedRequest, Storage };

// 核心函数
export { createRequestClient } from "./createClient";

// Token 存储实现
export { WebTokenStorage, NativeTokenStorage } from "./src/request-client/tokenStorage";

// 存储抽象
export { default as webStorage } from "./src/storage/webStorage";
export { default as storage } from "./src/storage/index";

// 异步获取 nativeStorage（避免 Web 环境加载 RN 依赖）
export async function getNativeStorage(): Promise<Storage>;
```

#### 入口文件

| 文件 | 用途 | 导出内容 |
|------|------|----------|
| `index.ts` | 通用入口 | 类型、`createRequestClient`、存储抽象 |
| `index.web.ts` | Web 端入口 | 预配置的 `request` 实例 + 类型和工具 |
| `index.native.ts` | React Native 入口 | `createNativeRequestClient()` + 类型和工具 |

### `@project_neko/web-bridge`

桥接层，将 React 组件和请求能力暴露到 `window` 对象，供非 React 代码使用。主要功能：

- **bindStatusToastToWindow()**: 将 `StatusToast` 绑定到 `window.showStatusToast()`
- **bindModalToWindow()**: 将 `Modal` 绑定到 `window.showAlert()`、`window.showConfirm()`、`window.showPrompt()`
- **bindRequestToWindow()**: 将请求客户端绑定到 `window.request`，并提供 URL 构建工具
- **createAndBindRequest()**: 创建请求客户端并自动绑定到 `window`
- **autoBindDefaultRequest()**: 自动绑定默认请求客户端（UMD 加载时自动执行）

### `@project_neko/common`

公共工具与类型定义，当前包含：

- **ApiResponse<T>**: 标准 API 响应类型
- **noop()**: 空函数工具

## 注意事项

1. **构建顺序**: 完整构建必须按顺序执行，因为某些包可能依赖其他包的构建产物
2. **React 版本**: 确保 `vendor/react/` 中的 React UMD 文件版本与 `package.json` 中的版本一致
3. **路径别名**: 仅在开发环境中生效，构建时会解析为实际路径
4. **UMD 全局变量**: 组件库 UMD 使用全局变量名 `ProjectNekoComponents`，请求库使用 `ProjectNekoRequest`，通用工具使用 `ProjectNekoCommon`，桥接层使用 `ProjectNekoBridge`
5. **TypeScript 配置**: 项目使用 `moduleResolution: "Bundler"`，适合 Vite 构建环境
6. **测试覆盖率**: 请求库的测试覆盖率报告位于 `packages/request/coverage/`，该目录已被 git 忽略

## 故障排查

### 构建失败

- 检查 Node.js 和 npm 版本是否符合要求
- 确保已执行 `npm install` 安装所有依赖
- 检查 `static/bundles` 目录权限

### 开发服务器无法启动

- 检查端口是否被占用（默认 5173）
- 确认 `vite.web.config.ts` 配置正确
- 查看控制台错误信息

### 类型错误

- 运行 `npm run typecheck` 查看详细类型错误
- 确保所有包的 `tsconfig.json` 配置正确
- 检查路径别名是否正确配置

### 运行时错误

- 检查浏览器控制台错误信息
- 确认服务端模板中脚本引用顺序正确
- 验证 `#root` 元素是否存在（使用 SPA 时）
- 确认 React/ReactDOM UMD 已正确加载（检查全局 `React` 和 `ReactDOM` 对象）
- 使用桥接层时，确认组件库和请求库已加载，且 `window` 对象上存在相应 API

### 测试失败

- 确保在 `packages/request` 目录下运行测试
- 检查是否有未安装的开发依赖（`vitest`、`@vitest/coverage-v8`）
- 查看测试输出中的具体错误信息


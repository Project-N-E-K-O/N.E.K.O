# N.E.K.O 前端

React 19 + Vite 7 的单页应用，使用 npm workspaces 管理组件库、通用工具与请求库。构建产物统一输出到仓库根的 `static/bundles`，供服务端模板直接引用。

## 技术栈

- **框架**: React 19.1.1
- **构建工具**: Vite 7.1.7
- **语言**: TypeScript 5.9.2
- **HTTP 客户端**: Axios 1.13.2
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
│   │   │   └── Button.css
│   │   ├── index.ts      # 组件导出入口
│   │   └── vite.config.ts
│   ├── request/          # HTTP 请求库（Axios 封装）
│   │   ├── createClient.ts
│   │   ├── index.ts      # 通用入口
│   │   ├── index.web.ts  # Web 端入口（默认实例）
│   │   ├── index.native.ts
│   │   └── src/
│   │       ├── request-client/  # 请求客户端核心
│   │       │   ├── requestQueue.ts
│   │       │   ├── tokenStorage.ts
│   │       │   └── types.ts
│   │       └── storage/          # 存储抽象层
│   │           ├── index.web.ts
│   │           ├── index.native.ts
│   │           └── ...
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
- **`packages/components/`**: UI 组件库，产出 ES/UMD 双格式，支持外部化 React/ReactDOM
- **`packages/request/`**: Axios 封装库，提供请求队列、Token 自动刷新等功能，支持 Web/React Native 双平台
- **`packages/common/`**: 公共类型定义（如 `ApiResponse<T>`）和工具函数
- **`scripts/`**: 构建辅助脚本，用于清理输出目录和复制 React UMD 文件
- **`vendor/react/`**: React/ReactDOM 生产环境 UMD 源文件，构建时复制到 `static/bundles`
- **`static/bundles/`**: 构建输出目录（位于仓库根目录，由脚本自动创建/清理）

## 环境要求

- **Node.js**: 推荐 22.x 或 20.x LTS；18.x 作为最低兼容基线，不建议再低
- **npm**: 推荐 11.x；10.x 作为最低兼容基线

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

示例页面内置一个请求按钮，调用 `/api/config/page_config` 并打印返回：

```17:28:frontend/src/web/App.tsx
function App() {
  const handleClick = useCallback(async () => {
    try {
      const data = await request.get("/api/config/page_config", {
        params: { lanlan_name: "test" }
      });
      // 将返回结果展示在控制台或弹窗
      console.log("page_config:", data);
    } catch (err: any) {
      console.error("请求失败", err);
    }
  }, []);
```

### 请求客户端使用

在 `App.tsx` 中创建请求客户端实例：

```7:15:frontend/src/web/App.tsx
const request = createRequestClient({
  baseURL: "http://localhost:48911",
  storage: new WebTokenStorage(),
  refreshApi: async () => {
    // 示例中不做刷新，实际可按需实现
    throw new Error("refreshApi not implemented");
  },
  returnDataOnly: true
});
```

也可以直接使用 `packages/request/index.web.ts` 导出的默认实例（已配置 Token 刷新）。

## 构建

### 完整构建

执行完整构建流程：

```bash
cd frontend && npm run build
```

（PowerShell: `cd frontend; npm run build`）

构建流程依次执行：

1. **`clean:bundles`**: 清空仓库根的 `static/bundles` 目录
2. **`build:request`**: 构建请求库，产出 ES/UMD 双格式
3. **`build:common`**: 构建通用工具包，产出 ES/UMD 双格式
4. **`build:components`**: 构建组件库，产出 ES/UMD 双格式，外部化 `react`/`react-dom`，生成 `components.css`
5. **`build:web`**: 构建 Web 应用入口，生成 `react_web.js`（ES 模块）
6. **`copy:react-umd`**: 复制 `vendor/react/*.js` 到 `static/bundles`

### 单独构建

可以单独构建某个包或入口：

```bash
# 构建组件库
cd frontend && npm run build:components

# 构建请求库
cd frontend && npm run build:request

# 构建通用工具
cd frontend && npm run build:common

# 构建 Web 应用
cd frontend && npm run build:web
```

### 构建产物

主要产物位于 `static/bundles/`（仓库根目录）：

- **`react_web.js`**: SPA 入口（ESM 格式）
- **`components.js`** / **`components.es.js`**: 组件库（UMD/ES 格式）
- **`components.css`**: 组件库样式文件
- **`common.js`** / **`common.es.js`**: 通用工具（UMD/ES 格式）
- **`request.js`** / **`request.es.js`**: 请求库（UMD/ES 格式）
- **`react.production.min.js`**: React 生产环境 UMD（由脚本复制）
- **`react-dom.production.min.js`**: ReactDOM 生产环境 UMD（由脚本复制）

所有构建产物均包含 source map 文件（`.map`）。

## 服务端集成

### HTML 模板引用

在服务端模板中按以下顺序引用构建产物：

```html
<!-- 1. 引入 React/ReactDOM UMD（组件库依赖） -->
<script src="/static/bundles/react.production.min.js"></script>
<script src="/static/bundles/react-dom.production.min.js"></script>

<!-- 2. 引入组件库样式 -->
<link rel="stylesheet" href="/static/bundles/components.css" />

<!-- 3. 引入组件库 UMD（依赖全局 React/ReactDOM） -->
<script src="/static/bundles/components.js"></script>

<!-- 4. 引入 SPA 入口（ES 模块） -->
<script type="module" src="/static/bundles/react_web.js"></script>
```

### 说明

- 组件库 UMD 依赖全局 `React` 和 `ReactDOM`，因此需要先加载 React UMD
- SPA 入口以 ES 模块形式挂载到页面中的 `#root` 元素
- 确保页面中存在 `<div id="root"></div>` 作为挂载点

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

- **Button**: 基础按钮组件

组件库使用经典 JSX 转换（`React.createElement`），确保 UMD 格式在浏览器中与 React UMD 兼容。

### `@project_neko/request`

HTTP 请求库，基于 Axios 封装，提供：

- **请求队列**: 自动管理并发请求
- **Token 管理**: 自动存储和刷新访问令牌
- **平台适配**: 支持 Web（localStorage）和 React Native（AsyncStorage）
- **错误处理**: 统一的错误处理机制

### `@project_neko/common`

公共工具与类型定义，当前包含：

- **ApiResponse<T>**: 标准 API 响应类型
- **noop()**: 空函数工具

## 注意事项

1. **构建顺序**: 完整构建必须按顺序执行，因为某些包可能依赖其他包的构建产物
2. **React 版本**: 确保 `vendor/react/` 中的 React UMD 文件版本与 `package.json` 中的版本一致
3. **路径别名**: 仅在开发环境中生效，构建时会解析为实际路径
4. **UMD 全局变量**: 组件库 UMD 使用全局变量名 `NEKOComponents`，请求库使用 `NEKORequest`，通用工具使用 `NEKOCommon`
5. **TypeScript 配置**: 项目使用 `moduleResolution: "Bundler"`，适合 Vite 构建环境

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
- 验证 `#root` 元素是否存在


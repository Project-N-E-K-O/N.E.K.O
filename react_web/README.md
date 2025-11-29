## Xiao8 / Project N.E.K.O. React Web 前端

这是 Xiao8 / Project N.E.K.O. 的 **React Web 前端**，采用 **混合架构**：

### 🏗️ 双轨架构

1. **React Router v7 SPA 应用**（主轨道）
   - **纯客户端渲染（SPA 模式）** - 所有渲染在浏览器端完成
   - **主界面 UI（Live2D + Chat 容器）** - `app/routes/main.tsx`
   - 与后端 `/api` 的交互与关机 Beacon（`/api/beacon/shutdown`）
   - 与根项目 `static/` 目录中的 Live2D / JS 资源的集成

2. **独立组件构建系统**（渐进式迁移轨道）
   - 将 React 组件打包为 **ES Module**，供传统 HTML/JS 页面使用
   - 支持逐个组件替换旧代码，实现渐进式迁移
   - 示例：`ExampleButton` 组件

### 🎯 设计目标

- ✅ 支持全新 React Router SPA 应用开发
- ✅ 纯客户端渲染，无需 SSR 服务器
- ✅ 支持将现有 `static/app.js` 逐步迁移到 React
- ✅ 新旧代码可以共存运行
- ✅ 保持向后兼容，不影响现有功能
- ✅ 可部署到任何静态文件服务器

---

## 📁 目录结构

```txt
react_web/
├── app/                      # React Router 应用源码
│   ├── components/           # 可复用的 React 组件
│   │   ├── ExampleButton.tsx # 示例：可独立打包的组件
│   │   └── ...               # 其他组件
│   ├── routes/
│   │   └── main.tsx          # Lanlan Terminal 主页面
│   ├── utils/                # 工具函数
│   │   ├── api.ts            # API 封装
│   │   └── eventBus.ts       # 事件总线（新旧代码通信）*待创建
│   ├── root.tsx              # 应用根布局（注入全局脚本）
│   └── routes.ts             # 路由配置
├── scripts/
│   └── copy-component.js     # 复制组件到 static/ 目录
├── public/                   # 静态资源
├── build/                    # 构建输出
│   ├── client/               # React Router SPA 静态资源（HTML/JS/CSS）
│   └── components/           # 独立组件构建（临时）
├── vite.config.ts            # React Router 应用构建配置
├── vite.component.config.ts  # 独立组件构建配置
├── tsconfig.json
├── package.json
└── README.md
```

### 目录说明

- **`app/components/`** - 既可以用于 React Router 应用，也可以独立打包
- **`build/components/`** - 临时目录，独立组件构建的中转站
- **`../static/`** - 最终输出目录，供传统 HTML 页面使用

---

## 与主项目 Xiao8 的集成关系

- **此目录位置**：`Xiao8/react_web`
- **静态资源来源**：依赖根项目的 `static/` 目录（`Xiao8/static`）
- **脚本依赖**：`static/request.global.js`, `static/common_ui.js`, `static/app.js`, `static/libs/*.js`, `static/live2d.js` 等
- **API 地址**：通过环境变量 `VITE_API_BASE_URL` 统一配置，默认 `http://localhost:48911`
- **静态资源服务器地址**：通过 `VITE_STATIC_SERVER_URL` 配置，默认 `http://localhost:48911`

---

## 统一的 Request 模块

### 两套前端架构

**1. React Web (`react_web/`)**
- ✅ 使用统一的 `@xiao8/request` 模块
- 在 `app/api/request.ts` 中创建请求客户端实例
- 通过 `exposeRequestToGlobal()` 暴露到全局：
  - `window.request` - 统一的请求实例（基于 Axios）
  - `window.buildApiUrl(path)` - 构建 API URL
  - `window.buildStaticUrl(path)` - 构建静态资源 URL
  - `window.buildWebSocketUrl(path)` - 构建 WebSocket URL
- React 组件中直接使用 `import { request } from '~/api/request'`

**2. 静态模板 (`templates/index.html`)**
- ✅ 使用 `request.global.js`（打包了 axios 和 axios-auth-refresh）
- 自动初始化 `window.request` 等工具函数
- 旧版 JS 代码应使用 `window.request` 或 `window.buildApiUrl` 等工具函数

### Request 模块特性

- ✅ **Axios 基础** - 基于 Axios，提供强大的 HTTP 客户端能力
- ✅ **统一请求实例** - 一次配置，全项目使用
- ✅ **自动 Token 刷新** - 401 时自动刷新 access token，无需手动处理
- ✅ **请求队列** - 防止并发刷新 token，确保请求顺序执行
- ✅ **工具函数** - 提供 `buildApiUrl`、`buildStaticUrl`、`buildWebSocketUrl` 等

### 使用方式

**在 React 组件中：**
```typescript
import { request } from '~/api/request';

const data = await request.get('/api/users');
```

**在静态 HTML 或旧版 JS 中：**
```javascript
// 使用 request 实例
const data = await window.request.get('/api/users');

// 使用工具函数构建 URL
const apiUrl = window.buildApiUrl('/api/users');
const wsUrl = window.buildWebSocketUrl('/ws/chat');
```

### 构建 request.global.js

```bash
npm run build:request
```

这会打包 `app/api/request.global.ts` 及其所有依赖，并复制到 `static/request.global.js`

---

## 环境变量

可通过 `.env` 或命令行注入以下变量（Vite / React Router 标准）：

- **`VITE_API_BASE_URL`**  
  - 用途：指向 Xiao8 后端 API 根地址  
  - 默认值：`http://localhost:48911`
  - 影响位置：`app/root.tsx`、`app/routes/main.tsx` 里设置 `window.API_BASE_URL` 与 `fetchWithBaseUrl`

- **`VITE_STATIC_SERVER_URL`**  
  - 用途：指向提供 `static/` 目录的 HTTP 服务地址  
  - 默认值：`http://localhost:48911`
  - 用途示例：
    - 注入 CSS 变量 `--toast-background-url`
    - 拼接 `/static/xxx` 资源路径
    - 在运行时通过 `buildStaticUrl` 自动重写 `/static/` 路径

示例 `.env`：

```bash
VITE_API_BASE_URL=http://localhost:48911
VITE_STATIC_SERVER_URL=http://localhost:48911
```

--- 

## 安装依赖

```bash
cd react_web
npm install
```

---

## 开发模式

- **仅前端开发（需要后端已启动）**

```bash
cd react_web
npm run dev
```

默认监听 `http://localhost:5173`，前端会：

- 调用 `VITE_API_BASE_URL` 指向的后端接口（如 `/api/config/page_config`、`/api/characters`、`/api/live2d/models` 等）
- 从 `VITE_STATIC_SERVER_URL/static/` 拉取 `live2d.js`、`app.js`、Live2D 模型相关资源

---

## 构建与运行

### 1. 构建 React Router 应用（生产）

```bash
cd react_web
npm run build
```

输出目录：

```txt
build/
└── client/    # SPA 静态资源（HTML/JS/CSS）
```

> 注：SPA 模式只生成客户端资源，可以部署到任何静态文件服务器。

### 2. 预览生产构建

```bash
cd react_web
npm run preview
```

使用 Vite 的预览服务器查看构建结果。

### 3. 部署

构建后的 `build/client/` 目录可以部署到：
- Nginx / Apache 等静态文件服务器
- Vercel / Netlify / GitHub Pages
- CDN + 对象存储（OSS）
- Xiao8 主项目的静态文件服务（集成模式）

---

## 独立组件构建（渐进式迁移）

虽然主界面已经由 `main.tsx` + 传统 JS 管理，但这里提供一个完整的组件级渐进式迁移方案，用于将 React 组件单独打包成 ES Module，方便在模板 HTML 中按需挂载和逐步替换旧代码。

### 🎯 适用场景

- 希望逐步将 `static/app.js` 中的功能迁移到 React
- 需要新旧代码共存运行
- 想要降低迁移风险，一个组件一个组件替换
- 需要在传统 HTML 页面中使用现代 React 组件

### 📦 组件开发快速指南

#### 1. 创建新组件

```bash
# 方式 1: 简单组件（直接在 components/ 下）
cd app/components
touch MyComponent.tsx MyComponent.css

# 方式 2: 复杂组件（独立目录）
mkdir -p app/components/MyComponent
cd app/components/MyComponent
touch MyComponent.tsx MyComponent.css index.ts
```

#### 2. 组件结构

**简单组件** (用于 React Router 应用或简单的独立构建):
```txt
app/components/
└── MyComponent.tsx      # 组件实现（含样式导入）
```

**完整组件** (用于复杂的独立构建，需要全局 API):
```txt
app/components/MyComponent/
├── MyComponent.tsx      # 组件实现
├── MyComponent.css     # 组件样式（含 @import "tailwindcss"）
└── index.ts            # 挂载逻辑和全局 API
```

> **注意**：如果组件需要独立打包，必须在 CSS 文件中显式导入 Tailwind：
> ```css
> @import "tailwindcss";
> ```

#### 3. 简单组件模板

适合大多数场景的简洁模板：

```typescript
// app/components/MyComponent.tsx
import React from 'react'

interface MyComponentProps {
  title?: string
  onAction?: () => void
}

export function MyComponent({ title = 'Default', onAction }: MyComponentProps) {
  return (
    <div className="my-component p-4 bg-white rounded shadow">
      <h3 className="text-lg font-bold">{title}</h3>
      <button 
        onClick={onAction}
        className="mt-2 px-4 py-2 bg-blue-500 text-white rounded hover:bg-blue-600"
      >
        Click Me
      </button>
    </div>
  )
}
```

### 构建命令

```bash
cd react_web
npm run build:component
```

流程：

1. 使用 `vite.component.config.ts` 将 `ExampleButton` 打包为 ES Module（`build/components/ExampleButton.js`）
2. 在构建过程中：
   - 将 React / ReactDOM 标记为外部依赖，改为从 CDN (`https://esm.sh`) 加载
   - 自动处理 `process.env.NODE_ENV`
   - 自动把 Tailwind CSS 样式内联到 JS，中途注入到 `<head>`
3. 最后通过 `scripts/copy-component.js` 将结果复制到 **上级项目的** `static/ExampleButton.js`

构建输出：

- `build/components/ExampleButton.js`（临时文件）
- `../static/ExampleButton.js`（供模板页面使用）

### 在传统 HTML 中使用组件

#### 方式 1：ES Module 导入（推荐）

```html
<div id="example-button-container"></div>

<script type="module">
  import { ExampleButton } from "/static/ExampleButton.js";
  import React from "https://esm.sh/react@19";
  import { createRoot } from "https://esm.sh/react-dom@19/client";

  function mountComponent() {
    const container = document.getElementById("example-button-container");
    if (!container) return;
    const root = createRoot(container);
    root.render(
      React.createElement(ExampleButton, {
        buttonText: "打开 Modal",
        onSave: (text1, text2) => {
          console.log("保存的内容:", text1, text2);
        },
      })
    );
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", mountComponent);
  } else {
    mountComponent();
  }
</script>
```

#### 方式 2：通过全局 API 挂载（推荐用于复杂组件）

如果在组件的 `index.ts` 中暴露了全局 API，可以这样使用：

```html
<div id="my-component"></div>

<!-- React 依赖 -->
<script crossorigin src="https://unpkg.com/react@19/umd/react.production.min.js"></script>
<script crossorigin src="https://unpkg.com/react-dom@19/umd/react-dom.production.min.js"></script>

<!-- 组件脚本 -->
<script src="/static/my-component.js"></script>

<script>
  // 手动挂载
  if (window.ReactComponents?.MyComponent) {
    window.ReactComponents.MyComponent.mount('my-component', {
      // props
    });
  }
  
  // 或者使用组件提供的方法
  if (window.ReactComponents?.MyComponent?.show) {
    window.ReactComponents.MyComponent.show(message, duration);
  }
</script>
```

### 🔄 新旧代码通信（事件总线模式）

对于需要与 `static/app.js` 交互的组件，推荐使用事件总线：

#### 1. 创建事件总线

`app/utils/eventBus.ts`：

```typescript
class EventBus {
  private events: Map<string, Function[]> = new Map()

  on(event: string, callback: Function) {
    if (!this.events.has(event)) {
      this.events.set(event, [])
    }
    this.events.get(event)!.push(callback)
  }

  off(event: string, callback: Function) {
    const callbacks = this.events.get(event)
    if (callbacks) {
      const index = callbacks.indexOf(callback)
      if (index > -1) callbacks.splice(index, 1)
    }
  }

  emit(event: string, ...args: any[]) {
    const callbacks = this.events.get(event)
    if (callbacks) {
      callbacks.forEach(callback => {
        try {
          callback(...args)
        } catch (error) {
          console.error(`Error in event handler for ${event}:`, error)
        }
      })
    }
  }
}

export const eventBus = new EventBus()

// 暴露到全局，供旧代码使用
if (typeof window !== 'undefined') {
  (window as any).EventBus = eventBus
}
```

#### 2. 在 React 组件中监听事件

```typescript
import { eventBus } from '~/utils/eventBus'

export function MyComponent() {
  useEffect(() => {
    const handleEvent = (data: any) => {
      // 处理来自旧代码的事件
    }
    
    eventBus.on('my-event', handleEvent)
    return () => eventBus.off('my-event', handleEvent)
  }, [])
  
  // ...
}
```

#### 3. 在旧代码中触发事件

```javascript
// static/app.js
if (window.EventBus) {
  window.EventBus.emit('my-event', { data: 'value' })
}
```

### 📋 组件模板（带全局 API）

当组件需要提供全局 API 供旧代码调用时，创建完整的 `index.ts`：

**app/components/MyComponent/MyComponent.tsx**:

```typescript
import React, { useState } from 'react'
import './MyComponent.css'

export interface MyComponentProps {
  initialValue?: string
  onSave?: (value: string) => void
}

export function MyComponent({ initialValue = '', onSave }: MyComponentProps) {
  const [value, setValue] = useState(initialValue)

  const handleSave = () => {
    onSave?.(value)
  }

  return (
    <div className="my-component p-4 bg-white rounded shadow">
      <input
        type="text"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        className="w-full px-3 py-2 border rounded"
      />
      <button
        onClick={handleSave}
        className="mt-2 px-4 py-2 bg-blue-500 text-white rounded hover:bg-blue-600"
      >
        Save
      </button>
    </div>
  )
}
```

**app/components/MyComponent/index.ts**:

```typescript
import React from 'react'
import { createRoot, Root } from 'react-dom/client'
import { MyComponent } from './MyComponent'
import './MyComponent.css'

declare global {
  interface Window {
    ReactComponents?: {
      MyComponent?: {
        mount: (containerId: string, props?: any) => void
        unmount: (containerId: string) => void
        update: (containerId: string, props: any) => void
      }
    }
  }
}

const mountedInstances = new Map<string, { root: Root, props: any }>()

function mount(containerId: string, props: any = {}) {
  const container = document.getElementById(containerId)
  if (!container) {
    console.error(`[MyComponent] Container #${containerId} not found`)
    return
  }

  if (mountedInstances.has(containerId)) {
    unmount(containerId)
  }

  const root = createRoot(container)
  root.render(<MyComponent {...props} />)
  mountedInstances.set(containerId, { root, props })
}

function unmount(containerId: string) {
  const instance = mountedInstances.get(containerId)
  if (instance) {
    instance.root.unmount()
    mountedInstances.delete(containerId)
  }
}

function update(containerId: string, props: any) {
  const instance = mountedInstances.get(containerId)
  if (instance) {
    const newProps = { ...instance.props, ...props }
    instance.root.render(<MyComponent {...newProps} />)
    mountedInstances.set(containerId, { ...instance, props: newProps })
  }
}

// 暴露到全局
if (typeof window !== 'undefined') {
  if (!window.ReactComponents) {
    window.ReactComponents = {}
  }
  window.ReactComponents.MyComponent = { mount, unmount, update }
}

// 自动挂载（如果容器存在）
if (typeof document !== 'undefined') {
  const container = document.getElementById('my-component')
  if (container) {
    mount('my-component')
  }
}
```

### 🔧 添加新组件到构建

#### 方式 1: 修改 vite.component.config.ts（多入口）

如果需要构建多个独立组件：

```typescript
// vite.component.config.ts
export default defineConfig({
  // ...
  build: {
    lib: {
      entry: {
        ExampleButton: resolve(__dirname, "app/components/ExampleButton.tsx"),
        MyComponent: resolve(__dirname, "app/components/MyComponent/index.ts"),
        AnotherComponent: resolve(__dirname, "app/components/AnotherComponent/index.ts"),
      },
      formats: ["es"],
    },
    // ...
  },
})
```

然后更新 `scripts/copy-component.js` 来复制所有组件。

#### 方式 2: 单独构建配置（推荐用于大型项目）

为每个组件创建独立的构建配置：

```bash
# 创建组件专属配置
cp vite.component.config.ts vite.my-component.config.ts

# 修改 entry 指向你的组件
# 添加对应的 npm script
```

**package.json**:
```json
{
  "scripts": {
    "build:component": "vite build --config vite.component.config.ts && npm run copy:component",
    "build:my-component": "vite build --config vite.my-component.config.ts && npm run copy:my-component"
  }
}
```

### 📊 渐进式迁移优先级

#### 第一阶段：独立组件（低风险）
1. ✅ StatusToast - 独立显示，无复杂交互
2. ✅ Modal/Dialog - 独立弹窗组件
3. ✅ Button - 基础 UI 组件

#### 第二阶段：中等复杂度组件
1. ⚠️ ChatContainer - 需要 WebSocket 集成
2. ⚠️ ScreenshotThumbnails - 需要文件处理

#### 第三阶段：复杂组件
1. 🔴 Live2DCanvas - 需要 PIXI.js 集成
2. 🔴 VoiceControl - 需要 WebRTC 集成

### ⚠️ 注意事项

1. **React 版本一致性** - 确保所有组件使用相同版本（当前：React 19）
2. **样式隔离** - 使用 CSS 模块或 Tailwind 的作用域类名
3. **状态管理** - 组件间通信优先使用事件总线
4. **性能考虑** - 按需加载，避免重复打包 React
5. **向后兼容** - 保留旧代码作为降级方案
6. **CDN vs 本地** - 考虑使用 CDN 加载 React/ReactDOM 以减小包体积

### 🎓 开发最佳实践

#### 1. 组件设计原则

- **单一职责**: 每个组件只做一件事
- **Props 明确**: 使用 TypeScript 定义清晰的接口
- **可复用性**: 设计时考虑在多个场景使用
- **降级方案**: 对于关键功能，保留非 React 的降级方案

#### 2. 性能优化

```typescript
// 使用 React.memo 避免不必要的重渲染
export const MyComponent = React.memo(({ data }: Props) => {
  // ...
})

// 使用 useMemo 缓存计算结果
const expensiveValue = useMemo(() => computeExpensiveValue(data), [data])

// 使用 useCallback 缓存函数
const handleClick = useCallback(() => {
  // ...
}, [dependency])
```

#### 3. 类型安全

```typescript
// 定义清晰的 Props 接口
export interface MyComponentProps {
  title: string                    // 必需
  count?: number                   // 可选
  onSave?: (data: string) => void  // 回调
  children?: React.ReactNode       // 子元素
}

// 使用泛型
export function MyList<T>({ items, renderItem }: {
  items: T[]
  renderItem: (item: T) => React.ReactNode
}) {
  return <ul>{items.map(renderItem)}</ul>
}
```

#### 4. 错误处理

```typescript
// 组件内部错误处理
export function MyComponent() {
  const [error, setError] = useState<Error | null>(null)

  useEffect(() => {
    try {
      // 可能出错的操作
    } catch (err) {
      setError(err as Error)
      console.error('[MyComponent] Error:', err)
    }
  }, [])

  if (error) {
    return <div className="error">出错了: {error.message}</div>
  }

  return <div>正常内容</div>
}
```

#### 5. 调试技巧

```typescript
// 开发模式下添加调试信息
if (import.meta.env.DEV) {
  console.log('[MyComponent] Props:', props)
  console.log('[MyComponent] State:', state)
}

// 使用 React DevTools
// 安装浏览器扩展: React Developer Tools

// 性能分析
import { Profiler } from 'react'

<Profiler id="MyComponent" onRender={(id, phase, actualDuration) => {
  console.log(`${id} (${phase}) took ${actualDuration}ms`)
}}>
  <MyComponent />
</Profiler>
```

### 📚 常见问题 (FAQ)

#### Q1: 如何在旧代码中调用 React 组件？

**A**: 使用全局 API：

```javascript
// 旧代码 (static/app.js)
if (window.ReactComponents?.MyComponent) {
  window.ReactComponents.MyComponent.mount('container-id', { prop: 'value' })
}
```

#### Q2: React 组件如何访问旧代码的全局变量？

**A**: 直接通过 window 对象：

```typescript
// React 组件中
const oldValue = (window as any).someGlobalVariable

// 建议在 global.d.ts 中添加类型
declare global {
  interface Window {
    someGlobalVariable?: string
  }
}
```

#### Q3: 如何调试组件没有正确挂载？

**A**: 检查以下几点：

1. 容器元素是否存在：`document.getElementById('container-id')`
2. React/ReactDOM 是否正确加载
3. 组件 JS 文件是否加载（查看 Network 面板）
4. 查看浏览器控制台错误信息
5. 确认构建输出是否正确

#### Q4: 样式没有生效怎么办？

**A**: 

1. 确认 CSS 文件已导入：`import './MyComponent.css'`
2. 检查 Tailwind 配置：CSS 中是否有 `@import "tailwindcss"`
3. 查看构建输出，确认样式已注入
4. 检查样式是否被其他样式覆盖（使用浏览器开发者工具）

#### Q5: 如何处理组件之间的通信？

**A**: 使用事件总线：

```typescript
// 组件 A 发送事件
eventBus.emit('data-updated', { id: 1, value: 'new' })

// 组件 B 监听事件
useEffect(() => {
  const handler = (data) => console.log(data)
  eventBus.on('data-updated', handler)
  return () => eventBus.off('data-updated', handler)
}, [])
```

---

## 与 `static/` 下旧版 JS 的协作方式（重要）

`app/routes/main.tsx` 做了大量「桥接工作」，把现代 React 环境与旧版 `static/*.js` 串起来，核心点包括：

- **全局工具函数与变量**
  - `window.buildApiUrl` / `window.fetchWithBaseUrl`
  - `window.API_BASE_URL`、`window.STATIC_SERVER_URL`
  - `window.pageConfigReady`（异步加载 `/api/config/page_config`）
  - 全局菜单状态：`window.activeMenuCount`、`markMenuOpen`、`markMenuClosed`
- **静态资源路径重写**
  - 拦截 `HTMLImageElement.src` / `Element.setAttribute('src')`
  - 拦截 `style.cssText` / `backgroundImage` 等 CSS 属性
  - 自动把 `/static/...` 替换为基于 `VITE_STATIC_SERVER_URL` 的完整 URL
- **错误与日志处理（开发模式）**
  - 拦截 `console.error` 和 `window.onerror`，静默忽略 static 资源加载失败
- **Beacon 与跨页面通信**
  - 页面关闭时向 `/api/beacon/shutdown` 发送 `navigator.sendBeacon`
  - 通过 `localStorage` + `storage` 事件与设置页面通信，动态隐藏/显示主 UI 以及重新加载 Live2D 模型

修改这部分逻辑时，建议：

- 保持 `window.*` 的对外行为稳定（避免破坏 `static/*.js`）
- 如果新增全局变量或方法，同时在 `global.d.ts` 中补充类型声明

---

## 组件与样式约定

- **组件路径**：`app/components/`
- **样式**：默认使用 Tailwind CSS v4；
  - 若组件单独构建（如 `ExampleButton`），需要：
    - 在组件文件中显式导入 CSS：`import "./ComponentName.css";`
    - CSS 中包含 `@import "tailwindcss";`

---

## 技术栈

- **React Router v7**：React 框架（路由 + SPA 模式）
- **React 19**：UI 库
- **TypeScript**：类型安全
- **Tailwind CSS v4**：样式系统
- **Vite 7**：构建工具（主应用 & 组件构建）

## 架构说明

### SPA 模式（当前）

- ✅ **纯客户端渲染** - 所有渲染在浏览器中进行
- ✅ **无需 Node 服务器** - 只需静态文件服务
- ✅ **简化部署** - 直接部署到静态服务器
- ✅ **开发简单** - 无需处理 SSR 复杂性

### 为什么选择 SPA 而不是 SSR？

1. **简化部署** - Xiao8 是桌面应用，不需要 SEO
2. **降低复杂度** - 无需维护 Node SSR 服务器
3. **更好的集成** - 与现有 FastAPI 后端更容易集成
4. **开发效率** - 减少服务端/客户端状态同步问题

### 如果未来需要 SSR？

只需将 `react-router.config.ts` 中的 `ssr: false` 改为 `ssr: true`，并安装相应依赖：
```bash
npm install @react-router/node @react-router/serve isbot
```

---

## 📚 相关文档

- **[REACT_REFACTOR_PLAN.md](./REACT_REFACTOR_PLAN.md)** - 重构计划与进度跟踪

---

## 🤝 贡献指南

### 开发流程

1. **创建功能分支**
   ```bash
   git checkout -b feature/my-component
   ```

2. **开发组件**
   - 遵循上述组件开发指南
   - 添加必要的类型定义
   - 编写清晰的注释

3. **测试**
   ```bash
   npm run dev        # 开发测试
   npm run build      # 构建测试
   npm run typecheck  # 类型检查
   ```

4. **提交代码**
   ```bash
   git add .
   git commit -m "feat: add MyComponent"
   ```

### 代码规范

- 使用 TypeScript，避免使用 `any`
- 组件名使用 PascalCase
- 函数名使用 camelCase
- 常量使用 UPPER_SNAKE_CASE
- 添加必要的 JSDoc 注释

---

如需后续对 README 做更细的中文说明（比如面向非开发者的部署/使用文档），可以再单独拆一份到 `docs/` 或上层项目的文档中。

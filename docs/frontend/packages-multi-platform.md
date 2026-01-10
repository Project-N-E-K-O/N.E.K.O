### packages 多端兼容设计（React Web / legacy HTML+JS / React Native）

本篇描述 `@N.E.K.O/frontend/packages/*` 的总体设计：让同一套“业务基础能力”同时服务于：

- **React Web App**：`@N.E.K.O/frontend/src/web/*`（Vite）
- **legacy HTML + 原生 JS**：`@N.E.K.O/templates/*` + `@N.E.K.O/static/*`（通过 `static/bundles/*.js` 的 UMD/ES 构建产物）
- **React Native App**：`@N.E.K.O.-RN/*`（Expo/Metro）

> 目标不是“所有包 100% 同时在三端运行”，而是让**可共享的部分最大化共享**，并把平台差异收敛在明确的边界文件中。

---

### 1. 分层原则（必须遵守）

- **core 层优先 host-agnostic**：
  - 不假设浏览器 DOM（`window/document/navigator`）存在
  - 不假设 React Provider 存在
  - 不引入平台特有依赖（例如 `react-native`、WebAudio、PixiJS）——除非该文件明确属于 web/native adapter
- **平台适配层显式隔离**：
  - Web 端适配：只放在 `index.web.ts` / `src/web/**`
  - RN 端适配：只放在 `index.native.ts` / `src/native/**`
- **桥接层（legacy HTML/JS）集中到 `web-bridge`**：
  - 允许读写 `window`，将能力暴露成 `window.request / window.showStatusToast / window.showAlert ...`
  - 避免把兼容逻辑散落到其他包

---

### 2. 入口文件规范（推荐标准形态）

对“需要多端”的 packages，推荐具备以下入口：

- **`index.ts`**：默认入口（尽量安全：SSR/Node/Metro 解析时不应崩溃）
- **`index.web.ts`**：Web 专用入口/便捷方法（可读取 `window/location`，可依赖浏览器 API）
- **`index.native.ts`**：RN 专用入口/便捷方法（可依赖 RN 模块）

并在 `package.json` 中配置条件导出：

- `react-native`：给 Metro 优先解析
- `browser`：给浏览器 bundler 优先解析
- `exports`：细粒度条件入口（推荐）

例如（以 `@project_neko/request` 为例）：

```json
{
  "main": "index.ts",
  "browser": "index.web.ts",
  "react-native": "index.native.ts",
  "exports": {
    ".": {
      "react-native": "./index.native.ts",
      "browser": "./index.web.ts",
      "default": "./index.ts"
    },
    "./web": "./index.web.ts"
  }
}
```

---

### 3. 当前 packages 分类建议

#### 3.1 强烈建议保持“纯/跨端”的 packages（核心能力）

- `common`
- `request`
- `realtime`

特点：不依赖 React/DOM，或仅在 `index.web.ts` / `src/web` 里做少量浏览器便捷逻辑。

#### 3.2 跨端但必须有 adapter 的 packages（允许 platform 依赖）

- `audio-service`
  - `src/web/*`：WebAudio（麦克风采集、AudioWorklet、播放/解码）
  - `src/native/*`：依赖 `react-native-pcm-stream`（原生 PCM 录放）
- `live2d-service`
  - `src/web/*`：PixiJS + Cubism（可通过注入 `PIXI/Live2DModel` 或使用 `window.PIXI`）
  - `src/native/*`：RN adapter（建议对接 `@N.E.K.O.-RN/packages/react-native-live2d`，并以最小契约隔离）

#### 3.3 Web-only UI packages（默认不保证 RN 可运行）

- `components`
  - 现状包含 `react-dom`、Portal、CSS、`document/window/navigator` 等 Web 特性
  - 若未来要“同包名同时支持 RN”，需要引入 `index.native.ts` + RN 组件实现，并在 `exports` 中提供 `react-native` 条件入口

#### 3.4 legacy HTML/JS 桥接层（Web-only）

- `web-bridge`
  - 负责把 `request/realtime/components` 的能力暴露到 `window`
  - 允许 `window/document` 操作与全局事件（`requestReady` / `modalReady` / `react-ready` 等）
  - 不建议让 RN native 侧依赖该包（除非 Expo Web 需要复用）

---

### 4. 兼容性测试建议（最低集合）

- **类型检查**
  - Web：`tsc --noEmit`（或 `npm run typecheck`）
  - Native：各包的 `tsconfig.native.json`（lib 不含 DOM），用于发现不小心引入的浏览器 API
- **运行态 smoke**
  - Web（Vite）：验证 `request/realtime/audio-service/live2d-service` 关键路径无报错
  - legacy HTML：验证 `static/bundles/*` 构建产物可被模板页按顺序加载并可用 `window.*` API
  - RN：验证 Metro 能解析 `@project_neko/*` 并且 native-only 代码不会被 web bundle 误打入


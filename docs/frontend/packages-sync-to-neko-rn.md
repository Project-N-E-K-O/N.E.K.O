### 将 `@N.E.K.O/frontend/packages` 同步到 `@N.E.K.O.-RN/packages`（以前者为源）

本篇描述“以 `@N.E.K.O/frontend/packages` 为源代码仓库”，把可共享 packages 同步到 `@N.E.K.O.-RN/packages` 的流程与注意事项。

---

### 1. 当前同步机制（已存在）

`@N.E.K.O.-RN` 仓库内有脚本：`scripts/sync-neko-packages.js`，其行为是：

- **镜像拷贝**：默认会先删除目标包目录，再从源目录复制（mirror）
- **忽略噪声目录**：`node_modules/dist/coverage/.vite/.turbo` 等
- **路径后处理**：会对 `vite.config.ts` 的 `outDir` 进行路径修正（两仓库层级不同）

默认映射（当前脚本内置）：

- `frontend/packages/common` → `N.E.K.O.-RN/packages/project-neko-common`
- `frontend/packages/components` → `N.E.K.O.-RN/packages/project-neko-components`
- `frontend/packages/request` → `N.E.K.O.-RN/packages/project-neko-request`
- `frontend/packages/realtime` → `N.E.K.O.-RN/packages/project-neko-realtime`

---

### 2. 同步的核心约束（必须先定规矩）

因为同步脚本是“先清空再复制”，所以目标目录有一个硬约束：

- **目标目录必须视为“生成物（generated）”**，否则一定会丢失 RN 侧手改文件。

因此迁移/同步有两种推荐策略（二选一，避免混乱）：

#### 方案 A：目标目录禁止手改（最简单、最干净）

- `N.E.K.O.-RN/packages/project-neko-*` 完全由同步生成
- RN 侧若发现 bug/需要新功能：一律回到源 `@N.E.K.O/frontend/packages/*` 修改，再同步过去

适用：RN 侧不需要额外 assets/patch，或这些差异也能回到源仓库解决。

#### 方案 B：同步后叠加 Overlay（更实用，允许少量 RN 特有内容）

定义一个“覆盖层目录”，仅存放 RN 侧必须的差异文件（例如 assets、metro 适配、极少量平台分支）。

示例结构（建议）：

- `@N.E.K.O.-RN/packages-overrides/project-neko-components/**`
- `@N.E.K.O.-RN/packages-overrides/project-neko-request/**`

同步流程变成：

1) clean 目标包目录  
2) copy 上游 packages（源为准）  
3) apply overlay（覆盖/追加少数文件）

好处：依然保持“源为准”，同时避免 RN 必需文件被镜像清空。

---

### 3. 建议纳入同步的 packages（现状与缺口）

当前脚本只同步了：`common/components/request/realtime`。

但从“跨端能力共享”的目标看，通常还应纳入：

- `audio-service`（RN 侧依赖 `react-native-pcm-stream`，已具备 `index.native.ts` 与 `exports` 条件入口）
- `live2d-service`（RN 侧应逐步补齐 native adapter，建议对接 `@N.E.K.O.-RN/packages/react-native-live2d`）

而以下包一般不建议纳入同步（除非明确需要）：

- `web-bridge`：Web-only（绑定 `window.*`），RN native 侧不应依赖；如 Expo Web 需要可单独评估。

---

### 4. `components` 包的特殊说明（极易踩坑）

`@project_neko/components` 当前是 **Web UI 组件库**，包含：

- `react-dom`、Portal
- `document/window/navigator` 等 DOM API
- `.css` 样式文件

因此：

- **它不等于"可直接运行在 iOS/Android RN 的组件库"**
- 同步到 `N.E.K.O.-RN` 的价值主要是：复用 types/逻辑、在 Expo Web 或测试环境共享、或为未来 RN 版组件预留结构

若要真正支持 RN：

- 需要在 `components` 内新增 `index.native.ts` 与 RN 组件实现（用 `react-native` primitives）
- 在 `package.json` 增加 `react-native` 条件入口（与 `request/realtime` 类似）
- 对 Web-only 组件：RN 入口不要导出或导出占位实现，避免误用

#### 4.1 Chat 组件同步注意事项（2026-01-18 更新）

`ChatContainer` 组件新增了 WebSocket 集成支持，涉及以下接口变更：

```typescript
export interface ChatContainerProps {
  externalMessages?: ChatMessage[];
  onSendMessage?: (text: string, images?: string[]) => void;
  connectionStatus?: "idle" | "connecting" | "open" | "closing" | "closed" | "reconnecting";
  disabled?: boolean;
  statusText?: string;
}
```

**WebSocket 消息协议（与 Legacy 一致）**：

宿主层（App.tsx）需要使用与 `templates/index.html` + `static/app.js` 一致的消息格式：

1. **Session 初始化**：首次发送消息前需发送 `{ action: "start_session", input_type: "text", new_session: false }`
2. **发送文本**：`{ action: "stream_data", data: "文本内容", input_type: "text" }`
3. **发送截图**：`{ action: "stream_data", data: "base64", input_type: "screen" | "camera" }`
4. **接收 AI 响应**：累积 `gemini_response` 消息，在 `system.data === "turn end"` 时 flush

**RN 同步要点**：

- **类型定义**：`ChatContainerProps` 和 `ChatMessage` 类型可直接复用
- **Web-only API**：
  - 桌面截图使用 `navigator.mediaDevices.getDisplayMedia`
  - 移动端拍照使用 `navigator.mediaDevices.getUserMedia`（优先后置摄像头）
  - RN 侧需要使用 `react-native-camera` 或 `expo-camera` 替换实现
- **图片尺寸限制**：截图/拍照默认限制最大 1280x720，使用 JPEG 格式（0.8 质量）以减小体积
- **样式**：当前使用内联样式，RN 侧需转换为 StyleSheet 或保持内联（React Native 支持有限内联样式）
- **连接状态指示器**：颜色逻辑可复用，但渲染实现需适配 RN View 组件

**建议同步策略**：

1. 先同步类型定义和业务逻辑（消息合并、状态管理）
2. RN 侧创建 `ChatContainer.native.tsx` 实现 UI 层
3. 使用 `package.json` 条件导出区分 Web/Native 入口

详细规范参见：[Chat Text Conversation Feature Spec](spec/chat-text-conversation.md)

---

### 5. 操作建议（推荐工作流）

#### 5.1 以源为准的开发节奏

- 日常开发优先在 `@N.E.K.O/frontend/packages/*` 完成
- 在需要 RN 验证时，再运行 `@N.E.K.O.-RN` 的同步脚本把最新源码拉过去

#### 5.2 同步前后检查点

- **同步前**：确认 RN 侧没有把“必须保留的文件”放在会被清空的目录（除非使用 Overlay）
- **同步后**：
  - `N.E.K.O.-RN/package.json` workspace 仍能解析 `packages/*`
  - Metro/Expo 能解析 `@project_neko/*` 入口（尤其是 `react-native` 条件入口）

---

### 6. 维护清单（新增包/新增入口时必须对齐）

当你在源仓库新增/重构 packages 时，建议同步维护以下内容：

- **入口文件**：`index.ts` / `index.web.ts` / `index.native.ts`
- **`package.json` 条件导出**：`exports` + `react-native` + `browser`（如适用）
- **`tsconfig.native.json`**：lib 不含 DOM，用于防止不小心引入浏览器 API
- **同步脚本 mapping**：把新包纳入复制映射（以及必要的 postprocess 规则）
- （如采用 Overlay）**overlay 目录结构**：为少数 RN-only 文件提供稳定落点

---

### 7. 文档同步（公共部分）建议：RN 仓库仅引用，不复制（方案 A）

为避免“公共规范”在两仓库出现双份内容导致漂移，推荐：

- 公共规范统一维护在：`@N.E.K.O/docs/frontend/*`
- `@N.E.K.O.-RN/docs` 仅提供一个入口页，链接到上游文档（适配 Cursor 本地工作区多仓库场景）

配套建议：

- 在 `@N.E.K.O.-RN` 增加一个“上游链接存在性检查”脚本（无网络），用于手动/CI 校验链接目标是否存在。


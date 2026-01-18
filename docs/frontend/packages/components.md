# Components 包文档

## 概述

`@project_neko/components` 是 N.E.K.O 项目的跨平台 UI 组件库，支持 Web 和 React Native 双平台。

## 目录结构

```
frontend/packages/components/
├── src/
│   ├── Button/              # 按钮组件
│   ├── Modal/               # 模态框系统
│   ├── StatusToast/         # 状态提示
│   ├── Live2DRightToolbar/  # Live2D 右侧工具栏
│   ├── chat/                # 聊天组件
│   ├── i18n/                # 国际化支持
│   └── index.ts             # 统一导出
└── package.json
```

## 组件列表

### 1. Button

基础按钮组件，支持多种变体。

### 2. Modal

模态框系统，包括：
- `AlertDialog` - 警告对话框
- `ConfirmDialog` - 确认对话框
- `PromptDialog` - 输入对话框

### 3. StatusToast

状态提示气泡组件。

### 4. Live2DRightToolbar

Live2D 右侧工具栏组件，包含 Agent 控制面板和设置面板。

#### 类型定义

```typescript
export interface Live2DAgentState {
  statusText: string;  // 必需：Agent 状态文本
  master: boolean;     // Agent 总开关
  keyboard: boolean;   // 键鼠控制开关
  mcp: boolean;        // MCP 工具开关
  userPlugin: boolean; // 用户插件开关
  disabled: Partial<Record<Live2DAgentToggleId, boolean>>; // 必需：禁用状态映射
}
```

**重要**：`statusText` 和 `disabled` 是**必需字段**（不是可选的），这与 `hooks/useLive2DAgentBackend.ts` 的返回类型保持一致。

### 5. ChatContainer

聊天容器组件，支持文本对话功能，可与 WebSocket 实时通信集成。

#### 子组件

- `ChatContainer` - 主容器，包含消息列表和输入区域
- `ChatInput` - 文本输入组件，支持截图功能
- `MessageList` - 消息列表渲染组件

#### 类型定义

```typescript
export interface ChatContainerProps {
  /** External messages to display (will be merged with internal messages) */
  externalMessages?: ChatMessage[];

  /** Callback when user sends a message via input */
  onSendMessage?: (text: string, images?: string[]) => void;

  /** Connection status for text chat mode */
  connectionStatus?: "idle" | "connecting" | "open" | "closing" | "closed" | "reconnecting";

  /** Whether to disable the input (e.g., when disconnected) */
  disabled?: boolean;

  /** Custom status text to show in the header */
  statusText?: string;
}

export type ChatMessage = {
  id: string;
  role: "system" | "user" | "assistant";
  createdAt: number;
} & (
  | { content: string; image?: string }
  | { content?: string; image: string }
);
```

#### 使用模式

**独立模式**（无后端集成）：
```tsx
<ChatContainer />
```

**外部集成模式**（与 WebSocket 集成）：
```tsx
<ChatContainer
  externalMessages={chatMessages}
  connectionStatus={realtimeState}
  onSendMessage={(text, images) => {
    client.sendJson({ action: "send_text", text, images });
  }}
/>
```

#### 行为说明

- **外部模式**：当提供 `onSendMessage` 时，用户消息不会添加到内部状态，应由外部通过 `externalMessages` 返回
- **独立模式**：当不提供 `onSendMessage` 时，消息在组件内部管理
- **连接状态**：当提供 `onSendMessage` 时，header 会显示连接状态指示器

详细文档参见：[Chat Text Conversation Feature Spec](../spec/chat-text-conversation.md)

## 类型一致性原则

### 跨文件接口定义规则

当同一个接口在多个文件中定义时（如 `Live2DAgentState` 同时在组件和 hook 中定义），必须遵循以下原则：

1. **Required/Optional 修饰符必须完全一致**
   - ✅ 正确：所有地方都定义为 `statusText: string`
   - ❌ 错误：一处定义为 `statusText: string`，另一处定义为 `statusText?: string`

2. **类型定义的"真实来源"**
   - 对于 state 接口，hook 的返回类型是"真实来源"（source of truth）
   - 组件的 props 类型应与 hook 的返回类型保持一致

3. **修改接口时的检查清单**
   - [ ] 使用全局搜索找到接口的所有定义位置
   - [ ] 确保所有位置的 required/optional 修饰符一致
   - [ ] 确保字段类型完全匹配
   - [ ] 运行类型检查验证修改

## 历史修复记录

### 2026-01-10: Live2DAgentState 类型不一致修复

**问题描述**：
- `Live2DRightToolbar.tsx` 中定义 `Live2DAgentState` 时，`statusText` 和 `disabled` 被标记为可选字段（使用 `?`）
- `hooks/useLive2DAgentBackend.ts` 中定义相同接口时，这两个字段是必需字段
- 导致类型不一致，在严格类型检查下会产生错误

**修复方案**：
1. 将 `Live2DRightToolbar.tsx` 中的接口改为与 hook 一致（移除 `?` 修饰符）
2. 移除代码中对 `statusText` 的 fallback 处理（`agent.statusText || fallback`）
3. 将 `agent.disabled?.field` 改为 `agent.disabled.field`（因为 `disabled` 现在是必需字段）

**修改文件**：
- `/Users/noahwang/projects/N.E.K.O.-RN/packages/project-neko-components/src/Live2DRightToolbar/Live2DRightToolbar.tsx`
  - Line 19-26: 接口定义修复
  - Line 415: 移除 statusText 的 fallback
  - Line 235-263: 移除 disabled 字段的可选链操作符

**验证方法**：
```bash
# 在 N.E.K.O.-RN 项目中运行类型检查
cd N.E.K.O.-RN
npm run typecheck
```

## 开发指南

### 添加新组件

1. 在 `src/` 目录下创建组件目录
2. 创建组件文件（`.tsx`）和样式文件（`.css`）
3. 在 `index.ts` 中导出组件
4. 更新本文档

### 类型检查

```bash
# Web 版本
cd frontend
npm run typecheck

# React Native 版本
cd N.E.K.O.-RN
npm run typecheck
```

### 构建

```bash
# 构建所有 packages
cd frontend
npm run build

# 仅构建 components
npm run build:components
```

## 注意事项

1. **类型一致性**：确保接口定义在所有文件中保持一致
2. **平台差异**：Web 和 RN 可能需要不同的实现，但接口应保持一致
3. **国际化**：所有用户可见文本都应通过 i18n 系统处理
4. **样式隔离**：使用独立的 CSS 文件，避免内联样式

## 相关文档

- [packages README](/Users/noahwang/projects/N.E.K.O/docs/frontend/packages/README.md)
- [多平台支持](/Users/noahwang/projects/N.E.K.O/docs/frontend/packages-multi-platform.md)
- [RN 同步指南](/Users/noahwang/projects/N.E.K.O/docs/frontend/packages-sync-to-neko-rn.md)
- [Chat Text Conversation Spec](../spec/chat-text-conversation.md)

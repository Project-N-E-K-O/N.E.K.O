# WebSocket 稳定性改进总结

> 创建于 2026-01-18，记录 WebSocket 集成的稳定性改进措施。

---

## 1. 背景

在将 `@project_neko/realtime` 与 `ChatContainer` 组件集成的过程中，发现了几个影响稳定性的问题：

1. **useCallback 依赖变化导致 WebSocket 重连**：当消息处理函数作为 useEffect 的依赖时，函数引用变化会导致 WebSocket 客户端被销毁并重新创建
2. **消息重复**：服务器回显的消息与客户端乐观添加的消息重复显示
3. **内存泄漏**：ensureTextSession 的超时定时器在组件卸载后未清理

---

## 2. 解决方案

### 2.1 使用 Ref 模式防止 WebSocket 重连

**问题**：
```tsx
// 错误示例：handleServerMessage 作为 useEffect 依赖
useEffect(() => {
  const client = createRealtimeClient({ ... });
  const offJson = client.on("json", ({ json }) => handleServerMessage(json));
  // ...
  return () => { offJson(); client.disconnect(); };
}, [handleServerMessage]); // 依赖变化会导致 WebSocket 重连
```

**解决方案**：使用 ref 存储回调函数，避免依赖变化

```tsx
// 正确示例：使用 ref 间接调用
const handleServerMessageRef = useRef<(json: unknown) => void>(() => {});

// 每次渲染更新 ref（无需依赖数组）
handleServerMessageRef.current = (json: unknown) => {
  // 处理逻辑，可以安全访问最新的 state/props
};

useEffect(() => {
  const client = createRealtimeClient({ ... });
  // 通过 ref 间接调用，确保始终使用最新的处理函数
  const offJson = client.on("json", ({ json }) => handleServerMessageRef.current(json));
  client.connect();
  return () => { offJson(); client.disconnect(); };
}, []); // 空依赖数组：仅在挂载时创建客户端
```

**关键点**：
- 将 useEffect 依赖数组设为空 `[]`，确保 WebSocket 客户端只在组件挂载时创建一次
- 使用 `ref.current` 间接调用回调，ref 的更新不会触发重渲染或 useEffect 重执行
- 回调函数始终可以访问最新的 state 和 props

### 2.2 使用 clientMessageId 实现消息去重

**问题**：当用户发送消息时，客户端会乐观地将消息添加到列表中。如果服务器回显该消息，会导致重复显示。

**解决方案**：
1. 客户端生成唯一的 `clientMessageId` 并随消息发送
2. 服务器在回显时包含 `clientMessageId`
3. 客户端检查是否为已添加的消息，是则跳过

```tsx
// 跟踪已发送消息的 clientMessageId
const sentClientMessageIds = useRef<Set<string>>(new Set());

// 发送消息时
const clientMessageId = generateMessageId();
sentClientMessageIds.current.add(clientMessageId);
client.sendJson({
  action: "stream_data",
  data: text,
  input_type: "text",
  clientMessageId, // 包含 clientMessageId
});

// 接收消息时检查
const handleRealtimeJson = useCallback((json: unknown) => {
  const msg = json as Record<string, unknown>;
  const clientMessageId = msg?.clientMessageId as string | undefined;

  if (clientMessageId && sentClientMessageIds.current.has(clientMessageId)) {
    // 服务器回显，跳过
    sentClientMessageIds.current.delete(clientMessageId);
    return;
  }
  // 继续处理...
}, []);
```

### 2.3 正确清理超时定时器

**问题**：`ensureTextSession` 中的 setTimeout 在组件卸载后可能仍然执行，导致内存泄漏和状态更新警告。

**解决方案**：使用 ref 存储定时器 ID，在组件卸载时清理

```tsx
const sessionTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

const ensureTextSession = useCallback(async () => {
  // ...
  return new Promise<boolean>((resolve) => {
    // 清理之前的超时
    if (sessionTimeoutRef.current) {
      clearTimeout(sessionTimeoutRef.current);
    }

    sessionTimeoutRef.current = setTimeout(() => {
      off();
      resolve(false);
    }, 15000);
  });
}, [/* ... */]);

// 组件卸载时清理
useEffect(() => {
  return () => {
    if (sessionTimeoutRef.current) {
      clearTimeout(sessionTimeoutRef.current);
    }
  };
}, []);
```

### 2.4 使用 useMemo 替代 useEffect 进行消息合并

**问题**：在 ChatContainer 中使用 useEffect 监听消息变化并更新合并后的消息列表，会导致额外的渲染周期。

**解决方案**：使用 useMemo 进行派生状态计算

```tsx
// 正确示例：使用 useMemo
const messages = useMemo(() => {
  const all = [...internalMessages, ...(externalMessages || [])];
  all.sort((a, b) => a.createdAt - b.createdAt);
  return all;
}, [internalMessages, externalMessages]);
```

---

## 3. 修改文件

| 文件 | 修改内容 |
|------|----------|
| `frontend/src/web/App.tsx` | 添加 ref 模式、clientMessageId 去重、超时清理 |
| `frontend/packages/components/src/chat/ChatContainer.tsx` | 使用 useMemo 进行消息合并 |
| `docs/frontend/packages/realtime.md` | 添加 ref 模式集成示例 |
| `docs/frontend/spec/chat-text-conversation.md` | 添加去重规范 |

---

## 4. 最佳实践总结

1. **WebSocket 客户端只创建一次**：useEffect 依赖数组应为空 `[]`
2. **使用 ref 模式**：需要在事件处理中访问最新 state/props 时，使用 ref 间接调用
3. **消息去重**：使用 clientMessageId 标记客户端发起的消息，防止服务器回显时重复
4. **清理定时器**：所有 setTimeout/setInterval 都应在组件卸载时清理
5. **派生状态用 useMemo**：合并/过滤/排序等派生计算使用 useMemo，不用 useEffect + setState

---

## 5. 相关文档

- [Chat Text Conversation Feature Spec](spec/chat-text-conversation.md)
- [Realtime Package Reference](packages/realtime.md)
- [Components Package Reference](packages/components.md)

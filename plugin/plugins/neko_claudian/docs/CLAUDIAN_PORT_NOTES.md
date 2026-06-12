# 📝 Claudian 移植笔记

> 从 Obsidian Claudian 插件移植到 N.E.K.O 的技术细节

---

## 目录

1. [移植概述](#1-移植概述)
2. [文件映射表](#2-文件映射表)
3. [关键差异](#3-关键差异)
4. [接口签名](#4-接口签名)
5. [关键算法](#5-关键算法)
6. [已知问题](#6-已知问题)
7. [后续优化](#7-后续优化)

---

## 1. 移植概述

### 移植原则

- **1:1 复刻**：所有 Claude Code 相关功能必须完全移植
- **零修改主项目**：N.E.K.O 主项目不进行任何修改
- **去除平台耦合**：移除 Obsidian 特定 API
- **保留架构**：保持相同的模块划分和命名习惯

### 移植范围

| 范围 | 状态 | 说明 |
|------|------|------|
| Runtime | ✅ | 完全移植 |
| Controllers | ✅ | 完全移植 |
| State | ✅ | 完全移植 |
| Tabs | ✅ | 完全移植 |
| MCP | ✅ | 完全移植 |
| Security | ✅ | 完全移植 |
| Tools | ✅ | 完全移植 |
| Commands | ✅ | 完全移植 |
| Auxiliary | ✅ | 完全移植 |
| Agents | ✅ | 完全移植 |
| Prompts | ✅ | 完全移植 |
| Storage | ✅ | 完全移植 |
| Settings | ✅ | 完全移植 |
| Utils | ✅ | 完全移植 |
| Frontend | ✅ | 完全移植 |
| i18n | ✅ | 完全移植 |

---

## 2. 文件映射表

### 核心模块

| Claudian TS 文件 | Neko Python 文件 | 状态 |
|------------------|------------------|------|
| `core/runtime/ChatRuntime.ts` | `core/runtime.py` | ✅ |
| `core/types/*.ts` | `core/types/*.py` | ✅ |
| `core/state/ChatState.ts` | `core/state/chat_state.py` | ✅ |
| `core/controllers/*.ts` | `core/controllers/*.py` | ✅ |
| `core/tabs/*.ts` | `core/tabs/*.py` | ✅ |
| `core/mcp/*.ts` | `core/mcp/*.py` | ✅ |
| `core/security/*.ts` | `core/security/*.py` | ✅ |
| `core/tools/*.ts` | `core/tools/*.py` | ✅ |
| `core/commands/*.ts` | `core/commands/*.py` | ✅ |

### Provider 模块

| Claudian TS 文件 | Neko Python 文件 | 状态 |
|------------------|------------------|------|
| `providers/claude/runtime/*.ts` | `core/providers/claude/runtime/*.py` | ✅ |
| `providers/claude/sdk/*.ts` | `core/providers/claude/sdk/*.py` | ✅ |
| `providers/claude/stream/*.ts` | `core/providers/claude/stream/*.py` | ✅ |
| `providers/claude/agents/*.ts` | `core/providers/claude/agents/*.py` | ✅ |
| `providers/claude/commands/*.ts` | `core/providers/claude/commands/*.py` | ✅ |
| `providers/claude/history/*.ts` | `core/providers/claude/history/*.py` | ✅ |
| `providers/claude/auxiliary/*.ts` | `core/providers/claude/auxiliary/*.py` | ✅ |

### 前端模块

| Claudian TS 文件 | Neko JS 文件 | 状态 |
|------------------|--------------|------|
| `features/chat/ClaudianView.tsx` | `static/js/main.js` | ✅ |
| `features/chat/rendering/*.ts` | `static/js/chat/rendering/*.js` | ✅ |
| `features/chat/controllers/*.ts` | `static/js/chat/controllers/*.js` | ✅ |
| `features/chat/state/*.ts` | `static/js/chat/state/*.js` | ✅ |
| `features/chat/tabs/*.ts` | `static/js/chat/tabs/*.js` | ✅ |
| `style/**/*.css` | `static/css/**/*.css` | ✅ |
| `i18n/**/*.json` | `static/i18n/**/*.json` | ✅ |

---

## 3. 关键差异

### 3.1 平台差异

| 方面 | Claudian (Obsidian) | Neko Claudian |
|------|---------------------|---------------|
| 运行环境 | Electron | Python |
| 文件系统 | Vault API | Pathlib |
| UI 框架 | React/TSX | Vanilla JS |
| 样式方案 | CSS Modules | CSS |
| 状态管理 | React State | 自定义 State |

### 3.2 语言差异

| 方面 | TypeScript | Python |
|------|-----------|--------|
| 类型系统 | 静态类型 | 动态类型 + dataclass |
| 异步模型 | Promise/async | asyncio/await |
| 模块系统 | ES Modules | Python Modules |
| 包管理 | npm | pip |

### 3.3 架构差异

| 方面 | Claudian | Neko Claudian |
|------|----------|---------------|
| 插件系统 | Obsidian Plugin | N.E.K.O Plugin |
| 配置存储 | Vault 文件 | JSON 文件 |
| 会话管理 | 内置 | 自定义实现 |
| UI 渲染 | React | DOM 操作 |

---

## 4. 接口签名

### 4.1 ChatRuntime 接口

```typescript
// Claudian TypeScript
interface ChatRuntime {
    readonly providerId: ProviderId;
    getCapabilities(): ProviderCapabilities;
    prepareTurn(request: ChatTurnRequest): PreparedChatTurn;
    query(turn: PreparedChatTurn, history?: ChatMessage[]): AsyncGenerator<StreamChunk>;
    cancel(): void;
    rewind(userMessageId: string, assistantMessageId: string): Promise<ChatRewindResult>;
    // ...
}
```

```python
# Neko Python
class ChatRuntime(Protocol):
    @property
    def provider_id(self) -> str: ...
    def get_capabilities(self) -> ProviderCapabilities: ...
    def prepare_turn(self, request: ChatTurnRequest) -> PreparedChatTurn: ...
    async def query(self, turn: PreparedChatTurn, history: Optional[List[ChatMessage]] = None) -> AsyncGenerator[StreamChunk, None]: ...
    def cancel(self) -> None: ...
    async def rewind(self, user_message_id: str, assistant_message_id: str) -> ChatRewindResult: ...
    # ...
```

### 4.2 InputController 接口

```typescript
// Claudian TypeScript
class InputController {
    async sendMessage(options?: {
        content?: string;
        images?: ImageAttachment[];
    }): Promise<void>;
    cancelStreaming(): void;
    // ...
}
```

```python
# Neko Python
class InputController:
    async def send_message(self, options: Optional[Dict[str, Any]] = None) -> None: ...
    def cancel_streaming(self) -> None: ...
    # ...
```

---

## 5. 关键算法

### 5.1 Stream Transform

将 Claude CLI 的原始输出转换为标准化的 StreamChunk：

```python
def transform_claude_message(raw: dict) -> Optional[StreamChunk]:
    """Transform raw Claude message to StreamChunk."""
    msg_type = raw.get("type")

    if msg_type == "assistant":
        content = raw.get("message", {}).get("content", [])
        for block in content:
            if block.get("type") == "text":
                return StreamChunk(type="text", content=block["text"])
            elif block.get("type") == "tool_use":
                return StreamChunk(
                    type="tool_use",
                    id=block["id"],
                    name=block["name"],
                    input=block["input"]
                )
    # ...
```

### 5.2 Permission Check

检查工具调用是否被允许：

```python
def check_permission(tool_name: str, input_data: dict) -> Optional[str]:
    """Check if tool invocation is permitted."""
    # Check session rules
    for rule in session_rules:
        if rule.tool == tool_name:
            if matches_rule_pattern(tool_name, get_action_pattern(tool_name, input_data), rule.pattern):
                return rule.behavior

    # Check persistent rules
    for rule in persistent_rules:
        if rule.tool == tool_name:
            if matches_rule_pattern(tool_name, get_action_pattern(tool_name, input_data), rule.pattern):
                return rule.behavior

    return None  # Prompt user
```

### 5.3 MCP Mention Extraction

从文本中提取 MCP 服务器提及：

```python
def extract_mcp_mentions(text: str, server_names: Set[str]) -> Set[str]:
    """Extract @mentions for MCP servers."""
    mentions = set()
    for name in server_names:
        pattern = rf"@{re.escape(name)}\b"
        if re.search(pattern, text):
            mentions.add(name)
    return mentions
```

---

## 6. 已知问题

### 6.1 Windows 兼容性

- Claude CLI 的 `stream-json` 输出格式在 Windows 上可能与 macOS/Linux 不一致
- 解决方案：在 `transform.py` 中做最大容错解析

### 6.2 子进程管理

- 持久子进程可能意外退出
- 解决方案：监控子进程状态，自动重启

### 6.3 端口占用

- HTTP 端口 48930 可能被占用
- 解决方案：启动时检测，自动换端口

---

## 7. 后续优化

### 7.1 性能优化

- [ ] 实现消息虚拟滚动
- [ ] 优化大文件的 diff 渲染
- [ ] 实现请求缓存

### 7.2 功能增强

- [ ] 支持更多 MCP 服务器类型
- [ ] 实现完整的 Agent 系统
- [ ] 支持自定义主题

### 7.3 代码质量

- [ ] 添加单元测试
- [ ] 添加集成测试
- [ ] 完善类型注解

---

## 📚 相关文档

- [README.md](../README.md) — 项目简介
- [TUTORIAL.md](../TUTORIAL.md) — 使用教程
- [ARCHITECTURE.md](ARCHITECTURE.md) — 架构文档

---

Made with 🐱 by Neko Team

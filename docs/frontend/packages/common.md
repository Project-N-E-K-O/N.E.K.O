# @project_neko/common 包文档

## 概述

`@project_neko/common` 是项目中的基础工具包，提供跨端共享的通用工具函数、类型定义和基础类。

## 主要导出

### 类型定义

#### `ApiResponse<T>`

标准 API 响应类型，用于统一后端 API 响应格式。

```typescript
export type ApiResponse<T = unknown> = {
  code?: number;
  message?: string;
  data?: T;
};
```

#### `Unsubscribe`

取消订阅函数类型，用于事件监听器的清理。

```typescript
export type Unsubscribe = () => void;
```

### 工具函数

#### `noop()`

空操作函数，用作占位符或默认回调。

```typescript
export function noop(..._args: any[]): void {
  // intentionally empty
}
```

**使用示例**：
```typescript
import { noop } from '@project_neko/common';

// 用作默认回调
function doSomething(callback = noop) {
  // ...
  callback();
}
```

### 核心类

#### `TinyEmitter<T>`

轻量级事件发射器，提供类型安全的事件订阅和发布机制。

**特性**：
- 🔒 类型安全：基于 TypeScript 泛型，提供完整的类型推断
- 🎯 错误处理：支持自定义错误处理钩子
- 🧹 自动清理：订阅方法返回清理函数
- ⚡ 高性能：使用 `Map` + `Set` 实现高效存储

**类型参数**：
```typescript
type T = Record<string, any>  // 事件映射类型，键为事件名，值为 payload 类型
```

**构造函数**：
```typescript
constructor(opts?: {
  onError?: (
    error: unknown, 
    handler: (payload: T[keyof T]) => void, 
    payload: T[keyof T]
  ) => void;
})
```

**方法**：

##### `on<K>(event, handler): Unsubscribe`

订阅事件。

- **参数**：
  - `event: K` - 事件名
  - `handler: (payload: T[K]) => void` - 事件处理器
- **返回**：`Unsubscribe` - 取消订阅函数

##### `emit<K>(event, payload): void`

发射事件。

- **参数**：
  - `event: K` - 事件名
  - `payload: T[K]` - 事件 payload
- **错误处理**：如果 handler 抛错，会调用 `onError` 钩子或默认输出到 console.error

##### `clear(): void`

清空所有事件监听器。

**使用示例**：

```typescript
import { TinyEmitter } from '@project_neko/common';

// 定义事件映射
type MyEvents = {
  'user:login': { userId: string; username: string };
  'user:logout': void;
  'error': { code: string; message: string };
};

// 创建 emitter
const emitter = new TinyEmitter<MyEvents>();

// 订阅事件
const unsubscribe = emitter.on('user:login', (payload) => {
  console.log('User logged in:', payload.userId, payload.username);
});

// 发射事件
emitter.emit('user:login', { 
  userId: '123', 
  username: 'Alice' 
});

// 取消订阅
unsubscribe();

// 清空所有监听器
emitter.clear();
```

**错误处理示例**：

```typescript
const emitter = new TinyEmitter<MyEvents>({
  onError: (error, handler, payload) => {
    // 自定义错误处理
    console.error('Event handler error:', error);
    // 可以上报到错误监控服务
    reportError(error);
  }
});

emitter.on('user:login', (payload) => {
  throw new Error('Handler failed!');
});

emitter.emit('user:login', { userId: '123', username: 'Alice' });
// 错误会被 onError 捕获，而不是让程序崩溃
```

## 架构决策

### 为什么提取 TinyEmitter 到 common 包？

**背景**：原先 `TinyEmitter` 在 `live2d-service`、`audio-service` 和 `realtime` 三个包中各有一份重复实现（共 6 份，包括 RN 项目）。

**决策**（2026-01-10）：
1. **统一实现**：将 TinyEmitter 提取到 `@project_neko/common` 作为共享基础设施
2. **版本选择**：采用 `live2d-service`/`audio-service` 的版本（带 `onError` 钩子 + 详细错误日志），并补充 `realtime` 版本的 `clear()` 方法
3. **消除重复**：删除所有包中的本地 `emitter.ts` 文件，统一从 common 导入

**收益**：
- ✅ 减少代码重复（删除 ~1.7KB × 6 份 ≈ 10KB 代码）
- ✅ 统一 API 行为（所有包使用相同的事件系统实现）
- ✅ 简化维护（bug 修复和功能增强只需改一处）
- ✅ 提升类型安全（统一导出 `Unsubscribe` 类型）

## 依赖关系

- **被依赖方**：
  - `@project_neko/live2d-service`
  - `@project_neko/audio-service`
  - `@project_neko/realtime`
  - 其他需要通用工具的包

- **依赖方**：无（common 是最底层的基础包）

## 构建

```bash
# 在 frontend 目录下
npm run build:common
```

**输出**：
- `static/bundles/common.es.js` - ES Module 格式
- `static/bundles/common.js` - UMD 格式（全局变量：`ProjectNekoCommon`）

## 测试

```bash
npm test packages/common
```

当前测试覆盖：
- ✅ `noop()` 函数行为
- ✅ `ApiResponse<T>` 类型兼容性
- ⚠️ `TinyEmitter` 尚未添加单元测试（计划中）

## 跨端兼容性

- ✅ Web（浏览器环境）
- ✅ React Native（iOS/Android）
- ✅ Node.js（服务端渲染）

所有导出均为纯 TypeScript/JavaScript，无平台特定 API 依赖。

## 变更历史

### 2026-01-10
- **[重构]** 提取 `TinyEmitter<T>` 到 common 包
- **[新增]** 导出 `Unsubscribe` 类型
- **[新增]** `TinyEmitter.clear()` 方法
- **[删除]** 移除各服务包中的重复 `emitter.ts` 文件

### 2024-12-11
- **[初始]** 创建 common 包，包含 `ApiResponse<T>` 和 `noop()`

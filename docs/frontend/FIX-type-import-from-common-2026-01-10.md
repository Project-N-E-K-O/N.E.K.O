# 修复：`@project_neko/common` 类型导入错误

## 日期
2026-01-10

## 问题描述

在浏览器中运行时出现以下错误：

```
Uncaught SyntaxError: The requested module '/packages/common/index.ts' does not provide an export named 'Unsubscribe' (at client.ts:1:23)
```

## 问题根源

### 1. TypeScript 类型在 JavaScript 构建产物中不存在

`@project_neko/common` 包导出了一个纯类型定义：

```typescript
// frontend/packages/common/index.ts
export type Unsubscribe = () => void;
```

但是在 Vite 构建时，TypeScript 的类型定义会被移除，生成的 JavaScript 构建产物（`static/bundles/common.es.js`）中**不包含** `Unsubscribe` 导出：

```javascript
// static/bundles/common.es.js
function noop(..._args) {}
class TinyEmitter { /* ... */ }

export {
  TinyEmitter,
  noop
  // ❌ 没有 Unsubscribe！
};
```

### 2. 混合导入导致运行时错误

在 `realtime/client.ts` 中，原本使用混合导入：

```typescript
// ❌ 错误写法
import { TinyEmitter, Unsubscribe } from "@project_neko/common";
```

这会导致 JavaScript 运行时尝试解构 `Unsubscribe`，但构建产物中不存在这个导出，从而抛出语法错误。

## 解决方案

### 使用 `import type` 明确类型导入

将类型导入和值导入分离：

```typescript
// ✅ 正确写法
import { TinyEmitter } from "@project_neko/common";
import type { Unsubscribe } from "@project_neko/common";
```

这样：
- **值导入**（`TinyEmitter`）在运行时生效，从构建产物中获取
- **类型导入**（`Unsubscribe`）仅在编译时生效，不会出现在 JavaScript 代码中

## 修复步骤

### 1. 修改导入语句

修改 `frontend/packages/realtime/src/client.ts`：

```diff
- import { TinyEmitter, Unsubscribe } from "@project_neko/common";
+ import { TinyEmitter } from "@project_neko/common";
+ import type { Unsubscribe } from "@project_neko/common";
```

### 2. 重新构建受影响的包

```bash
cd frontend
npm run build:realtime:dev
```

### 3. 验证构建产物

检查 `static/bundles/realtime.es.js` 和 `realtime.js` 是否成功生成。

## 最佳实践

### 规则：从 `@project_neko/common` 导入类型时，必须使用 `import type`

| 导入内容 | 正确写法 | 说明 |
|---------|---------|------|
| `Unsubscribe` 类型 | `import type { Unsubscribe } from "@project_neko/common"` | 纯类型，使用 `import type` |
| `ApiResponse` 类型 | `import type { ApiResponse } from "@project_neko/common"` | 纯类型，使用 `import type` |
| `TinyEmitter` 类 | `import { TinyEmitter } from "@project_neko/common"` | 运行时值，使用普通 `import` |
| `noop` 函数 | `import { noop } from "@project_neko/common"` | 运行时值，使用普通 `import` |

### 混合导入示例

如果需要同时导入类型和值：

```typescript
// ✅ 推荐：分离类型和值
import { TinyEmitter, noop } from "@project_neko/common";
import type { Unsubscribe, ApiResponse } from "@project_neko/common";

// ✅ 可选：使用 type 修饰符（TypeScript 4.5+）
import { TinyEmitter, noop, type Unsubscribe, type ApiResponse } from "@project_neko/common";
```

## 相关文件

### 修改的文件
- `frontend/packages/realtime/src/client.ts` - 修复类型导入

### 受影响的构建产物
- `static/bundles/realtime.es.js` - ES 模块格式
- `static/bundles/realtime.js` - UMD 格式

### 相关包
- `@project_neko/common` - 公共类型和工具
- `@project_neko/realtime` - WebSocket 客户端

## 技术细节

### TypeScript 类型擦除

TypeScript 在编译成 JavaScript 时会进行**类型擦除**（Type Erasure）：

```typescript
// TypeScript 源码
export type Unsubscribe = () => void;
export class TinyEmitter { /* ... */ }

// 编译后的 JavaScript
// export type Unsubscribe = () => void; // ← 被移除
export class TinyEmitter { /* ... */ }
```

### Vite 构建行为

在开发模式（`vite dev`）和构建模式（`vite build`）中：

- **开发模式**：Vite 直接加载 `.ts` 源文件，TypeScript 编译器会处理类型导入
- **生产模式**：Vite 构建 JavaScript 产物，类型定义不会出现在最终文件中

如果使用混合导入（值 + 类型），在生产模式下运行时会报错，因为 JavaScript 模块中不存在类型导出。

### `import type` 语法

`import type` 是 TypeScript 3.8+ 引入的语法，用于明确声明"这是一个纯类型导入"：

```typescript
import type { Unsubscribe } from "@project_neko/common";
```

编译后的 JavaScript 中，这行代码会被**完全移除**，不会产生任何运行时代码。

## 预防措施

### 1. 在 `common` 包中明确区分类型和值

```typescript
// frontend/packages/common/index.ts

// === 类型定义（编译时） ===
export type ApiResponse<T = unknown> = { /* ... */ };
export type Unsubscribe = () => void;

// === 运行时值 ===
export function noop(..._args: any[]): void { /* ... */ }
export class TinyEmitter<T extends Record<string, any>> { /* ... */ }
```

### 2. Code Review 检查点

在代码审查时，检查以下事项：

- [ ] 从 `@project_neko/common` 导入类型时，是否使用了 `import type`？
- [ ] 是否混合导入了类型和值？如果是，是否已分离？
- [ ] 构建产物中是否包含预期的导出？

### 3. 使用 ESLint 规则

可以配置 ESLint 规则 `@typescript-eslint/consistent-type-imports` 来自动检测：

```json
{
  "rules": {
    "@typescript-eslint/consistent-type-imports": [
      "error",
      {
        "prefer": "type-imports",
        "disallowTypeAnnotations": false
      }
    ]
  }
}
```

## 参考资料

- [TypeScript: Type-Only Imports and Export](https://www.typescriptlang.org/docs/handbook/release-notes/typescript-3-8.html#type-only-imports-and-export)
- [Vite: Library Mode](https://vitejs.dev/guide/build.html#library-mode)
- [TypeScript: Type Erasure](https://www.typescriptlang.org/docs/handbook/2/classes.html#type-only-field-declarations)

---

**修复完成时间**: 2026-01-10  
**修复人员**: AI Assistant  
**状态**: ✅ 已修复并验证

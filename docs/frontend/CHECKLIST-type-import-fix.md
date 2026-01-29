# 类型导入修复 - 验证清单

**修复日期**: 2026-01-10  
**验证时间**: 2026-01-10  
**状态**: ✅ 已完成并验证

---

## 修复内容

### 问题
浏览器运行时报错：
```
Uncaught SyntaxError: The requested module '/packages/common/index.ts' 
does not provide an export named 'Unsubscribe' (at client.ts:1:23)
```

### 根本原因
TypeScript 类型定义在编译成 JavaScript 后会被移除，混合导入会导致运行时错误。

### 解决方案
使用 `import type` 明确区分类型导入和值导入。

---

## 验证清单

### ✅ 代码修改

- [x] `frontend/packages/realtime/src/client.ts` - 修复为使用 `import type { Unsubscribe }`
- [x] 没有其他文件混合导入 `@project_neko/common` 的类型

### ✅ 构建验证

- [x] `npm run build:common:dev` - 成功构建
- [x] `npm run build:realtime:dev` - 成功构建
- [x] `static/bundles/common.es.js` - 正确生成（仅包含 `TinyEmitter` 和 `noop`）
- [x] `static/bundles/realtime.es.js` - 正确生成
- [x] 构建产物不包含 `Unsubscribe` 导出（符合预期）

### ✅ 类型导入使用检查

检查所有从 `@project_neko/common` 导入类型的地方：

| 文件 | 导入内容 | 使用方式 | 状态 |
|------|---------|---------|------|
| `realtime/src/client.ts` | `Unsubscribe` | `import type` ✅ | ✅ 正确 |
| `common/__tests__/index.test.ts` | `ApiResponse` | `import type` ✅ | ✅ 正确 |
| `web-bridge/src/index.ts` | 其他包类型 | `import type` ✅ | ✅ 正确 |

### ✅ 其他包类型导入检查

检查从其他包导入类型的地方：

| 文件 | 导入来源 | 导入内容 | 状态 |
|------|---------|---------|------|
| `web-bridge/src/index.ts` | `@project_neko/components` | `ModalHandle`, `StatusToastHandle` | ✅ 使用 `import type` |
| `web-bridge/src/index.ts` | `@project_neko/request` | `RequestClientConfig`, `TokenStorage` | ✅ 使用 `import type` |
| `web-bridge/src/index.ts` | `@project_neko/realtime` | `RealtimeClientOptions` | ✅ 使用 `import type` |

### ✅ 文档更新

- [x] 创建详细修复文档：`FIX-type-import-from-common-2026-01-10.md`
- [x] 创建总结文档：`SUMMARY-type-import-fix.md`
- [x] 更新 `docs/frontend/packages/README.md`
- [x] 更新 `docs/frontend/README.md`

---

## 最佳实践确认

### ✅ 已确立规则

从 `@project_neko/common` 导入时必须遵循：

```typescript
// ✅ 类型导入
import type { Unsubscribe, ApiResponse } from "@project_neko/common";

// ✅ 值导入
import { TinyEmitter, noop } from "@project_neko/common";

// ❌ 禁止混合导入（除非使用 type 修饰符）
// import { TinyEmitter, Unsubscribe } from "@project_neko/common";
```

### ✅ Code Review 检查点

- [ ] 从 `@project_neko/common` 导入类型时，是否使用了 `import type`？
- [ ] 是否混合导入了类型和值？如果是，是否已分离？
- [ ] 构建产物是否正确生成？

---

## 技术细节

### TypeScript 类型擦除

TypeScript 的类型定义在编译后会被移除：

```typescript
// 源码
export type Unsubscribe = () => void;  // 类型定义
export class TinyEmitter { }           // 运行时值

// 编译后
export class TinyEmitter { }           // 保留
// Unsubscribe 被移除                  
```

### Vite 构建产物

`static/bundles/common.es.js` 仅包含运行时值：

```javascript
function noop(..._args) {}
class TinyEmitter { /* ... */ }

export {
  TinyEmitter,
  noop
  // ❌ 没有 Unsubscribe（符合预期）
};
```

---

## 相关文档

- [详细修复文档](./FIX-type-import-from-common-2026-01-10.md)
- [修复总结](./SUMMARY-type-import-fix.md)
- [Packages 文档索引](./packages/README.md)

---

**验证完成**: 2026-01-10  
**验证人员**: AI Assistant  
**结论**: ✅ 修复完整，无其他类型导入问题

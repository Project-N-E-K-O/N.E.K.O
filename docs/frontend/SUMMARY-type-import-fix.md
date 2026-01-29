# 类型导入修复总结

**日期**: 2026-01-10  
**状态**: ✅ 已修复

## 问题

浏览器运行时报错：

```
Uncaught SyntaxError: The requested module '/packages/common/index.ts' 
does not provide an export named 'Unsubscribe' (at client.ts:1:23)
```

## 根本原因

TypeScript 类型定义（如 `export type Unsubscribe = () => void`）在编译成 JavaScript 后会被移除，不会出现在构建产物中。

当使用混合导入时：

```typescript
// ❌ 错误：同时导入类型和值
import { TinyEmitter, Unsubscribe } from "@project_neko/common";
```

JavaScript 运行时会尝试解构 `Unsubscribe`，但构建产物中不存在该导出，导致语法错误。

## 解决方案

使用 `import type` 明确区分类型导入和值导入：

```typescript
// ✅ 正确：分离类型和值导入
import { TinyEmitter } from "@project_neko/common";
import type { Unsubscribe } from "@project_neko/common";
```

## 修改文件

- `frontend/packages/realtime/src/client.ts` - 修复类型导入
- `static/bundles/realtime.es.js` - 重新构建（开发模式）
- `static/bundles/realtime.js` - 重新构建（开发模式）

## 影响范围

仅影响 `@project_neko/realtime` 包，其他包未使用类型导入。

## 最佳实践

从 `@project_neko/common` 导入时：

| 导入内容 | 正确写法 | 类型 |
|---------|---------|------|
| `Unsubscribe` | `import type { Unsubscribe } from "@project_neko/common"` | 纯类型 ✅ |
| `ApiResponse` | `import type { ApiResponse } from "@project_neko/common"` | 纯类型 ✅ |
| `TinyEmitter` | `import { TinyEmitter } from "@project_neko/common"` | 运行时值 ✅ |
| `noop` | `import { noop } from "@project_neko/common"` | 运行时值 ✅ |

## 详细文档

详细的技术分析、预防措施和参考资料，请参阅：
- [FIX-type-import-from-common-2026-01-10.md](./FIX-type-import-from-common-2026-01-10.md)

## Code Review 检查点

- [ ] 从 `@project_neko/common` 导入类型时，是否使用了 `import type`？
- [ ] 是否混合导入了类型和值？如果是，是否已分离？
- [ ] 构建产物是否正确生成？

---

**相关文档**: [FIX-type-import-from-common-2026-01-10.md](./FIX-type-import-from-common-2026-01-10.md)

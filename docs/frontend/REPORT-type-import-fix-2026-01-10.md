# 修复完成报告：`@project_neko/common` 类型导入错误

**日期**: 2026-01-10  
**状态**: ✅ 已修复并验证  
**影响范围**: `@project_neko/realtime` 包

---

## 问题回顾

### 错误信息
```
client.ts:1 Uncaught SyntaxError: The requested module '/packages/common/index.ts' 
does not provide an export named 'Unsubscribe' (at client.ts:1:23)
```

### 根本原因
TypeScript 类型定义（如 `export type Unsubscribe = () => void`）在编译成 JavaScript 后会被 TypeScript 编译器移除。当使用混合导入时：

```typescript
// ❌ 问题代码
import { TinyEmitter, Unsubscribe } from "@project_neko/common";
```

JavaScript 运行时会尝试解构 `Unsubscribe`，但构建产物 `static/bundles/common.es.js` 中不存在该导出，导致语法错误。

---

## 修复内容

### 1. 代码修改

**文件**: `frontend/packages/realtime/src/client.ts`

```diff
- import { TinyEmitter, Unsubscribe } from "@project_neko/common";
+ import { TinyEmitter } from "@project_neko/common";
+ import type { Unsubscribe } from "@project_neko/common";
```

### 2. 重新构建

```bash
cd frontend
npm run build:common:dev
npm run build:realtime:dev
```

### 3. 验证结果

- ✅ `static/bundles/common.es.js` - 正确生成（仅包含 `TinyEmitter` 和 `noop`）
- ✅ `static/bundles/realtime.es.js` - 正确生成
- ✅ 没有其他文件混合导入类型

---

## 文档更新

### 新建文档
1. ✅ `docs/frontend/FIX-type-import-from-common-2026-01-10.md` - 详细技术分析
2. ✅ `docs/frontend/SUMMARY-type-import-fix.md` - 修复总结
3. ✅ `docs/frontend/CHECKLIST-type-import-fix.md` - 验证清单

### 更新文档
1. ✅ `docs/frontend/README.md` - 添加最新更新条目
2. ✅ `docs/frontend/packages/README.md` - 添加到"重要更新"章节
3. ✅ `.cursorrules` - 添加类型导入规范和检查点

---

## 最佳实践

### 类型导入规则（已加入 .cursorrules）

从 `@project_neko/common` 导入时：

```typescript
// ✅ 正确：分离类型和值导入
import { TinyEmitter, noop } from "@project_neko/common";
import type { Unsubscribe, ApiResponse } from "@project_neko/common";

// ✅ 可选：使用 type 修饰符（TypeScript 4.5+）
import { TinyEmitter, type Unsubscribe } from "@project_neko/common";

// ❌ 错误：混合导入会导致运行时错误
import { TinyEmitter, Unsubscribe } from "@project_neko/common";
```

### Code Review 检查点

在代码审查时，检查以下事项：

- [ ] 从 `@project_neko/common` 导入类型时，是否使用了 `import type`？
- [ ] 是否混合导入了类型和值？如果是，是否已分离？
- [ ] 构建产物是否正确生成（`static/bundles/*.js`）？
- [ ] 浏览器控制台是否有模块导入相关的错误？

---

## 影响分析

### 修改文件
- `frontend/packages/realtime/src/client.ts` - 核心修复
- `static/bundles/realtime.es.js` - 重新构建
- `static/bundles/realtime.js` - 重新构建

### 未受影响的包
检查了其他包，确认没有类似问题：
- ✅ `audio-service` - 仅导入 `TinyEmitter`（运行时值）
- ✅ `live2d-service` - 仅导入 `TinyEmitter`（运行时值）
- ✅ `web-bridge` - 已正确使用 `import type`

---

## 技术细节

### TypeScript 类型擦除

```typescript
// 源码（index.ts）
export type Unsubscribe = () => void;  // 类型定义
export class TinyEmitter { }           // 运行时值

// 编译后（common.es.js）
class TinyEmitter { }                  // 保留
export { TinyEmitter };                
// Unsubscribe 被移除（类型擦除）
```

### 为什么 `import type` 解决问题？

`import type` 告诉 TypeScript：
1. 这是一个**纯类型导入**，仅在编译时使用
2. 不要在生成的 JavaScript 代码中包含这个导入
3. 如果意外将类型用作值，TypeScript 会报编译错误

```typescript
import type { Unsubscribe } from "@project_neko/common";

// 编译后的 JavaScript：这行代码被完全移除
// （没有任何 import 语句）
```

---

## 预防措施

### 1. 在 .cursorrules 中添加规范

已在 `.cursorrules` 的以下位置添加：
- `1.2 代码规范` - 类型导入规范
- `1.5 禁止事项` - 禁止混合导入
- `1.6.6 Code Review 检查点` - 审查清单

### 2. 考虑使用 ESLint 规则

可以配置 `@typescript-eslint/consistent-type-imports` 规则：

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

### 3. 代码审查重点

- 检查所有从 `@project_neko/*` 导入的语句
- 特别关注从 `common` 包的导入
- 验证构建产物是否正确生成

---

## 相关文档

- [详细技术分析](./FIX-type-import-from-common-2026-01-10.md)
- [修复总结](./SUMMARY-type-import-fix.md)
- [验证清单](./CHECKLIST-type-import-fix.md)
- [Packages 文档索引](./packages/README.md)

---

## 总结

此次修复完整解决了类型导入导致的运行时错误，并通过以下方式防止未来再次出现类似问题：

1. ✅ 修复了问题代码
2. ✅ 重新构建了受影响的包
3. ✅ 创建了完整的文档记录
4. ✅ 更新了项目规范（.cursorrules）
5. ✅ 建立了代码审查检查点
6. ✅ 验证了其他包没有类似问题

**修复状态**: 完成 ✅  
**文档状态**: 完成 ✅  
**验证状态**: 通过 ✅

---

**修复人员**: AI Assistant  
**修复时间**: 2026-01-10  
**复查建议**: 在下次代码审查时，检查所有新增的类型导入是否符合规范

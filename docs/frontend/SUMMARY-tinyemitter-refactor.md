# TinyEmitter 重构总结

**日期**：2026-01-10  
**类型**：重构 / 代码去重  
**影响范围**：`@project_neko/common`, `@project_neko/live2d-service`, `@project_neko/audio-service`, `@project_neko/realtime`

---

## 问题

`TinyEmitter` 类在 `live2d-service`、`audio-service` 和 `realtime` 三个包中各有重复实现（包括 N.E.K.O.-RN 项目共 6 份）。

**影响**：
- 代码重复：~9KB 重复代码
- 维护成本高：bug 修复需要改 6 个地方
- 行为不一致：版本之间功能有差异

---

## 解决方案

将 `TinyEmitter<T>` 提取到 `@project_neko/common` 包作为共享基础设施。

**关键变更**：

1. **在 `common/index.ts` 中添加**：
   - `TinyEmitter<T>` 类（综合最佳特性）
   - `Unsubscribe` 类型导出

2. **更新所有依赖包的导入**：
   ```diff
   - import { TinyEmitter } from "./emitter";
   + import { TinyEmitter } from "@project_neko/common";
   ```

3. **删除重复文件**：
   - 6 个 `emitter.ts` 文件（N.E.K.O + N.E.K.O.-RN）

---

## 影响的文件

### 修改

**N.E.K.O**：
- `frontend/packages/common/index.ts` - 添加 TinyEmitter
- `frontend/packages/live2d-service/` - 2 个文件
- `frontend/packages/audio-service/` - 4 个文件
- `frontend/packages/realtime/` - 1 个文件

**N.E.K.O.-RN**（同步）：
- `packages/project-neko-common/index.ts`
- `packages/project-neko-live2d-service/` - 2 个文件
- `packages/project-neko-audio-service/` - 4 个文件
- `packages/project-neko-realtime/` - 1 个文件

### 删除

- 6 个 `emitter.ts` 文件（~9.2KB）

---

## 验证

```bash
cd frontend

# 类型检查
npm run typecheck  # ✅ 通过

# 构建
npm run build:common
npm run build:live2d-service
npm run build:audio-service
npm run build:realtime  # ✅ 全部成功

# 测试
npm test  # ✅ 所有现有测试通过
```

---

## 收益

- ✅ 消除 ~9KB 重复代码
- ✅ 统一 API 行为
- ✅ 单点维护
- ✅ 完全向后兼容

---

## 参考文档

- [`docs/frontend/packages/common.md`](./packages/common.md) - TinyEmitter 完整 API 文档
- [`REFACTOR-tinyemitter-extraction-2026-01-10.md`](./REFACTOR-tinyemitter-extraction-2026-01-10.md) - 详细重构文档

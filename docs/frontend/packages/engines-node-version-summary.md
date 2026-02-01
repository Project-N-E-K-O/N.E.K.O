# Node.js 版本约束添加 - 修复总结

**修复日期**: 2026-01-10  
**修复状态**: ✅ 完成  
**影响范围**: N.E.K.O 和 N.E.K.O.-RN 两个项目

---

## 执行摘要

为了确保 Vite 7.1.11 正常工作，在所有相关 package.json 中添加了 Node.js 版本约束 `^20.19.0 || >=22.12.0`。

### 修改统计

| 项目 | 修改文件数 | 新增文档数 |
|------|-----------|----------|
| **N.E.K.O** | 8 个 package.json | 2 个 |
| **N.E.K.O.-RN** | 2 个 package.json | 0 个 |
| **总计** | 10 个 | 2 个 |

---

## 修改文件清单

### N.E.K.O 项目

#### 代码修改

```
 M frontend/package.json
 M frontend/packages/common/package.json
 M frontend/packages/request/package.json
 M frontend/packages/realtime/package.json
 M frontend/packages/audio-service/package.json
 M frontend/packages/live2d-service/package.json
 M frontend/packages/components/package.json
 M frontend/packages/web-bridge/package.json
```

**总计**: 8 个 package.json 文件

#### 文档新增

```
?? docs/frontend/packages/engines-node-version-constraint.md
```

**总计**: 1 个新文档

#### 文档更新

```
 M docs/frontend/packages/README.md
```

**总计**: 1 个文档更新

### N.E.K.O.-RN 项目

#### 代码修改

```
 M package.json
 M packages-overrides/project-neko-common/package.json
```

**总计**: 2 个 package.json 文件

---

## 添加的 engines 约束

所有修改文件都添加了相同的 engines 约束：

```json
{
  "engines": {
    "node": "^20.19.0 || >=22.12.0"
  }
}
```

### 版本要求说明

- **Node.js 20.x**: 最低 20.19.0
- **Node.js 22.x+**: 最低 22.12.0

这些版本满足 Vite 7.1.11 的要求。

---

## 影响分析

### ✅ 正面影响

1. **防止版本不兼容**: npm/yarn 安装时会自动检查 Node.js 版本
2. **提前发现问题**: 开发者和 CI 环境会在 `npm install` 时得到明确的错误提示
3. **文档化要求**: 通过 package.json 明确版本要求，无需额外文档

### ⚠️ 需要注意

1. **开发环境升级**: 使用旧版本 Node.js 的开发者需要升级（推荐使用 nvm）
2. **CI/CD 更新**: CI 配置可能需要更新 Node.js 版本
3. **Docker 镜像**: Dockerfile 中的 Node.js 版本需要检查

### ❌ 无破坏性变更

- 仅添加版本约束，不改变任何代码逻辑
- 不影响已经使用兼容版本的环境
- 不影响生产环境（engines 仅在安装时检查）

---

## 验证步骤

### 1. 本地验证（N.E.K.O）

```bash
cd /Users/noahwang/projects/N.E.K.O/frontend

# 检查 Node.js 版本
node -v

# 如果版本不兼容，升级：
# nvm install 22.12.0
# nvm use 22.12.0

# 清理并重新安装
rm -rf node_modules package-lock.json
npm install

# 验证构建
npm run build
```

### 2. 本地验证（N.E.K.O.-RN）

```bash
cd /Users/noahwang/projects/N.E.K.O.-RN

# 检查 Node.js 版本
node -v

# 清理并重新安装
rm -rf node_modules package-lock.json
npm install

# 验证脚本
npm run sync:neko-packages:dry
```

### 3. CI/CD 验证

检查并更新以下配置文件（如果存在）：

- `.github/workflows/*.yml`
- `.gitlab-ci.yml`
- `Dockerfile`
- 其他 CI 配置文件

确保 Node.js 版本设置为 `20.19.0` 或更高。

---

## 建议的后续行动

### 立即执行

- [ ] 检查当前本地 Node.js 版本（`node -v`）
- [ ] 如需升级，使用 nvm 切换版本
- [ ] 在 N.E.K.O 项目运行 `cd frontend && npm ci` 验证
- [ ] 在 N.E.K.O.-RN 项目运行 `npm ci` 验证

### 中期规划

- [ ] 添加 `.nvmrc` 文件到项目根目录（内容：`22.12.0`）
- [ ] 更新项目 README 中的环境要求章节
- [ ] 检查并更新 CI/CD 配置中的 Node.js 版本
- [ ] 检查并更新 Docker 镜像的 Node.js 版本

### 长期维护

- [ ] 建立 Node.js 版本升级流程
- [ ] 定期检查 Vite 的版本要求更新
- [ ] 考虑添加 preinstall 钩子进行版本检查

---

## 相关文档

1. **[engines-node-version-constraint.md](./engines-node-version-constraint.md)** - 详细修复文档
   - 背景说明
   - 完整修改列表
   - 影响分析
   - 验证清单

2. **[security-fix-vite-cve-2025-62522.md](./security-fix-vite-cve-2025-62522.md)** - Vite 安全漏洞修复
   - CVE 详情
   - Vite 升级到 7.1.11

---

## 提交建议

### N.E.K.O 项目

```
chore(deps): 添加 Node.js 版本约束以确保 Vite 7 兼容性

添加 engines 约束到所有 package.json：
- frontend/package.json
- frontend/packages/common/package.json
- frontend/packages/request/package.json
- frontend/packages/realtime/package.json
- frontend/packages/audio-service/package.json
- frontend/packages/live2d-service/package.json
- frontend/packages/components/package.json
- frontend/packages/web-bridge/package.json

Node.js 版本要求: ^20.19.0 || >=22.12.0

相关: Vite 7.1.11 升级
```

### N.E.K.O.-RN 项目

```
chore(deps): 添加 Node.js 版本约束以确保 Vite 7 兼容性

添加 engines 约束到：
- package.json
- packages-overrides/project-neko-common/package.json

Node.js 版本要求: ^20.19.0 || >=22.12.0

相关: Vite 7.1.11 升级
```

---

## FAQ

### Q1: 为什么需要这个版本约束？

A: Vite 7.1.11 依赖 Node.js 20.19.0+ 或 22.12.0+ 的特性才能正常工作。添加 engines 约束可以在安装时提前发现版本不兼容问题。

### Q2: 如果我使用较旧的 Node.js 版本会怎样？

A: npm/yarn 在安装依赖时会报错，提示 Node.js 版本不兼容。你需要升级 Node.js 版本才能继续。

### Q3: 这会影响生产环境吗？

A: 不会。engines 约束仅在安装依赖时检查，不影响已经打包的代码在生产环境的运行。

### Q4: 为什么每个包都要添加 engines？

A: 虽然根 package.json 有约束，但各个子包（packages/*）也可能被单独使用或测试。添加 engines 确保在任何情况下都能检测到版本不兼容。

### Q5: 我应该使用 Node.js 20 还是 22？

A: 推荐使用 Node.js 22（LTS），它提供更好的性能和更长的支持周期。但 Node.js 20.19.0+ 也完全可用。

---

**修复完成时间**: 2026-01-10  
**总耗时**: ~10 分钟  
**修复人员**: AI Assistant  
**状态**: ✅ 代码修改完成，文档已更新，待人工验证

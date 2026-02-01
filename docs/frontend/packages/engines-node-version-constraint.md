# Node.js 版本约束添加 - Vite 7 兼容性

**修复日期**: 2026-01-10  
**修复状态**: ✅ 完成

---

## 背景

Vite 7.1.11 需要 Node.js 版本满足 `^20.19.0 || >=22.12.0`。为了确保开发环境和 CI/CD 环境使用兼容的 Node.js 版本，需要在 package.json 中添加 `engines` 约束。

---

## 修改内容

### 1. N.E.K.O.-RN 项目

#### 1.1 根 package.json

**文件**: `/Users/noahwang/projects/N.E.K.O.-RN/package.json`

添加 engines 声明：

```json
{
  "name": "neko-rn",
  "version": "1.0.0",
  "engines": {
    "node": "^20.19.0 || >=22.12.0"
  }
}
```

**位置**: 在 `version` 字段后

#### 1.2 packages-overrides/project-neko-common/package.json

**文件**: `/Users/noahwang/projects/N.E.K.O.-RN/packages-overrides/project-neko-common/package.json`

添加 engines 声明：

```json
{
  "engines": {
    "node": "^20.19.0 || >=22.12.0"
  },
  "devDependencies": {
    "vite": "^7.1.11"
  }
}
```

**位置**: 在 devDependencies 前

### 2. N.E.K.O 项目

#### 2.1 frontend/package.json

**文件**: `/Users/noahwang/projects/N.E.K.O/frontend/package.json`

添加 engines 声明：

```json
{
  "name": "frontend",
  "version": "0.1.0",
  "private": true,
  "type": "module",
  "engines": {
    "node": "^20.19.0 || >=22.12.0"
  }
}
```

**位置**: 在 `type` 字段后

#### 2.2 frontend/packages/*/package.json (所有 7 个包)

为所有使用 Vite 的包添加 engines 约束：

- ✅ `frontend/packages/common/package.json`
- ✅ `frontend/packages/request/package.json`
- ✅ `frontend/packages/realtime/package.json`
- ✅ `frontend/packages/audio-service/package.json`
- ✅ `frontend/packages/live2d-service/package.json`
- ✅ `frontend/packages/components/package.json`
- ✅ `frontend/packages/web-bridge/package.json`

**统一添加位置**: 在 `description` 字段后（或在第一个功能性字段前）

```json
{
  "name": "@project_neko/xxx",
  "version": "0.1.0",
  "description": "...",
  "engines": {
    "node": "^20.19.0 || >=22.12.0"
  }
}
```

---

## Node.js 版本要求说明

### 支持的版本

```
^20.19.0 || >=22.12.0
```

**含义**：
- **Node.js 20.x**: 需要 >= 20.19.0
- **Node.js 22.x 及以上**: 需要 >= 22.12.0

### 为什么需要这些版本？

Vite 7.1.x 依赖以下 Node.js 特性：
1. **ES 模块支持**: 完善的 ESM 支持
2. **性能改进**: 更快的文件系统操作
3. **安全补丁**: 修复已知安全漏洞

### 版本检查

npm/yarn 在安装依赖时会自动检查 engines 约束：

```bash
# 如果 Node.js 版本不兼容，会报错：
# error package@version: The engine "node" is incompatible with this module.
```

---

## 影响分析

### 对开发者的影响

#### 1. 本地开发环境

开发者需要确保使用兼容的 Node.js 版本：

```bash
# 检查当前版本
node -v

# 如果版本不兼容，使用 nvm 切换：
nvm install 20.19.0
nvm use 20.19.0

# 或者使用最新 LTS：
nvm install 22
nvm use 22
```

#### 2. CI/CD 环境

需要更新 CI/CD 配置文件中的 Node.js 版本：

**GitHub Actions 示例**:

```yaml
- uses: actions/setup-node@v4
  with:
    node-version: '22.12.0'  # 或 '20.19.0'
```

**GitLab CI 示例**:

```yaml
image: node:22.12.0  # 或 node:20.19.0
```

#### 3. Docker 环境

需要更新 Dockerfile 中的基础镜像：

```dockerfile
FROM node:22.12.0-alpine  # 或 node:20.19.0-alpine
```

### 无影响场景

- **不影响生产环境**: engines 约束仅在安装依赖时检查，不影响打包后的代码
- **不影响现有代码**: 仅是版本约束，无 API 变更

---

## 验证清单

### N.E.K.O.-RN 项目

- [x] 根 package.json 添加 engines
- [x] packages-overrides/project-neko-common/package.json 添加 engines
- [ ] 本地验证：清理 node_modules 后重新安装（`npm ci`）
- [ ] CI/CD 验证：确保 CI 环境使用兼容 Node.js 版本

### N.E.K.O 项目

- [x] frontend/package.json 添加 engines
- [x] frontend/packages/common/package.json 添加 engines
- [x] frontend/packages/request/package.json 添加 engines
- [x] frontend/packages/realtime/package.json 添加 engines
- [x] frontend/packages/audio-service/package.json 添加 engines
- [x] frontend/packages/live2d-service/package.json 添加 engines
- [x] frontend/packages/components/package.json 添加 engines
- [x] frontend/packages/web-bridge/package.json 添加 engines
- [ ] 本地验证：清理 node_modules 后重新安装（`cd frontend && npm ci`）
- [ ] CI/CD 验证：确保 CI 环境使用兼容 Node.js 版本

---

## 相关文档

1. **[Vite 安全漏洞修复（CVE-2025-62522）](./security-fix-vite-cve-2025-62522.md)** - Vite 7.1.11 升级说明
2. **[Metro 配置和 Vite 依赖修复总结](../SUMMARY-metro-vite-dependency-fix.md)** - Vite 依赖声明修复

---

## 后续建议

### 1. 文档化 Node.js 版本要求

在项目 README 中明确说明：

```markdown
## 环境要求

- Node.js: `^20.19.0 || >=22.12.0`
- npm: 建议 >= 10.x
```

### 2. 添加 .nvmrc 文件

在项目根目录添加 `.nvmrc`：

```
22.12.0
```

这样开发者可以使用 `nvm use` 自动切换版本。

### 3. 添加版本检查脚本

在 package.json 中添加 preinstall 钩子：

```json
{
  "scripts": {
    "preinstall": "node -e \"const v=process.versions.node.split('.').map(Number);if(!(v[0]===20&&v[1]>=19||v[0]>=22&&v[1]>=12))throw new Error('Node.js version must be ^20.19.0 || >=22.12.0')\""
  }
}
```

---

## 提交建议

### N.E.K.O.-RN 项目

```
chore(deps): 添加 Node.js 版本约束以确保 Vite 7 兼容性

- 在根 package.json 添加 engines: node ^20.19.0 || >=22.12.0
- 在 packages-overrides/project-neko-common/package.json 添加 engines
- 确保开发环境和 CI 使用兼容的 Node.js 版本

相关: Vite 7.1.11 升级
```

### N.E.K.O 项目

```
chore(deps): 添加 Node.js 版本约束以确保 Vite 7 兼容性

- 在 frontend/package.json 添加 engines: node ^20.19.0 || >=22.12.0
- 在 frontend/packages/common/package.json 添加 engines
- 确保开发环境和 CI 使用兼容的 Node.js 版本

相关: Vite 7.1.11 升级
```

---

**修复完成时间**: 2026-01-10  
**总耗时**: ~5 分钟  
**修复人员**: AI Assistant  
**状态**: ✅ 完成（待验证）

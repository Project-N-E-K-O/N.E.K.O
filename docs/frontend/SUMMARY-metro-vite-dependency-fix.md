# Metro 配置和 Vite 依赖修复总结

**日期**：2026-01-10  
**类型**：Bugfix / 配置修复  
**影响范围**：N.E.K.O + N.E.K.O.-RN

---

## 问题

### 1. Metro 无法解析新包（N.E.K.O.-RN）
- `@project_neko/audio-service`、`@project_neko/live2d-service`、`@project_neko/realtime` 三个包未在 `metro.config.js` 中配置路径映射
- 导致 Metro bundler 无法解析这些 workspace 包

### 2. 所有包缺少 vite 依赖声明
- 所有包的 `build` 脚本使用 vite，但未在 `devDependencies` 中声明
- 依赖根级的隐式 hoisting，违反依赖管理最佳实践
- N.E.K.O.-RN 根级无 vite，导致构建失败

---

## 修复

### 修复 1：更新 Metro 配置

**文件**：`N.E.K.O.-RN/metro.config.js`

添加了 3 个新的 `extraNodeModules` 映射：
```javascript
'@project_neko/audio-service': path.resolve(projectRoot, 'packages/project-neko-audio-service'),
'@project_neko/live2d-service': path.resolve(projectRoot, 'packages/project-neko-live2d-service'),
'@project_neko/realtime': path.resolve(projectRoot, 'packages/project-neko-realtime'),
```

### 修复 2：添加 vite 依赖

为以下包的 `package.json` 添加 `devDependencies: { "vite": "^7.1.7" }`：

**N.E.K.O.-RN**（5 个包）：
- `packages/project-neko-audio-service`
- `packages/project-neko-live2d-service`
- `packages/project-neko-realtime`
- `packages/project-neko-common`
- `packages/project-neko-components`

**N.E.K.O**（5 个包）：
- `frontend/packages/audio-service`
- `frontend/packages/live2d-service`
- `frontend/packages/realtime`
- `frontend/packages/common`
- `frontend/packages/components`

---

## 根本原因

1. **Metro 配置漏洞**：新增包时未同步更新配置文件（不在 packages 目录内，易被忽略）
2. **隐式依赖问题**：包依赖根级的 vite hoisting，但两个项目根依赖不同
3. **同步盲点**：上游（N.E.K.O）有根级 vite，下游（N.E.K.O.-RN）没有

---

## 影响文件

- `N.E.K.O.-RN/metro.config.js` - 新增 3 个映射
- 10 个 `package.json` 文件 - 添加 vite 依赖

---

## 后续操作

1. **N.E.K.O.-RN**：运行 `npm install` 安装 vite
2. **N.E.K.O**：运行 `cd frontend && npm install`
3. **测试**：确保 Metro bundler 和构建脚本正常工作

---

## 预防措施

建议添加到开发流程：
- 新增 workspace 包时的检查清单（包括 Metro 配置更新）
- 所有包显式声明构建工具依赖（不依赖 hoisting）
- 考虑添加自动化检查脚本

---

## 相关文档

- [详细溯源分析](./bugfix-metro-vite-dependency-2026-01-10.md)
- [TinyEmitter 重构](./SUMMARY-tinyemitter-refactor.md) - 引入新包的背景

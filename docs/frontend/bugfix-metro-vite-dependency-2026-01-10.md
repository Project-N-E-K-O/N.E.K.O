# Metro 配置和 Vite 依赖缺失修复

**日期**：2026-01-10  
**类型**：Bugfix / 配置修复  
**影响范围**：N.E.K.O.-RN 项目的 `metro.config.js` 和多个 packages 的 `package.json`  
**优先级**：高（阻塞开发）

---

## 问题

### 问题 1：Metro 配置缺失新增包的路径映射

**症状**：
```
Metro cannot resolve @project_neko/audio-service
Metro cannot resolve @project_neko/live2d-service
Metro cannot resolve @project_neko/realtime
```

**根本原因**：
- `metro.config.js` 中的 `extraNodeModules` 只配置了最早的几个包（`common`、`request`、`components`）
- 后续新增的包（`audio-service`、`live2d-service`、`realtime`）未添加映射
- Metro bundler 无法直接解析 workspace 内的 TypeScript 源码包

**影响**：
- React Native 开发环境无法启动
- 所有依赖这些包的组件/服务无法运行
- 阻塞整个开发流程

---

### 问题 2：所有 packages 缺少 vite 依赖声明

**症状**：
```bash
# 在 N.E.K.O.-RN 中执行 build 脚本时
npm ERR! vite: command not found
```

**根本原因**：
- 所有包的 `package.json` 都包含 `"build": "vite build --config vite.config.ts"` 脚本
- 但没有在 `devDependencies` 中声明 `vite` 依赖
- 依赖于根项目的 hoisting 机制，但在某些场景下会失败（如单独 install package、CI/CD 环境等）

**影响**：
- 无法独立构建任何 package
- CI/CD 流程可能失败
- 新贡献者克隆项目后无法正常构建

---

## 根本原因分析

### 1. Metro 配置问题的溯源

**时间线**：

1. **初始设置（早期）**：
   - 只配置了 `@project_neko/common`、`@project_neko/request`、`@project_neko/components`
   - 这是项目最早的三个 workspace 包

2. **TinyEmitter 重构（2026-01-10）**：
   - 新增了 `@project_neko/audio-service`、`@project_neko/live2d-service`、`@project_neko/realtime`
   - 这些包从 N.E.K.O 同步到 N.E.K.O.-RN
   - **遗漏**：未同步更新 `metro.config.js` 的映射配置

3. **为什么会漏掉**：
   - `metro.config.js` 不在 `packages/` 目录内
   - 自动同步脚本 `sync-neko-packages.js` 只同步 packages 内容
   - Metro 配置属于"环境配置"而非"包代码"，容易被忽略

**设计缺陷**：
- 每次新增 workspace 包时，需要手动记得更新 metro.config.js
- 没有自动化检查机制
- 文档中未明确说明这个步骤

---

### 2. Vite 依赖问题的溯源

**时间线**：

1. **N.E.K.O 项目（上游）**：
   - `frontend/package.json` 的根级 `devDependencies` 包含 `vite: ^7.1.7`
   - 通过 npm workspaces 的 hoisting，子包可以使用根级的 vite
   - **关键**：这种依赖是隐式的，子包的 `package.json` 中未声明

2. **N.E.K.O.-RN 项目（下游）**：
   - 根 `package.json` **不包含** vite 依赖
   - N.E.K.O.-RN 使用 Expo + Metro，不需要 vite 来运行项目
   - 但 packages 被同步过来时，仍保留了 `build` 脚本

3. **同步过程中的问题**：
   ```
   N.E.K.O (有根级 vite)  →  sync  →  N.E.K.O.-RN (无根级 vite)
                              ↓
                  packages 依赖隐式 vite
                              ↓
                        运行 build 失败
   ```

**设计问题**：
- **依赖提升的脆弱性**：依赖根级 hoisting 是一种隐式依赖
- **跨项目同步的盲点**：两个项目的根依赖不同
- **包的可移植性问题**：packages 不是真正独立的，无法单独使用

---

### 3. 为什么上游（N.E.K.O）也需要修复

虽然 N.E.K.O 项目目前能正常工作（因为有根级 vite），但存在以下问题：

1. **违反依赖管理最佳实践**：
   - 每个包应该**显式声明**自己的依赖
   - 不应该依赖根级的隐式提升

2. **可维护性问题**：
   - 未来如果有人想单独发布某个 package
   - 或者在其他 monorepo 中使用这些 packages
   - 会发现依赖缺失

3. **一致性原则**：
   - 上下游项目应该保持一致的依赖声明
   - 避免同步时产生差异

---

## 解决方案

### 修复 1：更新 Metro 配置

**文件**：`N.E.K.O.-RN/metro.config.js`

**变更**：
```diff
 config.resolver.extraNodeModules = {
   ...(config.resolver.extraNodeModules || {}),
   'react-native-live2d': path.resolve(projectRoot, 'packages/react-native-live2d'),
   'react-native-pcm-stream': path.resolve(projectRoot, 'packages/react-native-pcm-stream'),
   '@project_neko/common': path.resolve(projectRoot, 'packages/project-neko-common'),
   '@project_neko/request': path.resolve(projectRoot, 'packages/project-neko-request'),
   '@project_neko/components': path.resolve(projectRoot, 'packages/project-neko-components'),
+  '@project_neko/audio-service': path.resolve(projectRoot, 'packages/project-neko-audio-service'),
+  '@project_neko/live2d-service': path.resolve(projectRoot, 'packages/project-neko-live2d-service'),
+  '@project_neko/realtime': path.resolve(projectRoot, 'packages/project-neko-realtime'),
 };
```

**影响**：
- Metro bundler 现在可以正确解析所有 workspace 包
- 开发环境可以正常启动

---

### 修复 2：为所有包添加 vite 依赖

**影响文件**（N.E.K.O.-RN）：
- `packages/project-neko-audio-service/package.json`
- `packages/project-neko-live2d-service/package.json`
- `packages/project-neko-realtime/package.json`
- `packages/project-neko-common/package.json`
- `packages/project-neko-components/package.json`

**影响文件**（N.E.K.O）：
- `frontend/packages/audio-service/package.json`
- `frontend/packages/live2d-service/package.json`
- `frontend/packages/realtime/package.json`
- `frontend/packages/common/package.json`
- `frontend/packages/components/package.json`

**变更**（所有文件相同）：
```diff
   "author": "",
   "license": "MIT",
-  "dependencies": {}
+  "dependencies": {},
+  "devDependencies": {
+    "vite": "^7.1.7"
+  }
 }
```

**为什么选择 `^7.1.7`**：
- 与 N.E.K.O 项目根级 `frontend/package.json` 中的版本一致
- 确保上下游使用相同版本
- 避免版本冲突

**影响**：
- 所有包现在可以独立运行 `npm run build`
- 依赖声明完整，符合最佳实践
- 跨项目同步时行为一致

---

## 验证清单

### N.E.K.O.-RN 项目

- [ ] Metro 配置已更新（3 个新映射）
- [ ] 所有包已添加 vite 依赖（5 个 packages）
- [ ] 运行 `npm install` 安装依赖
- [ ] 测试 Metro bundler 启动：`npm start`
- [ ] 测试包构建（如需要）：`npm -w @project_neko/audio-service run build`

### N.E.K.O 项目

- [ ] 所有包已添加 vite 依赖（5 个 packages）
- [ ] 运行 `cd frontend && npm install`
- [ ] 测试完整构建：`npm run build`
- [ ] 测试单个包构建：`npm run build:audio-service`

---

## 预防措施

### 1. 文档更新

**建议添加到 `.cursorrules`**：

```markdown
### 新增 Workspace 包时的检查清单

每次添加新的 `@project_neko/*` 包时，必须执行：

1. **N.E.K.O 项目**：
   - [ ] 在 `frontend/package.json` 中添加 workspace 路径（通常自动）
   - [ ] 如果包使用 vite 构建，在包的 `package.json` 的 `devDependencies` 中添加 `vite`

2. **N.E.K.O.-RN 项目**：
   - [ ] 更新 `metro.config.js` 的 `extraNodeModules`，添加新包的映射
   - [ ] 在 `package.json` 的 `workspaces` 中添加路径（通常自动）
   - [ ] 如果包使用 vite 构建，在包的 `package.json` 的 `devDependencies` 中添加 `vite`

3. **两个项目通用**：
   - [ ] 更新文档，说明新包的用途和依赖关系
```

### 2. 自动化检查

**建议添加 lint 脚本**（可选）：

```json
// package.json
{
  "scripts": {
    "lint:metro": "node scripts/check-metro-mappings.js",
    "lint:deps": "node scripts/check-package-deps.js"
  }
}
```

示例 `scripts/check-metro-mappings.js`：
```javascript
// 读取 metro.config.js
// 读取 packages/* 目录
// 检查是否所有 @project_neko/* 包都有映射
// 如果缺失，退出并报错
```

### 3. CI/CD 集成

在 CI pipeline 中添加：
```yaml
- name: Check Metro mappings
  run: npm run lint:metro

- name: Check package dependencies
  run: npm run lint:deps
```

---

## 相关文档

- [TinyEmitter 重构总结](./SUMMARY-tinyemitter-refactor.md) - 引入新包的背景
- [Packages 同步文档](./packages-sync-to-neko-rn.md)
- [多平台支持文档](./packages-multi-platform.md)

---

## 总结

**问题性质**：
- 配置漏洞（Metro 配置）
- 依赖管理不规范（隐式依赖 vite）

**修复内容**：
- 更新 1 个配置文件（metro.config.js）
- 更新 10 个 package.json（两个项目共 10 个包）

**长期改进**：
- 建立新增包时的检查清单
- 考虑添加自动化检查脚本
- 更新项目文档和开发规范

**影响**：
- 修复后，两个项目的开发环境可以正常工作
- 所有包的依赖声明完整、规范
- 为未来的包迁移和发布做好准备

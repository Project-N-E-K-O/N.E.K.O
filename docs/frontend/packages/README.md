### packages 文档索引（跨端：Web / legacy HTML+JS / React Native）

本目录聚焦 `@N.E.K.O/frontend/packages/*` 的“可共享基础能力”文档化（公共 SSOT 视角）。

阅读建议顺序：

1) `common.md`
2) `request.md`
3) `realtime.md`
4) `audio-service.md`
5) `live2d-service.md`
6) `components.md`（UI 组件库）
7) `web-only-boundaries.md`（了解 web-only 边界：components/web-bridge）

---

### 重要更新（2026-01-10）

**Metro 配置和 Vite 依赖修复**：
- 所有包的 `package.json` 现在显式声明了 `vite` devDependency
- N.E.K.O.-RN 的 `metro.config.js` 已添加新包（audio-service、live2d-service、realtime）的路径映射
- 详见：[Metro 配置和 Vite 依赖修复总结](../SUMMARY-metro-vite-dependency-fix.md)


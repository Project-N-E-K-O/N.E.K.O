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

**Node.js 版本约束添加**：
- 在所有相关 package.json 添加 `engines` 约束：`^20.19.0 || >=22.12.0`
- 确保开发环境和 CI 使用与 Vite 7 兼容的 Node.js 版本
- 详见：[Node.js 版本约束添加](./engines-node-version-constraint.md)

**Audio Service 错误处理修复**：
- 修复 Native 版本录音启动失败被静默吞掉的问题
- 修复 Web 版本并行启动时麦克风资源泄漏的问题
- 已同步到 N.E.K.O.-RN
- 详见：[Audio Service 错误处理修复](./audio-service-error-handling-fix.md)

**Metro 配置和 Vite 依赖修复**：
- 所有包的 `package.json` 现在显式声明了 `vite` devDependency
- N.E.K.O.-RN 的 `metro.config.js` 已添加新包（audio-service、live2d-service、realtime）的路径映射
- 详见：[Metro 配置和 Vite 依赖修复总结](../SUMMARY-metro-vite-dependency-fix.md)

**Vite 安全漏洞修复（CVE-2025-62522）**：
- 升级 vite 从 `^7.1.7` 到 `^7.1.11` 修复安全漏洞
- 同时在 N.E.K.O.-RN 添加 override 机制防止不安全版本被同步
- 详见：[Vite 安全漏洞修复和溯源分析](./security-fix-vite-cve-2025-62522.md)

---

## 相关文档

- [Node.js 版本约束添加](./engines-node-version-constraint.md)
- [Audio Service 架构设计](./audio-service.md)
- [Audio Service 错误处理修复](./audio-service-error-handling-fix.md)
- [Metro 配置和 Vite 依赖修复总结](../SUMMARY-metro-vite-dependency-fix.md)
- [Vite 安全漏洞修复（CVE-2025-62522）](./security-fix-vite-cve-2025-62522.md)
- [Vite CVE 修复总结](./fix-summary-vite-cve.md)


# @project_neko/live2d-service

跨平台 Live2D Service（**宿主无关 / host-agnostic**）：

- **Web**：计划用于 PixiJS + Live2D Cubism（`pixi-live2d-display` 或其他实现）
- **React Native**：计划适配 `@N.E.K.O.-RN/packages/react-native-live2d`（原生 Cubism）
- **旧 HTML/JS**：通过 `@project_neko/web-bridge` 暴露到 `window` 使用

## 目标

- 统一三端的 **控制接口**（load / transform / motion / expression / mouth / events）
- 底层差异通过 **Adapter** 隔离，避免 service 直接依赖 DOM / window / React Provider

## 当前状态

此包目前提供：

- `createLive2DService(adapter)`：service 工厂（事件 + 状态机 + 命令委托）
- `TinyEmitter`：最小事件系统（与 `audio-service` 同风格）
- `Live2DAdapter` / `ModelRef` / `MotionRef` / `ExpressionRef` 等基础类型

Web / Native 的具体 adapter 会在后续逐步补齐（先把工程结构与类型契约落地）。


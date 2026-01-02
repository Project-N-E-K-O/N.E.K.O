# @project_neko/live2d-service

跨平台 Live2D Service（**宿主无关 / host-agnostic**）：

- **Web**：计划用于 PixiJS + Live2D Cubism（`pixi-live2d-display` 或其他实现）
- **React Native**：计划适配 `@N.E.K.O.-RN/packages/react-native-live2d`（原生 Cubism）
- **旧 HTML/JS**：通过 `@project_neko/web-bridge` 暴露到 `window` 使用

## 目标

- 统一三端的 **控制接口**（load / transform / motion / expression / mouth / events）
- 底层差异通过 **Adapter** 隔离，避免 core 直接依赖 DOM / window / React Provider
- 允许在保持 core 纯净的前提下，通过 **runtime（可选）** 提供“旧版 Live2DManager 等级”的高级能力（参数编辑、bounds、吸附等）

## 分层设计（非常重要）

本包建议按三层理解：

### 1) Service Core（跨端共享，强稳定）
- `createLive2DService(adapter)`：事件 + 状态机 + 命令委托
- `Live2DAdapter` / `ModelRef` / `MotionRef` / `ExpressionRef` / `Transform` / `Live2DEvents` 等类型
- **原则**：不引入 Pixi/DOM，也不引入 React Native / Expo 依赖

### 2) Runtime（平台相关，可选）
- `Live2DAdapter.getRuntime?.(): Live2DRuntime | null`
- `Live2DRuntime` 用于暴露 *best-effort* 的平台能力：
  - `getBounds()`：模型 bounds（吸附/命中/调试）
  - `getTransformSnapshot()`：读取 position/scale（偏好保存）
  - `parameters.*`：参数读写（参数编辑器/常驻表情/叠加层）

### 3) Manager Facade（跨端共用的“语义层”，对齐 legacy Live2DManager）
- `createLive2DManager(adapter, options?)`
- `Live2DManager`：把宿主侧的 UI/业务更方便地映射到引擎控制语义（loadModel / setEmotion / applyModelParameters / setMouth / prefs 等）
- **注意**：UI（浮动按钮/HUD/popup 等）属于宿主工程，不放在 live2d-service 包内

## 对外导出（entrypoints）

### Core（跨端）
```ts
import { createLive2DService, createLive2DManager } from "@project_neko/live2d-service";
```

### Web adapter
```ts
import { createPixiLive2DAdapter } from "@project_neko/live2d-service/web";
```

### React Native
- `@project_neko/live2d-service` 通过 package.json 的 `react-native` / `exports` 条件解析到 `index.native.ts`
- `index.native.ts` 目前只暴露类型占位，后续将补齐 native adapter 工厂

## Web 用法（React WebApp）

典型用法是：WebApp 负责加载 Pixi/Live2D 依赖脚本 → 创建 Web adapter → 创建 manager/service。

在 N.E.K.O WebApp 中，`Live2DStage` 已支持通过 `onReady(manager)` 把引擎实例交给 `App.tsx` 接管：

```tsx
<Live2DStage
  staticBaseUrl={STATIC_BASE}
  modelUri="/static/mao_pro/mao_pro.model3.json"
  onReady={(mgr) => {
    // App 层可以像 legacy Live2DManager 一样控制
    mgr.setMouth(0.2);
    // mgr.applyModelParameters({ ParamAngleX: 10 });
  }}
/>
```

## 交互（拖拽 / 滚轮缩放 / 锁定）

旧版 `static/live2d-ui-drag.js` / `live2d-ui-buttons.js` 的交互逻辑 **不会** 自动出现在新架构里，这是设计使然：

- `live2d-service` 的 **core/manager 不绑定 DOM 事件**（避免强耦合浏览器输入系统，便于 RN 复用）
- 交互应由 **宿主层（WebApp / RN App）** 监听 pointer/wheel/gesture，再调用：
  - `manager.setTransform(...)`
  - `manager.savePreferences()`（可选：拖拽/缩放结束后持久化）
  - `manager.setLocked(true/false)`（仅存状态，宿主用它来决定是否响应输入）

Web 端最小示例（伪代码）：

```ts
const canvas = canvasRef.current!;
canvas.addEventListener("pointerdown", (e) => {
  if (locked) return;
  const snap = manager.getTransformSnapshot();
  // 记录 start pointer + start position
});
canvas.addEventListener("pointermove", (e) => {
  if (!dragging) return;
  manager.setTransform({ position: { x: startX + dx, y: startY + dy } });
});
canvas.addEventListener("pointerup", () => {
  manager.savePreferences();
});
canvas.addEventListener(
  "wheel",
  (e) => {
    if (locked) return;
    e.preventDefault();
    const snap = manager.getTransformSnapshot();
    manager.setTransform({ scale: { x: snap.scale.x * k, y: snap.scale.y * k } });
    debounce(manager.savePreferences);
  },
  { passive: false }
);
```

## React Native 适配（需要的接口清单）

你们当前的 `react-native-live2d`（expo module）已经具备：
- model load / motion / expression / mouth / transform（部分）/ tap events（Android）

为了实现“对齐 legacy Live2DManager 的全部能力”，**需要补齐并稳定**以下能力（Android/iOS 一致）：

### A. 必须：参数能力（用于参数编辑/常驻表情/叠加层/口型优先级）
- `setParameterValueById(id: string, value: number, weight?: number)`
- `getParameterValueById(id: string): number | null`
- `getParameterCount(): number`
- `getParameterIds(): string[]`（若 SDK 不支持，可退化为 `param_{i}`）
-（可选）`getParameterDefaultValueById(id: string)`（用于“additive offset”策略）

> 这些能力最终会通过 `Live2DAdapter.getRuntime().parameters` 暴露给 `Live2DManager` 使用。

### B. 必须：motionFinished 事件
- `onMotionFinished({ group, index })`：用于上层状态机（例如“动作结束后恢复”）

### C. 必须：Transform/坐标系约定
跨端要统一坐标语义，否则偏好保存无法复用。

建议约定其一（两端保持一致）：
- **像素坐标系**：`position {x,y}` 以 View 左上角为原点的像素坐标；native 内部映射到 Cubism view
或
- **归一化坐标系**：`position {x,y} ∈ [-1, 1]`

### D. 建议：bounds/hittest
- `getModelBounds(): {left,top,right,bottom,width,height}`
- `hitTest(x,y): string[]`

## 迁移建议（从旧版 static/live2d-*.js 到 Web/RN 共用）

1. 先让宿主（WebApp / RN App）拿到 `Live2DManager` 实例（context/ref/onReady）
2. 迁移最小闭环：loadModel / mouth / transform / motion / expression
3. 迁移偏好（position/scale/parameters）持久化
4. 迁移参数叠加层（saved params + persistent expressions + lipsync priority）
5. 再迁移高级交互（吸附/多屏幕/hit test），这些通常是宿主特性，不强制跨端 1:1


### `@project_neko/live2d-service`（跨端 Live2D：Service + Adapter + Runtime + Manager）

#### Overview

- **位置**：`@N.E.K.O/frontend/packages/live2d-service`\n- **职责**：把 Live2D 控制能力抽象成跨端接口：\n  - `Live2DService`：跨端内核（model lifecycle / motion / expression / mouth / transform / events）\n  - `Live2DAdapter`：平台适配（Web: Pixi+Cubism；Native: RN 原生模块契约）\n  - `Live2DRuntime`：可选的“运行时能力”口（参数读写、bounds、transform snapshot）\n  - `Live2DManager`：语义化门面层（对齐 legacy Live2DManager 的部分语义，best-effort）\n- **非目标**：不包含具体 UI（拖拽按钮/HUD/编辑器 UI 由宿主实现）；不强耦合 Pixi/DOM/RN SDK。\n
---

#### Public API（推荐用法）

- `import { createLive2DService, createLive2DManager } from "@project_neko/live2d-service";`\n- Web adapter：\n  - `import { createPixiLive2DAdapter } from "@project_neko/live2d-service/web";`\n- Types：\n  - `Live2DAdapter/ModelRef/MotionRef/ExpressionRef/Transform/Live2DEvents` 等。\n
---

#### Entry points & exports

- `index.ts`：导出 types + `createLive2DService` + `createLive2DManager` + runtime types。\n- `index.web.ts`：`export * from "./src/web/index"`（Pixi adapter）。\n- `index.native.ts`：`export * from "./src/native/index"`（native adapter 契约占位）。\n- `package.json`：`exports["."]` 提供 `react-native` / `default`；`exports["./web"]` 提供 web 入口。\n
---

#### Key modules

- `src/types.ts`\n  - 定义跨端契约：\n    - `Live2DAdapter`：platform/capabilities/loadModel/playMotion/setExpression/setMouthValue/setTransform/getViewProps 等\n    - `Live2DEvents`：stateChanged/modelLoaded/tap/motionFinished/error 等\n    - `Live2DCapabilities`：是否支持 parameters/transform/mouth 等\n- `src/service.ts`\n  - `createLive2DService(adapter)`：\n    - 注入 event sink（`adapter.setEventSink`）\n    - 做最小输入校验与 state 管理（loading/ready/error）\n    - 复杂平台差异不在这里处理（保持 core 稳定）\n- `src/runtime.ts`\n  - `Live2DRuntime`：可选能力访问口（transform snapshot、bounds、parameters runtime）\n  - `Live2DParametersRuntime.installOverrideLayer()`：用于对齐 legacy 的“口型优先级 + 参数叠加/覆盖”能力（best-effort）\n- `src/manager.ts`\n  - `createLive2DManager(adapter)`：\n    - 以 service 为核心，提供更语义化接口\n    - preferences 保存/恢复（position/scale/parameters）通过注入 repository\n    - emotion mapping 通过注入 provider（不在包内做 IO）\n    - 交互（drag/zoom/tap）只存状态，由宿主绑定手势\n- `src/web/pixiLive2DAdapter.ts`\n  - Web adapter：\n    - 支持注入 `PIXI/Live2DModel`，或使用全局 `window.PIXI`\n    - 负责默认布局、tap 事件、mouth 参数写入、runtime.parameters best-effort\n    - override layer：通过覆盖 motionManager.update/coreModel.update 实现“叠加/覆盖/常驻”策略（best-effort）\n- `src/native/index.ts`\n  - Native adapter 当前为契约占位：建议对接 `@N.E.K.O.-RN/packages/react-native-live2d`，并保持“最小能力契约”注入式设计。\n
---

#### Platform Notes（常见坑）

- **Web**：Pixi 与 Cubism SDK 版本差异较大，因此 adapter 内多为 best-effort 调用；避免把 Pixi 打进 UMD（通过注入/全局方式）。\n- **RN**：不应在上游包直接依赖 expo/原生模块实现细节；通过 `NativeLive2DModuleLike` 等契约由宿主注入。\n- **legacy HTML+JS**：通常由宿主加载 Pixi/Live2D 脚本后，再通过 bundles 调用 adapter/service。\n
---

#### Sync to N.E.K.O.-RN Notes

- 该包当前尚未纳入 `N.E.K.O.-RN/scripts/sync-neko-packages.js` 默认 mapping（需要时再扩展）。\n- RN 侧的 `react-native-live2d` 是独立原生模块，属于 RN repo 的实现层；上游应只定义契约与门面。\n

### `@project_neko/common`（跨端共享：工具与类型）

#### Overview

- **位置**：`@N.E.K.O/frontend/packages/common`\n- **职责**：提供跨端共享的最小工具与类型（例如 `ApiResponse<T>`、`noop()`）。\n- **非目标**：不做 i18n 初始化、不做网络请求、不做 DOM/RN 平台判断。\n
---

#### Public API

- `index.ts`：导出 `ApiResponse<T>`、`noop()`。\n
---

#### Entry points & exports

- **入口**：仅 `index.ts`（无 web/native 分支）。\n- **`package.json`**：exports 仅指向 `./index.ts`。\n- **构建产物**：通过 Vite 输出到 `@N.E.K.O/static/bundles/common(.es).js`，用于 legacy HTML/JS 场景。\n
---

#### Key modules

- `index.ts`\n  - `ApiResponse<T>`：用于统一接口返回类型（可按需扩展字段）。\n  - `noop()`：占位函数，便于注入回调或默认实现。\n- `vite.config.ts`\n  - UMD 全局名：`ProjectNekoCommon`。\n  - 输出目录：仓库根 `static/bundles/`（注意：不要手改构建产物）。\n- `tsconfig.native.json`\n  - Native 类型检查建议使用 `lib: ["ES2020"]`，防止不小心引入 DOM。\n
---

#### Platform Notes

- **Web/RN/legacy**：均可直接使用（无运行时依赖）。\n
---

#### Sync to N.E.K.O.-RN Notes

- `N.E.K.O.-RN` 侧通过 `scripts/sync-neko-packages.js` 镜像同步到 `packages/project-neko-common`。\n- 目标目录默认视为生成物（mirror copy），不要在 RN 侧手改。\n

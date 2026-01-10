### `@project_neko/common`（跨端共享：工具与类型）

#### Overview

- **位置**：`@N.E.K.O/frontend/packages/common`
- **职责**：提供跨端共享的最小工具与类型（例如 `ApiResponse<T>`、`noop()`）。
- **非目标**：不做 i18n 初始化、不做网络请求、不做 DOM/RN 平台判断。

---

#### Public API

- `index.ts`：导出 `ApiResponse<T>`、`noop()`。

---

#### Entry points & exports

- **入口**：仅 `index.ts`（无 web/native 分支）。
- **`package.json`**：exports 仅指向 `./index.ts`。
- **构建产物**：通过 Vite 输出到 `@N.E.K.O/static/bundles/common(.es).js`，用于 legacy HTML/JS 场景。

---

#### Key modules

- `index.ts`
  - `ApiResponse<T>`：用于统一接口返回类型（可按需扩展字段）。
  - `noop()`：占位函数，便于注入回调或默认实现。
- `vite.config.ts`
  - UMD 全局名：`ProjectNekoCommon`。
  - 输出目录：仓库根 `static/bundles/`（注意：不要手改构建产物）。
- `tsconfig.native.json`
  - Native 类型检查建议使用 `lib: ["ES2020"]`，防止不小心引入 DOM。

---

#### Platform Notes

- **Web/RN/legacy**：均可直接使用（无运行时依赖）。

---

#### Sync to N.E.K.O.-RN Notes

- `N.E.K.O.-RN` 侧通过 `scripts/sync-neko-packages.js` 镜像同步到 `packages/project-neko-common`。
- 目标目录默认视为生成物（mirror copy），不要在 RN 侧手改。


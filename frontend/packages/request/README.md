# @project_neko/request

本包是 **N.E.K.O monorepo 内部使用的源码包**（workspace package），`package.json` 的 `main`/`types`/`exports` **刻意指向 TypeScript 源码**（如 `index.ts`、`index.web.ts`、`index.native.ts`），供同一仓库内的 TypeScript 工程直接引用与类型推导。

## 发布说明（重要）

- 本包在 `package.json` 中已标记为 `"private": true`，并且配置了 `prepublishOnly` 防护脚本：即使有人误执行 `npm publish`，也会先跑一次 `build`，随后直接失败并提示原因。
- 如需对外发布（npm），需要先实现 **标准的 dist 编译产物**（例如 `dist/index.js` 与 `dist/index.d.ts`），并将 `main`/`types`/`exports` 指向 `dist/*`，然后再移除 `"private": true` 与 `prepublishOnly` 防护。

## 构建（build）的真实用途

本包的 `build` 脚本通过 Vite **生成浏览器可直接加载的 ES/UMD bundle**，并输出到仓库根目录的：

- `static/bundles/request.es.js`
- `static/bundles/request.js`

该产物用于 `templates/` 中以 `<script src="/static/bundles/request.js"></script>` 的方式引入（以及给 `web-bridge` 等 UMD 依赖使用）。

> 注意：这里的 `build` **不是** 传统意义上把 npm 包编译到 `dist/` 目录再通过 `main/types` 指向 `dist/*` 的发布流程；本包也未设计为从本仓库外通过 Node 直接 `import` 其 `.ts` 源码使用。

## 使用方式（仓库内）

- Web 工程：`import { ... } from "@project_neko/request"` 或 `import { ... } from "@project_neko/request/web"`
- React Native：Metro/React Native 会解析 `exports["."]["react-native"]` 指向的 `index.native.ts`



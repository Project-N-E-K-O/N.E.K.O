# @project_neko/common

本包是 **N.E.K.O monorepo 内部使用的源码包**（workspace package），`package.json` 的 `main`/`types`/`exports` **刻意指向 TypeScript 源码**（如 `index.ts`），以便同仓库内的 TypeScript 工程直接引用并获得类型推导。

## 构建（build）的真实用途

本包的 `build` 脚本通过 Vite **生成浏览器可直接加载的 ES/UMD bundle**，并输出到仓库根目录的：

- `static/bundles/common.es.js`
- `static/bundles/common.js`

该产物用于 `templates/` 中的静态脚本引入，以及给 `web-bridge` / 其他 UMD 入口使用。

> 注意：这里的 `build` **不是** 传统意义上把 npm 包编译到 `dist/` 目录再通过 `main/types` 指向 `dist/*` 的发布流程；本包也未设计为从本仓库外通过 Node 直接 `import` 其 `.ts` 源码使用。



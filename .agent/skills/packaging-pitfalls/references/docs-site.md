# docs 站点的构建与部署

`docs/` 是 VitePress 站点（英文为根，另有 `/zh-CN/` 和 `/ja/`）。文档分层、镜像和链接写法见 `docs/contributing/documentation.md`，这里只讲构建闸门和对账方法。

## 部署闸门在合并之后才跑

`.github/workflows/docs.yml` 只在 push 到 main 且改动了 `docs/**` 时触发，依次跑 `npm ci`、`npm test`、`npm run build`、`npm run check:seo`，然后发布到 GitHub Pages。

⚠️ PR 上不会构建文档站。PR 阶段只有 `analyze.yml` 里的 `scripts/check_docs_no_relative_paths.py` 这一道静态检查。也就是说，一个带死链的 docs PR 可以全绿合并，然后把 main 上的部署搞红。

**合并 docs 改动前在本地跑**：

```bash
cd docs
npm ci
npm test
npm run build:check
```

`build:check` 等于 `vitepress build` 加 `check:seo`。看到 client/server bundle 构建完成、页面渲染完成并且退出码为 0，才算过。依赖装在 `docs/node_modules`（已忽略），在 worktree 里也能跑。删章节、改链接、搬文件之后一定要重跑。

## 死链即构建失败

- VitePress 默认检查死链。任何 `[文字](x.md)` 形式的链接指向不存在的页面，`vitepress build` 就失败，整个部署变红。
- ⚠️ `docs/` 下所有 `.md` 都参与构建，包括不在侧栏里的 `docs/design/*.md` 这类内部设计文档。被排除的只有四个 README 译文（`docs/.vitepress/config.ts` 的 `SRC_EXCLUDE`，要和 `check_docs_no_relative_paths.py` 里的同名列表保持一致）。
- 反引号包起来的文件名是代码，不算链接，不会触发检查。要引用站外的仓库文件，要么写成行内代码，要么用完整的 GitHub 链接。
- `check_docs_no_relative_paths.py` 拦两类写法：以 `..` 开头、跳出文档根的相对链接；指向仓库源码文件的相对链接。
- 死链可能是早就存在的：某个页面一直有死链，但一直没人改 `docs/`，直到下一次不相干的 docs 合并触发部署才暴露。部署红了先看报错里点名的文件，不要默认是刚合并的那个 PR 引入的。

## 两份架构图

站点用的是 `docs/public/framework.svg`，根 README 用的是 `assets/framework.drawio.svg`。两份都是 draw.io 导出的 SVG，互相独立，改了一份另一份不会变。更新架构图时两份一起换；它们内容相同，直接复制即可。

## 对账文档和代码的方法

文档站会明显落后于代码重构，过时的陈述会成批出现。对账时：

1. **先按页审，每条都回代码核对。** 文档为了好懂会做简化，简化不等于过时，逐条对代码能压掉这类误报。
2. **再按事实全站扫一遍。** 按页审有盲区：同一个过时的事实常常散落在没被点名的页面里（架构页改了，API 参考页漏了）。每确认一个过时的标识符或路径，就全站 grep 一次。
3. **三种语言的镜像一起改。** 英文页有的问题，`zh-CN` 和 `ja` 的镜像页通常同样有。

容易过时的几类内容和对应的权威来源：

| 内容 | 去哪里核对 |
|---|---|
| REST 路径 | `main_routers/` 下各路由的 `prefix=` 和 `@router` 装饰器 |
| 各角色实际用的模型 id | `config/api_providers.json` 的 `core_api_providers` 和 `assist_api_providers` 下各厂商的条目，不要只看代码里的兜底常量 |
| 环境变量 | 先 grep 代码里有没有引用，文档里写着但代码零引用的就是死配置 |
| 数据目录位置 | 以代码里的路径解析为准，文档里的旧默认路径可能已经降级成兼容路径 |

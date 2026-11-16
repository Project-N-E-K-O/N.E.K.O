# `static/` 里搬文件、拆文件

`static/` 根目录原来堆着几十个扁平的 `.js`，已经按域分批搬进了子目录（`static/app/`、`static/live2d/`、`static/vrm/`、`static/mmd/`、`static/avatar/`、`static/subtitle/`、`static/jukebox/`、`static/tutorial/` 等），其中几个大文件又拆成了同目录多个文件。根目录还剩一些散件。再搬、再拆时按下面的做法来。

## 布局约定

- 搬到 `static/<域>/<原文件名>`，**保留原文件名**，方便追溯历史。
- 一个域一个 PR，小批量做。
- 子目录里已经有模型、歌曲清单之类的资源文件没关系，但搬进去之前先确认后端是怎么枚举这个目录的：按文件名直接读（如 `main_routers/jukebox_router.py` 读 `static/jukebox/songs.json`）或按后缀过滤（如模型路由按模型后缀 `glob`）是安全的；如果是「目录里有什么列什么」，JS 文件会混进列表。
- 根目录的同名 `*.test.cjs` 留在 `static/` 根，不跟着搬。

## 引用点清单（每一批都要逐类查）

一个 JS 文件的路径会出现在下面这些地方。只改模板是不够的。

1. **模板里的 `<script src>` / `<link href>`**。`index.html` 之外，独立页面（`chat.html`、`subtitle.html`、`jukebox.html`、`viewer.html` 等）和 `static/` 下的独立 HTML（如 `static/subtitle-settings.html`）各有一份。
2. **JS 里的动态加载**。普通的模板搜索抓不到，搜索时一定要把 `*.js` 也包括进去：
   - 脚本路径数组，如 `static/js/model_manager/runtime-loaders.js`、`static/jukebox/jukebox-loader.js` 里的列表；
   - 运行时拼出来的 `script.src`，如 `static/vrm/vrm-vmc-loader.js`；
   - 各 `*-init.js` 内部的级联加载。
3. **`main_routers/pages_router.py` 的版本路径表**。模板里带 `?v={{ static_asset_version }}` 的文件会被 `_template_static_asset_version_paths()` 自动收进去；动态加载的文件要手动列在 `_YUI_GUIDE_ASSET_VERSION_PATHS` 里（聊天相关的在 `_REACT_CHAT_ASSET_VERSION_PATHS`）。⚠️ 路径写错不会报错：取修改时间时 `OSError` 被吞掉，结果只是改了这个文件之后缓存版本号不变，用户拿到旧脚本。
4. **pytest 里的四种写法**：
   - 整条字符串路径：`Path("static/x.js").read_text()`；
   - 分段路径：`ROOT / "static" / "x.js"`；
   - 对模板内容做子串断言：`source.index("/static/x.js")`；
   - **不带 `static/` 前缀的文件名列表**，由辅助函数拼前缀（如 `tests/test_agent_rewrite_regression.py` 的 `_HOME_YUI_RUNTIME_SCRIPTS`）。按带路径的模式做替换碰不到这一种，最容易漏。
5. **`*.test.cjs`** 里相对 `static/` 根传的参数（`readStatic('域/文件.js')` 之类）。传给 `vm` 的 `filename` 只是诊断标签，不用改。
6. **`.github/workflows/`**：`paths` 触发过滤器和 `node --test` 的显式文件列表。漏改不会报错，表现为对应的 CI 不再触发或不再跑那个文件。
7. **文档**：`docs/frontend/*.md` 等活文档里的目录表述（多语言版本一起改）。
8. **打包**：Nuitka 流程（`--include-data-dir=static=static`）和 `specs/launcher.spec`（`add_data('static', 'static')`）都是整目录收录，`scripts/check_nuitka_dist.py` 只按目录校验，子目录调整不用额外登记。前提是这两处保持整目录收录，不要改回白名单。

## 批量替换时的坑

- 替换模式要锚定 `static/` 路径前缀或完整文件名。文件词干经常同时是 DOM id 或 class（如 `mmd-animation-select`、`avatar-reaction-bubble`），也会是别的文件名的子串（`avatar-ui-popup` 是 `avatar-ui-popup-config` 的前缀；第三方库文件名里也带 `vrm-animation`）。
- 多条替换规则可能先后命中同一行，叠出 `static/app/app/` 这种双写。改完搜一遍 `域/域/`。
- 搬目录的 PR 里，每个改动文件的差异应该只有路径替换。审计办法：对每个改动文件看它相对主分支的差异，把符合路径替换模式的行过滤掉，剩下非空的就是混进来的逻辑改动——源码和配套的测试断言都要查。
- 提交前不要用 `git stash` 做前后对比：它会把已暂存的重命名拆成未暂存的「删除 + 新增」，恢复后要重新全部暂存并核对暂存区，否则会提交出残缺的改动。

## 把一个大 JS 拆成同目录多个文件

现有例子：`static/app/app-react-chat-window/`、`static/app/app-ui/`、`static/app/app-interpage/`、`static/tutorial/yui-guide/director/`。

- 拆出来的仍是经典脚本，靠挂在 `window` 上的共享对象互相调用，加载顺序由模板的 `<script>` 顺序保证。加载这组脚本的每个模板都要补齐新文件。
- `pages_router.py` 对这几个目录用的是 `glob("*.js")`，新文件自动进版本号计算。
- `static/app/` 下那三个目录的测试是读整个目录、按文件名排序拼接后做断言（`tests/static_app_parts.py`、`static/app-part-test-utils.cjs`），详见 `references/chat-surfaces.md` 第 3 节；教程导演那组有自己的辅助文件 `static/yui-guide-director-test-parts.cjs`。

## 验证

- pytest：把失败集合和主分支基线逐条对比（存量失败不少，只看数量会误判），范围包括 `tests/` 根目录下的散测试，不只是 `tests/unit/`。
- `node --test` 跑相关的 `*.test.cjs`。
- 起服务实测三条路径（`index.html` 宽屏、窄屏、`chat.html`），确认运行时级联加载的脚本全部 200，`window` 上该有的全局都注册了。
- `main_routers/pages_router.py` 在提交报告门禁的监视范围内（`scripts/check_pr_report.py` 的 `WATCHED_PREFIXES` 含 `main_routers/`），动了它 PR 描述里要有「回归报告」；一批改动的计数文件超过 20 个还要写「不拆分理由」。

# 内置资源、插件边界与依赖

## 离线模型权重

**不变式**：向量模型、端点检测（Silero VAD、Smart Turn）、声纹（CAM++）的权重都不入库（端点检测和声纹的目录在 `.gitignore` 里只放行 `manifest.json` 和第三方声明，向量模型目录整个忽略），由构建时的 prepare 脚本按锁定的版本下载并校验：

- `scripts/prepare_embedding_model.py` → `data/embedding_models/`
- `scripts/prepare_voice_turn_assets.py` → `main_logic/asr_client/endpointing/models/`
- `scripts/prepare_speaker_model.py` → `main_logic/asr_client/speaker_shadow/models/`

冻结版没有运行时下载路径。哪一类权重漏打，对应功能就静默降级，没有报错，用户和开发者都很难发现。

**校验为什么要具体到文件**：下载中断会留下一个空的子目录，「目录非空」这种检查能被它骗过去。所以两处都按具体文件查：`check_nuitka_dist.py` 断言文件存在（它不查大小，空文件能过）；workflow 的 `Verify bundled offline assets` 另外断言文件非空，还要求每个权重在包里只出现一份，并且已经废弃的旧目录（`data/vad_models`、`data/speaker_models`）不得进包。

**新增一类离线权重要动的地方**：prepare 脚本和它的清单、三处 Nuitka 步骤的 `--include-data-dir`、workflow 里的缓存步骤和 `Verify bundled offline assets`、`check_nuitka_dist.py` 的 `_REQUIRED_ASSETS`、`.gitignore` 的放行规则，再加上通知维护者同步本地打包脚本。

## 内置 Live2D / PNGTuber 模型

- Live2D 模型的真源是 `assets/<名字>.tar.gz`，解到 `static/<名字>/`（这个目录被忽略）。PNGTuber 的真源在 `frontend/pngtuber-packs/`，由 `scripts/unpack_builtin_pngtuber.py` 解到 `static/pngtuber/`。
- ⚠️ 解包逻辑有三份，要一起改：`build_frontend.sh` 的 `unpack_live2d_model`、`build_frontend.bat` 的 `:unpack_live2d`、`build-desktop.yml` 里 `Build production frontend projects` 步骤的内联循环。最后这一份是因为 Windows 和 macOS 腿不走 `build_frontend` 脚本，漏了它，这两个平台的包里就没有默认模型。
- 新增一个内置模型：把压缩包放进 `assets/`，三处解包各加一项，`.gitignore` 加上解包目录，`_REQUIRED_ASSETS` 里断言 `model3.json`、`moc3` 和纹理三样（只查 `model3.json` 抓不到解包不完整）。只在本机 `static/` 下放一份未跟踪的模型目录，本地出的包里有它，从干净检出构建的包里没有。
- 改模型的动作或表情文件，必须改压缩包再入库。`build_frontend` 每次按标记文件和修改时间决定是否重新解包，直接改 `static/<名字>/` 下的文件迟早被覆盖。
- 实测观察：`scripts/smooth_yui_mouth.py` 不是幂等的，对已经平滑过的动作文件再跑一遍会继续衰减。要重新生成，从没平滑过的源文件起步。

### 压缩包里的成员名保持 ASCII

`scripts/check_no_nonascii_asset_names.py`（在 `analyze.yml` 里跑）禁止往会进包的目录和压缩包里新增非 ASCII 文件名，脚本开头有完整的原因说明：macOS 正式签名会因此整包验证失败。

Windows 上还有第二个原因（实测观察）：Windows 自带的 `tar` 解压时会把 UTF-8 编码的非 ASCII 成员名解成乱码文件名。乱码文件随后被打进 Portable 包，旧版本的安装器在校验包内条目时会拒绝整个包，一键升级失败。基线里登记在案的历史文件不要新增同类。

## 内置插件和市场插件的边界

市场插件不会被打进桌面包，靠的是四层互相独立的机制：

1. **安装位置不同。** 用户从市场装的插件在 `plugin/settings.py` 的 `get_user_plugin_exec_root()` 返回的目录（状态根旁边的 `.neko-plugin-installations/plugins`），打包只看仓库里的 `plugin/plugins`。`PLUGIN_CONFIG_ROOTS` 把两个根都列出来，只是运行时的加载顺序，和打包无关。
2. **按 Git 索引过滤。** `scripts/prepare_nuitka_plugins.py` 的 `_skip_reason()` 只收 Git 跟踪的顶层目录，未跟踪的一律跳过；没有 `plugin.toml` 的目录也跳过（下划线开头的辅助包除外）。所以开发机上的私有实验插件、已经迁去市场但本地还留着的目录、改名后只剩 `__pycache__` 的空壳，都不会进包（#3056）。
3. **整体替换，不是合并。** `prepare_nuitka_plugins.py install` 把 dist 里的 `plugin/plugins` 整个换成分阶段目录的内容；`check_nuitka_dist.py --plugin-stage` 再逐文件比对，多一个、少一个、内容变了都失败。
4. **黑名单。** `check_nuitka_dist.py` 的 `_MARKETPLACE_ONLY_PLUGIN_IDS` 里的插件只允许走市场，出现在 dist 里就报 `marketplace-only plugin bundled`。理由是包里的只读内置副本会和市场安装、升级用的同一个插件 id 冲突。

**排查入口**：某个插件没进包或者不该进却进了，先看 prepare 步骤写出的 `build/nuitka-plugin-stage.json`，里面的 `skipped_entries` 列出了每个被跳过的条目和原因，`excluded_modules` 是生成的 `--nofollow-import-to` 列表。

**已知缺口（由代码推断，没有在产物上实测）**：被第 2 层跳过的目录只记进清单，不会生成 `--nofollow-import-to`。而 Nuitka 带着 `--include-package=plugin`，会按文件系统递归编译 `plugin.plugins.*`。推论是：物理上躺在 `plugin/plugins/` 下的未跟踪插件，它的 `.py` 仍可能被编进二进制，尽管它的源码和资源不在 dist 里，运行时也加载不到它。CI 是干净检出，不受影响；受影响的只有在留着本地残留目录的开发机上出的包。本地出正式包前，把 `plugin/plugins/` 下不属于仓库的目录移走。

**把内置插件移到市场的做法**：删除入库的文件，把整个目录加进 `.gitignore`（和已有的那组「本地安装的插件」放在一起），本地残留留给各开发者自己处理。需要硬性防止它再被打包的，把 id 加进 `_MARKETPLACE_ONLY_PLUGIN_IDS`。

## 依赖

### 重的可选依赖不进主依赖

- 只有个别可选功能用到、体积又大的原生依赖，放 `pyproject.toml` 的 `[dependency-groups]`，不放 `[project.dependencies]`。打包时用 `uv sync --group galgame` 带上，日常开发的 `uv sync` 不装。
- 小而通用的（`mss`、Windows 上的 `dxcam`）留在主依赖。
- OCR 用的是仓库内的 `deps/rapidocr_pillow`，它保留 `rapidocr_onnxruntime` 这个导入名，但去掉了 opencv 和 shapely 这条传递依赖链。主程序没有任何地方 import `cv2`。不要因为「以后可能用到」把 opencv 加回主依赖；真用上了再加，只是 `pyproject.toml` 里移动一行的事。
- 评估一个依赖的体积时，连同它拖进来的传递依赖一起看 site-packages 里的实际目录大小。上一次评估里，一个没人注意的传递依赖占了新增体积的八成以上。

### Intel Mac 的 wheel

`pyproject.toml` 的 `[tool.uv]` 里有 `required-environments`，把 macOS x86_64 列为必须可解的环境。原因写在那段注释里：uv 的通用解析不检查每个平台是否真有可装的 wheel，曾经把 `onnxruntime` 锁到一个没有 Intel Mac wheel 的版本，mac-x64 腿每晚挂在 `uv sync`。

这一行同时是一道门：以后哪个依赖在 Intel Mac 上无解，`uv lock` 当场失败。遇到这种失败，不要删掉这一行，去给那个依赖找一个该平台可用的版本范围。

### 旧版 Windows（Win7）

主仓库目前没有 Win7 适配代码。以下是评估「不改依赖锁定就支持 Win7」时的实测观察，结论和当时的依赖版本有关，再评估时要重新扫一遍：

- 用 `pefile` 扫虚拟环境里各扩展模块的导入表，`pydantic_core`、`cryptography`、`tiktoken`、`jiter`、`orjson`、`rpds`、`tokenizers`、`onnxruntime` 都静态导入了 Windows 8 才有的系统 API（例如 `WaitOnAddress`、`GetSystemTimePreciseAsFileTime`）。Rust 写的扩展还依赖较新 Rust 工具链才使用的系统随机数入口，而 Rust 从 1.78 起不再支持 Win7。
- FastAPI 依赖 pydantic，所以只要不换依赖版本，主服务在 Win7 上起不来。「只加安装指南和启动脚本、不动依赖锁定」的 Win7 适配，前提就不成立。
- 第三方的 Win7 版 Python 构建要求系统装有提供 `AddDllDirectory` 的更新补丁；`numpy`、`av` 在 import 时直接调用 `os.add_dll_directory`，缺补丁连 import 都过不了。

**做法**：评审或接手这类「兼容旧系统」的改动时，第一步先扫依赖的导入表，确认前提成立，再看安装脚本和文档。

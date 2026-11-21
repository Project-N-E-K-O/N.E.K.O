---
name: packaging-pitfalls
description: 桌面打包、冻结版专属缺陷、CI 构建矩阵、nightly 发布、Windows 签名、内置资源与依赖、docs 站构建里踩过的坑和既定规则。改 `.github/workflows/build-desktop*.yml`、Nuitka 参数、打包或校验脚本、内置模型和内置插件、`pyproject.toml` 依赖、`docs/` 下的页面之前读。
---

# 打包 / 构建 / 发布：坑与既定规则

基础契约已经写在别处，这里不重复：

- `docs/contributing/nuitka-packaging.md`：包名不带连字符、内置插件分阶段打包、加资源要同步哪几处、怎么安全地诊断。
- `docs/deployment/manual-desktop-release.md`：正式版在各平台本机构建、签名、手工发布的流程。
- `docs/contributing/documentation.md`：文档分层、多语言镜像、链接写法。

下面只写这三份里没有、读代码也不容易看出来的东西。

## 先记住这几条

1. **入库的打包真相只有 workflow 和脚本。** `.github/workflows/build-desktop.yml`、`build-desktop-linux.yml`，加上 `scripts/prepare_nuitka_plugins.py`、`scripts/check_nuitka_dist.py`、`scripts/check_dist_arch.py`、`scripts/check_frozen_media.py`。维护者本机还有一份不入库的 Windows 打包脚本，正式包由它出，它不会跟着 PR 自动更新。改了 Nuitka 参数、资源准备步骤或校验项，在 PR 描述里写明「本地打包脚本要同步」。
2. ⚠️ **PR 的 CI 不编 Nuitka。** `build-desktop.yml` 只由定时、手动触发和 `workflow_call` 启动。冻结版才有的问题要到 nightly 才暴露，曾经因此连红十几天。动 Nuitka 参数、加动态导入或加带原生扩展的依赖时，合并前要么在本地做最小 Nuitka 复现，要么加一条解析 workflow 的静态契约测试（现成例子：`tests/unit/test_pyav_packaging_contract.py`）。
3. ⚠️ **构建显示成功不等于产物对。** 出过三类「成功但产物是错的」：架构错（CI 上的 Intel Mac 包其实是 arm64）、目标错（arm64 机器打出 x64 文件名的包）、本地签名失败后脚本照样走到「全部完成」。判断产物只看产物本身：读二进制头、读签名、读包内文件。
4. **三处 Nuitka 步骤要一起改。** `build-desktop.yml` 的 `Build with Nuitka (Unix)`、`Build with Nuitka (Windows)`，以及 `build-desktop-linux.yml` 的 `Build with Nuitka`。后者是手动触发的 Linux 单平台镜像，没人天天跑，最容易落后。
5. **Nuitka 版本是锁死的**（两个 workflow 都是 `nuitka==4.2.2`）。不要为了跟上新版解锁，原因见 `references/frozen-build-bugs.md`。
6. **发布步骤的顺序：先把新的传完、验完，再动旧的。** nightly 曾经「先删旧 release 再建新的」，新建或上传一失败，nightly 就整个消失。
7. ⚠️ **`.gitignore` 里有一条 `build*`。** 任何目录下新建的、名字以 `build` 开头的文件默认都被忽略（白名单只有 `build_frontend.bat`、`build_frontend.sh`、`build-desktop*.yml`、`scripts/build-desktop-release.ps1`）。新增这类文件要 `git add -f` 或加白名单行，否则提交里没有它，本地却一切正常。

## 症状速查

| 症状 | 先想到 | 详见 |
|---|---|---|
| 冻结版里函数返回 `None`、进程静默退出或段错误，源码模式和单测全绿 | Nuitka 4.3.1 丢「`try` 里 `return` + `finally` 含 `while`」的返回值 | `references/frozen-build-bugs.md` |
| 冻结版 `No module named 'cython'` | PyAV 的影子 `.py` 被编进去盖掉了扩展模块 | 同上 |
| 冻结版里本地端点检测、声纹、向量记忆悄悄降级 | 模型权重没进包（权重不入库，冻结版没有运行时下载路径） | `references/assets-plugins-deps.md` |
| 某平台的包启动报 `Bad CPU type in executable`，CI 一直绿 | runner label 的原生架构和矩阵声明不一致 | `references/ci-matrix-and-nightly.md` |
| arm64 腿产物和 x64 腿文件名一模一样 | electron-builder 的 target 架构解析 | 同上 |
| arm64 链接报 `R_AARCH64_CALL26 relocation truncated` | GNU ld 不补跳转 thunk，该腿用 lld | 同上（workflow 里有完整注释） |
| nightly release 不见了，或上传附件报 422 | 单附件 2 GiB 上限、符号链接被展开 | 同上 |
| mac-x64 腿挂在 `uv sync` | 某依赖没有 macOS x86_64 的 wheel | `references/assets-plugins-deps.md` |
| macOS 本地正式签名报 `the sealed resource directory is invalid` | 包里有非 ASCII 文件名 | `scripts/check_no_nonascii_asset_names.py` 的文件头说明 |
| dist 校验报 `marketplace-only plugin bundled` 或 `plugin payload contains ... unstaged file(s)` | 插件目录和分阶段打包的清单不一致 | `references/assets-plugins-deps.md` |
| `.bat` 报莫名其妙的 `'xxx' is not recognized` | 批处理是 LF 行尾 | `references/local-build-and-signing.md` |
| 签名步骤没报错，产物却没有签名 | 签名失败没有中止构建，或 electron-builder 根本没配签名 | 同上 |
| docs 部署红，`dead link(s) found` | 某个 `.md` 里有指向不存在页面的链接 | `references/docs-site.md` |

## 不显然的既定规则

- **新增或改动 `runs-on` 之前先查 runner label 的原生架构。** `macos-15` 和 `macos-latest` 是 Apple Silicon，x64 的 label 是 `macos-15-intel`。`actions/setup-python` 的 `architecture: x64` 只让 Python 走 Rosetta，Nuitka 仍然吐 arm64。新 label 要先加进 `tests/unit/test_desktop_build_arch_contract.py` 的 `_RUNNER_NATIVE_ARCH`，矩阵里每条腿都要带 `expect_arch` / `expect_platform`，由 `scripts/check_dist_arch.py` 在上传前读二进制头把关（#2898）。
- **Unix 腿的后端产物先打成 tar 再上传。** `actions/upload-artifact` 会把符号链接展开成实体文件、丢掉可执行位；macOS 的 `.app` 里框架靠符号链接组织，展开后同一份框架存多份，包体积直接越过上限。
- **试验腿（目前是 linux-arm64）不能挡住正式产物。** 它带 `continue-on-error`；nightly 的发布条件是 `build-python` 成功且 `build-electron` 不是 failure。发布说明里的 arm64 行按 artifact 目录是否存在来生成，不要写死。
- **打包用的配置种子是 workflow 现场生成的中性文件**（`config/characters.json`、`config/core_config.json`、`config/user_preferences.json`），不是开发机上的真实配置。`core_config.json` 的出厂默认必须是 `coreApi: free` / `assistApi: free`，不写任何密钥；写成空对象会让新装用户没有默认服务商。
- **`check_nuitka_dist.py` 只查「在不在」，不查「新不新」，也查不到编进二进制的模块。** 所以前端构建失败必须让整个构建失败，不能降级成警告：旧的构建产物能通过存在性检查，然后被原样发出去。
- **重的可选原生依赖放 `[dependency-groups]`，不进主依赖。** 构建时用 `uv sync --group galgame` 带上。OCR 走仓库内的 `deps/rapidocr_pillow`，目的就是不引入 opencv 那条依赖链；主程序不 import `cv2`，不要把它加回来。
- **市场插件不随包。** 只有 Git 索引里的 `plugin/plugins/<名字>/` 会进桌面包；把内置插件移到市场的做法是删除入库文件，并把整个目录加进 `.gitignore`。
- **内置 Live2D 模型的真源是 `assets/<名字>.tar.gz`。** `build_frontend` 每次按标记文件和修改时间重新解包，直接改 `static/<名字>/` 下的文件会被覆盖，也不会入库。
- **正式版的版本号必须是三段式 semver。** `build-desktop.yml` 的 `version` 任务会拒绝 `0.9.1.0` 这类四段号；`sync-update-release.yml` 要求稳定版 tag 是 `vMAJOR.MINOR.PATCH`，并且五个平台的 Portable 全量包、manifest、`.sig` 齐全。
- **`config/application.py` 的 `APP_VERSION` 是编进二进制的常量。** 改它必须全量重编。发版时同步补 `config/changelog/<版本>.md`，缺这个文件，从旧版升级上来的用户看到的更新日志是空的。

## references 索引

- `references/frozen-build-bugs.md`：冻结版才出现的问题（Nuitka 版本缺陷、PyAV 影子 `.py`）、冻结冒烟都有哪些、本地最小复现的注意事项。冻结版行为和源码模式不一致，或者要动 Nuitka 版本和参数时读。
- `references/ci-matrix-and-nightly.md`：构建矩阵的架构陷阱、linux-arm64 试验腿、产物传递、nightly 的四步发布和体积预检、契约测试对 workflow 写法的依赖。改 `build-desktop*.yml` 时读。
- `references/local-build-and-signing.md`：本地打包脚本和 CI 的关系、有意保留的差异、编译后失败怎么续跑、Windows 批处理和签名的坑。帮维护者出本地包、排查签名问题时读。
- `references/assets-plugins-deps.md`：内置模型和离线权重、内置插件与市场插件的边界、依赖体积、Intel Mac 和旧版 Windows 的依赖约束。加内置资源、移除内置插件、加依赖时读。
- `references/docs-site.md`：`docs/` 站点的构建和部署闸门、对账方法。改 `docs/` 下任何 `.md` 时读。

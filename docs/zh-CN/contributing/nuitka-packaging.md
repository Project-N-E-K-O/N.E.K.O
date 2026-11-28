
# Nuitka 打包

当前可追踪的打包契约是桌面 workflow 及其准备、校验脚本。仓库中没有受跟踪的 `build_nuitka.bat`，因此不要把它写成第二个权威入口。

## Python 包命名

含可导入 `.py` 的目录使用下划线名称并包含 `__init__.py`。连字符包名不符合普通 Python 命名，也会干扰数据包含。`tests/unit/test_no_hyphen_python_packages.py` 会检查此规则。

不要用 `--include-data-dir` 分发可导入 Python。Nuitka 会从数据目录过滤类代码后缀。应正常编译 package；只有存在明确运行时契约的解释型/沙箱源码 payload 才作为原始数据包含。

## 内置插件 staging

当前桌面 workflow 依次执行：

1. `scripts/prepare_nuitka_plugins.py prepare`
2. 编译生成的 `build_nuitka_launcher.py`
3. 用 `scripts/prepare_nuitka_plugins.py install` 安装到构建分发目录
4. 执行 `scripts/check_nuitka_dist.py <dist> --plugin-stage build/nuitka-plugins`

Staging 脚本按各插件的 `[tool.neko.build]` 规则处理并生成选择性排除。不要恢复全量 `--include-data-dir=plugin/plugins=plugin/plugins` 或全量 `--nofollow-import-to=plugin.plugins`；两者都会以不同方式绕过 staging 契约。

## 资源与动态导入

新增运行时资源或动态导入可能需要协调修改：

- 跨平台和 Linux-only workflow 的 Nuitka include 选项；
- 插件 payload 的 `scripts/prepare_nuitka_plugins.py`；
- `scripts/check_nuitka_dist.py` 的必要资源验证；
- 定向 import/package 测试。

Embedding 与 tiktoken 资源由 workflow 分别准备和验证。

## 安全诊断

打包后的 launcher 会启动多个服务。只终止父进程可能留下子进程占用 `dist/Xiao8`。启动前优先做静态检查：

```bash
uv run python scripts/check_nuitka_dist.py dist/Xiao8 --plugin-stage build/nuitka-plugins
uv run pytest tests/unit/test_no_hyphen_python_packages.py -q
```

确需运行打包产物时，记录精确 artifact/revision，并在重建前关闭所有子服务。不要用未经审查的递归删除处理被旧进程锁定的分发目录。

## 本地 Nuitka 打包步骤

桌面后端通常由 CI 构建，但同样的构建可以在本机复现。Nuitka 参数以 `.github/workflows/build-desktop.yml` 为准（`Build with Nuitka (Unix)` 与 `Build with Nuitka (Windows)` 两个步骤），下面只给出顺序。所有命令都在仓库根目录执行，需要 C 编译器（Windows 用 MSVC，其他平台用 gcc 或 clang）。

1. 环境：`uv sync --group galgame`，再执行 `uv pip install --python .venv nuitka==4.2.2 ordered-set zstandard`。Nuitka 版本是锁死的，以 workflow 里写的为准。Windows 上要先构建并安装打过补丁的唤醒词运行时（workflow 的 `Build and install compatible Windows wake-word runtime` 步骤，说明见 `docs/development/wake-word-runtime.md`），因为 Windows 的参数会打包 `sherpa_onnx`。
2. 前端：`./build_frontend.sh`（Linux/macOS）或 `.\build_frontend.bat`（Windows）。
3. 离线资源与种子配置，与 workflow 一致：运行 `scripts/prepare_embedding_model.py`（参数见 `Prepare embedding model assets` 步骤）、`scripts/prepare_voice_turn_assets.py`、`scripts/prepare_speaker_model.py`；把 tiktoken 缓存预热到 `data/tiktoken_cache/`（命令见嵌入模型页面）；如果不存在，生成中性的 `config/characters.json`、`config/core_config.json`（`{"coreApi": "free", "assistApi": "free"}`，不写任何密钥）和 `config/user_preferences.json`（`[]`）。请在不含个人 `config/*.json` 的干净检出（或副本）里构建：workflow 会把这三个文件原样打进包，已有的文件如果带着密钥或个人数据，就会随包一起分发。不要分享用带真实配置的检出构建出来的包。
4. `uv run --no-sync python scripts/prepare_nuitka_plugins.py prepare` 准备内置插件的分阶段目录，并生成 `build_nuitka_launcher.py`。
5. 删掉 `steamworks/` 里其他平台的原生库，然后编译：`uv run --no-sync python -m nuitka --standalone --output-dir=dist --output-filename=projectneko_server <workflow 里的参数> build_nuitka_launcher.py`。macOS 在 workflow 里用 `--mode=app --macos-app-mode=background` 代替 `--standalone`。
6. 整理产物。Linux 和 Windows：把 `dist/build_nuitka_launcher.dist` 改名为 `dist/Xiao8`，以 `dist/Xiao8` 作为运行目录。macOS（`--mode=app`）：新建 `dist/Xiao8`，把 `dist/build_nuitka_launcher.app` 移到 `dist/Xiao8/projectneko_server.app`，补上 workflow 里 `Rename build output` 步骤写的那个 `dist/Xiao8/projectneko_server` 小包装脚本，并以 `dist/Xiao8/projectneko_server.app/Contents/MacOS` 作为运行目录。然后执行 `uv run --no-sync python scripts/prepare_nuitka_plugins.py install --destination-dir <运行目录>/plugin/plugins`，最后用 `uv run --no-sync python scripts/check_nuitka_dist.py <运行目录> --plugin-stage build/nuitka-plugins` 校验。

三处 Nuitka 步骤（`build-desktop.yml` 的 Unix 和 Windows、`build-desktop-linux.yml`）里的参数要一起改；改动同时影响本机打包脚本时，在 PR 描述里写明。

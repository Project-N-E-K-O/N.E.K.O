# Wake-word runtime 安装与启动

唤醒词功能需要项目维护的 Sherpa ONNX 定制 wheel。上游 `sherpa-onnx==1.13.8` 不包含 NEKO 所需的时间戳和 decoder 修复，不能用于生产唤醒检测。backend 会同时校验 Python 包和 native runtime 的版本必须为 `1.13.8+neko.kws2`。

## 一键修复（普通用户）

Windows x64、Python 3.11 用户启用唤醒词后，在声纹页点击「修复资源」即可依次补齐兼容运行组件与唤醒模型。运行组件使用[固定版本预编译包](https://github.com/Project-N-E-K-O/N.E.K.O/releases/tag/wake-word-runtime-v1.13.8-neko.kws2-win-py311)，按代码固定的大小与 SHA256 校验，并在独立工作进程中真实加载后发布私有缓存指针，不要求用户安装编译工具或在运行环境中执行 pip。资源检查与实际唤醒检测共用缓存加载器，Python/native 双版本仍须匹配。

默认组件缓存为后端用户的 `N.E.K.O/voice-resources/wake-runtime`，与唤醒模型缓存相邻。关闭唤醒词时不自动安装可选组件；组件与模型不会在初次打开页面时自动下载。独立组件保留至多三份不可变版本，损坏时发布新版本，不覆盖正在使用的 DLL。分发新构建时应创建新发布版本并同步代码中的下载地址、大小与摘要，不能替换已被固定摘要引用的附件。

## 源码构建定制 runtime（维护者）

以下流程适用于已安装 Visual Studio 2022、CMake、Git 和 `uv` 的 Windows x64、Python 3.11 环境。构建脚本会固定上游 commit，应用两个补丁，运行 native helper 测试，并验证 wheel 的 Python/native 版本。

```powershell
$python = (Resolve-Path .venv\Scripts\python.exe).Path
powershell -File scripts\wake_word\build_wake_word_runtime.ps1 `
  -Python $python `
  -OutputDirectory .wake-word-runtime

$wheel = (Get-ChildItem .wake-word-runtime\sherpa-onnx\dist\*.whl |
  Select-Object -First 1).FullName
uv pip install --python $python --force-reinstall $wheel
uv run --no-sync python -c 'import sherpa_onnx as s; print(s.__version__, s.version)'
```

最后一条命令必须输出两次 `1.13.8+neko.kws2`。如果只看到 `1.13.8`，说明普通上游 wheel 仍在环境中，应重新安装构建产物；不要用 `uv sync --extra wake-word` 来替代这一步。

`uv sync --extra wake-word` 现在只启用项目的空 extra，不会安装错误的上游 wheel。它可以用于同步项目其余依赖，但不会替代定制 runtime 的安装。

## 发布新构建（维护者）

`.github/workflows/wake-word-runtime.yml` 平时只把 wheel、`build-manifest.json`、native 测试程序和上游 sherpa-onnx 的 `LICENSE` 上传为保留 14 天的 Actions artifact。需要把一次构建发布成「修复资源」可用的固定附件时：

1. 在 Actions 页手动运行 **Wake word runtime**，选择要构建的分支，`release_tag` 填一个新 tag。tag 必须以 `wake-word-runtime-v<版本>` 开头，`<版本>` 是这次实际构建的版本（构建脚本里的 `$wakeVersion`，即 `build-manifest.json` 的 `runtime_version`，`+` 写成 `-`），后面可以加 `-<后缀>`，例如当前版本 `1.13.8+neko.kws2` 重新构建时用 `wake-word-runtime-v1.13.8-neko.kws2-win-py311-build2`。版本对不上时 publish job 会在创建任何东西之前失败，免得把旧版本 wheel 发到新版本的 tag 下（附件不能替换）。不填 `release_tag` 时只上传 artifact，和以往一样。
2. 构建通过后，独立的 publish job（只有它有 `contents: write`）以本次运行的提交为 `--target` 新建这个 tag，并创建一个预发行版（prerelease，不标记为 latest），附件为 wheel、`build-manifest.json` 和 `SHERPA-LICENSE.txt`。tag 或同名 Release（包括草稿）已存在时直接失败，从不替换已有附件；构建前也会先检查一次，避免白跑。
3. Release 说明和运行摘要里给出新 wheel 的下载地址、字节数和 SHA256。把它们同步到 `main_logic/voice_input/wake_word/runtime_bundle.py` 的 `WHEEL_URL`、`WHEEL_BYTES`、`WHEEL_SHA256`（文件名或版本变化时同时改 `WHEEL_NAME` / `SUPPORTED_RUNTIME_VERSION`），走 PR 合并。
4. 合并后手动运行 **Wake word runtime URL check**，`mode` 选 `full`，确认转绿。

如果 publish job 在建好 tag 之后失败（例如超时），重跑会因 tag 或同名 Release 已存在而失败。先看这个 Release 和三个附件是否已经完整发布：完整就不用重跑，直接做第 3、4 步。确实需要重建时，先确认这个 tag 没有被代码引用，再由维护者手动删除同名 Release 和 tag 后重跑，或者换一个新 tag。

## 下载地址巡检

`.github/workflows/wake-word-runtime-url-check.yml` 防止固定附件丢失或被替换而长期无人发现。它通过 `scripts/check_wake_word_runtime_url.py` 从 `runtime_bundle.py` 读取上述常量（workflow 里不另存一份），请求不带任何 token：

- 每天定时：匿名 HEAD 请求并跟随重定向，最终不是 2xx 即失败；对象存储对 HEAD 返回 403 时改用只取 1 字节的 `Range` GET，不下载整个 wheel。服务器给出总大小时也核对字节数。
- 发布 Release（published）时，或手动运行并选择 `mode: full`：完整下载 wheel，核对字节数和 SHA256。Release 事件检查的是默认分支上的常量。注意由 workflow 自身 `GITHUB_TOKEN` 创建的 Release 不会触发其他 workflow，所以用上面的 publish job 发布后需要按第 4 步手动运行。

这个检查变红说明用户点「修复资源」时下载会失败（或校验不通过），应尽快恢复附件或发布新构建并更新常量。本地可以用 `uv run --no-project --python 3.11 python scripts/check_wake_word_runtime_url.py --head`（或 `--full`）复现。

## 准备模型

模型权重与 Python runtime 分开管理。使用下面的命令下载并校验固定 SHA-256 的模型归档：

```powershell
uv run --no-sync python scripts\provision_wake_word_model.py --model-dir .wake-word-model
# 将 NEKO_WAKE_WORD_MODEL_DIR 设为脚本输出的实际 versions/<version> 目录。
```

必须先按上一节安装定制 wheel，再执行模型准备。脚本在临时目录下载、验证完整包并真实加载；成功后才原子发布当前版本清单。模型缓存根目录不能直接作为显式模型目录。缺资源、取消或发布失败时保留原有效版本，不覆盖正在运行的文件。

## 启动前检查

在启动 NEKO 的同一个 Python 环境中运行：

```powershell
uv run --no-sync python -c 'import sherpa_onnx as s; from main_logic.voice_input.wake_word.sherpa_backend import SUPPORTED_RUNTIME_VERSION; assert s.__version__ == SUPPORTED_RUNTIME_VERSION; assert s.version == s.__version__; print(s.__version__, s.version)'
```

仅在该检查通过且模型实际加载成功时启用唤醒词。未启用唤醒词时，模型或组件缺失不阻止纯声纹激活；已经启用的唤醒能力异常时，统一激活门控保持阻断。页面中的下载不改变启用偏好；页面缓存发现也支持加载已安装版本。

## 分发边界

独立构建 workflow 产生 Windows x64、Python 3.11 的 Actions artifact；产品修复入口使用固定 Release 附件，不依赖会过期的 Actions artifact。桌面后端构建 workflow 同样构建、安装定制 wheel，显式纳入冻结程序并执行加载 smoke 检查。不同 Python 版本或平台必须重新构建并验收匹配组件。支持私有组件加载器的已打包应用可通过页面一键修复唤醒组件；其他缺失依赖仍需应用更新或安装修复，运行中不执行 `pip install`。

完整资源、录入和两仓库部署边界见 [声纹录入与语音激活修复](voice-readiness.md)。

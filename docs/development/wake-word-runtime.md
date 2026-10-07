# Wake-word runtime 安装与启动

唤醒词功能需要项目维护的 Sherpa ONNX 定制 wheel。上游 `sherpa-onnx==1.13.8` 不包含 NEKO 所需的时间戳和 decoder 修复，不能用于生产唤醒检测。backend 会同时校验 Python 包和 native runtime 的版本必须为 `1.13.8+neko.kws2`。

## 安装定制 runtime

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

独立构建 workflow 产生 Windows x64、Python 3.11 的 Actions artifact。桌面后端构建 workflow 同样构建、安装定制 wheel，显式纳入冻结程序并执行加载 smoke 检查。不同 Python 版本或平台必须重新构建并验收匹配组件。已打包应用通过应用更新或修复恢复组件，运行中不执行 `pip install`。

完整资源、录入和两仓库部署边界见 [声纹录入与语音激活修复](voice-readiness.md)。

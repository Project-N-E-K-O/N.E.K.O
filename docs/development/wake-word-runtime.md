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
& $python -c 'import sherpa_onnx as s; print(s.__version__, s.version)'
```

最后一条命令必须输出两次 `1.13.8+neko.kws2`。如果只看到 `1.13.8`，说明普通上游 wheel 仍在环境中，应重新安装构建产物；不要用 `uv sync --extra wake-word` 来替代这一步。

`uv sync --extra wake-word` 现在只启用项目的空 extra，不会安装错误的上游 wheel。它可以用于同步项目其余依赖，但不会替代定制 runtime 的安装。

## 准备模型

模型权重与 Python runtime 分开管理。使用下面的命令下载并校验固定 SHA-256 的模型归档：

```powershell
uv run python scripts\provision_wake_word_model.py --model-dir .wake-word-model
$env:NEKO_WAKE_WORD_MODEL_DIR = (Resolve-Path .wake-word-model).Path
```

模型准备脚本完成后仍必须按上一节安装定制 wheel。模型目录缺失或 runtime 版本不匹配时，唤醒检测会保持关闭并报告对应错误。

## 启动前检查

在启动 NEKO 的同一个 Python 环境中运行：

```powershell
& $python -c 'import sherpa_onnx as s; assert s.__version__ == "1.13.8+neko.kws2"; assert s.version == s.__version__; print("wake-word runtime ready")'
```

仅在该检查通过、`NEKO_WAKE_WORD_MODEL_DIR` 指向已准备的模型目录时启用唤醒词。没有模型或没有定制 wheel 时，其他声纹激活路径仍可正常使用。

## 分发边界

当前构建 workflow 产生的是 Windows x64、Python 3.11 的 Actions artifact。artifact 适合验证和短期取用，不是稳定发布地址；不同 Python 版本或平台必须重新构建匹配的 wheel。后续如需面向用户分发，应将经过验证的 wheel 发布到稳定 Release 资源或受控包源，并同步更新本页的下载步骤。

# RVC 翻唱插件

说「给我唱一首… / 翻唱…」时：联网搜歌 → 用**项目内** `vendor/rvc` 做音色转换 → 推到播放器。

## 插件界面（小前端）

与其他插件一致，在**插件管理器**中打开本插件可看到：

- **面板**：运行状态、功能菜单、试唱、推理设置
- **快速开始 / 声音训练**：Markdown 指南（`doc/`）
- **打开面板**：列表页「打开面板」→ `/plugin/rvc_cover/ui/`

也可直接访问：`http://127.0.0.1:<插件端口>/plugin/rvc_cover/ui/`

## 重要：不碰你原来的 D:\\RVC

本插件默认读写的是 N.E.K.O 仓库里的副本：

```
N.E.K.O/vendor/rvc/
```

第一次（或更新音色后）从你的整合包**只读复制**过来（默认含**声音训练** Gradio UI + pretrained）：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup_rvc_vendor.ps1
```

可选参数：

- `-SourceRoot "D:\RVC"`（默认）
- `-SkipRuntime` 不复制 6GB+ 的 python runtime
- `-SkipWeights` 不复制音色权重
- `-SkipTraining` 只要推理、不要训练 UI / pretrained
- `-SkipLogs` 不同步 `D:\RVC\logs` 实验项目（Gradio「无项目」时不要加这个）
- `-IncludeUvr` 额外复制 UVR5 分轨权重

脚本**不会**改写源目录里的任何文件。

## 声音训练（保留）

训练走 vendored 副本里的原版 Gradio，与 `D:\RVC\启动AI.bat` 同入口，但工作目录在 `vendor/rvc`：

```bat
scripts\start_rvc_training.bat
```

浏览器打开 `http://127.0.0.1:7897` →「模型训练」页。

训练产物写在 `vendor/rvc/logs/`。若 Gradio 训练页显示「无项目」，说明移植时未同步实验目录——重新跑一次：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup_rvc_vendor.ps1
```

（默认会从 `D:\RVC\logs` 只读复制实验项目；不要加 `-SkipLogs`。）

导出 / 同步到推理权重目录：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\sync_rvc_weights.ps1
```

然后在插件设置页或 `plugin.toml` → `[rvc].model_name` 填对应 `.pth` 文件名。

## 配置

`plugin.toml` → `[rvc]`（也可在面板「推理设置」里保存）：

| 键 | 默认 | 说明 |
|---|---|---|
| `rvc_root` | `vendor/rvc` | 相对仓库根目录 |
| `python_path` | `vendor/rvc/runtime/python.exe` | 用副本里的 python |
| `model_name` | `Ai糯糯雫.pth` | 音色文件名 |
| `use_uvr` | `false` | v1 未接分轨 |

## 触发

1. 语音/文本：`给我唱一首晴天`、`翻唱…`、`用糯糯的声音唱…`
2. LLM 工具：`sing_cover`
3. 插件面板「试唱」
4. HTTP：`POST /runs` → `plugin_id=rvc_cover` / `entry_id=sing_cover`（聊天正则与面板共用）

## 注意

- 首次加载模型较慢，占 GPU
- v1 整轨转换，非 UVR 专业分轨翻唱
- 大文件在 `.gitignore`（`vendor/rvc/runtime`、`assets` 等），需本机跑 setup 脚本

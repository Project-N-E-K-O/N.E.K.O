# RVC 声音训练

训练使用 **N.E.K.O 仓库内** 的 `vendor/rvc` 副本（与翻唱同一套环境），不会写入你原来的 `D:\RVC`。

## 启动训练界面

在**插件管理**里对「RVC 翻唱」点「启动」会自动拉起 `vendor/rvc` 的 Gradio（`http://127.0.0.1:7897`）；点「关闭」会自动停掉本插件拉起的进程（若端口上已是你手动开的训练窗，则不会强杀）。

也可手动：

```bat
scripts\start_rvc_training.bat
```

浏览器打开 `http://127.0.0.1:7897`，进入「模型训练」页，按原版 RVC 流程：预处理 → 提取特征 → 训练 → 导出。

若下拉里「无项目」，在仓库根目录执行（会只读同步 `D:\RVC\logs` 实验到 `vendor/rvc/logs`，不改原目录）：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup_rvc_vendor.ps1
```

然后刷新 Gradio 页即可看到项目名。

## 训练后给翻唱用

训练产物默认在 `vendor/rvc/logs/`。同步到推理权重目录：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\sync_rvc_weights.ps1
```

然后在本插件设置页把「默认音色」改成对应 `.pth` 并保存。

## 刷新训练环境

若缺少 `infer-web.py` 或 `assets/pretrained*`：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup_rvc_vendor.ps1
```

（默认已包含训练 UI；只要推理可加 `-SkipTraining`。）

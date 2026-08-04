# RVC 翻唱 · 快速开始

在插件管理器中打开本插件面板，即可查看状态、选音色、改推理参数并试唱。

## 能做什么

1. **语音/文本触发**：对角色说「给我唱一首晴天」「翻唱…」「用糯糯的声音唱…」
2. **LLM 工具**：模型可调用 `sing_cover`
3. **面板试唱**：在设置页填歌名后点「开始翻唱」
4. **声音训练**：见「声音训练」指南；入口脚本 `scripts/start_rvc_training.bat`

## 第一次使用

在仓库根目录执行（只读复制你的 RVC 整合包到 `vendor/rvc`，**不会改** `D:\RVC`）：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup_rvc_vendor.ps1
```

确认 `vendor/rvc/assets/weights` 里有 `.pth` 音色后，在面板选择默认模型并保存。

## 注意

- 首次加载模型较慢，会占用 GPU
- 当前为整轨转换（非专业 UVR 分轨翻唱）
- 大文件在 `.gitignore`，换机器需重新跑 setup

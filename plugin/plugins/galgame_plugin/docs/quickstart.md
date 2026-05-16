# Galgame 游玩助手快速开始

这份 Markdown 是备用说明；插件管理器里实际显示的是 `surfaces/quickstart.tsx`，会跟随界面语言切换。

## 推荐流程

1. 进入 `galgame_plugin` 详情页，打开“界面”标签，先看顶部依赖和运行状态。
2. 确认 OCR 资源。RapidOCR 和 DXcam 默认随程序提供；中文模型可直接使用，日文、韩文或英文模型缺失时按横幅提示下载。
3. 打开游戏，让画面停在有文字的位置，然后刷新 OCR 窗口列表。
4. 如果没有自动匹配到正确窗口，手动选择识别窗口。
5. 在 OCR 窗口区域应用推荐校准或自动重新校准，必要时为不同窗口尺寸保存 profile。
6. 如果截图黑屏或 OCR 效果差，安装 Textractor，并在 Memory Reader 区域选择候选进程。
7. 选择工作模式：`silent` 只记录，`companion` 生成陪伴回应，`choice_advisor` 关注选项建议。
8. 在插件状态页查看运行状态、OCR 运行时、快照、最近台词、最近选项、事件和 Agent 响应。

## 面板对应关系

| 区域 | 作用 |
| --- | --- |
| 安装与依赖 | 检查 RapidOCR 模型、Textractor，并管理后台安装任务 |
| OCR 与窗口 | 选择截图后端、识别窗口、截图校准、屏幕感知和视觉辅助 |
| Memory Reader | 选择 Textractor 候选进程，锁定读取目标，处理截图不可用的游戏 |
| 插件状态 | 集中查看运行状态、快照、历史台词、选项、事件和 Agent 推送 |
| 插件入口 | 解释当前台词、总结场景、建议选项、训练或验证 OCR 屏幕感知模型 |

## 排查提示

- 窗口列表为空：确认游戏已经启动、没有最小化，并停在有文字的画面。
- DXcam 黑屏：切到 MSS / PyAutoGUI / PrintWindow，或改用 Memory Reader。
- 识别语言不对：先调整 RapidOCR `lang_type` / OCR languages，再下载对应模型。
- Agent 不回应：确认插件正在运行，模式为 `companion` 或 `choice_advisor`，并且目标 AI 已配置。

本教程只说明路径，不会自动安装依赖、切换模式或推进游戏。

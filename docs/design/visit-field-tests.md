# 猫娘串门 · 实测清单与验收字段（T1~T15）

> **状态：当前记录（实测进行中）。** 本文是设计稿 [猫娘串门基础设施设计](./visit-infrastructure) §3.12「实测清单 T1~T15」的结果表：每项测什么、判据与失败退路以设计稿 §3.12 为准，这里只记每次实测的日期、机器、合成模式、结论与 `chrome://webrtc-internals` 关键字段。测量脚本与步骤见 `scripts/visit_probe/README.md`。

## 怎么填

- 一项在不同平台 / 合成模式下分别实测时各占一行（例如 T3、T4 的 Windows 与 macOS）。
- **日期**写实测当天（`YYYY-MM-DD`）；**机器 / 系统**写操作系统、屏幕分辨率与刷新率、Electron 窗口类型；**合成模式**写 Windows 的「直通」（GPU 合成）或「兼容」（`disable-gpu-compositing`），macOS 写系统默认。
- **结论**只写「已通过」「未通过」「待测」之一，再加一句关键现象；判据与失败后的退路不在这里改，改设计稿 §3.12。
- 关键字段取自 `chrome://webrtc-internals` 的 outbound-rtp（发送侧）或 inbound-rtp（接收侧）稳态值；本项不涉及视频编码时写「不适用」，没有记录的字段写「未记录」。
  - `framesPerSecond`：稳态帧率；`frameWidth / frameHeight`：稳态编码分辨率（看 QP 质量缩放器是否降分辨率）
  - `qualityLimitationReason`：`none / bandwidth / cpu / other`
  - `scalabilityMode`：LiveKit 应为 `L1T1`
  - `encodings.length`：`RTCRtpSender.getParameters().encodings.length`，应为 1（无 simulcast）
  - `encoderImplementation`：软编 / 硬编实现名

## 结果表

| 编号 | 内容 | 日期 | 机器 / 系统 | 合成模式 | 结论 | framesPerSecond | frameWidth | frameHeight | qualityLimitationReason | scalabilityMode | encodings.length | encoderImplementation |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| T1 | 同源 iframe 拿到原生 `WebSocket`，父页 `_activeWs` 不变 | 2026-10-02 | Windows，1920×1080 单屏 60 Hz，Electron Pet 窗 | 直通 / 兼容 | 已通过（细节见设计稿 §3.12） | 不适用 | 不适用 | 不适用 | 不适用 | 不适用 | 不适用 | 不适用 |
| T2 | 父页 `postrender` 同任务取帧：300 帧无黑帧，分数累加器恰好 30 fps | 2026-10-02 / 2026-10-03 | Windows，1920×1080 单屏 60 Hz，Electron Pet 窗 | 直通 / 兼容 | 已通过（兼容模式黑帧诊断重测见设计稿 §3.12） | 未记录 | 未记录 | 未记录 | 未记录 | 未记录 | 未记录 | 未记录 |
| T3 | iframe 透明；`pointer-events:none` 下穿透与无 iframe 时一致 | 2026-10-02 / 2026-10-03 | Windows，1920×1080 单屏 60 Hz，Electron Pet 窗 | 直通 / 兼容 | 已通过（兼容模式访客不透明像素命中 2026-10-03 补测；细节见设计稿 §3.12） | 不适用 | 不适用 | 不适用 | 不适用 | 不适用 | 不适用 | 不适用 |
| T3 | 同上（macOS） | — | macOS | 系统默认 | 待测 | 不适用 | 不适用 | 不适用 | 不适用 | 不适用 | 不适用 | 不适用 |
| T4 | iframe 内透明 WebGL 画布叠层的合成表现 | 2026-10-02 | Windows，1920×1080 单屏 60 Hz，Electron Pet 窗 | 直通 / 兼容 | 已通过（细节见设计稿 §3.12） | 不适用 | 不适用 | 不适用 | 不适用 | 不适用 | 不适用 | 不适用 |
| T4 | 同上（macOS） | — | macOS | 系统默认 | 待测 | 不适用 | 不适用 | 不适用 | 不适用 | 不适用 | 不适用 | 不适用 |
| T5 | `destination-in` 打包输出正确（alpha → 亮度），否则改 WebGL 打包 shader | 2026-10-02 | Windows，1920×1080 单屏 60 Hz，Electron Pet 窗 | 直通 / 兼容 | 已通过（细节见设计稿 §3.12） | 不适用 | 不适用 | 不适用 | 不适用 | 不适用 | 不适用 | 不适用 |
| T6 | TRTC `profile` 对自定义轨是否生效；限速 400 kbps 时掉帧还是掉画质；自定义消息超限行为 | — | — | — | 待测 | | | | | | | |
| T7 | TRTC 远端轨挂到 2 px `<video>` 稳定触发 rVFC；用量按视频计 | — | — | — | 待测 | | | | | | | |
| T8 | LiveKit vp9 `L1T1` + `maintain-framerate` 限速下保 30 fps、单编码层；vp8 / vp9 画质对比 | — | — | — | 待测 | | | | | | | |
| T9 | 跨区可达性与 RTT（各 ≥20 场），决定 `cross_region_unsupported` 是否放开 | — | — | — | 待测 | | | | | | | |
| T10 | Pet 被 hide-all / 遮挡 / 兼容模式 1×1 时抓帧是否继续 | — | — | — | 待测 | | | | | | | |
| T11 | `http://<LAN IP>` 非安全上下文下 SDK 是否拒发 canvas 轨 | — | — | — | 待测 | | | | | | | |
| T12 | alpha 边缘画质（发丝、半透明部件）经 H.264 / VP9 528 kbps（OD-15 v4 前为 560）| — | — | — | 待测 | | | | | | | |
| T13 | TRTC host 以观众角色进房能否收发自定义消息（大陆上线闸门） | — | — | — | 待测 | | | | | | | |
| T14 | 就地改打包画布尺寸后同一条轨是否继续出帧、对端尺寸跟着变 | — | — | — | 待测 | | | | | | | |
| T15 | LiveKit Cloud 能否关闭自动建房（`DeleteRoom` 后未过期 JWT 再连是否重建房间） | — | — | — | 待测 | | | | | | | |

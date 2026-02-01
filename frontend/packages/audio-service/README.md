# @project_neko/audio-service

跨平台音频服务（Web / React Native）：

- **Web**：`getUserMedia + AudioWorklet` 采集麦克风 → 通过 Realtime(WebSocket) 上行；下行支持 PCM16 与（可选）OGG/OPUS 流式解码后播放，并提供振幅回调用于口型同步。
- **React Native**：优先适配 `react-native-pcm-stream`（Android/iOS 原生模块），后续 native module API 变更仅需调整适配层。

> 注意：这是 N.E.K.O monorepo 内部 workspace 包，目前入口指向 TS 源码，不是可直接发布到 npm 的产物。


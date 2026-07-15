# Phase 3：Soniox 与 Core 独立 ASR 路由

## 路由

- `ASR_ROUTING_MODE=auto`：中国大陆走当前 Core 对应 ASR；非中国大陆在 Soniox key 可用时优先 Soniox，否则走当前 Core ASR。
- `ASR_ROUTING_MODE=core`：始终使用当前 Core 对应 ASR，不借用其他 Core 的凭据。
- `ASR_ROUTING_MODE=soniox`：强制 Soniox；缺 key 或建连失败时明确失败。
- `ASR_USER_REGION` 用于产品地区路由；缺失时保守按中国大陆处理。`SONIOX_REGION=us|eu|jp` 只覆盖固定数据区域，不接受任意 WebSocket URL。

凭据可由配置管理器的 `SONIOX_API_KEY` / `SONIOX_REGION` 或同名开发环境变量提供。桌面客户端不内置项目主 key，本阶段不包含临时 key 后端或统一计费。

## 话轮边界

Soniox 的稳定 token `is_final=true` 仍只是稳定片段。只有 `<end>` 生成一次完整 `ExternalTextTurn`。`<end>` / `<fin>` 被过滤，不进入字幕、历史、日志或 Core。

Provider metadata 显式声明 `provider_endpoint` 与 `semantic_endpoint`。Soniox 两项均为 true，因此启动时不构造、不加载 Smart Turn，也不发送 `finalize`；Silero 仅保留本地 speech-start 与打断职责。普通 manual Provider 继续动态构造 Smart Turn。

## Core 边界

ASR worker 只产出 provider-neutral transcript event，不知道当前 Core。完整话轮统一经过本地历史，再由全局应答仲裁器注入 Core。Qwen 保持 `conversation.item.create(input_text)` 与 `response.instructions` 双保险。独立 ASR 启用时不再向 Core 上传用户麦克风音频。

## 故障与费用边界

`auto` 只允许在任何音频上传前、Soniox 初始建连失败时回退 Core。已上传音频后断线仅在 Soniox 内重连一次并重放有限的当前 PCM；再次失败则本轮不提交。401/402 不重试；429 先指数退避，但仍只允许一次有限重连，禁止循环重试。连接在语音会话开始时建立、结束时关闭，并记录连接时长、音频时长、帧数与重连次数。

本阶段不实现基于 RNNoise/Silero/声纹的长期智能启停。因此 Soniox 在连接存续期间的静音、pause 与 keepalive 仍可能计费，尚不应作为所有用户永续开启的默认值。

## 不在本阶段

设置 UI、临时 key 服务、平台统一付费、VAD/RNNoise/声纹最终选型、智能永续启停、翻译、说话人分离、Soniox TTS 与游戏 ASR 适配均不在本 PR。

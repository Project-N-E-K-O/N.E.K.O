# Smart Turn v3 runtime contract

本文记录独立 ASR 运行时中 Smart Turn、流式端点、背压恢复和 Core 转录路由之间的契约。它描述当前生产实现，不替代 provider 或 Electron 的验收说明；人声与 Electron 验收流程见 [`smart-turn-v3-human-electron-validation.md`](smart-turn-v3-human-electron-validation.md)。

## 端点职责

- 原生流式端点由 provider 负责逻辑回合边界。Qwen、OpenAI `server_vad`、Soniox `<end>` 以及 Grok、Step 的 provider endpoint 路径不加载 N.E.K.O Smart Turn。
- 分段 ASR 使用 Smart Turn 在提交一个或多个有界 provider 请求前封存逻辑回合；GLM 和 Gemini 使用此路径。
- Silero、RNNoise 可以抑制空闲上传或唤醒 transport，但不决定原生 provider endpoint 的逻辑结束。provider buffer commit、硬超时、最大回合时长和手动 commit 仍由 ASR session/controller 负责。
- VAD 只产生 speech start、resumed speech 和 candidate pause 事件，不产生 `TURN_COMPLETE` 或 `FORCE_COMMIT`。

## 转录路由与身份

`VoiceInputRegistry` 是 Core、游戏路由和受信插件桥接共用的高层 transcript 边界；它不接收 PCM，也不选择 provider。每条路由由完整 `VoiceTurnToken` 固定：consumer 切换、注销、lease 变化、PCM 缺口、abort 和 session teardown 通常都会终止旧路由，不能把结果转交给新 consumer。

`ingress_backpressure` 是唯一的已声明例外：如果 final 已被 runtime 接受（包括仍在释放 Smart Turn lease 的 accepted final），其 pinned Registry route 必须保留到该 final 被提交或明确退休；被背压打断、尚未被接受的那一轮仍按上述规则终止。

最终结果在调用业务代码前先消费路由。重复、迟到、空 final 或回调失败都不能恢复或重定向已消费的路由；空 final 只做终结清理，不进入 Core 或游戏。provider-native final 与 Smart Turn 封存的 final 共用这套 Core 路由契约。

## 背压、封存与恢复

生产麦克风输入不等待 Silero 回调或 Smart Turn 推理。标准化的 16 kHz、单声道、signed PCM16 进入同时受 1 秒音频量和 128 帧限制的队列；音频不能占用控制槽。队列溢出会使整个 candidate 或 active turn 失效，不会丢弃中间帧后继续生成部分转录。

Core 处理 ingress 背压时清空待处理 PCM，并按 identity 调用恢复逻辑。背压只退休 PCM 被打断的那一轮：运行时已经接受的 final、仍在排队或派发中的 final，以及 Smart Turn lease 仍在释放的 accepted final，都保留 Core 投递和 pinned Registry route，因此上一句仍只回答一次。若 lease 释放期间回调被取消，accepted 槽位必须先提交或退休，再传播取消。

`IndependentAsrRuntime.abort()` 返回仍待投递的保留轮次集合；其他 abort 原因返回空集合。Core 使用这个结果保留路由，不重复判断背压原因或读取运行时状态。dispatcher 用同一份待投递记录覆盖 accepted reservation、排队与派发阶段，只在释放、完成或整体作废时移除。

保留轮次已经绑定的唤醒词修正票据也保留到该 final 消费，即使 activation runtime 已关闭。背压可推进 audio generation，但票据不能转移给下一轮；session epoch、麦克风路由或 lease 变化以及显式取消仍会使它失效。

只有 session epoch 变化或显式 teardown（停止、挂起、路由切换、致命错误）才能退休 accepted final。检测器自身队列溢出会安装串行 reset barrier；barrier 完成前提交返回 `BACKPRESSURE`。溢出前完成的评估不能推进新 detector epoch 的语义身份，也不能发布完成或写入完成诊断。

## Smart Turn 语义超时

Smart Turn 路径中，语义 `INCOMPLETE` 会按 continuation interval 重试，直到当前严格截止时间。首次严格截止时间由 `max_endpoint_wait_seconds`（当前 15 秒）设定；无 Silero 时，RNNoise 确认的人声活动可刷新该静默窗口，RMS 回退的延长则受首次 `INCOMPLETE` 起的绝对上限约束。截止后仍为 `INCOMPLETE` 时，通过正常完成路径以 `semantic_timeout` 封轮；这是语义结果，不是 endpointing failure，不会拆掉 ASR session。

`periodic_no_vad` 不得覆盖排队中的 `strict_retry`，不得在 strict 推理期间终止等待，也不得推迟截止后的封轮。模型资产缺失、推理异常和 VAD failure 才进入 `UNAVAILABLE`/BLOCKED 路径。

Silero 不可用时，PCM 仍可进入 Smart Turn 作为语义证据。RNNoise 明确判为人声活动时，长语音按正常静默窗口刷新严格截止时间。RNNoise 不可用或低于起音阈值时，则回退到 RMS 噪声底：低分数不能证明静音，达到 RMS 门槛的轻声帧仍允许延长等待，静音帧不刷新。RMS 无法可靠区分低音量语音与底噪，因此只有这条 RMS 回退路径受首次 INCOMPLETE 起两倍 `max_endpoint_wait_seconds` 的绝对上限约束（默认约 30 秒）；到期后仍为 INCOMPLETE 就正常封轮。这是缺少明确人声证据时的有界等待取舍，超长未完成语句也可能被分轮；有 VAD 的恢复说话事件仍按正常语义重置等待。

## Provider 重连裁剪

Soniox 重连时，`end_ms` 以当前连接的流起点为基准。重放从上一轮最后一个 final 词之后开始，并扣除重连后先发送的重放段；如果最后一个 final 词没有有效时间戳，则保留固定两秒尾部，不能沿用更早词的时间戳。

## 资源与验证边界

Smart Turn 模型资产由 `main_logic/asr_client/endpointing/models/manifest.json` 固定版本、来源和 SHA-256。运行时按需加载并单实例 single-flight；缺失或损坏资产是 `UNAVAILABLE`，与语义 `INCOMPLETE` 分开。关闭或失败 ASR session 时必须释放 detector、model adapter 和对应任务。

这些是安全和状态一致性契约，不是性能优化。回归测试覆盖 accepted final 的背压保留、DRAINING sealed turn、strict retry 与 semantic timeout、旧 epoch completion、Soniox 无时间戳回退，以及 route/identity 的一次性消费。


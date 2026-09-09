# ASR、VAD、SmartTurn、声纹链路排查

日常诊断默认写入应用的 `N.E.K.O_Main_YYYYMMDD.log`。新增代码须重启进程后生效；不需要开启原来的 `NEKO_SMART_TURN_DIAGNOSTICS` 独立 JSONL 开关，也不会开启音频录制。

## 检查方法

使用当前应用实际存储目录中的日志文件。启动器可改变存储根目录，不要仅根据仓库位置猜测日志目录。

```powershell
uv run --no-sync python scripts/check_asr_pipeline_log.py "日志绝对路径" --output "排查报告.json"
```

报告按匿名 `session_ref` 分组，列出各阶段证据、每轮准入结果、Core 处理结果和失败检查。默认保留最近 16 个会话、每个会话最多 512 条记录；截断、已知日志丢弃会标记。`not_observed` 表示未观察到证据，可能是阶段没执行、旧版本缺埋点、日志截断或丢弃，不能当作通过或直接判为根因。没有可关联记录的旧格式日志不会被猜测关联。

## 覆盖表

| 阶段 | 记录 | 能回答的问题 |
| --- | --- | --- |
| 服务端音频入口 | `audio_received` | 归一化音频是否进入独立 ASR，帧数与样本数是多少 |
| 本地处理 | `detector_audio`、`audio_submit` | 安静跳过、缓冲、抑制、背压、重连等待、身份过期、入队失败还是成功入队 |
| ASR 传输 | `provider_audio_written` | 音频写入回调是否完成；不等同于服务端确认识别 |
| VAD | `vad_activity` 或 `endpoint_diagnostic` 的 `vad_*` | 开口、继续、停顿，以及模型不可用／运行错误 |
| SmartTurn | `endpoint_diagnostic` | 为什么启动判断、完成／未完成、耗时、完成概率与阈值、过期／被新语音取代／取消、确认等待、重试等待、失败／降级 |
| Provider 边界 | `provider_started_received`、`provider_endpoint_received`、`provider_boundary_guard_failed` | Provider key、音频起止位置、具体失败条件及失败前的归属状态 |
| 最终文字 | `provider_final_received/ignored`、`asr_final_received/ignored` | final 是否进入处理，是否因回合已完成、身份／封口不匹配而忽略；不保存文字 |
| 声纹 | `speaker_score_*`、`speaker_capture_closed`、`speaker_fact_observed` | 有无评分、输入长度、最小长度、检查点、low／high／unavailable、候选归属；Provider 与 SmartTurn 路径均接入 |
| 准入和派发 | `admission_decision`、`transcript_resolution` | 正式 forward／drop／abandon 及原因，以及 dispatcher 是否应用 |
| Core | `transcript_callback`、`voice_registry_final`、`core_voice_delivery` | 回调进入／失败、路由拒绝、空 final、文本拒绝、热切换等待、取消、提交异常或请求已提交 |
| 失败收尾 | `ASR incident`、`ASR cleanup` | 首因、清理组件结果和剩余义务；新记录带同一匿名会话关联 |
| 时间轴与凭据清理 | `provider_state_change` | 谁发起重置、原因、清理前后时间轴／凭据状态，以及退休凭据为何不能继续使用 |

`speaker_fact_observed` 是事实观察，不等于最终拒绝。`transcript_callback=returned` 也不等于回复成功。`core_voice_delivery=submitted` 只证明应用提交调用返回，不证明远端生成完成、TTS 成功或扬声器播放。

Provider 自己负责断句的模式中，SmartTurn 没运行是正常现象；报告仅在观察到明确的 Provider 模式配置时标记 `not_applicable`，不凭日志缺失推断模式。

## 重置与凭据追踪

`provider_state_change` 会进入检查器的 `session_findings`，不把会话级清理猜配到最后一个回合。

- `initiator` / `reason` 记录直接调用路径及原因；Runtime 发起的 Detector 重置还携带 `reset_initiator` / `reset_reason`，用于区分用户停止、背压、候选超时与传输重连。VAD 加载／运行失败、语义队列溢出、声纹验证器替换和关闭也有明确来源。
- `operation` 区分重置请求、段状态清理、声纹身份清理、凭据退休、Runtime 别名清理和退休凭据校验失败。`operation_id` 在所属对象内递增；嵌套操作完成顺序可能不同于编号顺序。`outcome` 记录返回、拒绝、失败或取消；返回不代表远端回复成功。
- `before_*` / `after_*` 记录时间轴代次、Detector epoch、音频游标、末帧序号、物理凭据代次、退休证明数量、精确区间凭据数量，以及 Runtime 的物理／逻辑别名和账本数量。只记录数量、布尔值与代次，不输出凭据对象、声音或向量。保留游标的局部清理也会记录，不能仅凭操作名断言时间轴发生变化。
- `accounting_retirement_unproven` 记录 `proof_present`、旧凭据的 epoch／时间轴／操作号及匹配结果：证明仍在但时间轴不匹配，与证明已不在表中可以直接区分。仅凭这一失败不能推断具体重置来源，需与同会话前面的重置记录对照。
- Detector 的观察器固定绑定原会话，脱离 Runtime 后的关闭记录仍归属旧会话。Runtime 清理携带源会话；过期的凭据消费记录保留原请求会话，并通过 `identity_matches` 和 `before_session_epoch` 显示当前状态已不同。

每个操作输出一条包含前后状态的有界记录，复用现有异步 writer，不增加逐帧写盘或后台轮询。缺记录、队列丢弃或检查器截断时仍只能判定证据不足。此埋点用于下一次复现，无法补回旧进程当时未记录的重置来源。

## 关联和容量

- 会话使用进程内随机身份生成的匿名引用；技术身份保留 audio／route／lease generation、回合号及 Provider key。迟到的 SmartTurn 结果保留发起时的语义身份，不借用后继回合。
- 同一会话中若回合号被不同路由复用，检查器按完整技术身份拆分。身份字段不全且无法唯一归属的记录不参与拼接，报告 `ambiguous_partial_turn_ids`。
- 音频进度记录首帧、每 5 秒累计量及阶段转换时的累计量，不逐帧写盘；累计范围以该记录的身份和原因字段为单位，flush 后开始新的累计窗口，不能跨窗口直接取最大值当整场总量。
- 进度最多保留 32 个聚合桶；每 Runtime 一个日志任务，最多 32 条待写记录，单次最多 16 条。复用进程级单后台 writer 和 32 个有界队列槽。队列满、日志失败、取消只影响诊断，不改变语音裁决；已有裁决日志的任务额度保持独立。
- 不保存 PCM、embedding、声纹实际余弦分数、Profile 内容、完整转写文字和异常消息。SmartTurn 的完成概率不是声纹相似度。

## 验收边界

自动化验证使用真实 Runtime／Detector／Admission／Dispatcher 状态机和确定性模型替身。真实麦克风、远端 ASR／回复服务、客户端接收和音频播放仍需现场验证。日志覆盖的是本地可观察的处理与交付边界，不承诺每个外部服务内部步骤都有证据。现场故障应先按新日志确认断点，再讨论算法／阈值修改。

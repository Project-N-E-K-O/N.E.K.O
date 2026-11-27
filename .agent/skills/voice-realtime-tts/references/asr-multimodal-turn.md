# 独立 ASR 回合里的屏幕 / 摄像头帧

代码：`main_logic/core/multimodal_turn.py`（抽样记录）、`main_logic/core/asr_runtime.py`（帧归属与冻结）、`main_logic/asr_client/runtime.py`（回合时间戳）、`main_logic/omni_realtime_client/_responses.py`（provider 侧提交）。

## 一次发声是一段时间，不是一个瞬间

屏幕共享持续推帧（前端大约每秒一帧）。一个独立 ASR 回合要把整段**抽样**成最多 `MAX_MULTIMODAL_TURN_IMAGES`（`config/model_defaults.py`，值为 3）张：开头、中间、结尾。

- 只留最后一张，会丢掉用户开口时指着的东西。
- 全留是无界输入，而且写进 realtime provider 会话的 item 事后删不掉。

实现在 `_CoreMultimodalTurnRecord.observe()`：开头、结尾各一个槽；中间那张从一个**有界候选集**（`_MAX_MIDDLE_CANDIDATES`）里取正中间。候选满了就丢掉「前后邻居跨度最小」的那个内部候选，端点不丢，所以候选集不随发声长度增长，幸存者大致铺满整段。

## 判据

1. ⚠️ **别给图片发明「张数闸、多余丢弃」。** 要对偶的是 realtime 原生路径的机制：那边一帧到了就进上下文，客户端只留最新一张（`_latest_image_b64`），所以永远不会攒。照抄那个语义。
2. **纯文本模式不会攒图。** 前端 `static/app/app-screen.js::getLiveVisionStreamBlockedReason` 把屏幕 / 摄像头流硬闸在「正在录音且语音会话活着」上，文本模式直接停流。别拿「文本模式攒了几十张」当改动理由，那个场景不存在。会攒的是 ASR 回合。
3. **新鲜度 TTL（`_independent_visual_frame_ttl_s`）只判最新那张**，它回答的是「画面流还活着吗」。开头和中间那两张本来就比 TTL 老（一句话可以说很久），它们的有效性由「拍摄时刻落在本回合窗口内」保证。
4. **provider 侧要独立再兜一次上限**（`_normalize_multimodal_turn_images` 和文字客户端的 `submit_multimodal_turn`）。core 已经抽过样，但 provider 的历史写进去删不掉，不能只靠上游守约。
5. **环境帧和一次性附件不共用预算。** 上限只管屏幕 / 摄像头的抽样帧。用户拖进来、贴进来的图是明确选中要发的，按张数砍等于静默丢掉他刚选的东西；普通文本轮同样不闸，两条路要一致。
6. **落地顺序不等于拍摄顺序，要贯穿到底。** 帧的校验是并发的。比较开头 / 结尾、候选集淘汰、取中间，三处都必须按 `captured_at`。只修其中一处会留下「事后排序也捞不回来」的丢帧。
7. **多图 item 的 WebSocket 帧上限。** 三张图在同一个 user item 里，超限时要按总预算重压所有图片 part（`_transport.py::_try_shrink_image_payload` 走多图分支），压不下去从最旧的开始摘。只重压第一张会让整条 item 仍然超限被丢，而后面的 `response.create` 照发，得到一轮「既没图也没转写进历史」的回复。

线上形状：OpenAI 兼容路径是三个图片 part 加一个文本 part，同属**一个** user item；Gemini 是三个图片 part 加文本 part，同属**一个** Content。都只触发一次回复。

## 帧归属：读对时间戳

归属判断统一在 `_CoreMultimodalTurnRecord.accepts()`：同一会话 epoch、同一路由代数、在本回合开始之后拍摄、且不晚于本回合的端点时刻。缓存兜底路径也走这个入口。

⚠️ **借用别的子系统的时间戳，要同时查「在哪儿写」和「什么时候清」。** 这里连着栽过两次：

- **什么时候清**：`_asr_turn_endpointed_at` 在 provider 给出最终结果后的重置块里被清成 `None`，**紧接着**才把转写派发给 core。core 冻结多模态回合时读它，读到的必然是 `None`。所以 runtime 另存了一个不随最终结果清除的 `_asr_last_turn_endpointed_at`，core 读这个。
- **在哪儿写**：`_asr_turn_audio_started_at` 在有些「确认开口」的路径上是在一次 `await` 之后才打点的，拿它当「用户开口时刻」等于把投递耗时也算进去。开口时刻用只钉在状态转移那一行的 `_asr_turn_onset_at`；旧字段留给首个 partial 的延迟指标，口径不要动。
- 端点截止值读记录下来的精确时刻，不要去采样「core 什么时候看到状态变了」：那个时刻必然往后漂，落在间隙里拍的帧会被算进这次发声。

做法：grep 出该字段的**全部**赋值点和清除点，逐个确认它们相对于你这个消费者的先后。「语义对」不等于「我读的时候它还在」。「打点不许被 `await` 隔开」这类句法性的不变量，可以直接写成读源码的守卫断言。

测试：`tests/unit/test_multimodal_turn_frame_sampling.py`、`tests/unit/test_independent_asr_multimodal_handoff.py`。

# TTS 路由与 worker

总览见 `docs/architecture/tts-pipeline.md`。这里只写不显然的部分。

## 两张表，别混

| 表 | 位置 | 管什么 |
|---|---|---|
| dispatch 注册表 | `utils/tts/provider_registry.py`（`TTSProvider`、`DispatchContext`、`resolve_selected`） | 谁被选中、解析成哪个 worker、设置页和探活的元数据 |
| 传输形态表 | `main_logic/tts_client/_registry_meta.py::TTS_PROVIDER_REGISTRY` | `ws_bistream` / `http_sentence` / `local`、音频格式、是否认软 flush |

- `get_tts_worker()` 返回的第三个值 `provider_key` 是**传输形态表**的键。`'free'` 故意不在那张表里（调用方拿到 `None` 元数据后走带文本规整的路径）。
- core 自带音色（Gemini / Step / Grok / free）还在 `utils/tts/native_voice_registry.py`，规则见 neko-guide。`utils/` 顶层的同名模块只是兼容别名。

## 加一个可 dispatch 的 provider

1. worker 写在 `main_logic/tts_client/workers/<name>.py`，带 `_<name>_is_selected(ctx)` 和 `_<name>_resolve(ctx)`。
2. 在 `main_logic/tts_client/__init__.py` 末尾 `_tts_providers.register(TTSProvider(...))`。元数据模块保持轻量，带 `soxr` / `websockets` 这类重依赖的 worker 在 `tts_client` 里绑定，避免循环 import。
3. 传输形态登记进 `TTS_PROVIDER_REGISTRY`。
4. 不要在 `get_tts_worker` 里加内联特判，也不要在 core 里写 provider 名字；core 通过 `main_logic.tts_client` 暴露的门面函数（如 `tts_provider_falls_back_on_failure`）问注册表。

约束：

- `get_tts_worker`、`get_config_manager`、`_get_voice_meta` 这些要留在 `main_logic.tts_client` 的顶层命名空间。大量测试用 `monkeypatch.setattr(tts_client, ...)` 换它们，搬走就整片失效。
- `priority` 越小越先判。现在的顺序是：`gptsovits` 10、`vllm_omni` 20、`custom` 25、`minimax` 30、`elevenlabs` 40、`cosyvoice` 50、`deepseek` 59、`mimo` 60、`doubao_tts` 65、`glm_tts` 66（以代码为准）。先于 core 自带音色和按 core 类型的默认 worker。
- 两种选中方式：按配置选中（下拉框 / 开关）和按音色元数据选中（克隆 / 设计出来的音色，看 `voice_meta.provider`）。`DispatchContext.voice_meta` 是惰性的：按配置选中的 provider 必须在任何音色存储查找之前就短路返回。
- `resolve` 返回的 `api_key_override` 用来防串 key。用户自配端点没填 key 时返回**空串**而不是 `None`，`None` 会让调用方回落到别的 TTS 槽位的 key。
- 用户自配端点（`fallback_on_failure=True`）失败时，日志只记错误码、provider、阶段，不记原始异常：异常文本可能带 key 或签名 URL。
- `is_selected` 抛异常不会中断 dispatch，只跳过该 provider 并留一条日志。

## 不显然的归类

- **MiMo 是 hosted，不是 core 自带音色。** 它的预置音色目录挂在 `TTSProvider.preset_catalog`，`/voices` 和 `validate_voice_id` 都问 dispatch 注册表。
- **MiMo 克隆没有远端音色 id。** 参考音频以 base64 存在本地音色元数据里（`clone_sample_b64`），每次合成请求内联带上。`get_voices_for_current_api(for_listing=True)` 会剥掉这个字段，列表接口不背大块数据；dispatch 和试听用 `for_listing=False`。
- **描述生成的音色不需要独立 worker。** ElevenLabs 的 design 落成普通 voice id（元数据 `source='design'`），合成走克隆那条路。

## GPT-SoVITS：启用开关与配置槽位

- 启用状态只有一个来源：`get_core_config()` 里派生的 `GPTSOVITS_ENABLED`。下拉框 `ttsModelProvider` **明确**选了某家时以它为准（是 `gptsovits` 才启用）；值为空、`follow_assist`、`follow_core` 时算「没明确选」，回落旧开关 `gptsovitsEnabled`。
  - 不能把 `follow_*` 当成明确选择：老用户用勾选框开的 GSV，下拉框一直停在默认的 `follow_assist`，当成明确选择会让他们升级后 GSV 失效。
  - 不能用「旧开关 OR 下拉框」：前端已不再写旧开关，保存是增量合并，旧的 `true` 会粘住，切走下拉框也关不掉。保存时提交了非 `gptsovits` 的下拉值，后端顺手把旧开关清成 `False`（`main_routers/config_router/core_config.py`）。
- ⚠️ **选中与配置可以分叉。** `_gptsovits_is_selected` 只看 `GPTSOVITS_ENABLED` 加 `tts_custom.is_custom`，不看角色有没有自定义音色。而 `_resolve_tts_worker_spec` 返回的 config 是 `tts_custom if has_custom else tts_default`。角色选了 core 自带音色时 `has_custom=False`，于是 provider 是 `gptsovits` 而 config 是 `tts_default`；没开自定义 API 时 `tts_default` 回落到 LLM 地址，拿它校验「GSV 地址是否合法」恒为真。
  - 做法：GSV 的可用性 / 地址预检读 `tts_custom`（worker 自己读的那个槽位），最好和 worker 共用一个 helper。评审意见说「用路由返回的 config」时，先查 worker 自己读哪个槽位。
- GSV 和 vLLM-Omni 的地址允许远程主机，只校验 scheme 和主机名（`utils/gptsovits_config.py::is_valid_http_url`）。这是既定取舍，「只放行本机地址」的建议不收。

## 语音模式下谁出声

入口是 `main_logic/core/tts_runtime.py::_resolve_session_use_tts`。文本模式恒用外部 TTS；语音模式默认用 realtime 模型自带的声音，以下情况改用外部 TTS：

1. 音色归属于用户自配的 provider（`selected_configured_tts_preset_provider_key`）。
2. 选中的 provider 声明了 `realtime_output_override=True`（现在是 `custom`、`deepseek`、`mimo`、`doubao_tts`），并且是**明确选择**：
   - TTS 下拉框（`TTS_PROVIDER` / `ttsProvider` / `ttsModelProvider`）的值就是这家；或
   - 角色的音色是这家目录里的音色**原名**（别名不算）。
   - 「跟随辅助 API」加上辅助 API 恰好是这家，不算。理由：很多人只是把辅助 API 当聊天模型用，下拉框明确选择才代表想换声音。
   - 音色 id 在任何本地音色存储里存在时不接管，保持原有路由（同名音色可能属于别家）。
3. 接管前问 `realtime_output_tts_provider_ready`：provider 自己 resolve 出来的 worker 是 `dummy_tts_worker` 或 `configured_tts_unavailable_worker` 都算不可用，退回 realtime 自带声音。自定义端点没填 Voice ID、又没有内置默认音色时也不接管。
   - 不要退回成「只看有没有 API Key」：本地自定义端点可以没有 Key。

既定取舍，同类评审意见按此驳回：

- 「辅助 API 默认值压住了另一家时，跳过它去认另一家」不收。实际 worker 由 `get_tts_worker` 的优先级决定，跳过只会让辅助默认值接管。
- 外部 TTS 接管后，realtime 侧如果只支持「文字加音频」输出，实时音频仍会生成然后被丢弃，等于两份都算用量。这是已接受的代价。

守卫：`tests/unit/test_tts_realtime_output_override.py`。

## 收尾信号 `audio_done`

下行消息 `{"type":"audio_done","speech_id":X}`。worker 往响应队列放 `("__audio_done__", sid)`（`AudioDoneEmitter`，`main_logic/tts_client/_infra.py`），`tts_response_handler` 转成 `send_audio_done`；realtime 自带声音那条路由 `on_audio_done` 回调触发。

- ⚠️ **方向不可反。** 音频头只有 `{type, speech_id}`，没有结束标记；逐句 TTS 的句间空档、网络停顿、真结束在前端完全同构。前端已有的 `voice_play_end`（前端发给后端，给主动搭话的闸门用）是前端从自己的播放状态推出来的，不是独立信息源，不能反过来当成「流结束」的依据。再有人提「前端播完回传后端」先讲清这一点。
- **必须 `await` 在该 sid 最后一块音频之后发。** 任何 fire-and-forget 都会让它插到尾音前面，正是这个信号要修的毛病。有 AST 守卫扫 `main_logic/`（`tests/unit/test_tts_audio_done_forward.py`）。
- **宁可漏发不可早发。** 被打断的轮不发；没放干净的轮不发；归属不明（没有 `speech_id`、输出运行时已换代）不发。漏发只让前端退回自己的放弃计时器，可以接受。
- 不镜像到 `sync_message_queue`：字幕窗、监视端都不消费音频。
- **Gemini（原生和免费档海外路由）故意不接。** 它只有 `turn_complete`，而 `turn_complete` 可能早于迟到的音频（`_gemini_support.py` 里有注释）。
- 前端入口是 `static/app/app-audio-playback.js::noteAssistantAudioStreamClosed`，它的几道兜底缺一条就会被某家 provider 的怪行为击穿：按 `speech_id` 对账（不回落到「当前轮」）；没见过音频头的 sid 忽略（后端存在零音频也发信号的轮）；本机已打断的 sid 丢弃；播放 epoch 不匹配作废（打断后迟到的信号）。信号和音频块排在同一条入队队列里，所以不会抢到还没解码的块前面。
- 前端判断「这一轮放干净了没有」（`isAssistantTurnPlaybackDrained`）要把「已出队、正在解码」的块也算进去，那段时间块不在任何队列里。

## 尾句晚一轮：收尾绑在 `response.done` 上

症状：语音模式加自定义音色，最后半句要等用户开口说下一轮才播出来。

根因（实测观察）：

- 流式克隆音色服务（CosyVoice）会把最后一句扣到收到 FINISH 才合成，句尾带标点也一样。
- core 只在 realtime 的 `response.done` 到达时才发收尾 `(None, None)`。
- 免费档海外路由（`lanlan_app_gemini`）的 `response.done` 要等它把自己那份音频流完才来，而外部 TTS 接管时这份音频被整段丢弃。转写结束到 `response.done` 的间隔从一秒多到近十秒不等。客户端关不掉上游这份音频。

做法（现行）：

- `_enqueue_tts_text_chunk` 每入队一块文本就重置空闲定时器，到点发 `TTS_SOFT_FLUSH_SENTINEL`，让 worker 先把攒着的尾句合成出来。默认 1 秒，`NEKO_TTS_SOFT_FLUSH_IDLE_SECONDS` 可调，下限 0.3 秒。
- 只对 `TTS_PROVIDER_REGISTRY` 里 `soft_flush=True` 的 provider（现在只有 `cosyvoice`）且 `input_mode == 'audio'` 生效。不认这个哨兵的 worker 会把它当成新的 `speech_id`，所以 core 按能力过滤，不广播。
- 软 flush 不是收尾：不发 `audio_done`，同一个 `speech_id` 后面还可能来文本。
- `TtsRuntimeMixin` 这类 mixin 模块顶层不许有函数（`scripts/check_core_contracts.py` 的形状约束），helper 要写成类里的方法或 `staticmethod`。

已排除、不用再走的方向（实测观察）：`response.done` 之后没有迟到的文本；前端解码器只扣一帧；worker 的收尾和 `__audio_done__` 都按时到。

守卫：`tests/unit/test_tts_soft_flush.py`。

## 卡顿的两类根因

1. **整段卡 8 到 10 秒**：主事件循环被阻塞。出过的原因是同步构造 LLM 客户端时读盘加载 CA 证书。async 路径一律用 `utils.llm_client.create_chat_llm_async()`。这类问题和 TTS provider 无关，换 provider 没用。
2. **一字一顿、开头碎音**：上游流式音频节奏碎，首包之后的间隔最大。靠 worker 侧的 `AudioJitterBuffer`（`main_logic/tts_client/_infra.py`，工厂 `make_audio_jitter_buffer`）先攒一段领先量再放。默认开局 400ms、之后每 200ms 放一批，环境变量 `NEKO_TTS_JITTER_INITIAL_MS` / `NEKO_TTS_JITTER_STEADY_MS`。worker 负责在打断或新一轮时 `reset()`、收尾时 `flush()`。新 worker 需要时一行接工厂；接了会增加首字延迟。

realtime 自带声音那条路在后端零缓冲直通，jitter buffer 只在外部 TTS worker 路径上。

## 音色 id 的存储

- 角色卡里的音色字段有两种形态并存：旧的扁平字符串（可能带 provider 前缀），和结构化对象 `{source, provider, ref, config}`。不做一次性批量迁移。
- **读**一律经 `utils/voice_config.py::read_legacy_voice_id`，它把两种形态都还原成 dispatch 认的旧字符串。直接对读出来的值 `.strip()` 会在对象形态上崩。
- **写**只有「用户在界面上设音色」这一条路径落结构化对象（`ConfigManager.voice_id_to_storage_value`）。内部默认值、清空、清理仍写字符串。
- 归一化放在后端这个收口，前端不直接写结构化对象。
- 不变式：存进去再读出来等于原来的旧字符串，迁不迁移对 dispatch 和校验透明。

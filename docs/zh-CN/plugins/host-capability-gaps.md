# 插件宿主能力缺口：audio / video parts 支持

> 本页记录插件**无法靠自己补齐**的宿主能力缺口。个别场景有受限的替代路径
> （音频可借前端播放器，见下文），但那替代不了原生通道，视频则连替代都没有。
> 结论均在本页引用的源码中
> 核实：宿主行为看 `app/main_server/character_runtime.py` 与
> `plugin/server/messaging/proactive_bridge.py`，`respond` 推送的图片如何投递看
> `main_logic/core/proactive.py`，wire 负载与其上限看 `plugin/core/context.py` +
> `plugin/settings.py` + `plugin/message_plane/ingest_server.py`，临时媒体存储看
> `plugin/server/routes/media.py` + `plugin/sdk/shared/core/images.py`，part
> schema 看 `plugin/sdk/shared/core/push_message_schema.py`，前端播放器看
> `static/jukebox/music_ui.js`。为避免行号漂移，下文只引用函数名与常量名。
>
> **状态（2026-08-28 复核）**：本页最初提出的「问题一：图片 parts 没有用户
> 可见通道」已由 #2835（`1d654e302`，2026-08-28 合并）落地，改写为下方
> 「已落地」一节；仍然缺失的是 audio / video parts 的宿主消费端。
>
> 插件自身可解决的适配问题（双重回复、会话打断、消息监听、后台发图门控、
> `target_lanlan` 缺失等）已在社区插件内自行修复，不在本页范围。

## 已落地：图片 parts 的用户可见通道（#2835）

`visibility` 含 `"chat"` 的 push，其 text / image parts 会**按原顺序**渲染到
用户聊天窗，渲染形态是带来源标签的 `role="system"` 气泡（system chip）——
既不是助手气泡也不是用户气泡，插件即使用角色口吻写文案，来源标签也会说明它
从哪来。

**触发条件只看 `visibility`，与 `ai_behavior` 无关**：`respond` / `read` /
`blind` 三种都会渲染。`ai_behavior` 只决定这批 parts 是否同时进入模型上下文：

```python
# 用户看得见图，模型完全不知道这张图
push_message(
    visibility=["chat"],
    ai_behavior="blind",
    parts=[{"type": "image", "data": <png bytes>, "mime": "image/png"}],
)

# 用户看得见图，模型也看得见，并且立刻回一句
push_message(
    visibility=["chat"],
    ai_behavior="respond",
    parts=[{"type": "image", "data": <png bytes>, "mime": "image/png"}],
)
```

`read` 有一处例外：它进模型是**尽力而为**。当前会话没有 `stream_image`
（或根本没有会话）时宿主会清空待注入图片；realtime provider 没有原生视觉时
同样清空——`read` 没有票据绑定的通道去投递 VISION_MODEL 描述，宿主宁可提前
退出并在日志里说明。这两种情况下聊天气泡照常渲染。**要保证图片进模型，用
`ai_behavior="respond"`**：它的图片随 callback 走，不依赖当前会话。

两种图片来源都支持：

- **inline**：`parts=[{"type": "image", "data": <bytes>, "mime": "image/png"}]`，
  在 wire 上编码为 `binary_base64`。请保持**小图**——见下方 wire 预算；
- **本地临时 URL**：`await ctx.images.upload(data)` 返回可直接放进 `parts` 的
  image part，其 URL 形如 `http://127.0.0.1:<port>/media/<id>`，由宿主的临时
  媒体存储（`plugin/server/routes/media.py`）提供。只要图片不是极小，都该走
  这条路：URL 在 wire 上只占几十字节。

实现路径：`_ordered_plugin_chat_blocks` / `_build_plugin_chat_blocks` 生成
blocks → `LLMSessionManager.render_chat_blocks` 发出 `chat_blocks` WS 帧 →
前端 `appendReactChatBlocks` 渲染成 system chip。

模型侧 `read` 与 `respond` 都先由 `_resolve_plugin_model_image` /
`_fetch_plugin_image_base64` 解出 base64，但投递方式不同——去日志里找图时值得
知道：`read` 直接交给会话的 `stream_image`；`respond` 则把图挂在 callback 上
（`media_images`），随主动搭话一起投递——语音模式经 `_stream_cb_media` →
`stream_image`，文本模式作为 `prompt_ephemeral` 的 `images=` 参数。

### 仍存在的限制

- **任意外部 URL 依然被拒**。`_is_local_plugin_media_url` 要求 `http` +
  回环地址 + `/media/<id>` 路径且无 query/fragment/凭据；不满足时聊天渲染直接
  跳过该 part，模型注入侧记 `plugin image resolve failed; dropped`。想发外部
  图片，先 `ctx.images.upload()` 转成本地临时 URL。
- **传输层上限比宿主配额先卡住你**。整个 `MESSAGE_PUSH` 信封打包后必须小于
  `MESSAGE_PLANE_PAYLOAD_MAX_BYTES`（默认 256 KiB，可用
  `NEKO_MESSAGE_PLANE_PAYLOAD_MAX_BYTES` 调整），而 `_build_wire_payload` 至今
  在 `parts[].binary_base64` 之外还带一份 legacy `binary_data` 拷贝——一张
  inline 图等于在 wire 上走了两遍，源图明显超过 ~110 KiB 就会在
  `plugin/message_plane/ingest_server.py` 被拒，根本轮不到宿主配额。更大的图
  必须走 `ctx.images.upload()`。
- **聊天与模型两条路径各有独立配额**。聊天路径：最多
  `_PLUGIN_CHAT_IMAGE_MAX_COUNT = 8` 张，inline 图合计
  `_PLUGIN_CHAT_INLINE_TOTAL_MAX_BYTES = 8 MiB`；模型路径：单图
  `_PLUGIN_IMAGE_MAX_BYTES = 8 MiB`，每条 push 复用每回合 callback 的
  `_PLUGIN_IMAGE_MAX_COUNT` / `_PLUGIN_IMAGE_TOTAL_MAX_BYTES` 预算。超出的图
  被丢弃，不影响同一条 push 的其余 parts。这些 8 MiB 是 push 过了传输层之后
  宿主愿意收多少——对 inline part 来说，你实际撞到的是上一条的 wire 上限。
- **`ctx.images.upload()` 不能在 lifecycle handler 里调用**——插件命令循环在
  lifecycle 期间不处理上传响应，会直接抛 `RuntimeError`；请在 entry、timer、
  message 或自定义事件 handler 中调用。
- **上传会归一化为 JPEG**：长边上限 `MAX_IMAGE_EDGE = 2048`，源图上限
  `MAX_SOURCE_IMAGE_BYTES = 32 MiB` / `MAX_SOURCE_IMAGE_PIXELS = 16M 像素`，
  产物上限 `MAX_UPLOADED_IMAGE_BYTES = 8 MiB`。

## 仍缺口：audio / video parts 被宿主丢弃

**现象**：推送 audio / video part 没有任何输出。模型注入侧只有
`ai_behavior in ("respond", "read")` 才进入媒体循环，循环内对非 image part
打一行 warning 后丢弃；`ai_behavior="blind"` 根本不进循环，**连诊断日志都没有**。
聊天渲染侧同样不认这两类 part（`_build_plugin_chat_blocks` 只处理 text 与
image），所以即使写了 `visibility=["chat"]` 也不会渲染、也不会有日志。

```python
# respond / read：宿主进入媒体循环，对 audio/video 打 warning 后丢弃
push_message(
    visibility=["chat"],
    ai_behavior="respond",
    parts=[{"type": "audio", "data": <wav bytes>, "mime": "audio/wav"}],
)
```

**根因**（源码核实）：

- `app/main_server/character_runtime.py` 的媒体循环整体由
  `ai_behavior_v2 in ("respond", "read")` 守卫；循环内 `part_type != "image"`
  的分支记
  `logger.warning("[EventBus] media_part type=%s not yet supported (mime=%s); dropped")`
  后 `continue`。注释说明 `stream_audio` 是实时麦克风 PCM 管线（特定采样率 +
  RNNoise 门控），不是通用文件注入器，并且没有 video API。
- 所以 `{"type": "audio"}` / `{"type": "video"}` 在 v2 schema 中虽有定义
  （`plugin/sdk/shared/core/push_message_schema.py` 与官方指引），宿主消费端
  未实现。

**影响**：插件无法向会话推送语音 / 视频内容（TTS 音频文件、音乐片段、
游戏过场视频等）。已核实的替代路径：

- **音频**：`ui_action=media_play_url` 可以让前端音频播放器播一个**可直接播放、
  非 HLS** 的音频 URL（bridge 把该 action 转成 `music_play_url` 事件，前端走
  `dispatchMusicPlay`）。两个前提：
  - URL 已在播放允许列表上。前端 `sendMusicMessageDetailed` 只给 500ms 等
    `music-allowlist-updated` 事件，超时仍不在名单上就以 `unsafe_url` 拒绝并弹
    toast。稳妥做法是先用 `ui_action=media_allowlist_add`（`domains` 或精确
    `http_urls`）登记，再推播放；
  - URL 不能是 HLS 流。`isUnsupportedMusicStream` 在起播前就跑，`.m3u8` 一律以
    `unsupported_stream` 拒绝并弹错误 toast，加没加白名单都一样。
- **视频**：没有替代。`media_play_url` 不能顶替——bridge 转出的
  `music_play_url` 事件不携带 `media_type`，前端固定把它交给音乐 / 音频播放器，
  前端也没有视频事件通道。

**期望**：为 audio / video parts 提供宿主侧消费端（音频可注入会话或转交前端
播放器；视频至少支持 url 形式），或者在 schema 层显式标注这两类 part 未实现，
让 `neko-plugin check` 能在插件发布前拦下。

---

### 验证方法

1. 推送
   `push_message(visibility=["chat"], ai_behavior="blind", parts=[{"type": "image", "data": <png bytes>, "mime": "image/png"}])`：
   用户聊天窗出现带插件名标签的 system chip 图片气泡；模型上下文里没有这张图。
2. 同一 payload 换 `ai_behavior="respond"`：聊天窗同样出现图片气泡，且图片进入
   模型上下文（随 callback 投递，文本模式走 `prompt_ephemeral`、语音模式走
   `stream_image`），她会就这张图回一句。
3. 把图片换成 `{"type": "image", "url": "https://example.com/cat.png"}`，并保持
   `ai_behavior="respond"`（打这条日志的是模型路径，`blind` 根本不进媒体循环）：
   外部 URL 被拒——聊天窗无图，宿主日志出现
   `plugin image resolve failed; dropped`。改用
   `await ctx.images.upload(<bytes>)` 返回的 part 则两侧都正常。
4. 推送 `parts=[{"type": "audio", "data": <wav bytes>, "mime": "audio/wav"}]` +
   `ai_behavior="respond"`：宿主日志出现 `media_part type=audio not yet supported`，
   无任何输出；换 `ai_behavior="blind"` 则连日志都没有（不进媒体循环，聊天渲染
   也不认 audio part）。

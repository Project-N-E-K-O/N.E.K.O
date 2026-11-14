# 消息推送与结果投递

本页说明插件如何把内容送到对话里：`push_message()` 的两条投递轴、`parts` 类型与大小限制、图片上传，以及 `finish()` 的投递模式、结果语义和角色占位符。方法签名一览见 [插件能力与 SDK 参考](./sdk-reference)。

## `push_message(**kwargs) -> PushMessageResult`

`push_message` 是插件 → 主系统的**唯一**消息推送入口。两条独立的轴决定下游行为，配合 `parts` 列表承载 OpenAI 风格的多模态内容。完整 schema 见 `plugin/sdk/shared/core/push_message_schema.py`。

```python
self.push_message(
    visibility=[],                  # ["chat"] / ["hud"] / ["chat","hud"] / []
    ai_behavior="respond",          # "respond" / "read" / "blind"
    parts=[                         # 有序的内容 parts（chat 保序；模型侧见下方限制）
        {"type": "text",  "text": "看这个"},
        {"type": "image", "data": img_bytes, "mime": "image/png"},
        {"type": "ui_action",
         "action": "media_play_url",
         "url": "https://example.com/song.mp3",
         "media_type": "audio"},
    ],
    source="my_feature",
    target_lanlan="灵",             # 可选，路由到指定 session
    metadata={...},
    priority=0,                     # 数字越大优先级越高
)
```

### 提交结果

返回值是本地提交结果：

```python
result = self.push_message(
    visibility=[],
    ai_behavior="respond",
    parts=[{"type": "text", "text": "请回应这条事件"}],
)
if not result["submitted"]:
    # 可以保留本地任务，稍后由插件自己的策略决定是否重试。
    # 不要把消息正文写入日志。
    self.logger.warning("message submission failed: %s", result.get("reason"))
```

`submitted=True` 只表示 SDK 已把 payload 交给权威本地提交路径，并接管后续提交责任；它不表示宿主已经消费、AI 已生成回复或音频已经播放。

`submitted=False` 会携带稳定的 `reason`：`backpressure`、`transport_error`、`transport_unavailable` 或 `payload_too_large`。前三个描述的是传输当时的状况（拥塞 / 发送失败 / 没有可用通道），换个时机原样重发是有意义的。`payload_too_large` 是另一类：它由 SDK 在**发送之前**本地量出来——整条 payload 打包后超过了 `MESSAGE_PLANE_PAYLOAD_MAX_BYTES` 减去 227 字节 host 预留后的阈值（判据见下面的[大小限制](#大小限制)），所以原样重试必然还是同一个结果，唯一的出路是把这条 push 变小：inline 图片改用 `ctx.images.upload()` 换成 URL part；`audio` / `video` 目前没有对应的上传接口，只能自己压小（更短的片段、更低的码率或分辨率），或者自己托管后用 `url=` 代替 `data=`。

结果不会暴露内部 transport 名称，也不会回显消息正文或异常内容。拒绝结果还会携带兼容旧调用方的 `ok=False`；新代码应以 `submitted` 为正式判据。调用方可以保留本地状态，但重试和去重仍由具体插件决定。

### 两条轴的语义

**`visibility`** — plugin 的 parts 直接渲染给用户的目标列表（**与 AI 无关**），可同时多选（如 `["chat", "hud"]`）：

| 值 | 含义 |
|---|---|
| `"chat"` | parts 在 chat 框里**原文**渲染（plugin 的话直接当 chat 气泡） |
| `"hud"`  | parts 在 agent UI / HUD 通知面板显示 |
| `[]`（默认） | 用户**不直接**看到 parts；如果 `ai_behavior="respond"`，用户看到的是 AI 的回复 |

**`ai_behavior`** — LLM 怎么处理 parts：

| 值 | LLM 上下文 | 触发回复 turn | 何时被提及 |
|---|---|---|---|
| `"respond"`（默认） | ✅ | ✅ 立即起 turn | AI 立刻接茬 |
| `"read"`  | ✅ | ❌ 不打断 | 下次用户开口时 AI 自然提及 |
| `"blind"` | ❌ | ❌ | AI 看不到 |

### `parts` 类型

每个 part 是个 dict，必须有 `type` discriminator：

| `type` | 字段 | 用途 |
|---|---|---|
| `text` | `text: str` | 纯文本 |
| `image` | `data: bytes` + `mime` 或 `url` + `mime` | 图（inline 或远端） |
| `audio` | 同 image | 音频（schema 占位，**当前 AI 注入链路尚未消费**，会 warn-drop） |
| `video` | 同 image | 视频（schema 占位，**当前 AI 注入链路尚未消费**，会 warn-drop） |
| `ui_action` | `action: str` + 各 action 的字段 | 前端 UI 副作用 |

inline `data: bytes` 由 SDK 自动 base64 编码后随 payload 传出。需要扩展 push_message 的能力时，在 `parts` 加新的 `type`，不要新增 `message_type` 值。

### 当前实现限制

- `ai_behavior in ("respond","read")` 时，inline `image` parts 和 `ctx.images.upload()` 返回的本地临时 URL 都能进入 LLM 上下文（最终走 `session.stream_image(base64)`）。任意外部 URL 仍会被拒绝，避免把远端抓取引入 agent event 投递路径。单条消息最多向模型注入 8 张、合计 8 MiB 图片；超出的 image parts 不会进入模型上下文。
- 聊天显示（`visibility=["chat"]`）有自己独立的单次 push 上限：最多显示 8 张通过筛选的图，其中 inline 图片合计不超过 8 MiB（`ctx.images.upload()` 返回的 URL 图不计字节；默认配置下整条 payload 先撞上下文的 512 KiB 上限，远到不了 8 MiB，这个字节上限只有调高 `NEKO_MESSAGE_PLANE_PAYLOAD_MAX_BYTES` 后才有意义）。被跳过的图（比如任意外部 URL）不占名额，通过筛选但超出上限的图会被丢掉。两条路径计数方式不同：模型路径在解析之前就截取前 8 个 image part，其中某张送达失败也不会补位；聊天路径只数真正渲染出来的图。每次 push 尽量最多放 8 张有效图片；即便如此也不要假设用户看到的图和模型收到的完全一致——模型路径仍可能因取图超时、上传过期或 8 MiB 预算丢图，而聊天照样显示。
- `respond` 的图随回复轮次送出。实时服务商不支持原生视觉时，图本身不会交给模型：先由单独的视觉模型（`VISION_MODEL`）把每张图转成文字描述，分析最终没有产出描述的图会被丢掉。`read` 的图只是尽力投给**当前**会话：没有当前会话时会被丢弃，实时服务商不支持原生视觉时也会被丢弃。在这类服务商上需要模型至少拿到图片内容的描述时，请用 `ai_behavior="respond"`。
- 上面的 8 张 / 8 MiB 是**单次 push** 的上限。文字模式下 `ai_behavior="read"` 的图不是立刻发给模型，而是先暂存、等用户下次开口时一起送出，所以**一个回合**里可能攒着好几次 push 的图。暂存按来源分开计额，互不侵蚀：

  | 来源 | 张数 | 字节 |
  | --- | --- | --- |
  | 用户自己的截图 / 摄像头帧 | 5 | 16 MiB |
  | 插件 `read` 图片 | 3 | 8 MiB |
  | 主动搭话遗留的屏幕截图 | 1（独立单槽，带 TTL） | — |

  超额时裁掉的**永远是同一来源里最旧的那张**——插件推得再猛也拿不走用户的帧，反之亦然。所以一个回合最多可能带 9 张图，比单次 push 的 8 张略多，这是有意的：共用一个总额度就必须在两个来源之间挑一个牺牲，而那没有正确答案。
- `parts` 的顺序在 **chat 渲染**里是保留的（文字和图按你给的次序出现）。但 **进模型的那条路不保序**：图片会被拆出来先注入，文字合成一段随后给出。所以别依赖「说明 A、图 A、说明 B、图 B」这种交错来让模型把说明和图对应起来——要对应就把说明写进同一段文字里（例如「第一张是…，第二张是…」）。
- `visibility=["chat"]` 可显示 image parts；HUD 通知目前只渲染 text part，不显示 image part。
- `audio` / `video` 当前没有对应的 realtime 注入通道（`stream_audio` 是 PCM 实时麦克风专用，video 完全没有 API），都会 warn-drop。这两种 type 现阶段只推荐配合 `ai_behavior="blind"` + `ui_action` 走纯前端展示；Hosted UI 面板也可以自行播放音视频。

### 大小限制

inline part 通过 message_plane 走 ZMQ，整条 payload 上限是 `MESSAGE_PLANE_PAYLOAD_MAX_BYTES` = 524288（512 **KiB** = 512*1024，不是十进制的 512 KB）。这是出厂默认值，部署方可以用环境变量 `NEKO_MESSAGE_PLANE_PAYLOAD_MAX_BYTES` 覆盖，下文所有数字都按默认值算。这个数字量的是**打包后**的信封，不是原图字节数——inline 图片以 base64 放在 `parts[].binary_base64` 里，是原始字节的 4/3（+33%）。所以一张 inline 图的原始字节上限是 512 KiB × 3/4 = **约 384 KiB**，再减掉几百字节的信封开销和同一条消息里的 text part。实测：一张 256 KiB 的图加一句短文字打包出来是 341.8 KiB，离上限还有约 170 KiB 余量；恰好卡满 524288 字节的原始图片大小约为 383.6 KiB。本地检查（见下）还会再预留 227 字节余量，所以同样形状的消息实际在本地被拦的位置要再低约 0.2 KiB——按大约 383 KiB 留量，不要压着最后一位小数。

这个「实际上限」曾经低得多。wire envelope 以前为了照顾还没迁到 v2 的下游消费者，把同一张 inline 图带**两遍**：一份 base64 在 `parts[].binary_base64`，一份原始 bytes 在 legacy 的 `binary_data` 字段，加起来约是原图的 2.34 倍，于是当时 256 KiB 的上限实际只兜得住约 110 KiB 的图。现在 `_build_wire_payload`（`plugin/core/context.py`）只在「调用方同时传了 `parts=` 和 `binary_data=`」这一种形状下才填 legacy 字段，普通的 inline 图片只走 base64 那一遍；双份去掉、上限又提到 512 KiB，两件事合起来才让 256 KiB 的图能过。

超限**在本地就被拦下**：`push_message()` 在把 payload 交给 message_plane 之前先打包量一次，打包大小超过「上限减去 227 字节预留」就直接返回 `submitted=False` + `reason="payload_too_large"`，那条消息一个字节都不会上线，日志里会写明是哪个 part（`image` / `audio` / `video`）吃掉了预算。这 227 字节是给 host 的预留：host 在量之前会往记录里补 `plugin_id` / `message_id` / `time` 字段，预留按它们的最坏情况算，保证 SDK 放行的 push 不会再在 ingest 因为大小被丢。插件侧和 host 侧（`plugin/message_plane/ingest_server.py`）读的是同一个常量，host 侧的检查作为最后一道兜底。

- 这道闸挂在**每一条**提交出口上：批量快路径、同步路径，以及 ZMQ 不可用时退下去的 legacy 控制面队列，都会先量一遍整条 payload。
- 校验的是整条 payload 而不只是内联图片——超大的纯文本或 metadata 同样会被拒。唯一例外是显式关闭 `NEKO_MESSAGE_PLANE_VALIDATE_PAYLOAD_BYTES`（默认开启）的部署，那时 SDK 和 host 都不量，超限消息会一路走到底。
- 超限影响的是**整条 push**，不是「图掉了、文字还在」：同一条消息里的 text part 和 `ui_action` 一起被拒。
- 这没有改变 `submitted=True` 的含义：它仍然只表示「已交给传输」，宿主背压、进程重启之类仍然可能让消息静默消失。

1080p 截图别指望 inline 走：先压成 JPEG q70 或 256x256 PNG，再大就用下面的上传接口。

### 图片上传：`ctx.images.upload()`

较大图片使用独立的临时图片上传接口；它会在线程池中规范化为最长边不超过 2048 的 JPEG，并通过独立 media transport 上传，不占用 `push_message` 的 512 KiB payload：

```python
image_part = await self.ctx.images.upload(image_bytes, mime="image/png")
self.push_message(
    visibility=["chat"],
    ai_behavior="respond",
    parts=[{"type": "text", "text": "看看这张图"}, image_part],
)
```

`images.upload()` 只准备当前运行期可用的临时资源；它不会显示图片、不会写入模型上下文，也不会触发回复，投递语义仍完全由 `push_message()` 控制。请在 plugin entry、timer、message 或 custom event handler 中调用；lifecycle handler（`startup` / `freeze` / `unfreeze` / `shutdown` / `config_change`）执行时调用会立即抛出 `RuntimeError`，而不是等待 timeout。

**`upload()` 的硬失败**——下面每一条都是**抛异常**，不是静默降级成一张小图，所以喂用户提供的图片时必须自己 `try`：

| 关卡 | 常量 / 判据 | 越界结果 |
|---|---|---|
| 源图字节 | `MAX_SOURCE_IMAGE_BYTES` = 33554432（32 MiB） | `ValueError` |
| 源图像素 | `MAX_SOURCE_IMAGE_PIXELS` = 16777216（16 MP，宽 x 高） | `ValueError` |
| 归一化后字节 | `MAX_UPLOADED_IMAGE_BYTES` = 8388608（8 MiB） | `ValueError` |
| 解码槽等待 | 全进程只有 2 个解码槽，排队时间计入你给的 `timeout`（默认 3s，上限 30s） | `TimeoutError` |
| 输出最长边 | `MAX_IMAGE_EDGE` = 2048 | 不报错，等比缩小 |

归一化后字节那条容易被漏掉：源图过了 32 MiB / 16 MP 两关，重编码出来的 JPEG 仍可能超过 8 MiB（噪点多的大图压不动），这时异常在**解码之后**抛出，你已经付过 CPU 了。此外 downlink 尚未就绪、transport 不回包同样是 `TimeoutError`；空 bytes 是 `ValueError`，非 bytes 是 `TypeError`。

**动图会被拍平**：只取第 0 帧，输出恒为单帧 JPEG，既不报错也不打日志。`mime=` 参数只是调用方自己的标注，真实格式由 Pillow 探测，输出永远是 JPEG。要动效请配合 `ui_action` 让前端自己播。

**传得更清晰买的是 chat 显示效果，不是模型精度**：每一张要进模型的图都会**无条件**先被重编码到模型档位：最长宽 `MODEL_IMAGE_MAX_WIDTH`（1280）、最高 `COMPRESS_TARGET_HEIGHT`（720）、JPEG q80，见 `utils/screenshot_utils.py` 的 `normalize_image_for_model`。已经在档位内的图原样通过、不会被反复重编码。所以按 2048 长边上传的图，到模型眼前必定已经是 720p 档。归一化之后，如果一个回合的图片总字节仍超过 `TURN_ATTACHED_IMAGE_MAX_TOTAL_BYTES`（8 MiB），才会再启动降级阶梯：先抽样（只留头/中/尾三张），再对剩下的重压一次，两步都不够才从最旧的开始丢，并且只有真丢了整张图才会提示用户。chat 显示的那一份不走上面任何一步，保留你上传的分辨率。

### 常见组合

```python
# 1. 让 AI 转述（最常用）：plugin 文本 → LLM 上下文 → AI 立即回复
self.push_message(parts=[{"type": "text", "text": "用户给你打赏了 100 块"}])

# 2. 安静通知 AI：下次用户说话时 AI 自然提及
self.push_message(
    visibility=[],
    ai_behavior="read",
    parts=[{"type": "text", "text": "B站直播间又有新弹幕"}],
)

# 3. 只在 HUD 通知用户，不打扰 AI
self.push_message(
    visibility=["hud"],
    ai_behavior="blind",
    parts=[{"type": "text", "text": "插件 X 已启动"}],
)

# 4. plugin 直接在 chat 渲染播放卡片，AI 不知情
self.push_message(
    visibility=["chat"],
    ai_behavior="blind",
    parts=[{
        "type": "ui_action",
        "action": "media_play_url",
        "url": "https://music.example.com/song.mp3",
        "media_type": "audio",
        "name": "Test Song",
        "artist": "Bot",
    }],
)

# 5. 文字 + 图同时给 AI
self.push_message(
    parts=[
        {"type": "text",  "text": "看这张图"},
        {"type": "image", "data": img_bytes, "mime": "image/png"},
    ],
)
```

`message_type`、`content`、`delivery`、`reply` 等 v1 字段已弃用，仍会被转换并发出 `DeprecationWarning`；逐项替代写法见 [`push_message` v2 说明](/changelog/plugin-push-message-v2) 和 [迁移指南](./migration-v0.9#push-message-v2)。

## 音乐播放域名白名单

N.E.K.O 默认只允许来自 `music.163.com` 等已知安全域名的音频播放。插件引入新的音频来源时，必须在播放前把域名加入白名单。

### 后端插件（Python SDK）

推一条 `ui_action: media_allowlist_add`，再让前端开播：

```python
from urllib.parse import urlparse

from plugin.sdk.plugin import Err, Ok, SdkError, plugin_entry

MUSIC_HOST = "music.my-cdn.com"

@plugin_entry(id="play_external")
async def play_external(self, url: str, **_):
    # 白名单里只放你自己固定的音源，不要直接放调用方传进来的 url：
    # url 可能是 LLM 选的，原样加进去等于绕过了白名单本身。
    # 同时要求 https：前端对 http 只放行白名单里精确匹配的 URL。
    try:
        parsed = urlparse(url)
    except ValueError:
        return Err(SdkError("invalid music url"))
    # 拒绝 userinfo 和反斜杠：浏览器把 "\" 当路径分隔符，
    # "https://evil.com\@music.my-cdn.com/" 能骗过 urlparse，实际却加载 evil.com。
    if (
        parsed.scheme != "https"
        or parsed.hostname != MUSIC_HOST
        or parsed.username is not None
        or "\\" in url
    ):
        return Err(SdkError("unsupported music source"))
    result = self.push_message(
        ai_behavior="blind",
        parts=[{"type": "ui_action", "action": "media_allowlist_add", "domains": [MUSIC_HOST]}],
    )
    if not result["submitted"]:
        # 白名单没加上，前端照样会拦下播放
        return Err(SdkError(f"allowlist push failed: {result.get('reason')}"))
    result = self.push_message(
        visibility=["chat"],
        ai_behavior="blind",
        parts=[{"type": "ui_action", "action": "media_play_url", "url": url, "media_type": "audio"}],
    )
    if not result["submitted"]:
        return Err(SdkError(f"play push failed: {result.get('reason')}"))
    # submitted 只表示请求已交出去，不代表已经在播放
    return Ok({"status": "play_requested", "url": url})
```

旧 SDK helper `self.register_music_domains(...)` 已删除。

### 插件页面拿不到 `window.MusicPluginAPI`

`window.MusicPluginAPI`（`getAllowlist()` / `addAllowlist()`）和 `music-ui-ready` 事件只存在于加载了 `music_ui.js` 的主页面（`index.html` / `chat.html`）。通过 `register_static_ui` 提供的页面是在插件管理器里用 `<iframe>` 打开的，插件管理器跑在另一个端口（不同源），也不加载 `music_ui.js`，所以页面的 `window` 和 `window.parent` 上都没有这个 API。请按上一节从后端推 `media_allowlist_add` 加白名单。

## `finish()` 的投递模式

`finish()` 用 `delivery` 控制任务结果如何到达主 AI（默认 `"proactive"`）：

| `delivery` | 是否进 LLM 上下文 | 是否立即起 turn 播报 | 前端 HUD/通知 |
|---|---|---|---|
| `"proactive"`（默认） | ✅ | ✅ 立即起 turn | ✅ |
| `"passive"` | ✅ 写入上下文 | ❌ 不打断；下次用户发言时由 AI 自然提及 | ✅ |
| `"silent"` | ❌ AI 不知情 | ❌ | ✅（只剩 task_update） |

```python
# 正常：角色立即播报结果
return await self.finish(data={"summary": "天气晴朗"})

# 安静通知：进上下文不打断；下次用户开口时 AI 顺嘴提一下
return await self.finish(data={"summary": "番茄钟到点了"}, delivery="passive")

# 完全静默：AI 不知情；前端只通过 task_update 看到任务终态
return await self.finish(data={"summary": "..."}, delivery="silent")
```

`reply=True` / `reply=False` 是已弃用的布尔别名，分别等价于 `"proactive"` / `"silent"`；同时传入时以 `delivery` 为准。`push_message()` 不使用 `delivery`，而是 `visibility` + `ai_behavior` 两条轴。

## 结果语义：任务汇报 vs 事件回应

主 AI 收到通知时会被套上一层外层 prompt，措辞分两类：

| 你调用 | 宿主分类 | AI 收到的外层 prompt 大意 |
|---|---|---|
| `await self.finish(...)`（默认） | `task_result` | "来自{你的插件}的任务已完成，请向主人**汇报**..." |
| 查询/即时回执 entry 声明 `result_kind="event"` | `event` | "来自{你的插件}的**新消息**，请按内容**回应**主人..." |
| `self.push_message(...)` | `event` | "来自{你的插件}的**新消息**，请按内容**回应**主人..." |

- 任务汇报：插件被调用后跑完，AI 应该说明「做了什么、结果怎样」。
- 事件流：插件持续监听外部事件（弹幕、IM 消息、定时器），AI 应该**回应这个事件本身**，而不是叙述成「我刚刚处理了一下…」。

查询、状态读取或「已开始」一类即时回执应显式降级为事件语义。可静态声明：

```python
@plugin_entry(
    id="service_status",
    metadata={"result_kind": "event", "expires_in_s": 30},
)
```

也可由某次运行结果覆盖静态声明：

```python
return await self.finish(
    data={"status": "running"},
    meta={"agent": {"result_kind": "event", "expires_in_s": 10}},
)
```

解析优先级为「运行时 `meta.agent` > entry 静态 `metadata` > 默认 `task_result`」。`expires_in_s` 只对 `event` 结果生效；过期回执不会再进入主 AI。宿主只允许成功的 `user_plugin task_result` 降级为 `event`，不能把 `proactive_message` 反向伪装成任务完成。`result_kind` 与 `visibility` / `ai_behavior` / `delivery` 正交，只决定外层 prompt 的措辞。

## 角色感知文本：`{MASTER_NAME}` / `{LANLAN_NAME}`

插件通过 `finish()` 的 `data.summary` / `data.detail`、`push_message()` 的 `parts[*].text` 等渠道把字符串送进对话 LLM 上下文（或在 `direct_reply` 时直接进 TTS / 聊天气泡）。这些字符串里**不要硬编码** `"用户"` / `"user"` / `"master"` / `"主人"`：

- **口吻别扭**：主 AI 看到「向用户汇报…」会原样照念，而不是用实际的 `master_name`。
- **多角色失真**：每个 `LLMSessionManager` 有自己的 `lanlan_name`；一个插件广播给多个角色时，硬编码会让所有角色用同一份措辞。

| 占位符 | 替换成 |
|---|---|
| `{MASTER_NAME}` | 当前会话的 `master_name`（用户起的名字） |
| `{LANLAN_NAME}` | 当前会话的角色名 |

宿主在 LLM 注入点（`main_logic.core.apply_role_placeholders`）按目标 session 展开。**不能在插件侧自己替换**——visibility 过滤在宿主端，插件不知道这条消息最终落到哪个 session。

```python
# ✅ 推荐：让宿主按 session 展开
await self.finish(data={
    "summary": "立即基于最新画面向 {MASTER_NAME} 叙述刚才发生的事",
})

self.push_message(parts=[{
    "type": "text",
    "text": "{MASTER_NAME} 刚刚发了一条弹幕：『...』",
}], ai_behavior="respond")

# ❌ 不推荐：硬编码，口吻泛化 + 多角色失真
await self.finish(data={"summary": "立即向用户叙述..."})
```

实现细节：

- **替换语义是 `str.replace`，不是 `str.format`**：`detail` 里嵌 JSON 片段 / 代码 / 含 `{` 的用户原文都不会触发 `KeyError`。
- **空 name 时占位符保持字面量**：宿主拿不到 name（极少见的初始化阶段）时，`{MASTER_NAME}` 留在原文，不会替换成空串造成破句。
- **拼写固定**：只用 `{MASTER_NAME}` / `{LANLAN_NAME}`（大写、下划线、单层花括号）。`{master_name}` / `{master}` / `{MASTER}` 是宿主内部模板的 `.format(...)` 占位符，不跨插件边界。

适用范围：

| 渠道 | 是否展开 |
|---|---|
| `finish(data={"summary": ..., "detail": ...})` | ✅ |
| `push_message(parts=[{"type": "text", ...}])` 进 LLM 上下文 | ✅ |
| `task_result` + `direct_reply=True`（绕过 LLM 直接 TTS） | ✅ |
| `push_message(visibility=["chat"], ai_behavior="blind")` 直进聊天气泡 | ✅ |
| `push_message(visibility=["hud"])` HUD toast 文本 | ✅ |
| 静态描述字段（`plugin.toml` 的 `description`、入口的 `name` 等） | ❌ 不展开（给开发者 / UI 看，不进对话渠道） |

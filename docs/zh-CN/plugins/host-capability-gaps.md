# 插件宿主能力缺口：图片用户可见通道 + 音视频 parts 支持

> 本 PR 汇总插件侧**无法自行解决**的宿主能力缺口（均已在
> `app/main_server/character_runtime.py` 源码中核实，行号以本 PR 提交时的
> `main` 为准）。与官方指引 `plugin/PLUGIN_DEVELOPMENT_GUIDE.md` 中
> 「schema 占位 part」一节相互印证。
>
> 插件自身可解决的适配问题（双重回复、会话打断、消息监听、后台发图门控、
> target_lanlan 缺失等）已在社区插件内自行修复，不在本 PR 范围。

## 问题一：图片 parts 没有「用户聊天窗可见」通道

**现象**：插件通过 `push_message` 推送 image part（`visibility=["chat"]`、
`ai_behavior="blind"`）时，用户聊天窗**看不到**图片；`visibility=["chat"]`
只对 text parts 生效。

```python
push_message(
    visibility=["chat"],
    ai_behavior="blind",
    parts=[{"type": "image", "data": <png bytes>, "mime": "image/png"}],
)
```

**根因**（源码核实）：

- `app/main_server/character_runtime.py` 只把媒体 parts 归入
  `media_parts` 随 `proactive_message` 事件传给 AI session；当
  `ai_behavior in ("respond", "read")` 时才把 **inline** 图片
  `stream_image` 喂给 LLM（视觉输入）。**没有任何分支把 image part
  渲染到用户聊天窗**。
- **inline vs url 差异**：只有 inline `binary_base64` 会真正进入
  `stream_image`；`url` 形式在 `character_runtime.py` 被
  warn-drop（"image media_part url not yet fetched; dropped"），既不进 AI
  也不渲染到用户窗。官方指引同样建议内联小图（≤256KB）。
- 用户可见图片目前唯一可行的路径是：text part 内嵌 markdown
  `![alt](http://...)`（前端 ReactMarkdown 渲染），且 URL 必须可外网/内网
  访问——插件只能借助自身静态服务 + 拼接 URL 实现，绕不开宿主通道缺失的
  本质。

**影响**：游戏/工具类插件想让用户「看见」一张图片（结果卡片、相册照片、
截图）时，没有原生通道。已核实的社区插件做法分两种：

- lifekit：图片块转 markdown，`visibility=["chat"]` + `ai_behavior="blind"`
  推送 text part（用户可见，依赖外链托管）；
- neko_live：头像图走 `visibility=[]` + inline image part（用户不可见，
  只作 AI 视觉输入）。

**期望**：`visibility=["chat"]` + image part（至少 inline `binary_base64`，
如可能也支持 `url`）应在用户聊天窗直接渲染为图片气泡（与 text part 同
通道），`ai_behavior` 仅控制是否同时注入 AI 上下文，不应影响用户可见性。

**关联**：该需求与 #2835 / #2905（宿主侧图片可见性工作）一致，建议在本 PR
落地通道后由官方统一实现。

## 问题二：audio / video parts 被宿主直接丢弃

**现象**：`push_message` 推送 audio/video part 时无任何输出；仅当
`ai_behavior in ("respond", "read")` 时宿主才进入媒体循环并对非 image
part 打 warning，`ai_behavior="blind"` 时整条媒体路径被跳过、**完全静默**
（无 warning）。

```python
# respond/read: 宿主进入媒体循环, 对 audio/video 打 warning 后丢弃
push_message(
    visibility=["chat"],
    ai_behavior="respond",
    parts=[{"type": "audio", "data": <wav bytes>, "mime": "audio/wav"}],
)
```

**根因**（源码核实）：

- `app/main_server/character_runtime.py`：媒体处理循环整体由
  `ai_behavior in ("respond", "read")` 守卫；循环内对
  `part_type != "image"` 打 `logger.warning("media_part type=%s not yet
  supported ... dropped")`，注释说明 `stream_audio` 是实时麦克风 PCM 管线
  （特定采样率 + RNNoise 门控），不是通用文件注入器，且没有 video API。
- 因此 `{"type": "audio"}` / `{"type": "video"}` parts 在 v2 schema 中虽已
  定义（见 `plugin/sdk/shared/core/push_message_schema.py` 与官方指引），
  但宿主消费端未实现，插件发送后无任何效果（`blind` 下连诊断日志都没有）。

**影响**：插件无法向会话推送语音/视频内容（TTS 音频文件、音乐片段、
游戏过场视频等）。已核实的替代路径：

- **音频**：`ui_action=media_play_url`（`media_type: "audio"`）+ 前端音频
  播放器可以播放音频 URL（bridge 会把该 action 转成 `music_play_url`
  事件，前端走 `dispatchMusicPlay` 音频播放器）；
- **视频**：没有对应的 URL 播放路径（前端无视频事件通道），目前无替代。

**期望**：为 audio/video parts 提供宿主侧消费端（音频可注入会话或转交
前端播放器；视频至少支持 url 形式）。

---

### 验证方法

1. 推送 `push_message(visibility=["chat"], ai_behavior="blind",
   parts=[{"type": "image", "data": <png bytes>, "mime": "image/png"}])`：
   期望用户聊天窗出现图片气泡，实际无。
2. 同一 payload 换 `ai_behavior="respond"`：inline 图片进 AI 上下文
   （stream_image），用户聊天窗仍无。
3. 把 payload 的图片换成 `{"type": "image", "url": <png url>, "mime":
   "image/png"}`（`ai_behavior="respond"`）：宿主日志出现
   `image media_part url=... not yet fetched; dropped`，AI 与用户都看不到。
4. 推送 `parts=[{"type": "audio", "data": <wav bytes>, "mime":
   "audio/wav"}]` + `ai_behavior="respond"`：宿主日志出现
   `media_part type=audio not yet supported`，无任何输出；换
   `ai_behavior="blind"` 则无任何日志（整条媒体路径被跳过）。

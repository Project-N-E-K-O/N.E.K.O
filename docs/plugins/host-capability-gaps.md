# 插件宿主能力缺口：图片用户可见通道 + 音视频 parts 支持

> 本 PR 汇总插件侧**无法自行解决**的宿主能力缺口（均已在
> `plugin/server/messaging/proactive_bridge.py` 与
> `app/main_server/character_runtime.py` 源码中核实）。
> 与之相对，插件自身可解决的适配问题（双重回复、会话打断、消息监听、
> 后台发图门控、target_lanlan 缺失等）已在社区插件内自行修复，不在本 PR 范围。

## 问题一：图片 parts 没有「用户聊天窗可见」通道

**现象**：插件通过 `push_message(parts=[{"type": "image", "data": ...}],
visibility=["chat"], ai_behavior="blind")` 推送图片时，用户聊天窗**看不到**
图片；`visibility=["chat"]` 只对 text parts 生效。

**根因**（源码核实）：

- `plugin/server/messaging/proactive_bridge.py::_media_parts` 把 image parts
  归入 `media_parts`，仅随 `proactive_message` 事件传给 AI session。
- `app/main_server/character_runtime.py`（约 L546-599）：只有当
  `ai_behavior in ("respond", "read")` 时才把图片 `stream_image` 喂给 LLM
  （视觉输入）；`ai_behavior="blind"` 时媒体被完全忽略，**没有任何分支把
  image part 渲染到用户聊天窗**。
- 用户可见图片目前唯一可行的路径是：text part 内嵌 markdown
  `![alt](http://...)`（前端 ReactMarkdown 渲染），且 URL 必须可外网/内网访问
  ——插件只能借助自身静态服务 + 拼接 URL 实现，绕不开宿主通道缺失的本质。

**影响**：游戏/工具类插件想让用户「看见」一张图片（结果卡片、相册照片、
截图）时，没有原生通道。社区插件（lifekit、neko_live 等）全部
`visibility=[]` 只发 AI 视觉，用户端靠 markdown URL 变通。

**期望**：`visibility=["chat"]` + image part（含 `binary_base64`/`url`）应
在用户聊天窗直接渲染为图片气泡（与 text part 同通道），`ai_behavior` 仅
控制是否同时注入 AI 上下文，不应影响用户可见性。

**关联**：该需求与 #2835 / #2905（宿主侧图片可见性工作）一致，建议在本 PR
落地通道后由官方统一实现。

## 问题二：audio / video parts 被宿主直接丢弃

**现象**：`push_message(parts=[{"type": "audio", "data": ..., "mime": ...}])`
到达宿主后被静默丢弃，仅打 warning 日志。

**根因**（源码核实）：

- `app/main_server/character_runtime.py`（约 L556-565）：
  `part_type != "image"` 时直接 `logger.warning("media_part type=%s not yet
  supported ... dropped")`，注释说明 `stream_audio` 是实时麦克风 PCM 管线
  （特定采样率 + RNNoise 门控），不是通用文件注入器，且没有 video API。
- 因此 `{"type": "audio"}` / `{"type": "video"}` parts 在 v2 schema 中虽已
  定义（见 `plugin/sdk/shared/core/push_message_schema.py`），但宿主消费端
  未实现，插件发送后无任何效果。

**影响**：插件无法向会话推送语音/视频内容（TTS 音频文件、音乐片段、
游戏过场视频等）。社区插件当前只能走 `ui_action=media_play_url` 让前端
播放 URL，无法直接携带数据。

**期望**：为 audio/video parts 提供宿主侧消费端（音频可注入会话或转交
前端播放器；视频至少支持 url 形式），或明确在 SDK 文档中标注
「该 part 类型当前不支持」。

---

### 验证方法

- 任一插件 `push_message(visibility=["chat"], ai_behavior="blind",
  parts=[{"type": "image", "data": <png bytes>, "mime": "image/png"}])`：
  期望用户聊天窗出现图片气泡，实际无。
- 同一 payload 换 `ai_behavior="respond"`：AI 上下文可见（stream_image），
  用户聊天窗仍无。
- `parts=[{"type": "audio", "data": <wav bytes>, "mime": "audio/wav"}]`：
  宿主日志出现 `media_part type=audio not yet supported`，无任何输出。

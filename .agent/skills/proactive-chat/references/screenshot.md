# 主动搭话的截图：暂存、水印、授权与拒绝

## 一张截图的路径

1. 前端随 `POST /api/proactive_chat` 带上 `screenshot_data` 和可选的 `avatar_position`。`sources.py::_fetch_source` 压缩它，有坐标就叠水印，作为 Phase 1 的截图。
2. Phase 2 前，`main_logic/core/proactive.py::request_fresh_screenshot` 通过 WebSocket 向前端要一张最新的（`request_screenshot`，带本轮的 `request_id`），失败时可能退到后端自己截屏。返回 `FreshScreenshot`，带 `source`（`websocket` / `backend_fallback`）、`avatar_position`、`declined`。
3. 服务层按来源决定要不要叠水印，然后把图给 Phase 2 的视觉模型。
4. 投递提交成功后，这张图被暂存到会话上，供用户下一条文字回复使用。

`request_fresh_screenshot` 两条路径都把图压到统一的尺寸和质量再返回。有的前端截图是原始分辨率，直接发给视觉模型会被上游以请求体过大拒绝。

## 水印（Avatar 标注）

截图发给视觉模型前，在 Avatar 所在位置叠一行字，说明这是角色自己的虚拟形象、不要主动提它，防止角色把自己的立绘当成用户屏幕上的内容来评论。实现在 `utils/screenshot_utils.py::overlay_avatar_annotation`。

### ⚠️ 核心判据：前端没发坐标是「否定」，不是「漏传」

前端在这些情况下故意不发 `avatar_position`：截的是单个窗口、摄像头画面、Avatar 被收起、已知多显示器、截图被裁剪过。后端的规则是成对的，不要改反：

- **前端参与过的图**（持续屏幕分享的帧、WebSocket 回传的最新截图）：有坐标就叠，没坐标就不叠。**不回退到缓存的旧坐标。** `main_logic/core/streaming.py` 和 `service.py::_resolve_phase2_avatar_position` 都是这条规则。
- **后端兜底截屏**（`source == "backend_fallback"`）：前端没见过这张图，不可能表过态。这时用原始请求里带的坐标，那是这条路径上唯一的 Avatar 信息。把两条路径「统一」成都不回退，兜底截图就永远没有水印。
- 坐标跟着图一起走（`resolve_screenshot_request` 把它放进专门的待取槽），不走 `_avatar_position` 这个公共字段：那个字段每一帧屏幕分享都会改写，中间插进一帧就会把坐标换掉。
- `request_fresh_screenshot` 自己不叠水印，由调用方叠。

已知局限：Phase 1 到 Phase 2 之间隔着一次完整的模型调用，兜底路径复用的坐标可能已经过时。

### 手动截图

截图按钮的结果经过裁剪后不带坐标。判断在 `static/app/app-buttons.js` 的 `enqueueCapturedScreenshotResult`：只有 `dataUrl === originalDataUrl`（没被裁剪改动过）才保留坐标。裁剪之后归一化坐标已经失效，不叠是对的，不要「修」。

### 多显示器

`static/app/app-screen.js::getAvatarScreenPosition` 的整屏分支用 `window.screenX`（虚拟桌面的全局坐标）做原点、用 `window.screen.width`（当前显示器的尺寸）做分母，两个参考系差一个「当前显示器的原点」。单屏时原点是 0，结果正确；多屏时会算出一个落在合法范围内的错误位置。

- 现在的做法是闸门 `isKnownMultiDisplay()`：确认是多屏就不叠。宁可不叠，也不叠到错误的屏上。
- ⚠️ 不要用 `availLeft` 之类去补原点。整条链路不携带「捕获的是哪块屏」这个信息，浏览器的屏幕捕获接口也拿不到它；补原点只会把目前碰巧正确的组合弄坏。相关讨论在 #2841。
- 后端兜底截屏覆盖哪些屏幕因平台而异（`utils/desktop_capture.py`），前端并不知道，所以兜底路径的坐标在多屏下本来就是尽力而为。

相关测试：`tests/unit/test_avatar_annotation_pairing.py`、`tests/unit/test_avatar_annotation_frontend.py`、`tests/unit/test_request_fresh_screenshot.py`。

## 截图暂存：让下一条用户回复看得见屏幕

问题：主动搭话评论了屏幕，但提交进对话历史的只有文字。用户接着回一句「这个啊？」，对话模型不知道「这个」是什么。

做法：`finish_proactive_delivery(vision_screenshot_b64=...)` 在提交成功后调用 `OmniOfflineClient.set_proactive_screenshot`，把截图放进一个单槽；下一次 `stream_text` 把它排在用户自己的图片之前，折进那条用户消息。

规则：

- **只要这一轮拿到了截图并且有可用的视觉模型就暂存**，不看最后投递走的是哪个通道。⚠️ 不要用「主通道是不是 `vision`」做条件：纯屏幕闲聊输出的是不带标签的文本，最终通道是 `chat`，`build_proactive_response` 只在表情包回退时才会给出 `vision`。服务层用的条件是 `phase2_use_vision`。
- **只在提交成功后暂存。** 暂存在归属检查之后；用户接管导致没投递的轮不留截图。
- **新一轮覆盖旧的。** 传 `None` 就是清空，所以没拿到截图的轮也会清掉上一轮的。
- **用完即清**，只注入一次。
- **独立单槽，不和 `_pending_images` 共用。** 那个队列是用户自己的下一张图；共用会把用户的图挤掉或者被它偷走。插件读图用的 `_pending_plugin_images` 也是同样的独立配额。
- 注入前有两道失效检查：
  - 超过 `_PROACTIVE_SCREENSHOT_TTL_SECONDS`：屏幕已经变了，旧图误导大于帮助。
  - 被取代：暂存时记下对话历史长度，注入时长度变了，说明中间插进了别的 AI 轮（问候、工具回调），这条用户回复回应的已经不是那句屏幕评论。
- 还有一类取代靠长度看不出来：头像互动这类 `persist_response=False` 的临时回复，用户看得见但不进历史。所以 `prompt_ephemeral` 在确实吐出了可见文本时直接清槽；中途放弃、没有文本的调用不清。
- 语音回复不处理。语音会话是另一个客户端对象，不继承文字会话的状态，槽随旧客户端关闭而丢弃。
- 实时撤权时不暂存（见下）。

相关测试：`tests/unit/test_proactive_vision_screenshot_staging.py`。

## 授权与拒绝

视觉授权有两层：持久化的 `proactiveVisionEnabled`（隐私模式的反面），和当前连接上的实时状态（前端通过 WebSocket 的 `proactive_vision_consent` 动作通知，`update_proactive_vision_consent`）。实时状态只能收紧，不能凭它授予持久化设置里没有的权限。

### 「明确拒绝」和「技术失败」必须分开

- 技术失败（超时、前端没连上、截屏出错）：可以退回用 Phase 1 那张旧图。
- 明确拒绝（`FreshScreenshot.declined`）：这一轮不能再通过任何旧图恢复视觉输入。隐私模式开启、实时撤权、轮次已经作废，都算拒绝。
- 拒绝判断读设置时用严格模式：读不到设置按拒绝处理，不能因为读失败而放行截屏。
- 请求里带了窗口限定（前端只授权了某个窗口）时，前端超时不能扩大成后端截整个桌面（`allow_backend_fallback=False`）。这种情况下普通超时仍可复用 Phase 1 已经验证过的窗口图，只有拒绝才禁止。
- 后端兜底截屏只在 WebSocket 来自本机时才做；远程连接下截的是服务器的屏幕，没有意义。

### 撤权要在每个边界复查

用户可能在一轮主动搭话进行到一半时关掉授权。带屏幕或窗口内容的轮次在这些位置都要复查：

- 采集来源之前（去掉屏幕和窗口来源）和之后（丢掉已经取回的结果）：`service.py::_collect_sources_under_vision_consent`。
- 创建模型客户端时：`generation.py::_make_proactive_llm` 的 `live_consent_check`。用了屏幕材料的轮，纯文本调用也要查（`vision_consent_required`），因为窗口标题和屏幕描述已经在 prompt 里了。
- 生成结束、提交之前；以及暂存截图的时候。
- 撤权把所有来源都清空时，结果是 `PASS_PRIVACY`，不是 500。
- 屏幕截图和窗口标题属于同一份视觉授权，撤权时一起去掉。

撤权用递增的序号表示，不用布尔值比较：后端是 `_proactive_vision_revocation_epoch`，前端是 `static/app/app-proactive.js` 的 `proactiveVisionRevocationSeq`。「关了又马上打开」之后布尔值和之前一样，但撤权之前截的图不能再用。前端发送前还会读一次 `localStorage` 里最新保存的设置（另一个窗口的撤权可能先落盘，同步事件还没到）。

### 已知并接受的残留窗口

- 前端判断完授权到请求发出之间的毫秒级窗口。
- 另一个窗口「撤权又立刻重新开启」而本窗口的同步事件还没到。

这两种都需要用户在极短时间内完成特定操作才会发生，不为它们增加新的跨窗口协议。评审再提同类意见按此回复。

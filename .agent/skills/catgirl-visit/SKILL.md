---
name: catgirl-visit
description: 串门（catgirl visit，含猫形态串门）的文档与代码地图、设计稿里没写全的不变式、评审反复提但已定不改的取舍。改 `main_logic/visit/`、`main_routers/visit_router/`、`static/visit/`、`visit-chat.js`、串门记忆，或处理串门相关评审意见之前读。
---

# 串门（catgirl visit）

## 文档和代码在哪

- `docs/design/visit-infrastructure.md`：设计稿，协议、常量、时序都以它为准。⚠️ 它有 1 MB 多，单行最长几万字符，不要整篇读：用 Grep 按常量名、端点、帧名或 `OD-xx` 搜，并用 `-o` 加 `.{0,200}关键词.{0,300}` 这类写法限制上下文。常用章节：§2 拍板清单、§3.2 时序、§3.3 iframe、§3.6 对话与轮次、§3.7 记忆、§4.5 显示 socket、§4.6 本机 HTTP、§4.7 Servers、§4.8 常量、附录 A.3 修订记录。抬头还写着「尚未实现」，文中的 `file:line` 是定稿时的旧提交，和代码不一致时以代码与测试为准。
- `docs/design/visit-servers-contract.md`：对 Servers（闭源、另一个仓库）的唯一契约。
- `docs/design/cat-form-visits.md`：猫形态串门的产品基线（设计稿正文不含猫形态）。
- `docs/design/visit-field-tests.md`、`scripts/visit_probe/README.md`：实测清单与脚本。
- `docs/contributing/developer-notes.md` 的「Neko visit: local development loopback」：本地联调。
- 代码：`main_logic/visit/`（纯逻辑：`room` 轮次、`outbox`、`spool`、`forget*`、`memory_commit`、`char_lifecycle`、`sanitize`）；`main_routers/visit_router/`（`runtime*.py` 一场串门的运行时、`display_socket.py`、`http.py`、`debrief.py`、`line_speaker.py`、`persona.py`、`background.py`、`credentials.py`）；`static/visit/`（父页脚本）与 `static/visit/transport/`（iframe 内）；`static/app/app-react-chat-window/visit-chat.js`（聊天面）；常量 `config/visit_settings.py`，提示词 `config/prompts/prompts_visit.py`。

## 不变式

1. **窗口分工**。Electron 多窗口时 Pet 窗也是 `index.html`：它负责取帧和传输 iframe（`static/visit/parent-bridge.js`）、语音旁路（`voice-relay.js`），不画任何串门界面；界面只由 chat 跟随窗（`/chat`、`/chat_full`）或单窗口的 `index.html` 画。主 WebSocket 常在跟随窗手里，所以 `visit_state_change` 要经 `static/visit/display-relay.js` 转给 Pet 窗，否则邀请建好了传输 iframe 却不启动。改这条链路要分别想单窗口和多窗口。
2. **前端事件契约**。`static/app/app-websocket.js` 对所有 `visit_*` 帧先派发一份 `neko-visit-frame`（只派发、不 `return`，后面的分支照常走）；`visit_bind` 发出后由 `visit-chat.js` 读一次 `GET /api/visit/state` 再派发 `neko-visit-bound`，`static/visit/visit-pacer.js` 收到它之前不回报播放进度。`active` 的含义是「当前有一场在进行」，不是「bind 已发」（细节见设计稿 §5 PR-12）。
3. **`static/visit/` 的静态门**（`tests/unit/test_visit_pacer_static.py` 等）：`transport/` 之外的脚本不许调 `requestAnimationFrame(`、不许 `new WebSocket(`，整个目录不许监听 `visibilitychange`。排帧一律用 `nekoFramePacing.requestPacedFrame`；Pet 页面不能再开第二条 WebSocket，要连后端只能在同源 iframe 里（理由见设计稿 §3.3）。
4. **两个开关别混**。`NEKO_VISIT_ENABLED`（环境变量，发布总闸）只关「发起 / 进行」这组路由；`main_routers/visit_router/__init__.py` 里另一组数据管理路由（结束、状态、转录、记忆、举报、debrief 选择）永远可用，用户关掉后仍能结束、导出、清除。新加端点时先想清楚属于哪一组。`visitEnabled` 是用户设置：建房 / 入房时查，中途关掉立刻结束在飞的串门（`runtime.end_visits_disabled`）。
5. **收尾顺序**（`runtime._exit_steps`）：已经 `ready_exchanged`、语音闸门开着、不是猫形态时，先在显示 socket 上直接写 `visit_state_change{voice_off}`，最多等 `_VOICE_OFF_PAGE_S` 秒；没等到页面确认只打一条 warning，照常往下走。然后释放接管（`release_takeover`），释放成功后才推 `ending`，最后推 `ended`。`voice_off` 必须在释放之前发出：串门语音旁路接在共享 TTS 链上，私聊的 TTS 音频随后走同一条连接，晚了就会把私聊的声音发给对方。页面看到 `ending` 就把输入框交还普通聊天。
6. **语音闸门锁存**：只有 Servers 凭证里 `publish.audio` 严格为 `true` 才收发语音轨；首次签发的值整场锁定，续期时 true 变 false 是本场关停且不恢复，false 变 true 忽略（`runtime._audio_gate`；设计稿 OD-15 v4 与附录 A.3.10）。用户关掉 `visitVoiceEnabled` 是本侧全场静音：不发也不播。
7. **JS 与 Python 的对偶常量是硬编码加静态测试**。例如 `static/visit/transport/loader.js` 的 `RX_GUARD_RATIO` / `RX_GUARD_WINDOW_MS` / `AUDIO_KBPS_DEFAULT` 对应 `config/visit_settings.py`，由 `tests/unit/test_visit_transport_static.py` 钉住。改一边必须改另一边，不要改成从 config 生成。
8. **串门记忆与私聊完全隔离**。私聊记忆只写用户在回家汇报里选「记成日记」的产物；串门事实不进 reflection 层。不要给串门记忆加任何「对方同意 / 对方要求忘掉」机制：对方不想被记可以不说，已拍板删除（设计稿附录 A.3.7）。
9. **角色改名 / 删除 / 新建必须走串门钩子**：`main_routers/visit_router/character_hooks.py`（逻辑在 `main_logic/visit/char_lifecycle.py`），在 `character_config_mutation_lock` 下调用；标记在事务提交之前写，事务结束后按配置里的实际结果迁移或撤回。新增任何会改角色名册的路径（含云存档导入）都要接上，否则名册和转录会挂在旧名字下。
10. **登录 / 登出 / 切换社区账号的入口都要包在 `community_oauth.visit_account_change()` 里**：凭证续期和转录上传必须用占房那个账号，所以先结束在飞的串门、等上传文件落盘，再清登录态。
11. **Servers 契约两处同改**：`visit-servers-contract.md` 的「端点」与设计稿 §4.7 逐字相同，由 `tests/unit/test_visit_servers_contract_sync.py` 钉住。新端点登记进该测试的 `ENDPOINTS` 时不要挪动已有项的位置（测试按下标取身份票那一项）。
12. **建房前的两道栅栏**：`background.wait_startup_invite_reset()` 和 `display_socket.wait_desktop_restart_reset()`，都返回 `Optional[Exception]`（`None` 为成功），失败时在建房请求里补跑一次并原样回 Servers 的错误。`visit_bind` 只等本地那一半清理，不等远端批量取消。
13. **猫形态是同一套串门的一个形态**（`visit_mode`，运行时 `cat_scene_active`）：不建对话房间、不传音视频、不写记忆。邀请项里 `visit_mode` 不是 `'cat'` 一律当模型串门，所以桌面壳转来的邀请必须带这个字段。

## 已定不改的取舍（评审再提，按既定结论回复）

每条的理由见 `references/decided-tradeoffs.md`。

- 释放接管连续两次失败，照常推 `ended`，不把 `ended` 延后到 teardown。
- 「清除这个人」只抹转录里的对端身份字段，不删正文；只作废还没开始写的 debrief。
- digest 的空批次表算「未登记」，不算结清；无转录的场次靠显式的「已放弃」终态结清。
- 黑名单没有列表端点；清除某人之后无处解除对他的拉黑，维持现状。
- 猫形态的世界意图发送失败不做重绑后重放。
- 邀请等待期间，用户不能和自家猫娘聊天。
- 「未登录」不当作「无需清理旧邀请」。
- 人设敏感词：裸城市名不登记；档案名只按间隔号拆。
- 接收端码率自保放在 iframe 里判定，不再加「连续 N 拍」计数。
- TTS 开播失败按句计数，不区分这句是否只有动作描写。

## 排查入口

见 `references/troubleshooting-and-testing.md`：一方猫娘只剩字幕没声音、人类插话后两只猫不再开口、转录迟迟不删、实测脚本怎么跑、测试里要注意的桩。

## references

- `references/decided-tradeoffs.md`：处理串门相关的评审意见、或想改上面「已定不改」里任何一条之前读。
- `references/troubleshooting-and-testing.md`：有人报串门故障、要跑实测、或给串门写测试时读。

# 串门：排查入口、测试与实测

## 一方猫娘只剩字幕、没有声音

先分清是哪一种：

1. **本侧这只猫娘整场不出声**。看后端日志里有没有
   `visit line …: speech did not start playing (N in a row)`（`line_speaker.py`）或 `visit: TTS warm-up failed`（`host_port.py`）。
   - 机制：每句台词开播有时限 `VISIT_TTS_START_TIMEOUT_S`，从这句第一次送字起算，TTS 的冷启动时间也算在里面。用户私聊时关着 TTS 的话，TTS 管线是停的，串门第一句才冷启动（实测观察：免费 TTS 冷启动要一到三秒），很容易超时。
   - 现行做法：串门激活时如果语音开着，就先在后台预热一次（`runtime` 激活末尾调 `host.warm_speech()`，失败只记 warning）；开播失败要连续 `VISIT_TTS_START_FAILURES_LATCH` 句才把这一场锁成纯文字（`VoiceState.fallen_back`），中间有一句正常开播就清零；每次开播失败都会打 warning。
   - 所以：只有一条 warning 而后面恢复了是正常的；连续两条之后整场改文字是按设计。回家仪式句和简述失败直接改文字，不计数。
2. **对方听不到本侧的声音，或本侧听不到对方**。查三处：Servers 凭证有没有给 `publish.audio: true`（没给时客户端不发也不订阅，不报错）；任一侧是否关了 `visitVoiceEnabled`（关掉是本侧全场静音）；场次是否已进入收尾（收尾一开始语音就关了）。

## 人类插了一句话之后，两只猫不再开口

设计上对端猫娘回给人类的那句，本侧猫娘会多等 `VISIT_HUMAN_REPLY_GRACE_S` 再接话，把对话带回两只猫之间（设计稿 §3.6.3 ②）。如果又出现「只剩人类问一句、猫娘答一句，最后 `idle_timeout` 结束」，重点查 `main_logic/visit/room.py` 里待回复计划有没有残留：

- 运行时自己排的回复要经 `VisitRoom.hold_reply` 登记，没开口就丢弃的要经 `drop_reply` 清掉；残留的计划会挡住后面的接话（已有待回复计划时不接、不顶掉）。
- 本侧开口时只清掉正在执行的那条计划（同一 `reply_to`，或告别行）。
- 接收侧写盘期间拿着的计划，写完要核对它仍是当前的待回复计划才排。

相关测试在 `tests/unit/test_visit_room.py`、`test_visit_runtime_talk.py`。

## 转录 `.jsonl` 迟迟不删

转录能回收的条件是 `spool.transcript_releasable`：串门区已结清（`region_settled`）并且 debrief 不再需要转录。常见的「不删」都是按设计：

- 对端整场没说话：`segments` 批次表为空，按「未登记」处理，等保留期清扫（`VISIT_SPOOL_RETENTION_DAYS`）。
- 日记写到一半或写入失败（`committing:diary` / `commit_failed:diary`）：`state.json` 会一直留着，直到写完或用户放弃。
- 用户还没在回家汇报里做选择：默认是 `ask_later`，芯片保留可点。

## 邀请建好了，传输 iframe 却没启动（Electron 多窗口）

Pet 窗自己不一定收得到 `visit_state_change`（主 WebSocket 常在 chat 跟随窗手里）。查跟随窗是否加载了 `static/visit/display-relay.js`、帧有没有经它的 `BroadcastChannel` 转到 Pet 窗（`window.nekoRelayVisitStateToDisplay` / `nekoRestoreVisitStateToDisplay`）。单窗口的 `index.html` 不经过这条桥。

## 新邀请刚发出就失效

多半是上一次运行的「批量作废旧邀请」晚到。确认建房是否等过两道栅栏（`background.wait_startup_invite_reset`、`display_socket.wait_desktop_restart_reset`），以及桌面壳每次启动是否在 `visit_bind` 里带了新的 `desktop_run_id`（同一进程里的页面重载不算新运行）。

## 写测试时注意

- ⚠️ 会触发启动恢复或建房栅栏的测试，必须桩掉 `credentials.cancel_pending_host_invites`（`tests/unit/test_visit_startup_tasks.py` 用 autouse fixture 做了）。不桩的话，在已登录社区账号的开发机上跑测试，会真的去作废线上的邀请。
- 检验「bind 不该等某件事」的排序类测试，对 bind 一律包 `asyncio.wait_for`。否则回归时测试是永久挂住，而不是变红。
- 改 `static/visit/`、`visit-chat.js`、`app-websocket.js` 里的串门分支后，跑对应的 `tests/unit/test_visit_*_static.py` 和 `test_visit_*_node.py`：事件契约、静态门、iframe 的 `pointer-events` 和 `z-index` 都钉在这些 Python 侧的测试里。
- 改 Servers 契约跑 `tests/unit/test_visit_servers_contract_sync.py`。

## 实测（T1~T15）

- 每项测什么、判据和失败后的退路在设计稿 §3.12；T1~T5 的结论和数字在 §3.12「T1~T5 实测结论」；结果表在 `docs/design/visit-field-tests.md`；脚本和步骤在 `scripts/visit_probe/README.md`。实现后的复验用 `measure_bridge.mjs`（只读、不连后端、不入房）。
- 会影响以后改动的结论（细节都在设计稿，这里只列结论）：
  - 兼容模式（关闭 GPU 合成）下取帧每帧约 8 ms，所以兼容模式取帧降到 15 fps，直通模式 30 fps。动取帧路径前先想兼容模式的主线程占用。
  - host 侧的访客 iframe 必须显式 `pointer-events:none`，并且 `z-index` 低于模型层；`transparent-overlay` 类只是标识，单靠它在直通模式下会让整窗变成可点。
  - ⚠️ T3 / T4 的 macOS 部分还没测。对外别说「T1~T5 全部通过」，要说「Windows 通过，macOS 待测」。
- 实测方法上踩过的坑（实测观察，Windows）：
  - 移动鼠标用 `SendInput`，不用 `SetCursorPos`：后者不经过低级鼠标钩子，壳的鼠标穿透转发收不到。
  - 判断点击是否穿透，一律看 `WindowFromPoint` 有没有命中 Pet 窗，并配一个模型脸部的正对照。兼容模式下窗口的透明扩展样式标志和实际穿透不一致，不能按标志判。
  - T3 / T4 用的背板窗口不能置顶：壳的置顶窗口都在最顶层，置顶的背板会盖住 Pet。
  - 跑黑帧检测（T2）期间不要操作电脑，后台也尽量别跑东西。有过一轮黑帧事后判定是测试期间的操作干扰。补测数据要逐项看完再下结论。
  - 测直通模式用临时的 `NEKO_USER_DATA_DIR`，不要改用户真实的壳配置。
  - 用户可能边测边用电脑，截图只截测试区域；要独占鼠标之前先问。

# 串门：已定不改的取舍

这些点评审（人或自动评审）会反复提。每条都是维护者拍过板的现行规则，再出现时按这里的理由回复，不要当成新的取舍重新讨论，也不要顺手「修掉」。要改其中任何一条，先找维护者。

设计稿 §2「拍板清单」和附录 A.3 里已有的决定（取消邀请退额度、举报不过期、按社区账号分区而黑名单不分区、写入失败的重试分类与退避封顶、在途字节上限等）同样不再重提，这里不重复。

## 收尾与接管

**释放接管连续两次失败，照常推 `ended`。** `runtime._exit_steps` 里 `release_takeover` 第一次失败就不推 `ending`；推 `ended` 之前再试一次，仍失败也照常推 `ended`，`takeover_token` 留给 teardown 再试。不采纳「释放成功之前不推 `ended`」：那样页面会卡在「正在收尾」直到封存和简述做完，teardown 再失败页面就永远停在串门态。现状下这段时间被拒的普通句带 `request_id` 回来，页面会放回原文并标「未送达」。

**邀请等待期间，用户不能和自家猫娘聊天。** host 领到凭证就接管输入，等客人期间（最长 `VISIT_INVITE_WAIT_S`）打字会收到 `VISIT_INPUT_REFUSED_NOT_READY`，文字留在输入框。不采纳「客人到达才接管」：多一个中间状态，复杂度不值。

**无人插话时按句数规则收尾回家是设计。** 人不在时两只猫聊到上限就道别回家，不是故障（设计稿 §3.6.3）。

## 显示 socket 与旧邀请清理

**「未登录」不当作「无需清理旧邀请」。** 启动时和换了新的桌面进程时都要作废上一次运行留下的邀请；清理失败时在建房请求里补跑一次，仍失败就把 Servers 的错误原样回给页面（未登录是 409 `VISIT_LOGIN_REQUIRED`）。上一次运行登录过的账号在服务端可能还留着旧邀请，跳过清理会让新邀请和旧邀请撞在一起。

**`visit_bind` 不传播本地重置的异常。** `display_socket._reset_for_new_desktop_run` 只等本地那一半任务结束，不取它的异常。抛出去会被 WebSocket 端点外层接住，整条聊天连接按服务器错误断掉；而本地失败时旧运行时还活着，重放的是真实状态，建房栅栏 `wait_desktop_restart_reset` 会补跑。`tests/unit/test_visit_socket_bind.py::test_failed_local_reset_keeps_the_display_socket_and_is_retried_by_admission` 钉住这一条。

**bind 要等本地重置（最多约 5 秒）是有意的。** 旧邀请不能重放给新页面，所以本地重置必须先于 `replay_for_bind`。远端批量取消不在 bind 里等，它在后台按运行顺序排队：旧的取消晚到会把刚发出的新邀请作废，所以才有建房栅栏。

**重置的超时用 `asyncio.timeout`，不用 `asyncio.wait_for`。** Python 3.11 的 `wait_for` 在内层刚好完成时会吞掉外层的取消，关机后恢复任务还会继续跑。

**未 bind 的连接收不到邀请码和台词；bind 之前错过的帧不补发。** 页面重连后靠 `GET /api/visit/state` 的快照和 `bind_replay_frames` 补齐。

**页面按快照校正时，`stale` 分支不向 Pet 窗中继快照。** `visit-chat.js` 的 `applyState(state, stale)`：读快照期间已经收到过 `visit_state_change` 时，阶段以帧为准。此时再中继快照的阶段帧会作废 Pet 窗自己的重载恢复，而且快照里的取景和显隐可能比实时帧旧。非 `stale` 时 `relayStateSnapshotToDisplay` 在阶段帧之后补 `departed` 和 `peer_hidden` / `peer_visible`，顺序不能反：建 iframe 会清掉出门样式和对端显隐。

**「作废本机旧邀请」的判据统一是「guest 还没有 `joined_at`」**，和单房取消一致，不写成「未兑换的邀请码」。

## 记忆、转录与清除

**「清除这个人」只抹身份，不删转录正文。** `VisitSpool.delete_peer_fields()` 抹掉 `state.json` 和 `.jsonl` 头行里的 `peer_uid` / `pair_id` / `peer_char_id` / `peer_char_tag`；正文由 `delete_if_settled` 或 `VISIT_SPOOL_RETENTION_DAYS` 的清扫回收。理由：正文还撑着日记补写、digest 进度、结清判定这些记账，以及本机的场次详情和导出；抹掉身份后正文和这个人在本机已无关联。

**「清除这个人」只作废还没开始写的 debrief。** `forget_runner._VOIDABLE_CHOICES` 是 `None` / `ask_later` / `generating:diary` / `preview:diary`；已经进入 `committing:diary` / `commit_failed:diary` 的有意不碰。「清除后写入中的日记仍会写进私聊」按此回复。

**作废和预览投递之间不加锁。** 投递之后有写后复查（`debrief.settled_after_delivery`），发现已作废就补一帧把按钮置灰。两者可以线性化为「先投递、后作废」，最终一定置灰；加锁或把锁下沉只会让契约继续膨胀。

**digest 的空批次表算「未登记」，不算结清。** `spool.region_settled`：记忆开着时至少要登记一轮，每轮的 `group` 和 `segments` 批次表都非空且全部完成。写入方是先登记轮次、再登记批次，如果把空表当完成，用户在两步之间选「不记」就会删掉还没抽取的转录。代价只是对端整场没说话的场次，转录多留到保留期清扫。不放宽 `segments`。

**无转录的场次用显式的「已放弃」终态结清，不改 `region_settled` 的判断。** 记忆开着但一行转录都没有时，`VisitSpool.mark_no_transcript()` 登记一轮 `abandoned='no_transcript'`，没人选过时把 `debrief_choice` 记成 `abandoned`。只改判断条件分不清「真的一句都没有」和「还有句子在写盘」，有误删的风险；终态写在补录一侧，也能兜住「写入晚于收尾」的路径。

**开轮之后转录行读不出或被改过，这一轮直接放弃。** `memory_commit` 记 `abandoned='batches_mismatch'`，剩下的批次不再发。损坏的文件宁可不写，也不带着旧的幂等键写出对不上的内容。

**云存档下载 / 快照导入改写角色记忆时**（`utils/cloudsave_runtime/_shared.py`）：删 `idempotency_keys.json` 和 `idempotency_staging/`；`scoped_tombstones.json` 保留 `forget_epoch`、只去掉 `erased_epoch`。整份删掉墓碑会撤掉围栏，放过清除之前发出的旧请求。

**清除代数只存在本机，每次用之前先和服务端对齐。** `visit_forget_epochs.json` 不进云存档；digest 开轮前和发清除前调 `memory_bridge.sync_forget_epochs`，取本地与服务端墓碑的较大值并抬高本地。文件读不出要报错（`ForgetEpochsUnreadable`），不能当成空：从零开始会让已清除对象后来的 digest 都显得比墓碑旧而被丢掉。

## 黑名单

**没有「列出黑名单」的端点。** 记忆浏览器的串门面板只能列当前角色名册里的人（`GET /api/visit/memory/peers` 带 `blocked`），拉黑 / 解除走 `POST /api/visit/contacts/block`。「清除这个人」会删名册项，之后就没有地方解除对他的拉黑。已定维持现状，不加列表端点和面板小节。

## 串门人设的敏感词（`main_routers/visit_router/persona.py`）

**裸城市名不登记。** 中文地址关键词（住址 / 地址 / 家住 / 住在 / 位于）后面没有分隔时，值要有地址形态（路名或小区后缀、两个字加省市区县镇村乡、门牌号或数字）才登记为敏感词；带冒号或「是 / 为 / 在」分隔的照样整段登记。理由：全收的话「住在一起」会把「一起」登记成敏感词，大量角色卡生成不出公开人设；城市级位置粒度粗，要和常用说法区分开就得维护地名词表，成本和误判都高于收益。这句话仍会作为私人段落交给独立扫描，并在用户确认前展示。

**档案名只按间隔号（`·` / `・`）拆出名和姓，不按空白拆。**（`local_context.family_names_of`）脱敏是大小写不敏感的整词替换，把 `Will Smith` 拆开会把人设里的 will、brown 这类普通词全换掉。只写名字的漏网由用户在昵称栏补。

**联系方式关键词后面的普通小写词不单独登记。** 值整段登记之外，只把像账号的词（带 `@`、下划线、字母数字混排、驼峰或连字符）单独登记；明确的账号关键词（`line id`、微信号这类）后面的第一个词另算账号。逐词全收会误收 usually 这类常用词。

## 语音与码率

**接收端码率自保的判定放在 iframe 里（`loader.js` 的 `checkRxGuard`），不放后端。** 只对 LiveKit 生效（适配器 `rxGuard: true`）：它没有服务端的单轨码率上限，改过的对端可以超发。放 iframe 的理由：`collectStats` 在取样超时或缺项时会沿用上一拍的接收码率补齐，后端分不清是真测到的还是补的；统计基准什么时候被重置（断线、重连、新轨）也只有 iframe 知道。不采纳「再加连续 N 拍计数」：判据已经要求从第一次超档起每一拍都超、且满 `VISIT_RX_GUARD_WINDOW_S`，没测到码率的一拍会让计时重新起算。`VISIT_AUDIO_KBPS` 只是客户端预算。

**TTS 开播失败按句计数，不看这句有没有可朗读的内容。** 开流失败或推送被拒是 TTS 一侧的故障，和内容无关；改成「先判可朗读再开流」会动到「纯动作描写的句子也提前开流」这条既有行为，有多条测试依赖它。

**回家仪式句和简述（`speak_home_segment`）开播失败仍然直接改文字**，不走「连续两句才锁」：已经在收尾，没有下一句可等。

## 对话轮次

**对端猫娘对人类说的那句，本侧猫娘会延后接话。** 规则和连带不变式在设计稿 §3.6.3 ②（多等 `VISIT_HUMAN_REPLY_GRACE_S`、期间任何人类行取消、已有待回复计划时不顶掉、收尾期间不接）。早期测试曾把「没人接」当成设计锁住；评审说「改了既有行为」时，按「没人接才是缺陷」回复。不采纳「不多等就接」：会抢人类的话。

## 猫形态

**世界意图发送失败，不在重新绑定后重放。** 意图（抓、放、生成毛线球）是一次性输入；重放的是过期的位置，过期的生成意图还会把毛线球重置。重新 bind 只重放已提交的稳定姿态和世界状态（`runtime.bind_replay_frames`）。同理，已绑定但单次写入失败的动作回显也不补发。

**松手是例外，由前端原地重试。** 世界状态显示本侧还抓着毛线球、本页却没有在拖时，`static/visit/cat-world.js` 会带退避地补发松手（每次世界更新只试几次、同时只有一条在途），避免两只猫都被锁住。这和上面的「后端重放」不是一回事。

**同一侧开两个窗口时意图编号可能撞号，不处理。** 编号按场次存在各窗口自己的 `sessionStorage` 里；要彻底区分得给意图契约加窗口标识，不做。

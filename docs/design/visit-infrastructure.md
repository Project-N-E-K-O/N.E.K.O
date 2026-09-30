# 猫娘串门基础设施设计

> 版本：v3 · 2026-09-30 · 状态：31 项全部已拍板（v2 于 2026-09-26 成稿，2026-09-30 逐条定稿，改动见附录 A.3）· 范围：交互机制（发起 / 邀请 / 发现不在本次范围，但邀请码是协议字段）
>
> v1（2026-09-11）→ v2 的变化一句话：视频从「每帧一张 WebP 图片走自建哑中继」改成「30 fps WebRTC 视频轨 + 堆叠 alpha 打包，大陆走腾讯 TRTC 托管、海外走 LiveKit（Cloud 起步 → GCP 自建）」，vendor SDK 跑在 Pet 页面内嵌的同源 iframe 里（闭源壳零改动）；文本与控制面走同一房间的数据通道，可靠性由两侧后端 outbox 兜底，Lamport 时钟定序；身份改为「Servers 核验社区账号后一次签发 vendor 凭证 + 身份票据」，记忆与黑名单绑定社区成员身份；对话默认流式、默认跟本地 TTS 对口型；仲裁触发后不是安静而是自然收尾回家；断线 30 秒判死；每句话立刻落本地流水文件；回家后猫娘简述并询问是否记下来。
>
> v2 → v3（2026-09-30 定稿）：TTS 改为与主聊天一样的流式双工，分句只用来对齐字幕，打断立即停；回家汇报只问「记成日记 / 不记」；确认框不出技术数字，结束后可在「查看详情」看时长、消耗与转录（转录上云）；记忆客户端自建。
>
> 文档类型：提案（proposal），31 项决策已全部拍板，尚未实现；实施后以代码与测试为准。
>
> 阅读顺序建议：先看「一页总览」（§3.0）和「拍板清单速览」（§2.1），再按需展开。凡需要 owner 拍板的条目都按 **现状 / 改成什么 / 回归风险 / 收益** 四段写在第 2 章，架构正文（第 3 章起）默认按推荐项展开。

## 0. 这份文档怎么来的

- **v1 理解与设计（2026-09-11）**：9 个读代理摸清群聊 / 多猫娘、记忆 scope、看板娘渲染与取帧、WebSocket 会话与轮次、网络与身份、插件边界、前端聊天面、仓库门禁；四条轴各 3 份独立设计（12 份）、4 个评审、1 次整合、3 路对抗核验、1 次修订；主会话另核对 25 处代码断言。记录见附录 A.1 与附录 B.1。
- **v2 第一轮反馈（2026-09-12）**：owner 要求 30 fps 不降、单档 600 kbps、贴角色取景默认上半身、大陆走腾讯 / 阿里托管 WebRTC、海外走 GCP、不再自建大陆中继、流式默认开、口型跟 TTS、删掉「≤5 次清零」。为此新做 5 路网络调研（腾讯 TRTC、阿里 ARTC、声网、LiveKit + GCP 报价、Chromium 146 平台事实）与 2 路代码读取（Pet 取帧路径、闭源壳 preload 与窗口），共 7 份带 URL / file:line 的事实文件。
- **v2 第二轮拍板（2026-09-26）**：owner 对 OD-01 / 03 / 05 / 08 / 09 / 10 / 11 / 13 / 15 / 16 / 17 / 21 / 24 / 25 逐条给出决定或追问。据此再跑 1 路复用路径读取（回答「为什么不复用 game / QQ 群聊路径」）、5 份设计（视频 + 传输三种立场、对话轴、身份 / 记忆 / 生命周期轴）、1 个评审（五维打分与合成）、3 路对抗核验（代码事实 / 平台与数学 / 产品安全成本）。核验共提出 5 条 blocker、22 条 major、22 条 minor，全部由主会话逐条裁决后写入本稿（裁决与处置见附录 A.2）。
- **v3 逐条定稿（2026-09-30）**：owner 逐条复核 31 项并全部拍板；OD-15 / 21 改为 TTS 流式双工（一行一个 speech_id，分句只辅助对齐字幕，打断立即停），OD-16 改为「记成日记 / 不记」两个芯片，OD-26 改为确认框不出技术数字、结束后「查看详情」、转录上云长期保留，OD-31 因 QQ 插件移出仓库改为自建记忆客户端。同时按 main 刷新全部代码引用，并补上一起看功能带来的接管路径（三个 takeover 属性、失败回滚、callback sink、独立 ASR 语音劫持点）。记录见附录 A.3。
- **独立复核**：本稿引用的 file:line 以 main `fd2df860e`（2026-09-30）为准——由 `b0b283e34` 的原引用逐条比对首尾行文本后刷新（QQ 插件相关引用保留 `b0b283e34`）；vendor 事实以官方文档 / npm 元数据 / LiveKit 源码为准。

## 1. 需求逐条落点

| 需求（原话与两轮反馈） | 落点 |
|---|---|
| 允许用服务器；A/B 都在 NAT 后；不再自建大陆中继；1000 同接可算 | 大陆腾讯 TRTC 托管（有标清档、自定义轨走正门、数据通道不要求已推媒体）；海外 LiveKit：上线期 LiveKit Cloud，月房·小时超过约 2,500 后切 GCP 自建；大陆零服务器零备案；成本表 §3.5、OD-07 v2 |
| 先不管发起，先做交互 | 本稿从「两侧各持 visit_id 与 Servers 凭证」开始；host 领凭证时 Servers 发一次性邀请码，guest 领凭证必须带邀请码（房间绑定）；发起 / 传递邀请码留下一阶段；§3.2、OD-01 v2 |
| A 的猫娘出现在 B 屏幕上；禁传模型只传视频；30 fps 不能低；画质可低于 720p；贴角色默认上半身 | A 父页每帧渲染完同步喊 iframe 抓上半身裁剪区（320×448），颜色与 alpha 上下叠成一张 320×896 不透明小图，经 WebRTC 视频轨 30 fps 发出；B 的 iframe 用 shader 拆回带 alpha 的画面叠在透明 Pet 窗上；模型文件永不出机；§3.4、OD-02 v2、OD-14 v2 |
| 大陆省流 + 兼顾延迟；只做 600 kbps 一档，更高档留付费 | 单档 sd600：视频 560 kbps + 数据 ≤40 kbps；打包帧 286,720 px 落 TRTC 标清档；hd1200 / fhd2400 只留表项作付费阶梯；拥塞只缩裁剪不动 fps；端到端约 150~300 ms（估算）；§3.4、OD-06 v2 |
| 暂无语音传输；猫娘↔猫娘文字；B 的人类可对访客打字；流式默认开；口型默认跟本地 TTS | 两侧各一个隔离 LLM 串门会话；LLM 边生成边推本地 TTS（一行一个 speech_id，与主聊天同一条流式路径），发给对方的文字按分句切片、逐片清洗、按已播音频估时对齐放出，整句以 `text{final}` 必达收口；B 看到字幕与视频里的嘴对齐；人类插话时她立即停；B 的人类文本经 router 级劫持并带 `source` 寻址；§3.6、OD-03、OD-15 v3、OD-21 v3 |
| 猫娘随时返回；仲裁一句话讲清；触发后自然收尾回家不可打断；断线 30 秒判死 | 连续 6 句没人插话或本侧满 40 句 → 收尾：客人告别一句、东家送客一句、客人回家，期间人类输入拒绝；任一侧可随时结束；心跳 5 s，对端 30 s 无消息判走，自身重连 25 s 上限；§3.6.3、§3.2、OD-08 v2、OD-11 v2 |
| 记忆隔离、保护、安全；握手前核验社区身份，管理员可封禁；记忆绑定社区成员身份 | 串门会话结构上拿不到私聊记忆；Servers 核验 OAuth 账号后签发 vendor 凭证 + Ed25519 身份票（guest 40 min / host 50 min），对端互验；记忆、黑名单、名册、举报都以 Servers 派发的稳定 `visit_uid` 为主键；对端文本当不可信数据（清洗、token 预算、限速、黑名单）；每场双方各把本侧转录与用量上传 Servers，作账单与举报证据；§3.7、§3.8、OD-01 v2、OD-05 v2、OD-23、OD-26 v3 |
| 接入群聊记忆里一块新区域；每句立刻落盘；回家后简述并询问是否记下来 | 复用 `group_chat / group_participant / participant` + platform `neko_visit`，memory/ 只改一处（`FactStore` 新建事实时透传 `origin / visit_id` 与 `absorbed` 初值，供「记成日记」的串门事实用）；每句立刻追加到本地崩溃安全流水文件，结束时做一次摘要进串门记忆区；回家后猫娘简述，聊天里出现「记成日记 / 不记」两个选项，不选就不写私聊记忆；选「记成日记」则日记段进近期记忆、另抽 ≤3 条串门事实进长期记忆的 fact 层（不进 reflection、不进铸卡）；§3.7、OD-04、OD-16 v3、OD-17 v2 |
| 为什么不复用 game 或 QQ 群聊路径 | QQ 路径只有记忆层通用（QQ 插件已于 2026-09-28 移出仓库；串门自建共享客户端 `memory/scoped_client.py` 直连 memory_server 五个端点，OD-31 v3），其余绑在插件进程与人类群聊语义上；game 路径驱动方向相反、归档写私聊记忆；两者原语都借，容器都不用；OD-03 补充问答、§3.10 |
| 敏感记忆筛除（上线前） | 留 issue 草稿：共享的敏感记忆筛除基础设施（串门 / 记忆卡片 / 卡牌系统共用）+ 全局「完全隔离亲人记忆」开关（默认关）；OD-10 |

## 2. 拍板清单

### 2.1 速览

31 项，**全部已拍板（2026-09-30）**。分五组：一、动到既有语义（OD-24 takeover 归属令牌、OD-03 路由注册表、OD-13 rename 守卫、OD-25 goodbye 语义、OD-08 收尾回家、OD-11 30 s 判死）；二、视觉与传输（OD-02、06、07、12、14、20、27~30）——vendor WebRTC 视频轨、托管传输、同源 iframe、数据通道 + outbox + Lamport 定序；三、身份与安全（OD-01、05、23、26）——Servers 核验社区账号、`visit_uid` 主键、出站清洗、知情同意与转录上云；四、对话（OD-15、19、21、22）——TTS 流式双工、分句只辅助对齐字幕、打断立即停；五、记忆与生命周期（OD-04、09、10、16、17、18、31）——逐句 spool、结束时 digest、回家后问「记成日记 / 不记」、自建 `memory/scoped_client.py`。标题带「v2」的条目替换或修改了 v1 同号决策；带「v3」的是 2026-09-30 定稿时 owner 改动的条目（OD-15、16、21、26、31），改动记录见附录 A.3；其余原文保留。速览表由脚本从 2.2 生成。

#### 一、动到既有语义（先做）

| 编号 | 决策 | 推荐 |
|---|---|---|
| OD-24 | takeover 归属令牌：manager 加 acquire_takeover/release_takeover 公共 API；game 与 icebreaker /route/start 查 external route 注册表 | 采纳（这是本轮修订唯一动到既有语义的项，必须做，否则 OD-03 的静音承诺不成立）。owner 已同意（2026-09-26）。 |
| OD-03 | 对话拓扑：两侧隔离 OmniOfflineClient + 主 manager takeover + 泛化 external route 注册表 + stream_data.source 寻址；v1 每角色同一时刻只在一个房间 | 采纳。owner 第二轮的问题「为什么不复用 game / QQ 群聊路径」见本条末尾补充问答。owner 已拍板（2026-09-30）。 |
| OD-13 | rename/delete/切换与串门在飞：rename 400 拒绝、delete 无新钩子、切换走注册表 finalize 且只等状态翻转 | 采纳。owner 已同意（2026-09-26）。 |
| OD-25 | 串门中收到告别（goodbye_state{active:true}）：两侧都 finalize('goodbye')，固定句、静音 | 采纳 finalize('goodbye')。owner 已同意（2026-09-26）。 |
| OD-08 v2 | 轮次仲裁：一句话规则（连续 6 句无人插话 / 本侧满 40 句 → 收尾；每分钟 6 句只顺延）+ 自然收尾回家（host 发起、guest 先告别、告别行即状态、15/45 s 超时、不可打断）+ Lamport 全序 + reply_to 陈旧 + 只有人类打断 | 采纳；数字 6/40/6/1.0~2.5/15/45/5/10。owner 已定方向（2026-09-26），细节已拍板（2026-09-30）。 |
| OD-11 v2 | 连接生命周期：30 s 判死；显式离开立即结束；自身重连 25 s；页面重载宽限 20 s；后端重启即结束；关机钩子 3 s | 采纳。owner 已定方向（2026-09-26），细节已拍板（2026-09-30）。 |

#### 二、视觉通道与传输

| 编号 | 决策 | 推荐 |
|---|---|---|
| OD-02 v2 | 视频通道：父页 postrender 同任务取帧（分数累加器精确 30 fps）→ iframe 内堆叠 alpha 打包到不透明小画布 → captureStream(0)+requestFrame → vendor 自定义轨；B 侧 WebGL 解包 | 采纳。owner 已拍板（2026-09-30）。 |
| OD-06 v2 | 档位：只发 sd600（320×448 上半身 / 256×560 全身 → 打包 286,720 px、30 fps、视频 560 + 数据 ≤40 kbps）；hd1200 / fhd2400 留付费表项；拥塞只缩裁剪不动 fps（最低 300 kbps）；免费额度由 Servers 按账号计分钟 | 采纳；免费额度 120 分钟/天保持占位，定价时由 owner 定。owner 已拍板（2026-09-30）。 |
| OD-07 v2 | 传输选型：大陆 TRTC 托管；海外 LiveKit（上线期 LiveKit Cloud Ship → 月 >≈2,500 房·小时切 GCP 自建，GCP 是稳态目标）；自建大陆中继目录作废 | 采纳。海外「Cloud 起步 → GCP」是对 owner「GCP 中转（你来选型）」的落地节奏：GCP 是稳态目标，Cloud 是小体量阶段更便宜且零运维的过渡。owner 已拍板（2026… |
| OD-12 v2 | 区域与 transport 判定：Servers 在 host 领凭证时按 host 区域定 transport；guest 拿同一 transport，region_hint 只判 cross_region；跨区默认 fail-closed 403；页面不接受任何外来 URL | 采纳；跨区首发 403；T9 实测后由 owner 决定是否放开。owner 已拍板（2026-09-30）。 |
| OD-14 v2 | B 侧承载：iframe 即访客图层（隐藏 video + 透明 WebGL 解包画布，rVFC 驱动；pointer-events:none + transparent-overlay）；A 侧 .visiting-away + 徽标沿 v1 | 采纳。owner 已拍板（2026-09-30）。 |
| OD-20 v2 | 视频拥塞控制交给 WebRTC：删客户端↔中继单 socket 与 2 帧在飞窗口；应用层只做 5 s stats 反馈阶梯 | 采纳。owner 已拍板（2026-09-30）。 |
| OD-27 | 同源 iframe 承载 vendor SDK 与访客图层（lanlan_frd 零改动）；能力门在领凭证之前 | 采纳（本稿前提；T1~T5 任一失败退设计 1，只损失前端两 PR，后端 PR 完全通用）。owner 已拍板（2026-09-30）。 |
| OD-28 | vendor SDK 随包分发：static/libs/trtc.js（5.20.1，ISC）与 static/libs/livekit-client.umd.js（2.22.3，Apache-2.0）；登记 THIRD_PARTY_NOTICES + licenses + check_nuitka_dist 必需表；只在 iframe 内按 transport 懒加载 | 采纳。owner 已拍板（2026-09-30）。 |
| OD-29 | iframe ↔ 本机后端走独立 WebSocket /api/visit/transport/ws；凭证只在这条 socket 下发；断开 = 页面重载宽限 20 s 的触发源 | 采纳。owner 已拍板（2026-09-30）。 |
| OD-30 | 文本 / 控制走 vendor 数据通道 + 后端 VisitOutbox 可靠层 + Lamport 定序（无 host 定序、无中继 order） | 采纳。owner 已拍板（2026-09-30）。 |

#### 三、身份与安全

| 编号 | 决策 | 推荐 |
|---|---|---|
| OD-01 v2 | 身份与凭证：Servers 核验社区账号后一次签发 vendor 凭证 + Ed25519 身份票（guest 40 min / host 50 min）；数据通道首包 hello 互验；房间绑 invite_code；封禁按 visit_uid；PSK 产品路径删除 | 采纳。owner 已拍板（2026-09-30）。 |
| OD-05 v2 | 记忆与黑名单绑定社区成员身份 `visit_uid`：对方亲人人级主体跨对累积；名册与黑名单主键 visit_uid | 采纳。owner 已拍板（2026-09-30）。 |
| OD-23 | 输出侧亲人名替换 + 出站文本过同一清洗函数 + 回家自述 n-gram 断言 | 做。owner 已拍板（2026-09-30）。 |
| OD-26 v3 | 知情同意与用量透明：guest 出门前确认框 + host 接待确认（60 s），确认框不出现技术数字；结束后藏得较深的「查看详情」（时长 / token / TTS 消耗 + 完整转录，数据来自云端）；转录每场上云、长期保留、只在隐私政策披露 | 采纳。owner 已拍板（2026-09-30）：确认框不出技术数字；「查看详情」入口藏深；转录上云、长期保留；只在隐私政策披露；对端撤销不删云端。 |

#### 四、对话机制

| 编号 | 决策 | 推荐 |
|---|---|---|
| OD-15 v3 | 口型与语音：单一设置 visitVoiceEnabled（默认 true）；一行台词一个 speech_id，LLM 边生成边推进本地 TTS（流式双工，与主聊天同一条推流路径）；字幕按分句估时对齐已播音频；打断立即停；删「本地静音但保留 RMS」开关 | 采纳；默认 visitVoiceEnabled=true；流式双工 + 分句只辅助对齐字幕 + 打断立即停。owner 已拍板（2026-09-30）。 |
| OD-19 | 前端访客身份：复用 role 'tool'，样式作为新增规则写进 static/css/index.css | 采纳。owner 已拍板（2026-09-30）：样式无所谓，只要标明来源（哪家的猫娘 / 哪家的亲人）。 |
| OD-21 v3 | 猫娘台词流式转发：默认开；LLM 边生成边推 TTS（OD-15 v3），发给对方的文字用增量分句器切片、逐片清洗、按已播音频对齐放出 line_delta（可丢、只上屏）+ text{final} 全文必达收口（被打断行 truncated + 已放出前缀）；VISIT_STREAM_DELTAS=False 留紧急开关 | 默认开；LLM 流式 + 增量分句逐片清洗 + 按已播音频对齐放出。owner 已拍板（2026-09-30）。 |
| OD-22 | guest 侧（A）人类在串门期间打字：拒绝 + toast，不做「捎话」 | 采纳。owner 已拍板（2026-09-30）。 |

#### 五、记忆与生命周期

| 编号 | 决策 | 推荐 |
|---|---|---|
| OD-04 | 串门记忆区建模：复用 group_chat/group_participant/participant + platform=neko_visit + 按 (subject_kind, platform) 选标题表（两张表同键）+ 群 digest/对端 segments 双形态 | 采纳。owner 已拍板（2026-09-30）。 |
| OD-09 v2 | 开关与撤销（人话版）：三开关进白名单与插件禁改集；本机中途 OFF = 这场不记 + 删 spool；对端撤销 scope=all 连群 subject 一起清；NEKO_VISIT_ENABLED 总闸 | 采纳；「记住串门内容」默认关。owner 已拍板（2026-09-30）。 |
| OD-10 | 隔离会话历史与召回：持久历史 + task 级打断 + 复读守卫防御 + 不带私聊召回 + 原始角色卡（亲人名中性化）+ bootstrap ≤2000 tok | 采纳；产品上线前依赖下方 issue（#TBD）。owner 已接受「串门时不记得家里最近的事」并要求留 issue（2026-09-26）。owner 已拍板（2026-09-30）。 |
| OD-16 v3 | 回家汇报（debrief）：她临时记得 → 回家简述 → 两个芯片「记成日记 / 不记」；超时与崩溃都不默认写私聊记忆；串门区 digest 与 debrief 无关；做进本次交付的小 PR | 做进本次交付（小 PR，叠在核心 PR 之后），不留 issue；芯片只留「记成日记 / 不记」两个。owner 已拍板（2026-09-30）。owner 2026-09-30：日记进近期记… |
| OD-17 v2 | 记忆写入时机：每句立刻追加本地崩溃安全 spool（config_dir/visit_spool/，fsync 30 s + finalize）；digest 在结束时做一次；10 min 周期作开关默认关；崩溃补录只重新弹芯片 | 采纳（spool + 结束时 digest；10 min 周期作开关默认关）。owner 已拍板（2026-09-30）。 |
| OD-18 | 记忆浏览器数据源：memory_server 加只读 GET /internal/memory/{name}/scoped_subjects?platform=；/api/visit/memory/peers 按 visit_uid 聚合 | 加只读端点。owner 已拍板（2026-09-30）。 |
| OD-31 v3 | 串门自建共享记忆客户端 memory/scoped_client.py（直接对 memory_server 五个 /internal/memory/* 端点）；bot 公共记忆组件的形态待 owner 与 QQ 插件作者商量后另定 | 独立 PR 先行（只新增）。owner 已拍板（2026-09-30）。 |


### 2.2 逐项四段

每项：现状（读过代码的事实，file:line 以 2026-09-26 三份核验报告修正后的为准）/ 改成什么 / 回归风险 / 收益 / 推荐 / 备选 / 卡住哪些实施项。每条的拍板结论与日期写在「推荐」末尾。


#### OD-01 v2 身份与凭证：Servers 核验社区账号后一次签发 vendor 凭证 + Ed25519 身份票（guest 40 min / host 50 min）；数据通道首包 hello 互验；房间绑 invite_code；封禁按 visit_uid；PSK 产品路径删除
- 现状: 桌面端唯一可云端核验的身份是社区 OAuth：PKCE 登录 `auth.project-neko.cn`（`main_routers/community_oauth.py:39-40`），`POST {social_base}/api/auth/session/bootstrap` 换社区会话（`:930-934`），`user.id` 经 `_normalize_local_user_id` 必须是合法 UUID 才算登录（`:811-813`；`card_drop_router.py:571-577`）；`_desktop_session_snapshot()` 给出 `base_url / access_token / refresh_token / local_user_id / auth_source / client_id`（`card_drop_router.py:585-598`）；社区基址 `https://community.project-neko.cn` 在 `card_drop_router.py:41` 与 `utils/social_base.py:12`；设备级 `client_id/client_proof` 本地铸造后 `POST /api/clients/register`（`client_registration.py:1-11`；`storage_roots.py:1048-1059`），登录时 `/api/auth/bind-client/challenge` 把设备绑到账号（`community_oauth.py:957-983`）——Servers 今天已知道「哪个账号绑了哪台设备」。平台 access token 出本机只发给 Servers 自己（`card_drop_router.py:999-1006`），给社区网页的是 10 min native delegate（`:52`、`:214`）。仓库无任何 Ed25519 校验代码（grep 零命中）；`cryptography>=45.0.6` 是直接依赖（`pyproject.toml:62`）；telemetry 软 HMAC 时间容差 ±300 s（`local_server/telemetry_server/security.py:38, :79`）。vendor 凭证形状：TRTC `UserSig = HMAC-SHA256(SDKSecretKey; SDKAppID, UserID, expire)` 服务端生成，`userId ≤32 字节 [a-zA-Z0-9_-]`、`strRoomId ≤64 字节`（https://web.sdk.qcloud.com/trtc/webrtc/v5/doc/en/TRTC.html ）；LiveKit 是 HS256 JWT（grants roomJoin/room/canPublish/canSubscribe/canPublishData），两者密钥都不能进客户端。v1 的 Ticket/MemberToken/Psk 三 verifier、member_token 续连、4404 boot_id 重建全部依附自建中继，v2 没有宿主。
- 改成什么: (1) 身份 = Servers 派发的稳定不透明 id `visit_uid = HMAC(server_secret, community_uuid)[:24]`，对所有对端相同、Servers 可反查；记忆、黑名单、名册、举报一律以它为主键；UI 只显示 display_name 与 6 位短码，永不显示裸 uuid。未登录（快照无 `local_user_id`）→ `POST /api/visit/rooms|join` 回 `409 VISIT_LOGIN_REQUIRED`。(2) Servers 新端点 `POST /api/visit/credentials`（`Authorization: Bearer <access_token>` + `X-Client-Id`），body `{role, visit_id, char_tag(32hex), region_hint:'cn'|'global'|'unknown', tier:'sd600', display_name?, invite_code?}` → 200 `{transport, expires_at, vendor:{trtc:{sdk_app_id, user_id, user_sig, str_room_id} | livekit:{url, token}}, identity_ticket, cross_region, invite_code?}`（只含所选 vendor）；错误码 401 未登录 / 403 `visit_banned` / 403 `tier_not_entitled` / 403 `cross_region_unsupported` / 403 `room_full` / 429 `quota_exceeded`。**核验社区账号发生在任何 vendor 连接之前**：没有 OAuth 就拿不到 UserSig / JWT。(3) 票据 claims 统一 `{v:1, iss:'neko-servers', aud:'neko-visit', kid, sub:<visit_uid>, vid, visit_id, role, transport, char_tag, display_name?, iat, exp, jti}`，Ed25519 签名，**TTL 按侧位**：guest **40 min**（`exp = iat + 2400`，`VISIT_CREDENTIAL_TTL_S`），host **50 min**（`exp = iat + 3000`，`VISIT_HOST_CREDENTIAL_TTL_S = VISIT_INVITE_WAIT_S 600 + VISIT_MAX_DURATION_S 1800 + 余量 600`——host 领凭证后最多先等 10 min 对端入房，再串 30 min），vendor 凭证与票同 TTL（TRTC `expire` / LiveKit `ttl` 同值）——等待 + 硬顶 30 min + 重连 ≤25 s 覆盖足够；时钟容差 ±300 s；同房同 `vid` 重连允许重放同一 jti。`vid`（vendor userId / identity）= `role[0] + '_' + sha256(visit_uid|visit_id)[:24]`（26 字符，落 TRTC 字符集），vendor 侧看不到稳定 id。(4) 票据留在本机后端（不给 iframe），作为第一条数据通道消息 `hello{ticket, caps{video, tier, proto:1, app_version(major.minor)}, lang}` 交换；核验顺序：验签（kid 查公钥表）→ `aud` / `visit_id` / `role` 互补 / `exp` → `vid == vendor 盖的发送者 id`（TRTC `CUSTOM_MESSAGE.userId` / LiveKit `participant.identity`）→ `sub ∉ 本地黑名单`；通过前不订阅视频、不接受 `text`、host 不弹接待确认；任何一步失败 → `leave{reason:'peer_identity_rejected'}` + finalize。(5) **房间绑定**：host 领凭证时 Servers 把 `visit_id` 登记到 host 的 `visit_uid` 下并返回一次性 `invite_code`（10 min）；guest 领凭证必须带 `invite_code`，Servers 校验后把 guest 绑到该房；每房最多 host + guest 各一，第三者领不到该房凭证；后端下发给 iframe 的凭证：guest 侧 `credentials.peer_vid` 必填；host 侧在对端 hello 核验通过后经 `media{peer_vid}` 下发，`REMOTE_USER_ENTER` 出现第二个未知 vid → 该来源全部丢弃并计数，且本侧发 `leave{reason:'peer_protocol_violation'}` 结束（房间绑定下正常不会发生）。发起 / 邀请的传递方式仍在范围外，但 `invite_code` 是协议字段。(6) **封禁闭环**：Servers `POST /admin/visit/bans {visit_uid, until?}` → 拒发新凭证；在飞场次由 Servers 调 vendor 服务端踢人（TRTC `RemoveUserByStrRoomId`，https://cloud.tencent.com/document/product/647/50426 ；LiveKit `RoomService.RemoveParticipant`，https://docs.livekit.io/home/server/managing-participants/ ）——列为 **Servers 侧 follow-up**，不阻塞 v1；客户端黑名单在 hello 阶段立即生效。举报 `POST /api/visit/reports {visit_id, peer_uid, transcript, reason}`。(7) 公钥：内置 `config/visit_settings.py::VISIT_SERVERS_PUBKEYS = {kid: base64}`，另 `GET /api/visit/pubkeys`（公开、缓存 24 h）作轮换期第二来源；kid 不命中且拉不到 → fail closed。(8) PSK 产品路径删除；开发环回 `NEKO_VISIT_DEV_KEYFILE=<path>` + `scripts/visit_dev_mint.py`，核验代码路径与生产完全相同（没有「跳过验签」分支），只多一把开发公钥。(9) 客户端落点：`main_routers/visit_router/credentials.py::fetch_visit_credentials(role, visit_id, char_tag, invite_code=None)`（401/403 映射本地错误码）；`main_logic/visit/identity.py::verify_identity_ticket(ticket, *, expect_visit_id, expect_role, expect_vid, now, pubkeys, blocklist, jti_window)`（`Ed25519PublicKey.verify`，纯函数）。
- 回归风险: 仓库内零回归（全新路径）。产品面：未登录不能串门（owner 要求）。跨仓库硬依赖：Servers 四端点（credentials / pubkeys / reports / admin bans）+ Ed25519 密钥 + 腾讯云 SDKSecretKey / LiveKit secret 托管 + UserSig（tls-sig-api-v2）与 JWT 签发 + invite_code 登记表；Servers 宕机 → 发不出新场次，**在飞场次不受影响**（凭证与票据都在手里）。时钟偏差 >300 s 的用户核验失败（与 telemetry 同容差）。IP：TRTC / LiveKit 都是 SFU，ICE 只在客户端与 SFU 之间，**对端拿不到你的 IP**；Servers 以来源 IP 复核区域所以 Servers 知道（vendor 亦知道）。40 min TTL 比 v1 的 10 min 长，但只在两台已过鉴权的机器间交换且绑 `visit_id + vid + role`，重放到别的房无效；被封账号手里的凭证在飞踢人 follow-up 落地前最多再有效 40~50 min。密钥轮换：内置公钥表随发版。
- 收益: 逐字满足「握手前核验社区身份 + 管理员按账号封禁」；每个参与者可追到社区账号；vendor 密钥永不出 Servers；房间绑定后「拿到邀请码 = 可旁听」的漏洞关闭；删掉 v1 一整个中继鉴权子系统（三个 verifier、member_token 表、boot_id 重建）；开发模式与生产同一条核验路径；不自建任何鉴权服务器。
- 推荐: 采纳。owner 已拍板（2026-09-30）。
- 备选: 票据放 iframe 由页面验（公钥与验证逻辑暴露给页面） | 只靠 vendor userId 互信（无法封禁与拉黑） | 每次重连重领凭证（Servers 成在飞硬依赖） | 裸社区 uuid 作 sub（对端磁盘拿到可对应社区站的账号 id，隐私面更大） | 设计 3 (b) OAuth-on-Upgrade 到 Servers WS（Servers 要做长连接房间状态机）
- 卡住: Servers 四端点与密钥托管, visit_router/credentials.py, main_logic/visit/identity.py, config/visit_settings.py 公钥表, scripts/visit_dev_mint.py, tests/unit/test_visit_identity.py（篡改 sub / 过期 / role 对调 / vid 不符 / 未知 kid 五种变异必红）

#### OD-02 v2 视频通道：父页 postrender 同任务取帧（分数累加器精确 30 fps）→ iframe 内堆叠 alpha 打包到不透明小画布 → captureStream(0)+requestFrame → vendor 自定义轨；B 侧 WebGL 解包
- 现状: `#live2d-canvas` 由 `PIXI.Application({view, width: screen.width, height: screen.height, transparent:true, backgroundAlpha:0, resolution, autoDensity:true})` 创建（`static/live2d/live2d-core.js:321-346`），未开 `preserveDrawingBuffer`；PIXI 7.4.3 renderer 对任何 `renderer.render(x)` 都 emit `postrender`（`static/libs/pixi.min.js:529` 压缩行内；`lastObjectRendered` 只在无 renderTexture 时更新），仓库零监听者；`avatar-portrait.js:1226 / :1256` 三轮 `renderer.render(tempStage)` 与 `generateTexture` 也会触发它。读像素的既有做法 = 同任务 `renderer.render(stage)` 再 `drawImage(sourceCanvas, 源矩形)`（`static/avatar/avatar-portrait.js:1385-1387`、`:1926-1936`）；未被调用的 `makeUpperBodyRect(subjectRect, options, biasY)`：宽 = max(w×1.04, h×0.58×aspect)，高 = max(h×0.64, 宽/aspect)（`:509-521`）。`getModelScreenBounds()` 在 edge-peek `hidden/hiding` 返回 null（`live2d-core.js:5271-5276`）；`getHeadDetectionGeometryInfo()` 无缓存（`:5030`）。帧率链：`LIVE2D_IDLE_FPS=30`（`:59`），`_resolveIdleFps = configured===0 ? 30 : min(30, configured)`（`:789-792`），`setTargetFPS` 写 `window.targetFrameRate`（`:752-780`），定时器 tick 周期 `Math.round(1000/fps)`（`:947`），`_hasRenderActivity`（`:1029-1044`），governor 300 ms（`:1046-1080`）；`frame-pacing.js:31 TIMER_DRIVE_REFRESH_RATIO=0.9`、`:56-63 activeTimerTickFps`。Pet 窗 `backgroundThrottling:false`（`lanlan_frd/src/window-manager.js:1009`），Electron 官方 BrowserWindow 文档「Page visibility」：backgroundThrottling 禁用时 visibility 在最小化 / 遮挡 / 隐藏下仍保持 `visible`（https://www.electronjs.org/docs/latest/api/browser-window ），`live2d-core.js:955-956` 注释同义；`screen-capture-ipc.js:1488-1491`、`:1705-1708` 记录 Pet hide 后 renderer 定时器可被拖慢到秒级。平台：WebRTC 载荷无 alpha、WebCodecs 拒 `alpha:'keep'`；`captureStream` 的帧在画到画布的脚本任务结束时抓取；不透明画布（`alpha:false`）才走一拷贝快路径；`contentHint='detail'/'text'` 切进 screencast 模式（VP9 钳 5 fps）；libwebrtc 无 hint / `kFluid` → `MAINTAIN_FRAMERATE`（`webrtc_video_engine.cc:2011-2046`），且 QP 质量缩放器仍会因高 QP 主动降分辨率。Electron 41 = Chromium 146，官方构建含 OpenH264；Windows 硬件 H.264 CBP 默认关、macOS 无；lanlan_frd Linux X11 追加 `--disable-accelerated-video-encode`（`src/main.js:490-497` 常量，`:563-566` 应用）。
- 改成什么: iframe 内两块画布 `scratch`（320×448，透明）与 `pack`（320×896，`getContext('2d',{alpha:false})`）——这是上半身尺寸；全身为 256×560 / 256×1120，画布与 `profile.width/height` 一律从当前构图几何推导，切构图时按新尺寸重建并 `updateLocalVideo`（§3.4.2）。每帧（同任务）：`pack` 填黑 → 上半 `drawImage(parentCanvas, 裁剪源矩形 → 0,0,320,448)`（颜色 over 黑 = 预乘色）→ `scratch` 填白、`destination-in` 画源矩形（白×alpha）→ `pack` 下半 `drawImage(scratch → 0,448)`（亮度 = alpha）→ `packTrack.requestFrame()`。**取帧门 = postrender 内分数累加器**：`acc += 30 / renderFps; if (acc >= 1) { capture(); acc -= 1 }`，`renderFps` 取最近 1 s 实测 postrender 频率——任何 ≥30 fps 的源平均恰好 30 fps（不再用「距上次 ≥33 ms」门：75 / 144 / 165 Hz 或定时器 60 fps 下会掉到 25 / 28.8 / 29.4 fps）；`0 < configured < 30` 时父页 `savedFps = window.targetFrameRate; setTargetFPS(30)` 并在结束恢复；`_hasRenderActivity()` 加一行 `if (this._visitCaptureActive) return true;`（live2d-core.js 唯一改动）。**postrender 过滤**：只在 `renderer.lastObjectRendered === pixi_app.stage` 且无 renderTexture 绑定时取帧（avatar-portrait 的临时舞台与 generateTexture 跳过），`avatarPortrait.capture` 前后 `parentBridge.suspendCapture()`。**隐藏判据**：不依赖 `document.hidden` / `visibilitychange`（Pet 窗下不一定为 true）；有帧就发、没帧就停，父页由「1 s 无 postrender」推导 1 Hz `state{hidden:true}`，首帧恢复即 `hidden:false`；B 显示最后一帧 `opacity:.6` + 徽标。`pack.captureStream(0)` 的轨 `contentHint='motion'` 交 vendor：TRTC `startLocalVideo({publish:true, option:{videoTrack, profile:{width:320, height:896, frameRate:30, bitrate:560}}})`（`profile` 对自定义轨是否生效未文档化 → T6）；LiveKit `publishTrack(track, {source:Camera, simulcast:false, videoCodec:'vp9', scalabilityMode:'L1T1', videoEncoding:{maxBitrate:560_000, maxFramerate:30}, degradationPreference:'maintain-framerate'})`——`scalabilityMode` 必须显式：不设则 SDK 对 vp9 默认 `L3T3_KEY` 三层 SVC（`LocalParticipant.ts` `opts.scalabilityMode ?? 'L3T3_KEY'`），560 kbps 会分给三个空间层；VP9 软编 CPU 超阈值（编码 fps <27 持续 10 s）→ 下次串门 vp8。**guest 收到 host `ready` 后才 `publish(track)`**；LiveKit `autoSubscribe:false`，`ready` 后 `setSubscribed(true)`。接收侧：TRTC `REMOTE_VIDEO_AVAILABLE{userId===peer vid}` → `startRemoteVideo({userId, streamType:STREAM_TYPE_MAIN, view:null})`（不传 view 不渲染但仍消耗带宽）→ `getVideoTrack` / `TRACK` 事件；LiveKit `TrackSubscribed` → `track.mediaStreamTrack`；→ 隐藏 `<video muted playsinline>` → `requestVideoFrameCallback` 每帧一次 `texImage2D` → 解包 shader（上半取 rgb、下半取 r 作 a，`blendFunc(ONE, ONE_MINUS_SRC_ALPHA)`，画布 `premultipliedAlpha:true, alpha:true`）。裁剪框每 300 ms~1 s 刷新 + 滞回（中心偏移 <4% 且尺寸变化 <8% 不动框，动框 300 ms 线性过渡）。
- 回归风险: 零既有热路径改动（v1 的 websocket_router 二进制分支与 app-websocket Blob 分支 diff 为空）；`live2d-core.js:1029` +1 行。技术风险：`destination-in` 在 2D 画布上的输出（T5；不成立退 WebGL 打包 shader）；alpha 经 4:2:0 有损编码后 1~2 px 灰边（估算，T12）；libwebrtc QP 缩放器可能把 320×896 稳态降到 240×672 而应用层 `stats{rx_fps}` 抓不到——接收端以 `videoWidth/Height` 观测，T6/T8 记录 `qualityLimitationReason` 与 `frameWidth/Height`；hide-all 后帧是否继续出要 T10 实测 `framesEncoded`。性能（估算）：主线程每帧 3 次 drawImage ≈0.3~1.5 ms；软编 CPU 按 2021 VGA 数据外推 H.264(OpenH264) 8~14%、VP9 单层 26~38% 单核。
- 收益: 真 30 fps、连续视频编码（帧间压缩）在 600 kbps 下画质远高于 v1 图片帧；alpha 保住；表情口型原样带走；视频完全不经 Python、不经 display socket；采样器在任何刷新率下都是精确 30 fps。
- 推荐: 采纳。owner 已拍板（2026-09-30）。
- 备选: 色键（半透明发丝 / 阴影全丢） | 并排打包（同面积，无本质差别） | WebCodecs 自编码 + 中继（回到自建视频中继，owner 已否） | 串门期间无条件 `setTargetFPS(30)`（本地渲染也降到 30，作为累加器失效时的退路）
- 卡住: static/visit/transport/{pack,unpack,frame-sink}.js, static/visit/parent-bridge.js, live2d-core.js:1029 +1 行, 实测 T2/T5/T10/T12

#### OD-03 对话拓扑：两侧隔离 OmniOfflineClient + 主 manager takeover + 泛化 external route 注册表 + stream_data.source 寻址；v1 每角色同一时刻只在一个房间
- 现状: 外部实体让本机猫娘回答只有 submit_proactive_callback（→ prompt_ephemeral 回复入主会话历史 _lifecycle.py:752-753、指令抄送插件总线 :559-568）；game 蓝图：隔离 OmniOfflineClient + mirror + takeover。websocket_router.py:1041-1047 的 _stamp_user_input_ingress/_record_stream_engagement_ingress 在 :1048 劫持点之前；:949-956 对 game 的 start_session{text} 是 ack-only；app-buttons.js:3104-3109 无会话时先发 start_session{text}。OmniOfflineClient 没有 send_text（只有 connect/stream_text/prompt_ephemeral/create_response/handle_interruption）。mgr 只有一个 takeover 槽。
- 改成什么: utils/external_route_registry.py（kind → is_active/route_stream_message/on_start_session/finalize_for_character + route_external_start_session）；websocket_router 三处、proactive 门、crud 切换改调注册表；visit 的 on_start_session 对 text ack-only、audio 拒；handler 读 source 定收件人；两侧隔离会话（tool_definitions=[]、max_response_length=VISIT_RESPONSE_MAX_TOKENS=160、master_name=FAMILY_NEUTRAL_TERM）；出站与入史拆成 relay_session.send_text（入队即返回；v2 实现 = VisitOutbox → iframe → vendor 数据通道，见 OD-30，接口不变）+ llm_session._conversation_history.append；B 人类文本经 mirror_user_input；engagement 记账保持原位（亲人在场是真实活动）。互访不再是「两个房间」，v1 明写每角色一房。
- 回归风险: 中低：game 路径逐字节等价；websocket_router 三处 + 两处守卫改动需回归报告。代价：串门不在主会话历史；两侧各付角色卡 token；互访要等 v1.5。
- 收益: 私聊记忆零泄漏靠结构成立；零新 WS action；旧窗口 stream_data 也被劫持；B 亲人第一次打字不会在 takeover 中的主 manager 上起普通文本会话。
- 推荐: 采纳。owner 第二轮的问题「为什么不复用 game / QQ 群聊路径」见本条末尾补充问答。owner 已拍板（2026-09-30）。
- 备选: submit_proactive_callback 走主会话（多轮不连贯、进私聊记忆、抄送总线） | 新 WS action visit_send（前后端同时上线） | 两个房间做互访（第二个 activate 必被 route_owned 拒）
- 卡住: visit_router/runtime.py, external_route_registry.py, websocket_router.py, session_pool.py, visit-chat.js

补充问答（为什么不复用 game / QQ 群聊路径；owner 已采纳，2026-09-30）。注：QQ 自动回复插件已于 2026-09-28 移出仓库（#2996），下文 QQ 相关文件与行号均以 `b0b283e34` 为准；结论不变，记忆层的复用改为 OD-31 v3 的自建客户端：QQ 群聊路径只有记忆层是通用的——`group_chat/group_participant` 主体、`scoped_history` 单 subject + segments 双形态、接收边界章、`name(id)` 标签、分批结算（`plugin/plugins/qq_auto_reply/memory_bridge.py:60-88`、`session_memory_service.py:1805-1893`、`message_dispatcher.py:432-449`）。其余全绑在「跑在插件进程里、给人类群聊当机器人」这个前提上：宿主是 agent_server 内嵌插件服务器的独立线程 / 事件循环（`app/agent_server/plugin_host.py:140-165`），对主进程 `SessionManager` 零引用（全插件 grep `submit_proactive_callback / prompt_ephemeral / append_context / _takeover_active` 无命中）。它自建 `OmniOfflineClient`（`session_bootstrap_service.py:231-244`）、自建 TTS 发 QQ 语音条（`voice_reply_service.py:75-110`），Pet 取帧、猫娘嘴型、主会话静音、回家汇报四件事一件都碰不到，每句每帧都得跨进程。门控是焦点群 / @bot / 疲劳 / 60 s 内 >3 条静默（`attention_gate_service.py:178-296`），发言人只有 admin/trusted/normal/none 四档（`permission.py:13`）；对端猫娘只能登记成一个「trusted 用户」，台词会进成员画像与信赖度池。prompt 写死「QQ群 {gid}」「（QQ: id）」「self_id」并注入亲人名（`session_instruction_service.py:345-349, :597-630, :1084-1087`），与 OD-10 中性化相反。game route 的驱动方向反了：页面 POST 事件进来（`/game_chat`、`/speak`、heartbeat），串门是服务端拿着传输连接被对端驱动。它的归档写进亲人的 legacy `/cache`（`archive.py:781-782`），正是 OD-10 禁止的；prompt 与记忆策略键全是 soccer/badminton 形状（`session_pool.py:124-160`、`mirror_meta.py:88-110`）。`start_session audio` 会去起 realtime 当 STT（`websocket_router.py:949-968`），`opened` 事件会隐藏 pet 容器（`route_lifecycle.py:94`），每句对端台词会被 `mirror_user_input` 记成用户活跃（`turn.py:1823-1824`）——做成 game_type 就是给这六处各加 if。所以：记忆层复用 QQ（`memory_bridge` 五方法上提为 `memory/scoped_client.py`，OD-31），对话层借 game 的 takeover flag / ws 劫持点 / 隔离 `OmniOfflineClient` / `mirror_assistant_*` / finalize 骨架另起 `visit_router`，把 game 的直连泛化成注册表。v1 §3.9 对照表写「复用注册表」实为「复用模式、新建机制」，行号 `runtime.py:2075` / `postgame.py:1277` 精确；v2 §3.10 补两行分别写明 QQ 路径与 game 路径各借了什么。

#### OD-04 串门记忆区建模：复用 group_chat/group_participant/participant + platform=neko_visit + 按 (subject_kind, platform) 选标题表（两张表同键）+ 群 digest/对端 segments 双形态
- 现状: SUBJECT_KINDS 冻结三种（`memory/scopes.py:31-38`），三个构造器 `group_chat/participant/group_participant`（`:120-150`），组件 ≤256 字符、`:`/`%` 百分号转义（`:47-70`），`group_participant` 必须三段（`:88-96`）；memory_server 契约 `Literal["group_chat","participant","group_participant"]`（`app/memory_server/routes.py:1221`）；`prompts_memory.py:3884-3900 get_scoped_persona_section_header` 今天按 `subject_kind` 取表，`_NAMED` 表优先，仓库零处 `neko_visit`（「按前缀选表」是 v1 待新增规则，不是现状）；tests/unit/test_participant_memory_and_display_name.py:461-480 硬断言两张表 kind 集合相等、8 locale、`_NAMED` 模板含 {display_name} 与 {subject_id}；四个 scoped 写 op 已登记 _CHARACTER_SCOPED_WRITE_OPS（runtime.py:98-103）；同一 subject ≥5 条未吸收事实才生成 reflection（`memory/reflection/_shared.py:41`）；含 `group_participant` 字面量的文件 7 个（含 `config/prompts/prompts_memory.py`）。
- 改成什么: 三个 subject 全部从 Servers 派发的 `visit_uid` 派生（OD-05 v2）：这一对的串门史 `group_chat('neko_visit', pair_id)` → `neko_visit:<pair_id>`；对方猫娘 `group_participant('neko_visit', pair_id, peer_char_id)` → `neko_visit:<pair_id>:c_…`；**对方亲人 `participant('neko_visit', peer_uid)` → `neko_visit:<peer_uid>`（人级主体，跨对累积）**。标题表：`prompts_memory.py` 两张表各加 `'group_chat@neko_visit'` 与 `'participant@neko_visit'` 两键（8 语、`_NAMED` 含两个占位符；`group_participant` 沿用通用成员标题），getter 按 **`(subject_kind, platform)`** 选表而不是裸前缀——`participant` 的 `neko_visit:<uid>` 与 `group_chat` 的 `neko_visit:<pair>` 前缀相同，按前缀会撞。写：(a) 群 digest `/scoped_history` 单 subject；(b) 对端两位 segments 批（`speaker_tier="none"`，display_name 过 `_sanitized_display_name`，`routes.py:1831`）。写侧统一走 `memory/scoped_client.py`（OD-31）。
- 回归风险: 极低：既有 qq 行 (key, scope) 字节不同；两张表各加两新键，既有 locale 守卫要纳入；`memory/` 零改动。
- 收益: 五个端点、lite 生命周期、归档、forget、围栏全部白拿；串门史按 pair 累积，人级画像按人累积，reflection 的 5 条门槛更易到。
- 推荐: 采纳。owner 已拍板（2026-09-30）。
- 备选: 新增 kind='visit'（7 处 schema） | 只用单形态（无对端画像） | 对方亲人按对 `group_participant(pair, 'u_'+uid)`（画像分裂，靠名册聚合）
- 卡住: subjects.py, memory/scoped_client.py, prompts_memory.py 两表两新键与 getter, spool digest 提交

#### OD-05 v2 记忆与黑名单绑定社区成员身份 `visit_uid`：对方亲人人级主体跨对累积；名册与黑名单主键 visit_uid
- 现状: 身份只有 local_user_id（UUID）/ client_id / Steam64；没有跨机器角色 id。`MemorySubject` 组件 ≤256 字符、`:`/`%` 转义（`memory/scopes.py:47-70`），`participant(platform, actor_id)` 构造 `platform:actor`（`:129-133`），`group_participant` 三段（`:135-150`）；信赖池 speaker_id 形状 `platform:actor`，actor `[A-Za-z0-9_.:@-]+`（`memory/speaker_trust.py:173-185`）；QQ 先例人级主体 `participant('qq', uid)`、群内成员 `group_participant('qq', gid, uid)`（`plugin/plugins/qq_auto_reply/memory_bridge.py:60-88`）；display_name 经 `_sanitized_display_name` 盖到 persona 元数据（`app/memory_server/routes.py:1831, :1855`）；`atomic_write_json`（`utils/file_utils.py:785`）；`config_dir`（`storage_roots.py:160`）。v1 用中继派生的成对假名 peer_id，owner 第二轮明确要改绑社区身份。
- 改成什么: (1) `visit_uid` = Servers 派发（OD-01 v2），两端各从已核验票据取 `peer_uid`、从自己领到的票据取 `own_uid`。(2) 派生规则（两端各算、结果相同）：`pair_id = sha256(min(own_uid, peer_uid) + '|' + max(...))[:24]`；`peer_char_id = 'c_' + sha256(peer_uid + '|' + peer_char_tag)[:24]`（`char_tag` 自报，但命名空间被核验过的 `peer_uid` 钉死）。(3) 三个 subject 见 OD-04：对方亲人是 **`participant('neko_visit', peer_uid)`**，同一个人带不同猫娘来、从不同设备登录同一账号，在你这里都是同一个主体——这正是 owner 要「绑社区身份」的收益；标题表按 `(subject_kind, platform)` 选。(4) 本地名册 `config_dir/visit_peers.json` **按本机角色分开**：`{peers: {<visit_uid>: {display_name, short_code, first_seen, last_seen, by_char: {<本机角色名>: {pairs:[pair_id], chars:{peer_char_id:{char_tag, display_name, last_seen}}}}}}}`（同一个人可能跟本机多只猫娘都串过门，各角色的 pair 与记忆 subject 互不相干）——`pair_id` 是双方 id 的哈希，光看 subject 列表反推不出「哪些 pair 涉及某个人」，所以必须有这份索引。(5) 黑名单 `config_dir/visit_blocklist.json` 主键 `visit_uid`：`{blocked:[{visit_uid, display_name_at_block, blocked_at, reason?}]}`；生效点 (a) hello 核验时 `sub` 命中 → 立即离房 `finalize('peer_blocked')`（对端只看到「离开」），(b) 邀请 / 接待 UI 拿到对端 uid 时直接灰掉（接口预留）。(6) 记忆浏览器 `GET /api/visit/memory/peers` 按 `visit_uid` 聚合（每人一行「小明 · 3 只猫娘 · 最近 9-20」）；「清除这个人」**只作用于当前角色**：对当前角色下的 `participant` 单 subject + 该人在 `by_char[当前角色]` 下所有 pair 的 `group_chat` 与 `group_participant` 逐个 `/scoped_forget`（`routes.py:2796`；信赖池未加载 fail closed，UI 提示稍后重试）+ 删 `by_char[当前角色]`，`by_char` 为空时才删整条 peer；黑名单折叠区。(7) 对端 `consent{scope:'all'}` 按 `visit_uid` 清三类 subject（OD-09 v2）。(8) UI 永不显示完整 id，只显 display_name + 6 位短码。
- 回归风险: 无既有路径；QQ 的 `qq:*` 键字节不变。隐私（如实写进 UI 与 README）：同一账号在所有对端机器上是同一个 `visit_uid` → 两个对端可对照确认「是同一个人」（**跨对可关联，现在是设计**，v1「不同对不可关联」目标删除）；对端磁盘留你的稳定 id（不是裸社区 uuid，反查只有 Servers 能做）；Servers 换盐 = 所有对端变新人（运维文档）。安全边界：uid 由 Servers 钉死，`char_tag` 自报只影响自己命名空间；换猫娘 / 换 `char_tag` / 换机器都绕不过黑名单；`display_name` 冒名由 OD-23 casefold+NFC 处理。对端撤销 `scope=all` 只删记忆 subject、名册项与 `state.json` 里的 `peer_uid/pair_id`，黑名单项保留（拉黑不是记忆）。
- 收益: 封禁、拉黑、记忆聚合同一主键；人级 reflection 更易攒够 ≥5 条事实；记忆浏览器能按人展示与清除；派生规则纯函数、两端一致、可单测。
- 推荐: 采纳。owner 已拍板（2026-09-30）。
- 备选: 裸 local_user_id 直接落盘（不必要泄漏） | 每对独立 group_participant 装亲人（可关联但画像不合并） | 成对假名（owner 已否） | peer_human_id 用 sha256(uuid)（哈希只是自欺，浏览器无法反查）
- 卡住: main_logic/visit/subjects.py, limits.py blocklist, visit_peers.json 名册, consent.py, memory_routes.py peers 端点, memory_browser 面板 + 8 locale, Servers 票据字段

#### OD-06 v2 档位：只发 sd600（320×448 上半身 / 256×560 全身 → 打包 286,720 px、30 fps、视频 560 + 数据 ≤40 kbps）；hd1200 / fhd2400 留付费表项；拥塞只缩裁剪不动 fps（最低 300 kbps）；免费额度由 Servers 按账号计分钟
- 现状: 仓库无带宽档位先例；`targetFrameRate` 等性能设置存 localStorage `project_neko_settings` 不进后端。TRTC 大陆按**像素面积**分档且带码率带：标清 ≤640×480=307,200 px 且 300~900 kbps → 14 元/千分钟；高清 ≤921,600 px 且 900~1800 → 28；全高清 ≤2,073,600 且 1800~4000 → 63；音频 7；扣减比 音频:标清:高清:全高清 = 1:2:4:9；「视频传输码率或自定义数据通道码率超出限制后跳档」；未订阅视频（含只推流）计音频时长（https://cloud.tencent.com/document/product/647/44248 ，页面更新 2024-09-20）；一次性 1 万分钟免费包（非每月）。TRTC Web SDK 无 codec / degradationPreference API（research_trtc.md）；libwebrtc 默认 `MAINTAIN_FRAMERATE`，但 QP 质量缩放器会因高 QP 主动降分辨率（`webrtc_video_engine.cc:2011-2046`）。
- 改成什么: `config/visit_settings.py::VISIT_TIERS` 一张表——sd600：裁剪 320×448（上半身）或 256×560（全身）、打包 320×896 / 256×1120、面积 286,720 px、30 fps、视频 560 kbps + 数据 ≤40 kbps = 600、TRTC 标清 14，**v1 唯一发布、免费默认**；hd1200：480×672 → 480×1344、645,120 px、1150 + 50 = 1200、高清 28，表项 + `tier` 字段、`enabled=False`；fhd2400：720×1008 → 720×2016、1,451,520 px、2300 + 100 = 2400、全高清 63，同上。sd600 两种构图像素数相同，切「上半身 / 全身」只换裁剪框与 `profile.width/height`（`updateLocalVideo`），不换档不换编码器；尺寸全为 16 倍数，打包分界 y=448 是 16 的倍数（4:2:0 色度块不跨界）。`tier` 进凭证请求，非 `sd600` Servers 403 `tier_not_entitled`。**拥塞阶梯**（只降分辨率不降帧）：B 每 5 s 发 `stats{rx_fps（rVFC presentedFrames 差分）, rtt_ms, loss_pct, rx_w, rx_h}`，A 看 TRTC `NETWORK_QUALITY.uplinkLoss` / LiveKit `ConnectionQualityChanged`；`rx_fps < 24` 或 `uplinkLoss > 15%` 连续 10 s → 320×448 → 256×352（bitrate 400）→ 192×272（**bitrate 300**，不低于标清带下限 300），面积 180,224 / 104,448 px 都 <307,200，档位不变；全身构图另一条阶梯 256×560 → 208×448（400）→ 160×352（300），打包面积 186,368 / 112,640 px；30 s 干净升一级。注明：libwebrtc 也可能自行降分辨率，接收端以 `videoWidth/Height` 观测，T6/T8 记录 `qualityLimitationReason` 与 `frameWidth/Height`。**免费额度（服务端唯一可强制的账单上限）**：Servers 按账号记「每日签发分钟数」= 签发次数 × 30 min，免费档 `VISIT_FREE_MINUTES_PER_DAY`（占位值 120，**由 owner 定价时拍板**）；每账号并发房 ≤2；重连不消耗；`expires_at` 进票据让对端也能核；付费档只改 entitlement（额度与 tier）。
- 回归风险: 零（新常量）。成本（按 1000 同接 = 500 房、每房 1 guest + 1 host、只有 guest→host 一路视频这一**假设**）：TRTC 大陆 host 收 1 路标清 60×14/1000 = 0.84 元 + guest 只推计音频 60×7/1000 = 0.42 元 → **1.26 元/房·小时**，500 房满载 630 元/h；基础版 625 元/110k 单位 ≈1.02 元/房·小时。免费额度上界（估算）：每账号·分钟 = 1.26/120 ≈ 0.0105 元 → 120 min/天 × 30 天 ≈ 38 元/活跃账号/月。体验：320 px 宽放大到本家模型 90% 高（最高 900 px）是 2~2.8× 放大会糊——30 fps 是 owner 明确取舍。供应商风险：TRTC 无 degradationPreference API，若 T6 实测拥塞时 SDK 自行降帧不可接受，大陆没有第二家同时满足「标清档 + 自定义轨正门 + 不钉 maintain-resolution」。档位约束风险：客户端开源、SDK 参数可改，`tier` 只在客户端生效挡不住改包用户——服务端约束（LiveKit token 按侧位收紧 + `track_published` webhook 超档踢人；TRTC 拉用量统计 / 事件回调比对超档走封禁；host 能否用 TRTC 观众角色待 T13）见 §4.7，检测处置之前单账号最坏按凭证允许的最高档计费，免费额度按此留余量。
- 收益: 一档一价可算；付费阶梯字段与判档规则今天定死，将来只改 Servers entitlement；免费账单有服务端上界。
- 推荐: 采纳；免费额度 120 分钟/天保持占位，定价时由 owner 定。owner 已拍板（2026-09-30）。
- 备选: 288×512（294,912 px，同档更高更窄） | 免费档 24 fps 省 20%（违反硬要求） | 三档全开（成本不可控） | 无每日分钟额度（开源客户端可改硬顶，账单无界）
- 卡住: VISIT_TIERS, pack.js 裁剪表, 设置页「上半身 / 全身」+ 8 locale, Servers entitlement 与分钟额度, 实测 T6/T8

#### OD-07 v2 传输选型：大陆 TRTC 托管；海外 LiveKit（上线期 LiveKit Cloud Ship → 月 >≈2,500 房·小时切 GCP 自建，GCP 是稳态目标）；自建大陆中继目录作废
- 现状: v1 的 `local_server/visit_relay_server` 未实现；`local_server/` 今天只有 cosyvoice / survey / telemetry。候选事实（`research/*.md` + 2026-09-26 复核）：**TRTC** 自定义轨走 `startLocalVideo option.videoTrack` 正门、标清 14 元/千分钟、内建 TURN（直连 → TURN UDP → TURN TCP 443）、`sendCustomMessage` 无需已推媒体、`REMOTE_USER_EXIT{userId, reason:0 主动/1 超时/2 被踢/3 切角色}`、`CONNECTION_STATE_CHANGED{prevState, state, isReconnecting(5.15.0+)}`、`KICKED_OUT{reason:'kick'|'banned'|'room_disband'}`、无 codec / degradationPreference API；npm 最新 **5.20.1（ISC）**，官方 changelog 首条 5.19.2 @2026-08-25，含 Electron 修复记录（条目版本号两次抓取不一致：5.13.1 / 5.17.0 或 5.11.1 / 5.17.1，标不确定）。**ARTC**：自定义轨只能占屏幕共享槽、SDK 钉死 `maintain-resolution`（拥塞先丢帧，与硬要求相反）、竖版 320×896 落哪档按宽高比较无依据、无免费额度。**声网**：无标清档，HD 28 = TRTC 标清 2×。**LiveKit**：server v1.13.6 Apache-2.0，`publishTrack` 支持 `maintain-framerate` / `simulcast:false` / vp9 / `scalabilityMode`，`publishData` reliable ≤15 KiB，JWT HS256 自签；Cloud Ship $50/月含 150k 分钟 / 250 GB / **1,000 并发**，Scale $500/月 5,000 并发；Cloud 无大陆 / HK 区域（https://livekit.com/pricing ）；server `room.max_participants` 可设、`departure_timeout` 默认 20 s（config-sample.yaml）；v1.13.6 已从默认 codec 去掉 H.264 baseline。
- 改成什么: `transport ∈ {trtc, livekit}` 由 Servers 决定（OD-12 v2）。大陆 **TRTC**（唯一有标清档、自定义轨走正门、数据通道不要求已推媒体）。海外 **LiveKit**：**上线期 LiveKit Cloud Ship**（零运维、零压测；1,000 并发正好卡 owner 的 1000 同接）；月房·小时 >≈2,500 持续两个月，或需要数据驻留 → 切 **GCP 自建**（东京 e2-standard-4 起步，Caddy 443 复用 HTTPS + TURN/TLS，官方单机模板；第二区法兰克福 / 俄勒冈按用户分布加；`room.max_participants: 2`）；客户端零改动，只换 Servers 下发的 `{url, token}` 与签发密钥。ARTC / 声网驳回。v1 `local_server/visit_relay_server` 目录、Caddy 8101、PSK 自建全部删除。
- 回归风险: 零仓库回归。运维：腾讯云账号 / SDKAppID / SecretKey 进 Servers；GCP 阶段多域名 + 证书 + 压测（LiveKit 只公布 c2-standard-16 基准：150 pub/150 sub 720p = 85% CPU；默认 400 轨/CPU → e2-standard-4 名义 1,600 轨刚够 500 房，**必须压测**）。成本（估算；流量一律十进制 MB/GB，GiB 只在引用 GCP 报价时出现）：每房·小时出站 600 kbps × 3600 s = 270 MB（= 0.251 GiB）；LiveKit Cloud $0.0005/min × 2 人 × 60 = $0.06 + 0.27 GB × $0.12 = $0.032 → ≈$0.09/房·小时，500 房满载 ≈$46/h；GCP 自建 0.251 GiB × $0.12 = $0.030/房·小时 + VM（e2-standard-4 us-west1 $97.84、东京 $125.51 /月）+ 在用外网 IP $0.005/h，500 房满载出站 ≈$15/h；盈亏点 ≈2,500 房·小时/月（三份核验按换算口径给出 2,517~2,700，量级一致）；TURN 中继的房出站翻倍。供应商锁定：TRTC 无 degradation API（见 OD-06 v2）。
- 收益: 大陆零服务器零备案；海外首发零运维、规模化后边际成本 ≈ 出站流量；两家客户端都是标准 WebRTC，A/B 后端完全不碰媒体；删掉 v1 一整个中继服务器目录与运维。
- 推荐: 采纳。海外「Cloud 起步 → GCP」是对 owner「GCP 中转（你来选型）」的落地节奏：GCP 是稳态目标，Cloud 是小体量阶段更便宜且零运维的过渡。owner 已拍板（2026-09-30）。
- 备选: 直接 GCP 自建（多一台机 + 域名 + 证书 + 压测 + 运维） | 海外也用 TRTC 国际站（无标清档 $3.99/千分钟，账号体系隔离要两套后端） | ARTC 大陆主选（违反 30 fps） | 自建 visit_relay_server（v1；owner「不一定划算」）
- 卡住: Servers 密钥托管与 transport 判定, deploy/livekit/（compose + Caddy + README）, 实测 T6/T7/T8, 压测 livekit-cli load-test

#### OD-08 v2 轮次仲裁：一句话规则（连续 6 句无人插话 / 本侧满 40 句 → 收尾；每分钟 6 句只顺延）+ 自然收尾回家（host 发起、guest 先告别、告别行即状态、15/45 s 超时、不可打断）+ Lamport 全序 + reply_to 陈旧 + 只有人类打断
- 现状: 仓库轮次仲裁全在单机内（`turn.py` 的 takeover 分支只静音主会话，`:64/150/181/424/780/807/1390`）；取消只是翻标志（`omni_offline_client/_lifecycle.py:836-838 cancel_response`），流循环 `_streaming.py:1228-1229` 才 break、`:1825` 才 `append(AIMessage)`——task 级 cancel 在 :1825 前抛 CancelledError 则半句不入史；人类文本入口 `websocket_router.py:1041-1048`。v1 稿「对端 human 每场 ≤5 次清零」owner 看不懂，「quiet」被要求改成收尾回家；v1 排序依赖中继盖 `order`，vendor 数据通道没有中继。
- 改成什么: (1) **一句话规则**：两只猫连续 6 句没有任何人类插话，或本侧猫娘这场已经说了 40 句，就进入「收尾」：客人说一句告别、东家猫娘送客一句、客人回家；本侧猫娘每分钟最多 6 句，超了只是多等一会儿。「人类」= 本地或对端 `sp:"h"`，都清零 6 句计数；但对端永远改不了本侧的 40 句 / 每分钟 6 句上限——这是对「对端全标 human」的全部防御；告别行（`wu:true`）不计数；v1 `VISIT_MAX_LINES=80` 降为 `peer_protocol_violation` 守卫。(2) **收尾状态机** `ACTIVE → WRAP_UP → ENDING`：host 发起 `wrap_up{ph:'begin', reason, lp}`，guest 侦测到条件只发 `propose`，5 s 无回应直接开始告别；**告别行本身就是收尾信号**（任一侧收到 `wu:true` 的 `line_delta` 即进 WRAP_UP，`begin` 丢了也不卡死）；顺序 guest 告别 → host 送客 → host `wrap_up{ph:'done'}` → guest `leave{reason:'home'}`、host `finalize('peer_left')`。超时：`VISIT_WRAP_UP_STEP_S=15`（从 `begin` 到对方告别行**第一片**到达，表示对方已开口；之后告别行按正常播放走完，≤400 tok 天然有界），`VISIT_WRAP_UP_MAX_S=45` 硬顶无条件 finalize；告别提示词要求 ≤40 字、最多两个分句。`recall`（A 家按「叫她回来」）= guest 以 `reason:'recall'` propose，host 不判条件即 `begin`；重复按 → `status{VISIT_RECALL_ALREADY}`。(3) **不可打断三件事**：WRAP_UP 内 host 侧人类文字 → `status{VISIT_INPUT_REFUSED_WRAPUP}` toast「她们正在道别，等一下」、文本留在 composer 不清空、返回 True（guest 侧本来就拒，OD-22）；未开口推理 `_reply_task.cancel()`，已开口说完当前行、从 `begin` 起 `VISIT_SPEAKING_ABORT_AFTER_S=10` 未说完 → `line_abort{reason:'wrap_up'}` + `interrupt_mirror_speech()`（只对 `begin` 时正在说的旧行；告别行 `wu:true` 本身不受此限，按正常播放走完）；`may_start_cat_line` 只放行一句 goodbye；UI `visit_state_change{action:'wrap_up', reason, initiated_by}` 徽标「道别中」+ composer 禁用，`ended` 恢复。(4) **ACTIVE 期间打断**：人类句（本地或对端）让说话中的本侧猫娘说完**当前分句**即 `line_abort{reason:'human_interrupt', i_done}` + `interrupt_mirror_speech()`，未开口推理直接 cancel；猫娘不打断猫娘；撞车（我在说话时收到对端猫娘第一片）guest 让一次（`yield_once`）。(5) **序与陈旧**：Lamport `lp = max(own, max_seen)+1` 在一行第一片发出时分配并贯穿该行，全序键 `(lp, side_rank)`（host=0、guest=1）用于显示、历史与 spool；`reply_to`（`rt`）定陈旧：存在对我说的、`lp` 更新的完整行 → 改回最新那句（重排不沉默）；被回行 `line_abort` → 丢弃 plan；开场双方 `rt==""` 并存豁免。(6) 常量：删 `VISIT_PEER_HUMAN_RESETS_MAX / VISIT_MIN_CAT_REPLY_GAP_S / VISIT_READ_DELAY_*`；新增 `VISIT_MAX_CAT_TURNS_WITHOUT_HUMAN=6`、`VISIT_OWN_LINES_PER_VISIT=40`、`VISIT_OWN_LINES_PER_MINUTE=6`、`VISIT_REPLY_GAP_S=(1.0, 2.5)`（读句时间已被流式播放吃掉）、`VISIT_WRAP_UP_STEP_S=15`、`VISIT_WRAP_UP_MAX_S=45`、`VISIT_WRAP_UP_PROPOSE_TIMEOUT_S=5`、`VISIT_SPEAKING_ABORT_AFTER_S=10`。告别是收尾期间一次独立 `stream_text`（`VISIT_SYSTEM_NOTICE_WRAP_UP_*`，≤8 s 超时用 `VISIT_GOODBYE_FALLBACK_*` 固定句），回家自述交 OD-16 v2。`VisitRoom` 纯状态机（不 await、不 I/O、不持锁，只吃事件吐 `RoomEffects`，执行顺序 violation → finalize → abort → cancel → wrap_up 出网 → ui → say_goodbye → reply）。
- 回归风险: 对既有代码零风险（全在新目录 + 新消息）。产品面：无人时约 1 min 自嗨即回家（每句 ≈8~16 s，估算）；对端造假 human 最坏让本侧多说到 40 句（≈7 min，估算）；收尾最长 45 s；每场多一次 LLM 调用（告别与自述分开）；LLM ≈240k input token/侧/场（40 句 × ≈6k，估算，见 OD-15 v2 成本）。15 s 步超时以「对方已开口」为止，避免 host 在 guest 还在说告别时就送客把她掐断。
- 收益: 规则一句话讲清；「安静」变成有仪式感的回家；两种传输一套排序代码、零额外消息、无等待；`begin` 丢包不卡死；纯函数可全覆盖单测；对端能触发收尾（等价于离开）但不能阻止（45 s 硬顶）也不能让本侧超预算。
- 推荐: 采纳；数字 6/40/6/1.0~2.5/15/45/5/10。owner 已定方向（2026-09-26），细节已拍板（2026-09-30）。
- 备选: N=10 或 L=60（更长自嗨，更贵） | host 权威序（多一个来回、host 掉线无序、不对称） | 纯 reply_to 链（只有偏序，落盘要另定规则） | 收尾期间人类文字排队到回家后重发（收件人已变，否）
- 卡住: main_logic/visit/room.py 全部, VisitRuntime effects 执行器, route_stream_message phase 分支, wrap_up 消息, prompts_visit 五个新键, visit_state_change{wrap_up} + 8 locale, tests/unit/test_visit_room.py（含「LLM 7.9 s + 三分句告别」用例）

#### OD-09 v2 开关与撤销（人话版）：三开关进白名单与插件禁改集；本机中途 OFF = 这场不记 + 删 spool；对端撤销 scope=all 连群 subject 一起清；NEKO_VISIT_ENABLED 总闸
- 现状: 会话设置白名单 `ALLOWED_CONVERSATION_SETTINGS`（`utils/conversation_settings_constants.py:17-41`）没有任何 visit 键；「插件禁改」集合 `_USER_OWNED_FIELDS` 今天只有 `proactiveVisionEnabled`（`main_routers/proactive_router.py:58-60`），且 `plugin/plugins/proactive_controller/__init__.py:43-45` 有一份镜像拷贝，两处必须同步改；QQ 群聊的接收边界章：消息一进队列就盖 `_group_memory_at_receipt` 等快照，处理侧不再晚读设置（`plugin/plugins/qq_auto_reply/message_dispatcher.py:432-449`）。v1 稿 scope=all 只清两个 participant，群 digest 里含对端事实——撤销只是部分撤销。
- 改成什么: 一句一个意思。三个开关都在设置页「串门」分组，都进 `ALLOWED_CONVERSATION_SETTINGS`。(1) `visitEnabled`（默认关）管「她能不能出门、别人能不能邀请她」。关着：拒绝一切邀请，也不能出门。中途关掉：正在串门就立刻结束，用固定句告别，不走自然收尾。进 `_USER_OWNED_FIELDS`（两处）。(2) `visitMemoryEnabled`（默认关）管「这场串门记不记」。开着：每句进 spool，结束后问你怎么记（OD-16 v2）。关着时她不记、不出芯片：不建 spool，结束后她只口头说一句，不问「记不记」；为了上传转录（OD-26 v3），待传内容会临时存到上传成功为止（`<visit_id>.upload.json`）。中途关掉：这场按「不记」处理，spool 文件立刻删除，结束时不出芯片。它同时决定我们向对端宣告的 `consent{memory:true|false}`。进 `_USER_OWNED_FIELDS`（两处）。(3) `visitVoiceEnabled`（默认开，OD-15 v2）管「串门时她用不用本地 TTS 出声」。关掉时口型改用文本估时驱动。它不是同意开关，中途切换从下一句生效，不影响记忆。(4) 每一句话存进 spool 时，同时记下当时「我允许记」和「对方允许记」两个是/否；最后只把两个都是「是」的句子拿去做记忆。(5) 对端发 `consent{memory:false, scope:'session'}`：这场里对端猫娘和对端亲人说过的话全部标为不可记；我方自己的话照常。(6) 对端发 `consent{memory:false, scope:'all'}`：除第 5 条外，还对这一对的三类 subject 各调一次 `/scoped_forget`——**只作用于这场串门所属的本机角色**：只清该角色下的 subjects 与名册 `by_char[该角色]`，`by_char` 为空才删整条 peer（该人与本机其它角色的串门记忆不动），`state.json` 里的 `peer_uid/pair_id` 一并删。代价：我方自己对这一对的串门史也一起清空，因为群 digest 里混着对方事实、切不开。UI 明说这个代价。(7) 对端撤销不影响我方黑名单；我方拉黑也不影响对端记忆。(8) 我方撤销的对偶：设置页「让对方忘掉我」按钮 = 向对端发 `consent{memory:false, scope:'all'}`；只在同一场在飞时可达，离线后无通道，UI 说明「对方机器上的副本无法远程清除」。(9) 发布总闸 `NEKO_VISIT_ENABLED` 环境变量（默认关）：关着时 `/api/visit/*` 全部 404，设置页不显示分组。
- 回归风险: 白名单只增三键；`_USER_OWNED_FIELDS` 两处（router + 插件镜像）加两键，`proactive_controller` 插件的写路径会多拒两键（预期，回归报告一句）。`scope=all` 会清掉本家自己写的群 digest（撤销语义的代价）。
- 收益: 每个开关一句话能讲清；接收边界章让「OFF 时代收到的话」不会被后处理误记；对端撤销真的能把对端事实清干净。
- 推荐: 采纳；「记住串门内容」默认关。owner 已拍板（2026-09-30）。
- 备选: 把 visitMemoryEnabled 拆成「记对方猫娘 / 记对方亲人」两键（更细但更难解释，留 v1.5） | 群 digest 只放本家行（失去对话语境） | 只清 participant（部分撤销）
- 卡住: conversation_settings_constants.py:17, proactive_router.py:58, proactive_controller/__init__.py:43, main_logic/visit/consent.py, 设置页 + 8 locale

#### OD-10 隔离会话历史与召回：持久历史 + task 级打断 + 复读守卫防御 + 不带私聊召回 + 原始角色卡（亲人名中性化）+ bootstrap ≤2000 tok
- 现状: OmniOfflineClient.connect() 只重置历史；stream_text 每轮追加 HumanMessage/AIMessage（:910/:1825），无串行锁（_client.py:146-147 只有两把无关锁）；_check_repetition（:1830）触发时 :317 把历史换成只剩 SystemMessage；character_runtime.py:1904-1907 构造 manager 时已把 {MASTER_NAME} 替换成亲人真名，_client.py:296-297 存 master_name；原始模板在 characters.py:219-225 lanlan_prompt_map。
- 改成什么: 持久历史；**历史按 Lamport 全序排**：每次开始新一轮 LLM 之前按 `(lp, side_rank)` 重排隔离会话历史（或插入时按 `VisitRoom.sort_key` 定位），同 `lp` 时 host 在前，保证两侧历史顺序一致；trim/pop 对 len≤1 早退、pop 校验尾部内容；所有 stream_text/append/trim 在 _llm_turn_lock 内；打断 = _reply_task.cancel + gather；build_visit_instructions 用原始 lanlan_prompt_map[name]，{MASTER_NAME}→FAMILY_NEUTRAL_TERM，OmniOfflineClient(master_name=FAMILY_NEUTRAL_TERM)；单测断言 instructions 与出站不含 master_name；永不读 /new_dialog。
- 回归风险: 隔离实例零影响；直接操作私有 _conversation_history（context_append 先例）；中段裁剪影响需单测。产品面：猫娘串门时不记得家里最近的事（owner 接受，附下方 issue）。
- 收益: 每句 prompt 4~7k；亲人真名不再出现在任何一条会被送到别人家的对话上下文里（原稿这条闸门实际漏了 system prompt）。
- 推荐: 采纳；产品上线前依赖下方 issue（#TBD）。owner 已接受「串门时不记得家里最近的事」并要求留 issue（2026-09-26）。owner 已拍板（2026-09-30）。
- 备选: 每轮 connect() 重建 | 带完整 /new_dialog 召回 | 沿用已替换的 lanlan_prompt（真名进 system prompt） | 在串门 PR 里顺手做只给串门用的关键词过滤（成为第一个「各自判断」的分叉，阻碍统一）
- 卡住: session_pool.py, prompts_visit.build_visit_instructions, memory_bridge.fetch_visit_context, issue 草稿

issue 草稿（memory: 敏感记忆分级与出境过滤共享基础设施 + 「完全隔离亲人记忆」总开关；不在本次交付）。背景：私聊记忆今天已有一条出境路径——社区铸卡 `POST /api/card-drop/facts/query`（`main_routers/card_drop_router.py:2226`）→ `_forge_facts_response`（`:2138-2155`）→ `main_logic/card_forge_facts.build_forge_facts_payload`（`:676`）直接读 `facts.json`（`:43, :138`）按重要度加权抽样（`:205 _weighted_pick`），`card_drop_router.py`、`memory/facts.py`、`config/memory_settings.py` 里 grep `sensitiv|敏感` 只命中无关注释，零敏感度过滤；串门靠结构隔离不读私聊记忆；未来记忆卡片 / 分享 / 导出会继续增加出境面。目标：一处判定「这条记忆能不能离开这台机器」，所有出境功能只问这一处。范围：串门 bootstrap 召回（若未来允许带私聊召回）、卡牌铸造 `facts/query`、记忆卡片、记忆导出 / 分享；不在范围：猫娘在家里对亲人本人说什么。接口草案（`memory/sensitivity.py`，L2）：`Sensitivity(StrEnum) = PUBLIC / PERSONAL / SENSITIVE / SECRET`（`requires-python == 3.11.*`，StrEnum 可用）；`classify_text(text, *, lang) -> SensitivityVerdict{level, tags, reason}`——**纯规则部分是纯函数、可离线单测；小模型只作可选异步增强**，`classifier_version` 区分；`stamp_fact(fact)` 写入期在 `FactStore.apersist_*`（`memory/facts.py:3423` 一带钩子存在）落库前盖 `sensitivity{level, tags, classifier_version}`；`outbound_allowed(fact, *, consumer, policy) -> bool` 读取期消费者只问这一句；`config/memory_settings.py::OUTBOUND_MEMORY_POLICY = {"visit": {"max_level": "PUBLIC"}, "card_forge": {...}}`；deny 默认（未分类 = SENSITIVE），allow 列表只放猫娘自己人设 / 口癖 / 喜好类事实（`source == "ai_disclosure"` 且不含亲人指代）。**总开关 `privateMemoryOutboundIsolation`（进 `ALLOWED_CONVERSATION_SETTINGS` + `_USER_OWNED_FIELDS`）默认 False（opt-in）**——owner 原话是「最坏情况下有个开关」= 可选逃生阀；为真时任何出境功能拿不到 legacy_private 一条事实，只能拿 scoped 区（群 / 串门）自己产生的内容。**筛除接口必须不改变现网铸卡结果**：铸卡默认继续读 `facts.json`，`filter(outbound_allowed)` 是可选前置过滤，policy 未配置时等价于全放行，回归断言「policy 未配置时铸卡响应字节等价」。老数据回填走 memory_server 后台低优先任务 + `classifier_version` 增量，**不进启动链路**。审计：本地 JSONL（event_logger 同规）记「哪个消费者、拒了多少条、什么 tag」，不记原文。测试：每类 tag ≥20 正例 / 20 反例（8 语）漏判即红；总开关为真时全拒断言；`card_forge_facts` 集成用例「含 address tag 的 fact 不出现在响应」。不在本 issue：LLM 二次审核（成本）、对端机器副本清除（无通道）。依赖：串门产品上线前完成（owner 要求）；卡牌铸造建议同期接入。

#### OD-11 v2 连接生命周期：30 s 判死；显式离开立即结束；自身重连 25 s；页面重载宽限 20 s；后端重启即结束；关机钩子 3 s
- 现状: vendor 事实：TRTC Web v5 `CONNECTION_STATE_CHANGED` 三态 `DISCONNECTED/CONNECTING/CONNECTED` + `isReconnecting`，SDK 自动重连，**文档没写重连最长多久**；`REMOTE_USER_EXIT.reason=1` 是心跳超时，**超时秒数未文档化**；`KICKED_OUT{banned}` 由 Server API 触发（https://web.sdk.qcloud.com/trtc/webrtc/v5/doc/en/module-EVENT.html ）。LiveKit `DefaultReconnectPolicy`：10 次，延迟数组 `[0,300,1200,2700,4800,7000×5]` ms 之和 = **44.0 s**，第 3 次起每次 +0~1 s 抖动 → 约 44~52 s（https://raw.githubusercontent.com/livekit/client-sdk-js/main/src/room/DefaultReconnectPolicy.ts ）；事件 `Reconnecting / Reconnected / Disconnected / ParticipantDisconnected`；服务端 `room.departure_timeout` 默认 20 s、`empty_timeout` 300 s（config-sample.yaml）。结论：两家都没有可依赖的、文档化的「对端掉线多久算走了」，30 s 必须自己计时。本地事实：display socket 断开时已有「按路由 finalize」先例 `finalize_icebreaker_route(reason="websocket_disconnect")`（`main_routers/websocket_router.py:1448`）；最新 socket 顶替 `session_id`（`:547-556`）。关机链：Electron `requestAppQuit` **先 `destroyAllWindows()` 再 `beginOwnedBackendShutdown`**（`lanlan_frd/src/main/backend-runtime.js:2483-2490`）→ `POST /api/runtime/shutdown`（`:1666-1684`，timeout 3 s）→ `web_app.py:581-631` → `request_application_shutdown_async` 先 `sleep(0.5)`（`app/main_server/__init__.py:1475-1481`）→ `on_shutdown`（`:1233-1275`，顺序 `close_voice_identity_runtime → cleanup() → join_sync_connector_threads(3 s) → 预加载 → game cleanup → _stop_neko_servers_integration_workers`）；Electron 宽限 60 s（`backend-runtime.js:1893`）。所以后端钩子跑的时候 Pet 页、iframe 和 vendor 数据通道都已不在了。v1 的退避 1→30 s / 90 s / member_token / boot_id 全依赖自建中继。
- 改成什么: 一句一个意思。(1) 每侧每 5 s 经数据通道发一次心跳 `hb{lp_seen}`（cmd 1）。(2) 收到对端任何消息都刷新 `peer_last_seen`。(3) `peer_last_seen` 超过 **30 s** → `finalize('peer_lost')`；两侧各自判，结果一致；host 侧这个计时器只在对端 `hello` 核验通过后才启动，此前（host 建房后的 `invite_ready` 阶段，对端可能还没入房）只有 `VISIT_INVITE_WAIT_S=600`（与邀请码 10 min 一致）超时 → `finalize('invite_expired')`；guest 入房时 host 早已在房，guest 自入房起在对端 hello 核验前只等 30 s，超时 `finalize('peer_lost')`。(4) 我自己断了：SDK 自己重连；同时起重连截止计时，截止 = `min(断线时刻 + 25 s, 最后一次成功发出心跳 / 必达消息的时刻 + 30 s − VISIT_RECONNECT_MARGIN_S(3))`——**25 s 仍是上限**（owner 拍板的数字不变），但若断线前最后一次成功发出的消息已经较早，就提前截止，保证重连后第一条消息赶在对端 30 s 判死之前到。(5) 25 s 内回到 `CONNECTED / Reconnected` → 继续，outbox 重发未 ack 项。(6) 超过上面的截止（最长 25 s）→ 我主动 `exitRoom()` / `room.disconnect()`，`finalize('relay_lost')`；不等 LiveKit 的 44 s、不猜 TRTC 的上限（不改 `reconnectPolicy`）。(7) 对方明确离开（TRTC `REMOTE_USER_EXIT reason 0` / LiveKit 主动 `Disconnected`）→ 立即 `finalize('peer_left')`，不用等 30 s。(8) vendor 的超时类事件（TRTC reason 1、`ParticipantDisconnected` 无 bye）不单独处理，交给第 3 条的心跳时钟，避免两套判据打架。(9) 被踢 / 凭证过期（`KICKED_OUT{banned|room_disband}` / `Disconnected(reason)`）→ 立即 finalize，不重连。(10) 正常结束先发 `leave{reason}`（尽力，一次重传后不等 ack），对端收到立刻结束。(11) Pet 页刷新 / iframe 消失 = transport WS（OD-29）断：后端保留状态 **20 s**。(12) 新页面 `GET /api/visit/state` 得知在飞 → 重建 iframe → 同一份凭证重入房（同 vid 再入房 = 重连）→ 重发 `hello`（同 jti）→ outbox 重发。(13) 超过 20 s → `finalize('local_page_lost')`。(14) 后端重启 / 崩溃：`VisitRuntime` 不落盘，这场结束；对端 30 s 后 `peer_lost`；下次启动只做 spool 补录（OD-17 v2），不重入房。(15) 关机：窗口已先被销毁，`leave` 发不出去；`on_shutdown` 最前 `await stop_all('shutdown')` ≤3 s：spool fsync + `state.json{finalized:'shutdown'}` + **同步写出 `<visit_id>.upload.json`**（从内存转录 + 用量构造，与 `visitMemoryEnabled` 无关，下次启动补录按它重传，OD-26 v3）+ 释放 takeover；**对端 30 s 后才知道——文档与产品文案明写**；PC 侧「销毁窗口前先给 Pet 页一个 ≤500 ms 的 leave 窗口」列为可选 follow-up（违反本轮 lanlan_frd 零改动前提，不写成已有能力）。(16) `visit_sweep_loop` 每 2 s 兜底：manager 被替换 → finalize。(17) 凭证 TTL（guest 40 min；host 50 min，另含最长 10 min 等邀请）≥ 硬顶 30 min + 25 s，不存在「凭证先过期」分支。(18) 常量：`VISIT_HEARTBEAT_S=5`、`VISIT_PEER_LOST_S=30`、`VISIT_SELF_RECONNECT_S=25`、`VISIT_LOCAL_PAGE_GRACE_S=20`、`VISIT_SHUTDOWN_BUDGET_S=3`、`VISIT_INVITE_WAIT_S=600`；删除 `VISIT_RELAY_GRACE_S=90`、`VISIT_LOCAL_SOCKET_GRACE_S=10`、`boot_id`、`member_token`。
- 回归风险: `app/main_server/__init__.py:1234` 多一个 `await`（try 包裹，无在飞场次时零成本）→ 回归报告一段；关机最多多 3 s，远小于 Electron 60 s 宽限。弱网用户断线 >25 s 直接结束（v1 是 90 s；owner 接受 30 s）。心跳走数据通道 0.2 条/s，配额可忽略。
- 收益: 一个数字（30 s）讲清，两侧判断一致；不依赖任何 vendor 未文档化的超时；页面重载不掉场；Servers 宕机不影响在飞会话；无 member_token / boot_id / replay_buffer 这些中继概念。
- 推荐: 采纳。owner 已定方向（2026-09-26），细节已拍板（2026-09-30）。
- 备选: 依赖 vendor 远端离开事件（TRTC 心跳超时秒数未文档化，LiveKit 44 s 比 30 s 长） | 60 s / 90 s（owner 否） | 自身重连也用 30 s（与对端判死掷硬币） | 后端重启后重入房（凭证还在，但隔离 LLM 会话与仲裁状态都没了，对端会看到一只「失忆」的猫娘回来）
- 卡住: main_logic/visit/liveness.py（纯函数计时器可单测）, transport.js 事件映射, transport_ws.py 断开 → 20 s 宽限, app/main_server/__init__.py:1234 钩子, tests/unit/test_visit_lifecycle.py（24 s 复联存活 / 31 s 判死 / 显式 leave 立即结束 / 页面 19 s 内重入房四用例）

#### OD-12 v2 区域与 transport 判定：Servers 在 host 领凭证时按 host 区域定 transport；guest 拿同一 transport，region_hint 只判 cross_region；跨区默认 fail-closed 403；页面不接受任何外来 URL
- 现状: `ConfigManager._region_cache` 只由 IP 探测写（`utils/config_manager/core_config.py:40-59` 不变量 1~5），`aensure_region_resolved(timeout=1.5)`（`:529`），`_check_non_mainland()` 会起探测（`:668`），只读先例 `voice_storage.py:979 _region_verdict_is_provisional`；Steam 从不写区域。跨区事实：大陆 TRTC 文档 41103 只有「国际链路端到端平均时延 <300 ms」「全球互通」营销句，无海外接入点表（https://cloud.tencent.com/document/product/647/41103 ）；国际站 trtc.io 是隔离账号体系（不能共享 SDKAppID）；LiveKit Cloud 无大陆 / HK 区域；GCP 无大陆区域、对华出站 $0.23/GiB，UDP 出境稳定性无任何数据。v1 的候选白名单 + `/health` 测速 + `relay_url` 校验依附自建中继。
- 改成什么: (1) `transport ∈ {trtc, livekit}` 由 Servers 在 **host** 领凭证时按 host 区域决定：客户端只带 `region_hint:'cn'|'global'|'unknown'`（`_region_cache` None → 等 `aensure_region_resolved` ≤1.5 s → 仍 None 给 `'unknown'`），Servers 以来源 IP 复核，不一致以 Servers 为准；串门路径**绝不**调 `_check_non_mainland()`。(2) guest 领凭证拿同一 transport；guest 的 `region_hint` 只用于判 `cross_region`。(3) **跨区默认 fail-closed**：两侧区域不同 → Servers 回 `403 cross_region_unsupported`，确认框直接说明「对方在另一区域，暂不支持」；Servers 侧开关可改为「允许 + 警告」，待 T9（海外 guest 连大陆 TRTC、大陆 guest 连东京 LiveKit 各 ≥20 场，记 RTT / 丢包 / `qualityLimitationReason`）实测后由 owner 决定。(4) 一房一 transport。(5) 页面不接受任何来自对端或邀请的 URL；LiveKit `url` 只来自 Servers 且主机名须命中 `config/visit_settings.py::VISIT_LIVEKIT_HOSTS`（含 Cloud 与自建域）；TRTC 无 URL。(6) 删除 v1 `VISIT_RELAY_ENDPOINTS`、`pick_relay_url`、`/health` 测速、`relay_url` 白名单、SOCKS 代理回落。
- 回归风险: 零（不动 core_config，不起探测）。产品面：首发砍掉跨区社交（大陆 ↔ 海外不能互访），体验可预测；SOCKS 用户的 UDP 媒体不经代理（vendor 内建 TURN TCP 443 兜底）。
- 收益: 自配 API 用户也能选对区域（Servers 复核）；假中继 / 假 URL 攻击面关闭；没有连通证据时不把用户放进「画面不稳」的场。
- 推荐: 采纳；跨区首发 403；T9 实测后由 owner 决定是否放开。owner 已拍板（2026-09-30）。
- 备选: 跨区「允许 + 警告」（大陆→GCP / LiveKit Cloud 连通无证据、TRTC 海外节点无表；T9 后再翻） | 复用 join_ip_probe | 客户端自选 transport（Servers 无法对账单负责）
- 卡住: Servers transport 判定与 cross_region 开关, visit_router/credentials.py region_hint, VISIT_LIVEKIT_HOSTS, 前端确认框文案 + 8 locale, 实测 T9

#### OD-13 rename/delete/切换与串门在飞：rename 400 拒绝、delete 无新钩子、切换走注册表 finalize 且只等状态翻转
- 现状: crud.py:743-757 只在语音时拒 rename；:1121-1124 `await finalize_game_routes_for_character(old)` 同步等待（game 的 finalize 毫秒级）；:1573 已拒删当前猫娘；原稿 finalize 若整段在锁内，切换 HTTP 会挂到 flush 完成（8+20+60 s）。
- 改成什么: crud.py:757 后 `if is_external_route_active(old_name): 400 EXTERNAL_ROUTE_ACTIVE`；:1121 改 finalize_external_routes_for_character（各 kind 只等状态翻转，不等 _exit_task）；delete 不加钩子；manager 被替换由 sweep 兜底。
- 回归风险: game 在飞时 rename 从不拒变 400（回归报告）；切换路径逐字节等价。
- 收益: 串门不变僵尸；切换请求不被 flush 挂住；消除 release 后 flush 必 503。
- 推荐: 采纳。owner 已同意（2026-09-26）。
- 备选: rename 静默 finalize | delete 在 character_runtime.py:2092 finalize | 切换等 flush 完成
- 卡住: crud.py 两个 hunk, 回归报告

#### OD-14 v2 B 侧承载：iframe 即访客图层（隐藏 video + 透明 WebGL 解包画布，rVFC 驱动；pointer-events:none + transparent-overlay）；A 侧 .visiting-away + 徽标沿 v1
- 现状: `#live2d-container` `position:fixed; z-index:10; pointer-events:none; background:transparent`（`static/css/index.css:351-363`），VRM / MMD / PNGTuber 容器同为 z 10（`:416/:441/:460`）；`getAvatarScreenPosition` 对 `.minimized` 或 `visibility:hidden` 返回 null（`static/app/app-screen.js:3495-3503`，水印坐标链会断）；`templates/index.html:312-325` 四个模型容器无 `<video>`。preload 命中测试用 `elementFromPoint`（`lanlan_frd/src/preload/bridges/pet-input-region-bridge.js:2722 / :3108 / :5205-5206`），`isModelBackgroundElement` 把 `.transparent-overlay` 当背景（`:2204-2222`，具体 `:2211`）。`requestVideoFrameCallback` Chrome 83+；`texImage2D(video)` 每次上传触发管线 flush → 一帧一次。Pet 页已有 PIXI 一个 WebGL 上下文（Chromium 每页上限 16）。Chrome 146 未实现 Transferable MediaStreamTrack。
- 改成什么: host 侧 iframe `position:fixed; z-index:9; border:0; background:transparent; pointer-events:none; class="transparent-overlay"`，尺寸 / 位置由父页每 300 ms 按 `getModelScreenBounds()` 摆到本家猫娘左右空位较大的一侧（高 `clamp(L.height×0.9, 200, 900)`，宽 = 高 × cropW/cropH（上半身 320/448、全身 256/560，随对端当前构图，经 `visit_state_change.peer_crop` 告知父页）；不是整窗，减少合成层面积）；guest 侧 iframe 1×1 置于视口外只当传输。子文档 `html,body{background:transparent;margin:0;overflow:hidden}`，只含隐藏 `<video muted playsinline>`（不能 `display:none`，rVFC 依赖帧送到合成器 → `position:absolute; width:2px; height:2px; opacity:0.01`）与透明 WebGL 画布（`premultipliedAlpha:true, alpha:true`）。**两道保险**：`pointer-events:none` 让 `elementFromPoint` 跳过 iframe；样式被覆盖时 `transparent-overlay` 类使 `isModelBackgroundElement` 仍当背景，整窗不会变可点击。首个 rVFC 后 `toBlob` 96×96 经 postMessage 给父页作访客 tool 气泡头像（OD-19 不变）。rVFC 1 s 内不触发 → 回落 `setTimeout` 30 Hz 采样（iframe 内无 `nekoFramePacing`）。对端 `state{hidden:true}`（由 A 侧「1 s 无 postrender」推导，OD-02 v2）→ 最后一帧 `opacity:.6` + 徽标「离开了一下」，`hidden:false` 去徽标，不计入 idle 超时。A 侧 `.visiting-away`（新类，缩小 + 半透明，不触发 `:3509` 的 minimized 判断）+ 徽标沿 v1，finalize 释放 takeover 前去掉。
- 回归风险: 元素懒创建、结束即移除；串门外零影响。需实测 T3（子 frame 在 Electron 透明窗内无底色）、T4（透明 WebGL 叠透明 iframe 叠透明窗在 DWM / macOS 的合成）。B 结束瞬间在飞截图可能含访客层（v1 §3.10.5 遗留；截图前父页对 iframe 置 `visibility:hidden` 一帧，交互轴留）。一个新的 WebGL 上下文。
- 收益: 传输、解码、渲染同一文档，远端轨道不用跨 realm；父页零渲染改动即得口型与表情（都在视频里）；不破坏截图水印链与穿透判定；零闭源改动、零 IPC。
- 推荐: 采纳。owner 已拍板（2026-09-30）。
- 备选: 父页 #visitor-container + 跨 realm 搬轨道（Chrome 146 不支持） | 2D 画布逐像素解包（每帧 CPU 读回 286k px） | Electron 卫星窗（改闭源） | 复用 .minimized（断水印坐标）
- 卡住: static/visit/parent-bridge.js（摆位）, templates/visit_transport.html CSS, unpack.js, index.css .visiting-away, 实测 T3/T4

#### OD-15 v3 口型与语音：单一设置 visitVoiceEnabled（默认 true）；一行台词一个 speech_id，LLM 边生成边推进本地 TTS（流式双工，与主聊天同一条推流路径）；字幕按分句估时对齐已播音频；打断立即停；删「本地静音但保留 RMS」开关
- 现状: 口型只有 RMS 驱动 → `LanLan1.setMouth`（`static/app/app-audio-playback.js:1549-1586`，排帧走 `scheduleLipSyncFrame :1536-1547` → `nekoFramePacing.requestPacedFrame`），在首块音频调度时启动（`:1675-1715`），`stopLipSync :1588-1598`；扬声器增益 `speakerGainNode` 在 analyser 之后（`:1478-1486`），per-source `playbackGainNode` 在 analyser 之前（`:1646-1666`）。`mirror_assistant_speech(line, *, metadata, request_id, mirror_text, emit_turn_end_after, interrupt_audio, playback_gain, reuse_synthesized_audio, wait_for_audio_completion, audio_completion_timeout, speech_correlation_id)`（`main_logic/core/turn.py:2096-2109`）每次调用换一个 speech_id（`:2157-2160`）；`interrupt_audio` 前奏 `_clear_tts_pipeline + release_speech_playback_gain + send_user_activity`（`:2130-2155`）；`mirror_text` 才发 `send_lanlan_response(is_first_chunk=True)`（`:2166-2174`）；`wait_for_audio_completion` 走单槽 `_begin_game_speech_completion_wait`（`:2239`；`tts_runtime.py:137-144` 新槽取消旧槽，`:218-241` 上限 55 s）；入队 `_enqueue_tts_text_chunk + _request_tts_done_locked`（`:2254-2257`）；`emit_turn_end_after` 才发 `turn end`（`:2261-2262`；缓存命中路径 `:2186-2187`）；返回 dict 含 `speech_id / audio_queued / audio_completed`（`:2311-2328`）。**completion 语义 = 音频字节送达前端**不是播完（`tts_runtime.py:2028-2046 __audio_done__ → _resolve_game_speech_completion_wait`）；分句边界只有 http_sentence 类 worker 才回报「送达」marker（`:2005-2026`，`_infra.py:579/593/600`），ws_bistream 类没有；`voice_play_start` 是 turn 级且 `resolveAssistantAudioTurnId` 会落到残留值（`app-audio-playback.js:739-741`、`:1130-1139`）；`chunk_scheduled` 事件按 speechId 发且带 `scheduledEndAudioTime / audioContextTime`（`:489-535`、`:1761-1771`）但在调度时刻发，可领先真开播最多 5 s（lookahead `:1619`，钳位 `:1630-1632`）；`neko-speech-playback-state` 是页面内唯一带 speech_id 粒度的播放事件（`:531`）。`voice_play_start/end` 回后端 `websocket_router.py:1334-1348`。句切规则现成 `SentenceBuffer._SENTENCE_END_RE`（`_infra.py:317`）、`_MIN_CHARS=2`（`:318`）；ws_bistream 每句一次 FINISH（`workers/cosyvoice.py:436-440`）。v1 OD-15 默认文本节拍、TTS 当开关，owner 要求反过来。
- 改成什么: (1) **单一设置 `visitVoiceEnabled`，默认 true**：串门期间本侧猫娘的台词走本地 TTS 且出声（各家只听见自家猫娘、读对方的字）；进 `ALLOWED_CONVERSATION_SETTINGS` + 8 locale（不进 `_USER_OWNED_FIELDS`，非同意开关，见 OD-09 v2）。**删除**「本地静音但保留 RMS」开关：它照付 TTS 额度；想安静的用户关语音（文本估时动嘴、零 TTS 费用），或调低系统 / 应用音量——`speakerGainNode` 在 analyser 之后，系统静音不影响 RMS 口型，效果与该开关相同。产品说明一句：她在邻居家说话，你在自家听见，像开着免提；`.visiting-away` 徽标解释她「不在家」。(2) **TTS 流式双工（v3）**：一行台词一个 speech_id。隔离 `OmniOfflineClient` 的 `on_text_delta`（构造参数，`main_logic/omni_offline_client/_client.py`；game `session_pool.py` 已有先例）每收到一段增量就推进主 manager 的 TTS 队列，走主聊天推 LLM 增量进 TTS 的同一条路径（`turn.py` `_enqueue_tts_text_chunk`，行尾 `_request_tts_done_locked`）：ws_bistream 类 worker 由服务端断句合成，http_sentence 类 worker 在 worker 内用 `SentenceBuffer` 切句（`main_logic/tts_client/_registry_meta.py` 分类表）——上层一律流式喂入，**不再按分句拆成多次 `mirror_assistant_speech`**。为此新增公共流式 mirror 入口 `SessionManager.open_mirror_speech_stream(*, metadata, request_id) -> MirrorSpeechStream`（`push(delta)` / `finish()` / `abort()`；内部复用 `_enqueue_tts_text_chunk / _request_tts_done_locked` 与 mirror 元数据，`mirror_text=False`，不入私聊历史）。本地 TTS 输入**不过出站清洗**：声音只在自家播放，且提示词里亲人名已是中性称呼（OD-10）；情绪标签按主聊天推 TTS 前的同一处理剥离。(3) **字幕对齐（分句规则只作辅助）**：前端 `static/visit/visit-pacer.js` 监听 `neko-speech-playback-state` 中该 speech_id 的 `chunk_scheduled`（带 `scheduledEndAudioTime / audioContextTime`），换算真开播时刻与已播音频时长，播放期间约 4 Hz 回报 `visit_speech_progress{speech_id, visit_id, played_ms, ended}`（`websocket_router.py` 新 `elif`，走 OD-03 注册表 `route_external_page_signal`）。后端把已生成的文本按增量分句器切片（OD-21 v3），第 i 片的放出条件 = `min(自开播起经过的时间, played_ms) ≥ Σ_{j<i} estimate_speech_ms(clause_j)`；`ended` 或末句 turn end 到达 → 剩余已生成分片一次放出。放出即发 `line_delta` + 本机 `visit_line_delta{self:true}`，全部放完后发 `text{final}`（OD-30）。(4) **打断立即停（v3）**：人类插话 / 收尾掐断旧行 → `MirrorSpeechStream.abort()`，内部即新增公共 `SessionManager.interrupt_mirror_speech()`（抽自 `turn.py` `mirror_assistant_speech` 的 `interrupt_audio` 前奏 `_clear_tts_pipeline + release_speech_playback_gain + send_user_activity`，行为不变的提取）立即停播；「已开口前缀」= 截至此刻**已放出的分片**——两侧看到的字一致，与实际音频相差不超过一个分片（owner 接受）。(5) **兜底**：首段推入后 `VISIT_TTS_START_TIMEOUT_S=4` 内没有该 speech_id 的首个 `visit_speech_progress`，或 TTS 未就绪 → 本行切到第 6 条的文本估时并 `status{VISIT_TTS_FALLBACK}` toast（每场一次），本场剩余各行不再重试 TTS；开播后若 `VISIT_SPEECH_PROGRESS_STALL_S=3` 秒没有新 progress 且未 `ended`，剩余已生成分片改按估时从最后一次 `played_ms` 继续放出，并以该行 `__audio_done__`（送达完成）后再加 `estimate_speech_ms(剩余)` 为硬上限，到点强制放完并发 `text{final}`。(6) **语音关**：`utils/visit_wire.py::estimate_speech_ms = 180×CJK字 + 250×拉丁词 + 250×句末标点 + 120×逗顿号`，钳 `[400, 12000]` ms（180/250 是 owner 指定，标点停顿是设计值）；后端定时器按 `t_i = Σ_{j<i} est(clause_j)` 放出第 i 片；`static/visit/text-mouth-driver.js` 消费 `visit_line_delta{self:true}` 排 8~10 Hz 开合驱动 `LanLan1.setMouth`，排帧只用 `nekoFramePacing.requestPacedFrame`，与 RMS 互斥（`S.lipSyncActive===true` 或语音开时不动嘴）；VRM/MMD/PNGTuber 语音开走各自 `startLipSync(analyser)`，语音关时只驱动 Live2D（follow-up）。
- 回归风险: 核心热路径新增一个公共流式 mirror 入口（`turn.py` / `tts_runtime.py`）+ 一处行为不变的方法提取 + `websocket_router.py` 一个新 `elif`——各回归报告一段；流式入口必须测「推流中途 abort」「finish 后 audio_done 对账」「与主聊天 speech_id 不串」三类。产品面：两侧各烧自家 TTS 配额（可关），**TTS 请求 ≈ 每行 1 次、一场 ≈40 次**（估算；v2 逐分句方案 ≈120 次）；字幕与语音靠估时对齐，语速偏快 / 偏慢的 provider 上字幕会领先或落后实际发音零点几秒（被「不超过已播音频时长」钳住，不会早于声音开播）；打断立即停，史里的已开口前缀与实际音频有若干字误差。用户侧成本（估算）：LLM ≈240k input token/侧/场。**实施期必测**：同一 speech_id 流式推入时口型连续；`chunk_scheduled` 可领先真开播最多 5 s（lookahead）时 `played_ms` 换算正确。
- 收益: 首字延迟 = LLM 首段 + TTS 首块 + 通道，与主聊天同量级；语调连贯（不在逗号处拆成多次请求）；TTS 请求数降到约三分之一，免费 TTS 限流风险下降；不改 TTS worker、不新增 worker 协议；语音关时零 TTS 费用仍有节拍；一个开关一句话讲清。
- 推荐: 采纳；默认 visitVoiceEnabled=true；流式双工 + 分句只辅助对齐字幕 + 打断立即停。owner 已拍板（2026-09-30）。
- 备选: v2 逐分句 `mirror_assistant_speech`（每分句新 speech_id，拆断双工流、≈120 次请求、每片 FINISH 尾延迟；owner 否） | 打断停在估算的分句边界（多说最长一个分句，与「人类插话即让」相反；owner 否） | visitVoiceMuted 静音借口型（白烧配额，删） | v1.5 评估：把 A 的 TTS 当音频轨随视频发给 B（TRTC 下 guest 本就按音频计费、零增量；B 听真声、口型天然对齐；代价 ≈32 kbps 要从 600 kbps 挤出 + 克隆音色出机）
- 卡住: visit-pacer.js, text-mouth-driver.js, SessionManager.open_mirror_speech_stream / interrupt_mirror_speech, VisitRuntime._speak_line / on_speech_progress, websocket_router 新 action visit_speech_progress, estimate_speech_ms, 设置项 + 8 locale

#### OD-16 v3 回家汇报（debrief）：她临时记得 → 回家简述 → 两个芯片「记成日记 / 不记」；超时与崩溃都不默认写私聊记忆；串门区 digest 与 debrief 无关；做进本次交付的小 PR
- 现状: v1 OD-16 让她在收尾那一轮顺带说 ≤80 字自述，经 `submit_proactive_callback`（`main_logic/core/proactive.py:2118-2124`）→ `prompt_ephemeral`（`_lifecycle.py:253-262`）投递，回复 `persist_response=True` 进主会话历史（`:752-753`），指令文本抄送插件总线（`:547-568`）——进不进记忆由不得用户。前端已有「系统消息 + 按钮」：`render_chat_blocks(blocks, *, request_id, source, source_name)`（`main_logic/core/turn.py:2001-2050`）发 `chat_blocks`；`app-websocket.js:3079-3094` → `appendReactChatBlocks`；adapter 拼成 `role:'system'`、author 取 `source_name || source`（`app-chat-adapter.js:1101-1126`）；React `buttons` 块（`frontend/react-neko-chat/src/message-schema.ts:83-86`）、按钮结构 `{id, label, action, variant?, disabled?, payload?}`（`:18-25`）；点击 → `MessageBubble.tsx:144 onAction` → `MessageList.tsx:182` → `FullChatSurface.tsx:3088 onAction={onMessageAction}` → 宿主 `handleMessageAction` 派发 `CustomEvent('react-chat-window:action')`（`static/app/app-react-chat-window/message-bundle-actions-and-prompts.js:319-337`；前缀 `bootstrap-state-and-geometry.js:76`），**今天没有任何监听者**；宿主 → React 的更新通道 `react-chat-window:update-message` 已存在（`resize-drag-and-api.js:434-451`）。私聊记忆写入口：`POST /cache/{lanlan}`（`app/memory_server/routes.py:912-985`，写 `recent.json` + `time_indexed.db` + 后台抽取，`_has_human_messages` 门 `:968`；但事实抽取 `app/memory_server/signal_extraction.py:494` 故意跳过没有用户消息的窗口，所以只写一条 AI 独白**不会**被抽成长期事实）；reflection 合成只取 `importance ≥ 5` 且 `absorbed` 为假的事实（`memory/facts.py:5483` `aget_unabsorbed_facts`，`min_importance=5`）；`POST /internal/memory/{name}/scoped_facts`（`:1875`，1..32 条、每条 ≤2000 字）。`is_mirror_event_memory_disabled`（`main_logic/mirror_meta.py:84-108`）只认 soccer/game 键，无键时 `return not has_user_input`，唯一消费点 `cross_server.py:911`。隔离会话历史裁到 40 条（v1 `VISIT_HISTORY_MAX_MESSAGES=40`）而一场最多 80 句——从会话历史做摘要会丢前半场；spool（OD-17 v2）有全场。
- 改成什么: 数据流：finalize（leave → release_takeover 之后）→ (1) 仪式句（v1 步骤 3，删「顺带 ≤80 字自述」）→ (2) 简述：读 spool 全场，只取「两枚章都为真」的对端句 + 我方全部句 → 隔离会话 `stream_text`（`VISIT_DEBRIEF_INSTRUCTION`：用两三句话讲讲今天去了谁家、聊了什么；不许复述对方原话）≤200 output tok、`_llm_turn_lock` 内、8 s → `strip_emotion_tags → redact_outbound → assert_no_peer_ngram(n=8)`（命中 → 8 语固定句）→ (3) 出声 `mgr.mirror_assistant_speech(简述, metadata=build_mirror_meta(source='neko_visit', kind='visit_debrief', ...) + {'memory_enabled': False})`——`mirror_meta.is_mirror_event_memory_disabled` 加显式 `memory_enabled` 键，不再依赖「无用户输入 → 过滤」默认；简述**不进**私聊历史 → (4) 芯片（仅 `visitMemoryEnabled=true` 且 spool 有可记句）：`render_chat_blocks([{type:'text', text:t('visit.debrief.question')}, {type:'buttons', buttons:[diary / forget(variant:'danger')]}], request_id=f'visit-debrief:{visit_id}', source='system', source_name=<猫娘名>)` → (5) 前端 `static/app/app-react-chat-window/visit-chat.js`（index.html 与 chat.html 都加载）监听 `react-chat-window:action`（`action=='visit_debrief_choice'`）→ `POST /api/visit/debrief/choice {visit_id, choice}` → 200 后经 `react-chat-window:update-message` 把两个按钮置 `disabled`、追加 status「已记成日记」；**监听器在 index.html 宽 / 窄 + chat.html 三上下文都要加载**（Electron 分发态聊天在 chat.html 独立窗）→ (6) 后端 `POST /api/visit/debrief/choice`（幂等，第二次 409 `already_chosen`）：`diary` → 两次写入按可恢复的两步提交执行：生成结果（日记段 + 事实）先原子写进 `state.json.debrief_pending`，重试时直接复用、不重新生成；先写 `visit_facts`（服务端精确哈希去重，重试幂等）并记 `debrief_writes.facts=true`，再写 `/cache` 并记 `debrief_writes.cache=true`；每步的「成功」判据看响应体：`/cache` 只有 **HTTP 200 且 `status=='cached'`** 才算成功——`app/memory_server/routes.py:985-987` 异常时也返回 HTTP 200 `{"status":"error"}`，这种与 HTTP 4xx/5xx、连接被拒一样算**明确失败**（可重试、不记 `debrief_writes.cache`）；`visit_facts` 同理只有 HTTP 200 且响应体 `ok==true` 才算成功；只有明确失败才重试该步，超时等结果不确定时按「已写」处理，所以日记最多进一次近期记忆、不会重复；两步都完成才把 `debrief_choice` 定为 `diary`，否则启动补录按 `debrief_writes` 只补未完成的那一步（7 天内）。**一次 LLM 调用同时产出两样**：(a) 第一人称日记段 ≤`VISIT_DIARY_MAX_TOKENS=300`（清洗 + `assert_no_peer_ngram(n=8)`）→ `POST /cache/{lanlan} input_history=[{"type":"ai","content":日记段}]`（`get_internal_http_client`，`utils/http/internal_client.py:69`）进**近期记忆**；(b) 最多 `VISIT_DIARY_FACTS_MAX=3` 条值得长期记住的串门事实（每条 ≤60 字，同样清洗 + n-gram 断言）→ memory_server **新增**端点 `POST /internal/memory/{lanlan}/visit_facts` 写进私聊事实池：`source='ai_disclosure'`（她自己的经历）、`importance=4`、`absorbed=True`、`origin='neko_visit'`、`visit_id`，走 `FactStore._apersist_new_facts` 的语义去重——`importance=4`（低于 reflection 门槛 5）与 `absorbed=True` 双保险保证 reflection 永不合成它们，但召回时能被取到；铸卡排除：`main_logic/card_forge_facts.py` 抽样前过滤 `origin=='neko_visit'`（邻居家的内容不进社区分享卡片）；`forget` → 不写私聊、spool 立即删除，名册 `last_seen` 仍更新。(7) **默认 `VISIT_DEBRIEF_DEFAULT='ask_later'`**：10 min 未点或用户先开新会话 → 芯片**保留可点**，spool 保留 7 天；启动补录（崩溃场次）只重新弹同一组芯片 + status「上次串门意外中断」，**不自动写日记**；7 天未答自动删 spool、芯片置灰「未记录」。(8) **串门记忆区（group_chat 等三 subject）的 digest 与 debrief 选择无关**：只受 `visitMemoryEnabled` 与对端 consent 控制，在 finalize 时（或补录时）做一次（OD-17 v2）；debrief 只决定**私聊记忆**写不写、怎么写。(9) 对端 `consent=false` 时：简述记录块不含对端句，指令加「不要提对方亲人」。(10) 8 语 key：`visit.debrief.question / choiceDiary / choiceForget / savedDiary / forgot / askLaterHint / memoryOffHint`（`static/locales/*.json` 同 hunk，`scripts/check_i18n_sync.py:16-25` 会卡）；后端 `prompts_visit.py`：`VISIT_DEBRIEF_INSTRUCTION / VISIT_DIARY_INSTRUCTION / VISIT_DEBRIEF_FALLBACK`（含 zh-TW）。(11) 不再调用 `submit_proactive_callback`，插件总线上不再出现串门任何文本；`visitMemoryEnabled=false` 时只做 1~3，不出芯片。成本（估算）：后端 1.5 人日 + 前端 0.5 + 测试 1.0 ≈3 人日（d5 估算）；核验补 chat.html 三上下文验证 0.5 + i18n 0.5 → ≈4 人日，仍是小 PR。
- 回归风险: 最多多两次 LLM（简述 + 日记与事实同一次）与一次 TTS；`mirror_meta.py:84` 加显式键（回归报告一段）。`/cache` 收到只含 AI 消息的批次：`_has_human_messages` 为假、跳过 review-clean（`routes.py:968-969`），`recent.json` 会出现一条她的独白——预期（她「跟你说过」）；事实抽取跳过无用户消息窗口（`signal_extraction.py:494`），所以日记段本身**不会**变成长期事实，长期记忆只经 (b) 的 ≤3 条。**既有路径改动两处**（各一段回归报告）：memory_server 新增 `visit_facts` 写端点（进围栏写 op 登记，复用 `_apersist_new_facts`）；`card_forge_facts.py` 抽样前加 `origin=='neko_visit'` 过滤——无该字段的存量事实结果逐字节不变。风险：私聊事实池多出 ≤3 条/场、`importance=4` 的串门事实，会在召回里出现；它们不进 reflection、不进铸卡。芯片 `source='system'`，author 显示猫娘名而非「plugin」。
- 收益: 用户决定记不记；两个按钮一眼看懂；进私聊记忆的是用户选的产物而不是 LLM 即兴复述；日记进近期记忆、少量事实进 fact 层能被日后召回，又不进 reflection 层，时间长了不会堆满无关的串门信息；不问就不写；插件总线零串门文本；零 React 重建；三上下文一套监听。
- 推荐: 做进本次交付（小 PR，叠在核心 PR 之后），不留 issue；芯片只留「记成日记 / 不记」两个。owner 已拍板（2026-09-30）。owner 2026-09-30：日记进近期记忆，另抽 ≤3 条事实进 fact 层、不进 reflection。
- 备选: 三个芯片「记成日记 / 只记要点 / 不记」（v2；要点与日记的区别对用户不直观，owner 改为两个） | 留 issue（owner：简单就直接做） | 沿用 v1 自述 + submit_proactive_callback（抄送总线、不可选择） | 超时默认写日记（不问就写记忆，与 OD-10 隐私姿态相反，否） | 芯片走 galgame 选项 UI（那是 assistant 轮的选项，语义不对）
- 卡住: main_routers/visit_router/debrief.py, prompts_visit.py 四条指令, app/memory_server 新端点 POST /internal/memory/{lanlan}/visit_facts, main_logic/card_forge_facts.py origin 过滤, static/app/app-react-chat-window/visit-chat.js 监听（三上下文）, mirror_meta.py:84 显式键, 8 locale × 7 key, tests/unit/test_visit_debrief.py（n-gram 变异必红 / choice 幂等 / ask_later 不写 / memoryOff 不出芯片 / consent=false 不含对端句 / /cache 请求体形状 / visit_facts ≤3 条且 importance=4、absorbed=True / reflection 不取 origin=neko_visit / 铸卡不含 origin=neko_visit）

#### OD-17 v2 记忆写入时机：每句立刻追加本地崩溃安全 spool（config_dir/visit_spool/，fsync 30 s + finalize）；digest 在结束时做一次；10 min 周期作开关默认关；崩溃补录只重新弹芯片
- 现状: 先用人话说 v1 为什么攒着写、崩了丢什么：「写进记忆区」不是写文件，是一次 LLM 调用——`/scoped_history` 收一批对话、抽事实、去重、落库（`app/memory_server/routes.py:1949-1957`，一批 1..200 条，`config/memory_settings.py:210 SCOPED_HISTORY_BATCH_MAX_MESSAGES=200`）；一句一调 = 一场 80 次调用、每次几千 token、且抽出大量「她说了你好」噪音，所以 v1 和 QQ 群一样攒 40 行再调（`session_memory_service.py:44 GROUP_DIGEST_BACKLOG_TRIGGER=40`）。v1 为了「磁盘上没有对端明文」把这 40 行只放内存：进程崩溃、taskkill、断电、关机 flush 超时 → 40 行没了；不到 40 行的串门等于整场没记。对比私聊记忆：每轮 `/cache` 就把原文写进 `recent.json` 与 `time_indexed.db`，LLM 抽取才攒批（10 轮或 5 min 空闲，`routes.py:912-985, :920`）——v1 串门比私聊更不耐崩，这是 owner 追问的根子。复用先例：稀疏事件 JSONL 写手 `utils/event_logger.py:24-40`（按天分片、append-only、7 天保留、单文件 500 KB、目录 20 MB、每行 <4 KB 靠单次 `write` 的 O_APPEND 原子性），`:263-264 open(path,'ab').write(payload)`——**先例不 fsync**（`facts_sync/sync_worker.py:78-81` 也是文本模式 append 不 fsync），fsync 是新增；`atomic_write_json`（`utils/file_utils.py:785`，`:770-783` tmp+fsync+replace）；owner-only 权限 `_write_private_json`（`card_drop_router.py:620-630`，`chmod 0o600`，Windows 无效）；`config_dir` 与 `memory_dir` 是兄弟目录（`storage_roots.py:160-161`）；Steam 云存档只拷 `MANAGED_MEMORY_FILENAMES`（`utils/cloudsave_runtime/snapshots.py:196, :330, :416`）；启动链路禁阻塞。
- 改成什么: (1) `main_logic/visit/spool.py::VisitSpool`，目录统一 **`config_dir/visit_spool/`**（与 OD-30 的 `<visit_id>.outbox.jsonl` 同目录、同原子追加 helper），每场 `<visit_id>.jsonl` + `<visit_id>.state.json`。(2) 第一行是头 `{v:1, visit_id, role, own_char, pair_id, peer_uid, peer_char_id, peer_char_tag, started_at, lang}`；之后每句一行 `{ln, lp, side, ts, from:'own_cat'|'peer_cat'|'peer_human'|'own_human', text, truncated, local_memory_at_receipt, peer_consent_at_receipt}`，`text` 是已过 `sanitize_relay_text` / `clamp_text_utf8(4096)` 的 `text{final}` 全文（`line_delta` 不入 spool），单行 <4 KB、单次 `write`，`asyncio.to_thread` 里做，顺序由单写线程队列保证。(3) `fsync` 每 30 s 一次 + finalize 时一次：进程崩溃丢 0 句（页缓存还在），断电最多丢 30 s。(4) `state.json`（`atomic_write_json`）：`{digested_through_lp, digest_runs, finalized:null|reason, debrief_choice:null|'ask_later'|'committing:diary'|'diary'|'forget', debrief_pending:{diary, facts}|null, debrief_writes:{facts:bool, cache:bool}, peer_revoked_scope}`（`debrief_pending` 暂存待写的日记与事实正文、`debrief_writes` 记两步提交各自是否已完成，OD-16 v3）。(5) **digest 触发**：finalize 时一次，只吃两枚章都为真的句子，写 `/scoped_history` 单 subject + 对端两位 segments（OD-04）；与 debrief 选择无关（OD-16 v2）。`VISIT_DIGEST_INTERVAL_S`（默认 0=关）保留代码路径：一场硬顶 80 句 / 30 min 一次 `/scoped_history` 装得下；有了 spool，周期 digest 对耐崩没有贡献，反而让「不记」清不掉前半场；付费档放宽时长时再打开。(6) **删原文**：digest 与 debrief 选择都落地 → 删 `.jsonl`，`state.json` 留 7 天（幂等、诊断）；`forget` 或 `visitMemoryEnabled=false` 中途关掉 → 立刻删 `.jsonl`；对端撤销 `scope:'all'` → 删 `.jsonl` 且 `state.json` 里的 `peer_uid/pair_id` 一并抹掉（对偶「删名册项」）。(7) **启动补录**：main_server 启动后作为后台任务（不在启动链路上）扫 `visit_spool/`：`finalized` 非空但 `digested_through_lp` 落后 → 补 digest；`finalized` 为空（崩溃）→ 标 `finalized='crash'`；补串门区 digest（`commit_visit_region`，只受 `visitMemoryEnabled` 与对端 consent 控制，与 debrief 无关）；不写任何私聊记忆（**不自动记要点**）；重新弹同一组芯片 + status「上次串门意外中断」（OD-16 v2 `ask_later`）；memory_server 不可用则下次再试；文件 >7 天或目录 >20 MB 直接删。(8) `memoryEnabled=false`：不建 spool 文件，转录只在内存（v1 行为）；待上传 Servers 的转录例外，一律临时存 `.upload.json` 到上传成功为止（OD-26 v3）；`true` 时 OD-26 导出改读 spool（页面重载也能导）。(9) 权限 `0o600`（Windows 无效，同凭证文件立场）。(10) 一句：Steam 云存档只同步 `MANAGED_MEMORY_FILENAMES`，spool 与 outbox 不会被同步。
- 回归风险: 新目录、新后台任务；关机钩子 ≤0.5 s 的 fsync（OD-11 v2 3 s 预算内）；不动 memory_server。成本（人话）：磁盘一场 ≤80 句 × ~300 B ≈25 KB，目录硬顶 20 MB；CPU 每句一次线程池写可忽略；LLM 结束时 1 次 digest（+ debrief 的 1 次），比 v1 的 30 min ≈4~5 次更少。风险 1：对端原文短暂落盘（v1 刻意避免）——缓解：digest 后即删、forget 即删、7 天硬顶、owner-only 权限，且用户本来就能在 OD-26 导出全文，落盘没有扩大能看到它的人。风险 2：fsync 30 s → 断电最多丢 30 s（进程崩溃不丢）。
- 收益: 进程崩溃、taskkill、关机超时、memory_server 暂不可用都不丢句；转录导出不再依赖内存；debrief 有全场记录；不问就不写。
- 推荐: 采纳（spool + 结束时 digest；10 min 周期作开关默认关）。owner 已拍板（2026-09-30）。
- 备选: 10 min 周期默认开（owner 原话「结束或每 10 min」——若更想要，默认改 600，代价「不记」清不掉前半段并需 UI 说明） | 每句直接 /scoped_facts（每句一次 LLM、事实噪音） | 只留内存（v1，owner 已否） | 崩溃补录默认记要点（不问就写，否）
- 卡住: spool.py, state.json 契约, 启动补录任务（app/main_server/__init__.py startup 后 create_task）, OD-26 transcript 端点改读 spool, tests/unit/test_visit_spool.py（写一半 kill -9 后重放行数一致 / 不记即删 / 补录只吃双章句 / scope=all 抹 peer_uid）

#### OD-18 记忆浏览器数据源：memory_server 加只读 GET /internal/memory/{name}/scoped_subjects?platform=；/api/visit/memory/peers 按 visit_uid 聚合
- 现状: 浏览器只看 recent.json 且走文件锁；memory_server 无 subject 列表端点；名册 `visit_peers.json` 是新增（OD-05 v2）。
- 改成什么: ≈60 行只读端点；`/api/visit/memory/peers` 按 **`visit_uid`** 聚合（`participant` 单 subject + 名册里该人所有 pair 的 `group_chat` / `group_participant`）并合并 blocklist；面板文案如实说明清除范围（含「对方机器上的副本无法远程清除」）。
- 回归风险: 只读；改 app/memory_server 需回归报告。
- 收益: 用户能看见谁来串过门并一键清空。
- 推荐: 加只读端点。owner 已拍板（2026-09-30）。
- 备选: 主进程直读三份 JSON | 只做全部清除
- 卡住: routes.py 端点, memory_routes.py, memory_browser 面板 + 8 locale

#### OD-19 前端访客身份：复用 role 'tool'，样式作为新增规则写进 static/css/index.css
- 现状: message-schema.ts:188 枚举含 tool；MessageBubble.tsx:26/35/42 给 tool 独立类但**仓库没有任何 .message-bubble-tool/.avatar-tool 的 CSS 规则**（tool 今天与 assistant 视觉相同）；CompactExportHistoryPanel.tsx:158/171 把 tool 与 assistant 同组；refreshReactAssistantAvatars（app-chat-adapter.js:1183）只碰 assistant。
- 改成什么: visit_line 由 adapter 直挂 role 'tool'；在 static/css/index.css **新增** .message-bubble-tool/.avatar-tool 规则（宿主页 CSS 作用到 React 根），不进 react styles.css；导出面板分组列 follow-up。
- 回归风险: 极低：不动 React 包；未来真正的 tool 消息生产者会与访客同款视觉。
- 收益: 零 React 重建；天然躲开三处 assistant 改名/改头像逻辑。
- 推荐: 采纳。owner 已拍板（2026-09-30）：样式无所谓，只要标明来源（哪家的猫娘 / 哪家的亲人）。
- 备选: 新增 role 'guest'（改 React 四处） | chat_blocks 系统 chip
- 卡住: app-chat-adapter.js, visit-chat.js, index.css

#### OD-20 v2 视频拥塞控制交给 WebRTC：删客户端↔中继单 socket 与 2 帧在飞窗口；应用层只做 5 s stats 反馈阶梯
- 现状: v1 的单 socket + 2 帧在飞窗口依附自建中继：`websockets` 客户端 `write_limit` 默认 2**15（client.py:74/319，connection.py:1006）之下还有内核缓冲，v1 靠应用层窗口防帧堆积。v2 视频走 vendor WebRTC 视频轨（OD-02 v2）：libwebrtc 拥塞控制 + `MAINTAIN_FRAMERATE` 在编码器侧自动降分辨率 / 码率（`webrtc_video_engine.cc:2011-2046`）；TRTC 无 degradationPreference API；LiveKit `degradationPreference:'maintain-framerate'` 可显式设。
- 改成什么: 删除 v1 `relay_client push_frame`、`backpressure.FrameWindow`、`frame_pump ack{frame_seq}`、`VISIT_FRAMES_IN_FLIGHT`；视频拥塞完全交 WebRTC；应用层只保留 B 每 5 s 的 `stats{rx_fps, rtt_ms, loss_pct, rx_w, rx_h}`（cmd 3 lossy）+ A 侧 vendor 网络质量事件驱动的裁剪阶梯（OD-06 v2）；文本 / 控制走数据通道 + 后端 outbox（OD-30），与视频同一 vendor 会话、同一失败域（OD-11 v2 统一处理，不会一边聊一边黑）。
- 回归风险: 零（v1 新路径整体删除）。体验：弱网下由 libwebrtc 决定降分辨率而非应用层，接收端以 `videoWidth/Height` 观测（OD-06 v2）。
- 收益: 少一整套在飞窗口 / ack / 自钳代码；标准 WebRTC 拥塞控制比应用层 2 帧窗口成熟；文本不再与视频同一条 socket 排队。
- 推荐: 采纳。owner 已拍板（2026-09-30）。
- 备选: 应用层再套一层帧窗口（与 libwebrtc 拥塞控制打架） | 视频与文本分两个 vendor 会话（双份计费与状态）
- 卡住: 删除 v1 PR-06 的 backpressure/frames 与 PR-09a 帧泵, stats 上报 transport.js

#### OD-21 v3 猫娘台词流式转发：默认开；LLM 边生成边推 TTS（OD-15 v3），发给对方的文字用增量分句器切片、逐片清洗、按已播音频对齐放出 line_delta（可丢、只上屏）+ text{final} 全文必达收口（被打断行 truncated + 已放出前缀）；VISIT_STREAM_DELTAS=False 留紧急开关
- 现状: `gemini_response` 按 delta 逐块发前端（turn.py:1713/1771；app-websocket.js:3097 消费）；`mirror_assistant_speech` 是整句入口；v1 OD-21 默认关、整句完成后发，首字延迟 +2~3 s；TRTC 数据通道 1 KB/次、30 次/s、8 KB/s、有序 best-effort（research_trtc.md:31-42，https://web.sdk.qcloud.com/trtc/webrtc/v5/doc/en/TRTC.html#sendCustomMessage ），LiveKit reliable 亦 best-effort（https://docs.livekit.io/transport/data/packets/ ）；句切规则 `_SENTENCE_END_RE`（`_infra.py:317`）；`truncate_to_tokens`（`utils/tokenize.py:143`）。
- 改成什么: (1) `VISIT_STREAM_DELTAS=True`。(2) **LLM 流式（v3）**：隔离会话 `on_text_delta` 一边推 TTS（OD-15 v3），一边把增量追加进本行缓冲；`utils/visit_wire.py::ClauseSplitter`（增量版 `split_clauses`，纯函数可单测）：主边界 = `main_logic/tts_client/_infra.py` `SentenceBuffer._SENTENCE_END_RE` 同一套句末标点 + 换行，遇到即切；次边界逗顿号只在当前分句 ≥`VISIT_CLAUSE_SOFT_MAX_CHARS=24` 个 CJK 字（或 12 个拉丁词）时才切；<2 字并入下一片；硬上限 UTF-8 ≤`VISIT_DELTA_TEXT_MAX_BYTES=800`，在字符边界拆、绝不切 codepoint；行尾 flush 残片。**24 字只影响字幕切片粒度，不影响出声快慢**（出声由 TTS 流式决定）。(3) **先脱敏再切片、其余逐片清洗**：`redact_outbound` 作用在本行累积缓冲上，每切出一片前先对缓冲整体脱敏；遇到 800 B 硬切时保留末尾 `max(len(受保护词)) - 1` 个字符不切出（等更多文本或行尾 flush 再判），保证受保护词不会跨片；OD-23 清洗链其余两步 `strip_emotion_tags`、`sanitize_relay_text` 仍逐片做；整行长度由 `max_response_length=VISIT_RESPONSE_MAX_TOKENS` 约束，出站前不再做整行 `truncate_to_tokens`，`text{final}` 仍 `clamp_text_utf8(4096)`；不变量「已放出分片拼接 == text{final}.txt」单测钉住。(4) **放出时机**：语音开见 OD-15 v3 第 3 条（按已播音频对齐）；语音关按估时。(5) 消息（OD-30 wire 权威）：`line_delta`（cmd 2，可丢，只上屏不入史不入 spool）`{t:'line_delta', v:1, ln, i, lp, txt(≤800 B)}`，`i==0` 额外带 `sp, ad, rt, wu`；每行**永远**以一条 `text`（cmd 2，必达）收口 `{t:'text', v:1, ln, lp, seq, sp, ad, rt, wu, final:true, txt(全文或已放出前缀 ≤4096 B), truncated, i_done, trunc_reason?}`；`line_abort`（cmd 2，可丢）只是让 UI 立刻截断的提示，随后必有 `text{truncated:true}`。接收侧以 `text` 为准覆盖气泡、入史、入 spool、计数；`line_delta` 缺片**不补洞**，等 `text`。(6) 显示端 `static/app/app-react-chat-window/visit-chat.js`：`Map<ln, {clauses, bubble, lastAt}>`，`line_delta` 按 `i` 落位、缺片留 `…` 占位；`text` 到达全文覆盖并标 final；`truncated:true` 尾部加 `visit.stream.truncated`（「（被打断）」）；20 s 无新片且无 `text` → 本地 stall 截断（`VISIT_LINE_STALL_S=20`），`text` 迟到仍覆盖。本机 display socket 对偶消息 `visit_line_delta{visit_id, line_id, i, text, speaker{side,kind,self}, lp}` 与 `visit_line{..., final:true, truncated}`；自家猫娘的气泡也只由这两条驱动（`mirror_text=False`），两侧字幕节拍同源。(7) 记忆与历史：接收侧 `text` 到达时 `append(HumanMessage)`（addressee 是我 → 触发回复；否则纯入史）+ spool；`truncated` 时入史前缀 + `VISIT_MARK_INTERRUPTED`（8 语「（说到这里被打断了）」）。发送侧整行生成完才把 AIMessage 入史；被打断 → 入史**已放出前缀** + 标记，未放出的不入史——对端看到的字双方史里都有。(8) 节流（后端 outbox 出站队列执行）：同 v2——总字节令牌桶 5 KB/s、条数桶 20 条/s（桶 10）、同行 delta 最小间隔 250 ms 合并（末片不合并；`i` 在发送时按实际发出的片连续分配，合并后的片占一个 `i`、后续顺延不留洞，`text{final}.i_done` 同步按发出片数计）、积压 >3 s 相邻 delta 合并到 ≤800 B、超限**排队不丢**；速率上界算式不变（≈12.4 条/s，真实峰值 ≈2.8 KB/s，估算）。(9) `VISIT_STREAM_DELTAS=False` 紧急开关：字幕退回整句模式（只发 `text`，恢复 `typing on/off`），TTS 仍流式。
- 回归风险: 无既有路径改动（分句与清洗全在新文件）。协议面同 v2：缺片时字幕短暂出现 `…` 占位直到 `text` 到达；一行文本字节 ≈2×（真实峰值 ≈2.8 KB/s，远低于 8 KB/s）。逐片清洗少了整行上下文：出站台词本身不做跨片 n-gram（那是猫娘自己的话），对方原话照抄只由回家汇报的 n-gram 断言兜（OD-23）。
- 收益: 首字不再等整行生成（v2 设计稿的步骤实际是整行 + TTS 首块，延迟估算与步骤自相矛盾，本版一并改正）；字随嘴走；可靠层只认一种消息（`text`），无 CRC / 补洞 / 按行 ack 三套机制；两种传输一套消息。
- 推荐: 默认开；LLM 流式 + 增量分句逐片清洗 + 按已播音频对齐放出。owner 已拍板（2026-09-30）。
- 备选: v2 整行生成后再切分句（首字要等整行；owner 否） | 字幕按 LLM 速度直接放出、不等语音（字比嘴早） | 按 token 级 delta（消息数 ×5~10，撞 30 条/s 且与 TTS 节拍无关） | d4 的 line{n,h} CRC + line_req 补洞（省一半字节，多三种消息与 lossy 历史） | ack 按片（消息数翻倍）
- 卡住: utils/visit_wire.py（ClauseSplitter / estimate_speech_ms / encode_* / 分片单测「最长合法 text 分片后每片 ≤1000 B」「已放出分片拼接 == 全文」）, VisitOutbox 节流器, visit-chat.js 拼接, visit_line_delta/visit_line 本机协议, spool 排序键 (lp, side)

#### OD-22 guest 侧（A）人类在串门期间打字：拒绝 + toast，不做「捎话」
- 现状: 文本唯一入口 stream_data→主会话。
- 改成什么: route handler 在 side=='guest' 时发 VISIT_INPUT_REFUSED_AWAY 返回 True；composer 只留「叫她回来」（收尾期间再按变 no-op + `VISIT_RECALL_ALREADY` toast，OD-08 v2）。
- 回归风险: 无。
- 收益: 不引入第四种发言人。
- 推荐: 采纳。owner 已拍板（2026-09-30）。
- 备选: 捎话 | A 人类文本只进 A 猫娘隔离会话
- 卡住: route_stream_message guest 分支, visit-chat.js A 侧 UI

#### OD-23 输出侧亲人名替换 + 出站文本过同一清洗函数 + 回家自述 n-gram 断言
- 现状: 仓库无出站审查；串门 prompt 亲人名已由 OD-10 中性化，输出侧只剩角色卡正文与 LLM 自由发挥。
- 改成什么: redact_outbound 整词替换亲人名为 FAMILY_NEUTRAL_TERM；出站再过 sanitize_relay_text；回家自述过 assert_no_peer_ngram(n=8)。
- 回归风险: 只作用于串门出站；常用词名字可能误替换。
- 收益: 配合 OD-10 后剩余泄漏面只剩角色卡正文。
- 推荐: 做。owner 已拍板（2026-09-30）。
- 备选: 不做 | LLM 二次审核
- 卡住: sanitize.py

#### OD-24 takeover 归属令牌：manager 加 acquire_takeover/release_takeover 公共 API；game 与 icebreaker /route/start 查 external route 注册表
- 现状: main_logic/core/manager.py:277-278 两个私有属性无归属；game_router/runtime.py:1898 game_route_start 只查 _character_route_owned_by_another_game（:658-692，只看 _game_route_states，自述 NOT A SECURITY BOUNDARY），:2075-2076 无条件写 takeover=True 与 dispatcher；postgame.py:1277-1278 /route/end 无条件置 False。icebreaker_router.py:263 /route/start 不置 takeover 但也不查别的路由。结果：串门期间用户从 Chat 窗打开任意小游戏 → 覆盖串门 dispatcher；关掉游戏 → 解除串门静音，主动搭话/文本回复恢复出声而串门仍在飞。
- 改成什么: manager.py 新增 acquire_takeover(owner, dispatcher) -> token（已被他人持有抛 TakeoverOwned）与 release_takeover(token) -> bool（不匹配 no-op + warning），私有属性只由它们写（main 上 takeover 已扩成三个属性、三个写入点——含一起看的 callback sink 与失败回滚——PR-02 按 §5 总则 2a 覆盖）；方法体放 `main_logic/core/takeover.py`（`TakeoverMixin`，登记 `MIXIN_SUPPORT_CLASSES`），`manager.py` 只加 base 与 `_takeover_owner` 字段——`scripts/check_core_contracts.py` 不允许 `manager.py` 新增方法；game_route_start 与 icebreaker /route/start 在自有检查旁加「注册表里有别的 kind 活动 → ok:false, reason:'route_owned_by_external'」；game :2075 改 acquire、postgame :1277 改 release(state.pop('_takeover_token'))；visit 同样经 token。
- 回归风险: game 两处改动 + icebreaker 一处 + manager 新 API → 三段回归报告。行为变化：串门在飞时打开小游戏被拒（新）；game 自己的 acquire/release 逐字节等价（同一 owner 正常配对）。test_external_route_registry.py 加「token 不匹配释放 no-op」变异必红。
- 收益: 串门静音不会被别的路由解除/覆盖；三种 route（game/icebreaker/visit）共享一个归属判定；今后第四种接管者不再复制 takeover 写法。
- 推荐: 采纳（这是本轮修订唯一动到既有语义的项，必须做，否则 OD-03 的静音承诺不成立）。owner 已同意（2026-09-26）。
- 备选: 只改 visit 侧检查、不动 game（game 仍会覆盖串门） | 在 websocket_router 拦 /route/start（game 的 HTTP 端点不经 ws）
- 卡住: manager.py, game_router/runtime.py:2075, postgame.py:1277, icebreaker_router.py:263, test_external_route_registry.py

#### OD-25 串门中收到告别（goodbye_state{active:true}）：两侧都 finalize('goodbye')，固定句、静音
- 现状: websocket_router goodbye_state → lifecycle.py:82 set_goodbye_silent 只静音主 manager 并 park proactive；隔离串门会话与 mirror_assistant_speech 不看该门，B 的猫娘会继续出声陪访客聊；A 的猫娘「出门中」被亲人告别也无处理。
- 改成什么: visit_router 订阅 goodbye 状态（websocket_router goodbye_state 分支旁一行 `if is_visit_route_active: create_task(finalize_visit_route(state, reason='goodbye'))`）：host 侧送客用 VISIT_FIXED_LINE 固定句且不出声，guest 侧叫她回家同样固定句静音；对端收 `leave{reason:'goodbye'}`；goodbye_state{active:false} 不自动恢复串门；**不走** OD-08 v2 的收尾流程（那是全局告别，越快越好）。
- 回归风险: websocket_router goodbye 分支多一个 if（回归报告一段）；产品面：说再见就结束串门，用户不能「让她自己继续玩」。
- 收益: 告别语义一致（说再见 = 家里安静）；不出现「亲人告别了猫娘还在隔壁大声聊」。
- 推荐: 采纳 finalize('goodbye')。owner 已同意（2026-09-26）。
- 备选: 只静音 host TTS、串门继续（A 那边看到 B 猫娘还在说，B 家却没声音，语义分裂） | 忽略 goodbye（现状，B 猫娘继续出声）
- 卡住: websocket_router goodbye 分支, prompts_visit 固定句, test_visit_router.py goodbye 用例

#### OD-26 v3 知情同意与用量透明：guest 出门前确认框 + host 接待确认（60 s），确认框不出现技术数字；结束后藏得较深的「查看详情」（时长 / token / TTS 消耗 + 完整转录，数据来自云端）；转录每场上云、长期保留、只在隐私政策披露
- 现状: 原稿 visitEnabled 一开即允许任何持 room_id 的登录用户接待她/来串门，A 看不到对方身份；中继零落盘、客户端不留原文，被骚扰用户只有拉黑。现有遥测只上报 counter / histogram（`utils/instrument.py` `instrument.snapshot()` → TokenTracker 定时 `POST {_TELEMETRY_SERVER_URL}/api/v1/telemetry`），event 通道只写本机 `config_dir/telemetry_events/`（`utils/event_logger.py`，7 天滚删）且从不上传——对话内容今天没有任何上云通道。
- 改成什么: (1) join 请求需 `confirm:true`：前端确认框显示对端 display_name + 6 位短码 + 跨区提示；**不显示 token / TTS 次数等技术数字**（owner 2026-09-30）。(2) host 收到对端 `hello` 核验通过后前端 `visit_invite` → `POST /api/visit/rooms/{id}/accept{accept}` 才发 `ready`（60 s 超时 `leave{declined}`）。(3) **用量**：每场 finalize 时本机汇总 `{visit_id, role, duration_s, llm_input_tokens, llm_output_tokens, tts_requests, tts_chars}`；计数部分经现有遥测 counter / histogram 上报（低基数维度，不带 visit_id）；带 visit_id 的整场记录随第 4 条一并上传 Servers。(4) **转录上云**：finalize 后本机后端把本侧转录（按 `(lp, side)` 排序的 `from / ts / text / truncated`，文本是已过出站清洗的 `text{final}`；本侧亲人行是其原文）+ 第 3 条用量 `POST {social_base}/api/visit/transcripts`（OAuth Bearer，按 `visit_id + role` 幂等），双方各传自己那份；与 `visitMemoryEnabled` 无关（记忆开关管「她记不记」，上云管账单与举报）。待上传转录**一律临时存盘，与 `visitMemoryEnabled` 无关**：finalize 时写 `config_dir/visit_spool/<visit_id>.upload.json`（只含上传字段，原子写，`0o600`；关机走 `stop_all('shutdown')` 时同样在 3 s 预算内同步写出），上传成功即删，失败则下次启动重试，自结束起 7 天仍失败则放弃并记一条本地诊断事件。Servers **长期保留**（与账单记录同期），作为账单与举报证据；对端「全部忘掉我」（OD-09 v2 `scope:'all'`）只清本机记忆，**不删云端转录**。(5) **查看详情**：结束后在该场系统消息的折叠区与记忆浏览器串门面板里放一个不显眼的「查看详情」入口 → `GET /api/visit/details/{visit_id}` 代理 Servers `GET {social_base}/api/visit/details/{visit_id}`，返回本场时长、扣减的免费分钟（OD-06 v2）、token / TTS 消耗、完整转录（双方两份按 `(lp, side)` 合并）；只有该场双方账号与管理员可读。(6) 披露：只写进隐私政策（owner 2026-09-30），确认框不提。(7) 本机导出 `GET /api/visit/transcript` 保留作离线兜底：本场及结束后 10 min 内可导（`visitMemoryEnabled` 开时读 spool，页面重载也能导）。
- 回归风险: 零仓库回归（新端点、新后台上传任务）。隐私面（owner 已知悉并拍板）：双方对话全文——含对方猫娘与对方亲人的原话——进入我方云端并长期保留，披露只在隐私政策，确认框不提；对端撤销不删云端副本。跨仓库：Servers 新增 transcripts 上传与 details 查询两端点、长期存储与访问控制（§4.7）。每次串门双方各多点一次。
- 收益: 双方对每一次串门都显式点头；用户能查到完整的用量与记录；举报证据在云端，不依赖本机文件是否还在；确认框不再堆技术数字。
- 推荐: 采纳。owner 已拍板（2026-09-30）：确认框不出技术数字；「查看详情」入口藏深；转录上云、长期保留；只在隐私政策披露；对端撤销不删云端。
- 备选: 确认框显示预计 token / TTS 消耗（v2；owner 否：不给用户看技术细节） | 转录只留本机可导出（v2；举报证据依赖本机文件） | 双方确认框写明「对话会上传」（owner 选择只写隐私政策） | 云端保留 30 天 / 7 天 | 对端撤销时连云端一起删（举报失证）
- 卡住: visit_router accept / transcript / details 端点, 转录上传任务与重试, visit-chat.js 两个确认框 + 查看详情入口, Servers transcripts / details 端点（§4.7）, 隐私政策文本

#### OD-27 同源 iframe 承载 vendor SDK 与访客图层（lanlan_frd 零改动）；能力门在领凭证之前
- 现状: Pet 窗 preload 把 `window.WebSocket` 换成 `PetWebSocket`，构造时不看 URL 就 `_activeWs = ws` 并向 Chat 窗发 CONNECTING（`lanlan_frd/src/preload/bridges/pet-websocket-bridge.js:333-346`，`:464` 安装，`:818-821` 卸载还原）；非当前 socket 的消息按 stale 丢（`:393-397`）；字符串消息逐条 console.log + IPC 扇出（`:426-431`）。Pet 窗 `webPreferences` = `preload / sandbox:false / contextIsolation:false / nodeIntegration:false / webSecurity:true / backgroundThrottling:false`（`src/window-manager.js:1001-1011`），**全仓无 `nodeIntegrationInSubFrames`**（grep 零命中）→ 子 frame 没有 preload，拿到原生 `WebSocket` / `RTCPeerConnection`，也没有任何 `window.electron*` 桥。权限处理器 `allowedPermissions`（`src/main.js:11336-11339`）与 `isTrustedAppMediaWebContents`（`:11351-11360`）只看顶层 URL，`RTCPeerConnection` / `WebSocket` 不被闸（`:11398 / :11408`）；`resolveServerUrl` 允许自定义后端 URL（`:1576`）。Xiao8 无 CSP meta；`/static` 由 `CustomStaticFiles` 挂载（`app/main_server/web_app.py:234`）；页面路由 `pages_router.py:265-273`（`/`）、`:453`（`/chat`）；`app-websocket.js:2746` ws URL 由 `window.location.host` 拼。preload 命中测试 `elementFromPoint`（`pet-input-region-bridge.js:2722 / :3108 / :5205-5206`），`isModelBackgroundElement` 把 `.transparent-overlay` 当背景（`:2204-2222`，`:2211`）。TRTC 文档的 HTTPS 要求针对浏览器 getUserMedia 采集（localhost / 127.0.0.1 例外），本设计不用 getUserMedia。
- 改成什么: 父页在用户点「出门」/ 收到邀请时**先**懒创建 `<iframe id="visit-frame" class="transparent-overlay" src="/visit/transport?v={static_asset_version}&side=host|guest&visit_id=…">`（新模板路由 `GET /visit/transport`，无末尾斜杠），iframe 连 transport WS（OD-29）跑**能力门**：① `isSecureContext`；② `iframe.contentWindow.WebSocket.name === 'WebSocket'` 且原型原生（防未来壳版本给子 frame 加 preload）；③ SDK 懒加载后 `TRTC.isSupported()`（返回 `{result, detail}`）或 `RTCRtpSender.getCapabilities('video')` 含 VP9 / VP8。**能力门拆两段**：①②（与 transport 无关的预检，含 `RTCPeerConnection` / 画布 / `captureStream` 在场）`caps{preflight_ok}` 通过后才向 Servers 领凭证；①② 失败 → 后端 409 `VISIT_UNSUPPORTED_ON_THIS_MACHINE`，**不消耗配额、不占 takeover**（`acquire_takeover` 放到凭证成功之后，失败即 release；不耗配额的承诺只覆盖这一段）；③ 要按 transport 加载 SDK，只能在收到凭证后执行，整体失败 → `finalize('unsupported')` + `release_takeover`，此时已计一次签发；③ 只视频子项失败 → `caps{video_ok:false}`，串门照常（文本 + 字幕 + TTS），`hello.caps.video=false` 让对端显示头像占位。`caps` 结果后端缓存，设置页「串门」分组打开时也可预跑。vendor SDK 只在 iframe 内按 Servers 返回的 `transport` 动态插一份 `<script>`；`ended` 时 `iframe.remove()`；同一时刻最多一个。iframe 持有：打包画布 + `captureStream`、vendor 会话、接收侧 `<video>` + WebGL 解包画布、到本机后端的独立 WS。父页 ↔ iframe：帧触发走**同步跨 realm 函数调用**（父页缓存 `iframe.contentWindow.__nekoVisitFrameSink.onFrame(canvas, rectPx, tsMs)`，调用前守卫 iframe 已就绪未销毁），裁剪框 / 摆位 / 状态 / 日志走 postMessage（来源校验 `event.source === iframe.contentWindow && event.origin === location.origin`）。自定义 `http://<LAN IP>` 后端：`isSecureContext===false` → 首发 409 + 8 语文案「自定义后端地址需 https 或 localhost」；T11 实测 SDK 在 http://LAN 下是否可发 canvas 轨，通过则改为只警告。模型类型切换（VRM `vrm-manager.js:877` render 后、MMD `mmd-core.js:1291` 两个 render 分支后 `_flushRenderWaiters()`（`:1294`）前各 +1 行、PNGTuber `<img>` 用 `nekoFramePacing.requestPacedFrame` 30 Hz 采样）由父页重挂钩子，iframe 不感知。
- 回归风险: lanlan_frd 零改动零回归。Xiao8：主页面新增 `static/visit/parent-bridge.js`（钩 `postrender`、算裁剪框、摆 iframe）；index.html 无新静态元素。风险全在新路径：iframe 透明与命中（T3/T4）、同任务取帧（T2）、能力门探测 iframe 的懒建时机；iframe 的 console 不被 preload 镜像到 Chat 窗（关键日志经 `postMessage({t:'log'})` 转发父页）。**版本偏斜**：单机零偏斜（父页、iframe、SDK、后端同一发布）；A/B 两机的 Xiao8 版本偏斜由 `hello.caps.proto` 协商（OD-30）。
- 收益: `_activeWs` / Chat 窗 IPC 扇出 / forge 闸完全不受影响；SDK 不进主页面热路径；远端轨道不用跨 realm 搬运；不需要 PC 先发版、不存在「老壳 + 新后端」偏斜；能力门 ①② 在 Servers 之前，失败不花配额。
- 推荐: 采纳（本稿前提；T1~T5 任一失败退设计 1，只损失前端两 PR，后端 PR 完全通用）。owner 已拍板（2026-09-30）。
- 备选: 设计 1（preload 加异 host 直通 + 能力旗；需 PC 先发版并对老壳 fail-closed） | Electron 卫星窗（改闭源） | 主页面直接跑 SDK（被 :333-346 劫持，Chat 窗全黑）
- 卡住: templates/visit_transport.html, pages_router 路由, static/visit/transport/*.js, static/visit/parent-bridge.js, 实测 T1~T5/T11

#### OD-28 vendor SDK 随包分发：static/libs/trtc.js（5.20.1，ISC）与 static/libs/livekit-client.umd.js（2.22.3，Apache-2.0）；登记 THIRD_PARTY_NOTICES + licenses + check_nuitka_dist 必需表；只在 iframe 内按 transport 懒加载
- 现状: 第三方库全部 UMD 落 `static/libs/*.js?v={{ static_asset_version }}`（`templates/index.html:331-337`），无 npm 构建；`static/libs/THIRD_PARTY_NOTICES.md:1-6` 明写不是全量清单；`scripts/check_nuitka_dist.py:53-71 _REQUIRED_ASSETS` 只列 three-mmd 两包与其 license。npm（2026-09-26）：`trtc-sdk-v5` latest 5.20.1、`license: ISC`、`main: trtc.js`、unpacked 31.2 MB（含多套构建与插件，只取一份 UMD）；`livekit-client` 2.22.3、Apache-2.0、`main/unpkg: dist/livekit-client.umd.js`、unpacked 12.4 MB（含 map，UMD 估算 <1 MB）。
- 改成什么: 两份 UMD 入库 + 两份 LICENSE 进 `static/libs/licenses/`；`THIRD_PARTY_NOTICES.md` 各加一节（版本钉死、来源 URL、本地改动 = 无）；`_REQUIRED_ASSETS` 追加 4 行；`templates/index.html` **不**引用，`visit_transport.html` 也不静态引用——iframe 脚本在能力门通过后按后端下发的 `transport` 动态插一份 `<script>`。
- 回归风险: 首屏零变化（只在串门时、只在 iframe 内加载）；包体 +2~3 MB（估算）；SDK 大版本更新要手动搬。许可：ISC / Apache-2.0 与仓库 Apache-2.0 兼容（设计 1 担心的「腾讯商业条款」按 npm 元数据不成立；实施时仍以包内 LICENSE 文件为准复核一次，不一致则改运行时 CDN 加载并记录供应链面）。
- 收益: 离线包一致；不依赖 vendor CDN 可用性与版本漂移；打包守卫抓漏；两家 SDK 互不加载。
- 推荐: 采纳。owner 已拍板（2026-09-30）。
- 备选: 运行时从 vendor CDN 加载（大陆网络不稳、引入第三方 origin、供应链面） | 静态 script（每个 Pet 页面都背 1~2 MB）
- 卡住: static/libs/*, THIRD_PARTY_NOTICES.md, static/libs/licenses/*, scripts/check_nuitka_dist.py, transport.js loader

#### OD-29 iframe ↔ 本机后端走独立 WebSocket /api/visit/transport/ws；凭证只在这条 socket 下发；断开 = 页面重载宽限 20 s 的触发源
- 现状: display socket `@router.websocket("/ws/{lanlan_name}")`（`main_routers/websocket_router.py:503`）accept 后最新连接顶替 `session_id`（`:547-556`）；二进制分支只认 `NEKO`（`:59`，`:789-800` 其它抛 ValueError 丢弃）。已有非 `/ws/{name}` 的 WS 先例 `WS /api/vmc/ws`（`main_routers/vmc_router.py:11`，CSRF 校验）。URL 无末尾斜杠约定（`websocket_router.py:24-28`）。display socket 断开已有「按路由 finalize」先例（`:1448 finalize_icebreaker_route`），但 display socket 会因 Chat 窗竞争被顶替，不是 vendor 会话消失的可靠信号。
- 改成什么: 新增 `main_routers/visit_router/transport_ws.py`：`@router.websocket("/transport/ws")`（挂在 `APIRouter(prefix='/api/visit')` 下，对外 URL `/api/visit/transport/ws`），query `visit_id, side`，同 `/api/vmc/ws` 的本机 Origin / CSRF 校验。上行：`caps` 分两段（OD-27 能力门拆两段，§4.3）——`caps{stage:'preflight', preflight_ok, reason?:'insecure_context'|'foreign_websocket'|'no_webrtc'}`（领凭证前）与 `caps{stage:'sdk', transport_ok, video_ok, reason?:'sdk_unsupported'|'sdk_load_failed', codecs}`（收到凭证后）、`state{joining|joined|reconnecting|connected|left|kicked|error, peer_present, remote_video, error_code?}`、`recv{from_vid, cmd, payload}`（已重组）、`stats{tx_fps, tx_kbps, rtt}`、`tx_backpressure`；下行：`credentials{visit_id, side, transport, vendor{…}, own_vid, peer_vid?（guest 侧必填，host 侧 null）, tier, crop, codec_pref?}`、`media{publish, subscribe, crop?, ladder?, peer_crop?, peer_vid?}`、`send{cmd:1|2|3, payload}`（iframe 负责分片 / 信封）、`stop{reason}`。发布/订阅/阶梯时机只由 `media` 驱动；host 侧 `peer_vid` 在对端 hello 核验通过后经 `media{peer_vid}` 补齐。JSON 文本帧 ≤16 KB。**该 socket 断 = iframe 消失 → 后端保留状态 20 s（`VISIT_LOCAL_PAGE_GRACE_S`，OD-11 v2 第 11~13 条），不挂 display socket**。
- 回归风险: 零（新端点新文件）。`websocket_router.py` 因此**不再需要** v1 的 NKVF 三处改动，只剩注册表（`:51/:765/:949/:1048`）与 goodbye 分支。
- 收益: 不与「最新 socket 赢」纠缠；凭证与控制流不经父页、不经 preload、不扇出 Chat 窗、不被 console.log；父页 `app-websocket.js` Blob 分支（`:3058-3066`）逐字节不动；页面重载宽限有唯一触发源。
- 推荐: 采纳。owner 已拍板（2026-09-30）。
- 备选: iframe 经 postMessage 让父页代发到 display socket（多两跳、字符串消息被 preload 扇出） | 设计 1 的 NKVC 二进制帧（要改两条热路径） | 宽限挂 display socket :1448（会被 Chat 窗顶替误触发）
- 卡住: transport_ws.py, static/visit/transport/backend-ws.js, test_visit_transport_ws.py

#### OD-30 文本 / 控制走 vendor 数据通道 + 后端 VisitOutbox 可靠层 + Lamport 定序（无 host 定序、无中继 order）
- 现状: 官方文档 2026-09-26 复核：TRTC `sendCustomMessage({cmdId:1..10, data:ArrayBuffer})` 每次 ≤1 KB、≤30 次/s、≤8 KB/s，「按序、尽力可靠，极差网络可能丢」，全房广播，须 `enterRoom` 后、无需发布媒体，收 `CUSTOM_MESSAGE{userId, cmdId, seq, data}`；**超限时是 reject 还是静默丢文档未写**；LiveKit `publishData(bytes, {reliable:true, destinationIdentities, topic})` reliable = 有序 + 重传、≤15 KiB，但「server does not buffer, limited retransmissions」；lossy ≤1300 B。v1 依赖自建中继保证文本不丢、有序、盖 `order`；v2 没有中继。原子追加先例 `utils/event_logger.py:263-264`（不 fsync）。
- 改成什么: (1) **iframe = 无状态转发器**：分片 / 重组、盖发送者 vendor id、按 cmd / topic 分流、丢非当前 `visit_id`、丢 `from_vid ≠ 已验证 peer vid` 的消息并计数；hello 验证通过前只接受 hello。(2) **消息集合（最终）**：cmd 1 ctl = `hello, ready, ack, hb, state, consent, wrap_up, leave`；cmd 2 text = `line_delta, text, line_abort`；cmd 3 lossy = `typing, stats`；LiveKit topic `visit.ctl / visit.text / visit.lossy`，cmd 1/2 reliable、cmd 3 lossy；心跳统一 `hb{t:'hb', lp_seen}` 每 5 s（删 `ping`）。(3) **分片信封（TRTC）** `{v:1, r:<visit_id 前 8>, m:<msg_id u32>, i, n, p}`，**每片总长 ≤1000 B（按字节，不是 1024）**，内层 JSON 转义膨胀已算进去：`txt` 上限 800 B 是为此留的余量；接收按 `(from_vid, m)` 重组，2 s 未齐丢整条（`text` 靠 outbox 重传）；单测断言「最长合法 `text` 分片后每片 ≤1000 B」。(4) **全序与陈旧 = Lamport**（OD-08 v2）：每条消息带 `lp`，`next_lp = max(own, max_seen) + 1`，在一行第一片发出时分配并贯穿该行；平局 host < guest；`reply_to`（`rt`）定陈旧与打断；开场 `rt==""` 两句并存豁免；撞车 guest 让一次。**删除** host 分配 `order` / `ack{seq, order, stale}`；`order` 字段整体从协议删除（不再有中继）。(5) **可靠单元 = `text{final}` 全文必达**（OD-21 v2）：`line_delta` / `line_abort` 可丢、不进 outbox；一行永远以一条 `text` 收口，被打断的行 `truncated:true`、`txt` = 已开口分句拼接。(6) **可靠层 = 后端 `VisitOutbox`**（每侧一个）：必达消息（`hello / ready / consent / wrap_up / leave / text`）带单调 `seq`，累计 `ack{seq}`（cmd 1，只推进到连续落地的最大序号）；未 ack 按 1→2→4→8→8 s 重传、之后每 8 s 继续，必达项 30 s 未确认 → `finalize('delivery_failed')`；接收侧 `ln` / `seq` 幂等 LRU(512)，重复只回 ack；`leave` 一次重传后不等 ack；outbox 落 `<config_dir>/visit_spool/<visit_id>.outbox.jsonl`（与 spool 同目录同 helper，原子追加 + fsync 新增），用途 = 页面重载 20 s 内重发与举报证据 / 诊断，**后端重启不回放**（OD-11 v2：这场结束）。(7) **限速**（后端出站队列）：总字节令牌桶 5 KB/s（40 kbps，与 560 kbps 视频合计 600，远低于标清 900 kbps 跳档线）；条数桶 20 条/s（桶 10）；同行 delta 最小间隔 250 ms 合并；文本重传只在桶有余量时发；超限**排队不丢**；桶满 → 先停 `typing` / `stats`、再停 delta，只保 `text` / ctl。最坏速率算式见 §4.1 / OD-21 v2（条数 4 + 5 + 2 + 1 + 0.2 + 0.2 ≈ 12.4 条/s ≤ 20（桶）≤ 30（TRTC）；字节纸面 ≈8.4 KB/s、真实峰值 ≈2.8 KB/s，桶钳 5 KB/s < 8 KB/s）。(8) **版本偏斜规则**：`hello.caps.proto` 主版本不同 → `leave{reason:'proto_mismatch'}` + 8 语 toast「对方版本不兼容，请双方更新」；未知 `t` 一律忽略并计数（恢复 v1 规则）；未知字段忽略；**不再**把 >1000 B / `i` 跳变 / 两行交叠判成 `peer_protocol_violation` 直接 finalize——改为丢弃该消息并计数，连续 20 条异常才 finalize；`lp` 回退 >1000 同样只丢弃计数。(9) 落点：`utils/visit_wire.py`（信封 + 消息 pydantic / zod 对偶 schema，两侧示例报文同过一个 schema）、`main_logic/visit/outbox.py`、`main_logic/visit/room.py`（Lamport / reply_to）、`static/visit/transport/transport.js` 分片。
- 回归风险: 零既有路径。产品面：vendor 会话断则文本视频同断（OD-11 v2 统一处理）；TRTC 8 KB/s 顶到时字幕中间态可能停顿，`text` 不受影响；A/B 版本偏斜时新消息类型被老端忽略（功能降级不踢人）。TRTC 超限行为未文档化 → T6 加「1 s 内连发 40 条 100 B，记录 reject / 静默丢 / 到达数」。
- 收益: 不自建任何服务器；两 vendor 可靠性差异被 outbox 抹平；零额外排序消息、无等待、两种传输一份代码；页面刷新 / SDK 重连不丢不重；无中继状态机；协议对未知消息宽容。
- 推荐: 采纳。owner 已拍板（2026-09-30）。
- 备选: host 定序 order（每行一个来回、host 掉线无序、不对称，已驳回） | Servers 提供文本 WS 中继（设计 3 (b)；Servers 成在飞硬依赖） | 自建文本中继 VM（owner 已否自建） | 只信 vendor（TRTC 尽力交付会丢句） | d4 的 line{n,h} + line_req 补洞（三套机制，见 OD-21 v2 备选）
- 卡住: utils/visit_wire.py, main_logic/visit/outbox.py, room.py, transport.js 分片, tests/unit/test_visit_wire.py（分片 ≤1000 B / 未知 t 忽略 / 20 条异常才 finalize / outbox 重传时序）

#### OD-31 v3 串门自建共享记忆客户端 memory/scoped_client.py（直接对 memory_server 五个 /internal/memory/* 端点）；bot 公共记忆组件的形态待 owner 与 QQ 插件作者商量后另定
- 现状: QQ 自动回复插件已于 2026-09-28 移出仓库（#2996，`3618e75fe`：`plugin/plugins/qq_auto_reply/` 整目录删除）。v2 引用的五个 scoped 方法（`b0b283e34` 版 `plugin/plugins/qq_auto_reply/memory_bridge.py`：`fetch_scoped_bootstrap_memory / post_scoped_mentions / post_scoped_forget / post_scoped_memory_history / post_scoped_memory_history_batch`）已不在仓库；main 上除 memory_server 自身外，没有任何 scoped 记忆端点的客户端。memory_server 的五个 `/internal/memory/*` 端点仍在。包分层门 utils L1 < memory L2（`scripts/check_module_layering.py`）；`main_routers`（L3）可以 import `memory/`。
- 改成什么: 新建 `memory/scoped_client.py::ScopedMemoryClient(base_url)`，直接对五个端点实现 `fetch_bootstrap / post_mentions / post_forget / post_history / post_history_batch`（wire 形状以 memory_server 路由的请求模型为准，以 `b0b283e34` 版 QQ 实现作对照，带「wire 请求体快照」单测）；串门的 `main_logic/visit/memory_bridge.py` 直接用它。**不等外部商量**（owner 2026-09-30）：作为独立小 PR（PR-05）先合。是否经插件 SDK 把它开放成「bot 公共记忆组件」、接口长什么样，由 owner 与 QQ 插件作者商量后另开 PR；届时本客户端可作底座，也可被替换。
- 回归风险: 零：只新增文件。若商量出的接口形状不同，串门侧要跟着改一次（只影响 `main_logic/visit/memory_bridge.py` 一处调用面）。
- 收益: 串门不被外部商量卡住；仓库内第一个 scoped 记忆客户端，以后内置功能不必各写一遍。
- 推荐: 独立 PR 先行（只新增）。owner 已拍板（2026-09-30）。
- 备选: 等公共记忆组件商量结果再做（记忆相关 PR-05 / 08 / 15 顺延） | 串门 PR 里内联五个方法（日后分叉）
- 卡住: memory/scoped_client.py, tests/unit/test_scoped_client_wire.py

## 3. 总体架构

以下正文按 §2 拍板清单的推荐项展开。引用形如 `文件:行号` 的位置以 Xiao8 main `fd2df860e`（2026-09-30 定稿时按 `b0b283e34` 的原引用逐条比对刷新；QQ 插件相关引用仍以 `b0b283e34` 为准，因插件已移出仓库）与闭源壳 lanlan_frd `f9424d7`（package.json 0.9.0）为准；vendor 事实以 `scratchpad/v2/research/*.md` 为底并经官方文档 / npm registry / GitHub 源码复核（URL 标在句尾）。标「估算」的数字没有实测。称呼一律「亲人 / 用户」。

### 3.0 一页总览

#### 3.0.1 原本是什么样
- 仓库没有任何猫娘↔猫娘通路、没有 visitor/guest 概念。每角色一个 `LLMSessionManager`；文本进 LLM 只有 `stream_data`（`main_logic/core/streaming.py:203`）；外部实体让本机猫娘回答只有 agent callback（`main_logic/core/proactive.py:2118`）；不经 LLM 直推原话只有 mirror 通道（`main_logic/core/turn.py:1797 / :1862 / :2096`）。
- 看板娘只在本机 PIXI 7.4.3 渲染于 `#live2d-canvas`（`static/live2d/live2d-core.js:321-346`，`transparent:true, backgroundAlpha:0`，未开 `preserveDrawingBuffer`）；renderer 会 emit `postrender`（`static/libs/pixi.min.js:529`），仓库零监听者；仓库零 `captureStream` / WebCodecs / MediaRecorder。读像素的唯一先例是同任务内 `renderer.render(stage)` 再 `drawImage(sourceCanvas, 源矩形)`（`static/avatar/avatar-portrait.js:1385-1387`、`:1926-1936`）；`makeUpperBodyRect`（`:509-521`）是未被调用的死代码。
- display socket `/ws/{name}`（`main_routers/websocket_router.py:503`）accept 后最新连接顶替 `session_id`（`:547-556`）；二进制分支只认 `NEKO`（`:59`、`:789-800`）。闭源 preload 把**每一个** `new WebSocket` 都包成 `PetWebSocket` 并置 `_activeWs`，不看 URL（`lanlan_frd/src/preload/bridges/pet-websocket-bridge.js:333-346`；`:464` 安装、`:818-821` 卸载）；字符串消息逐条 `console.log` 并 IPC 扇出 Chat 窗（`:426-431`）。Pet 窗 `webPreferences` = preload / `sandbox:false` / `contextIsolation:false` / `nodeIntegration:false` / `webSecurity:true` / `backgroundThrottling:false`（`src/window-manager.js:1001-1011`），全仓无 `nodeIntegrationInSubFrames` → **子 frame 没有 preload**，拿到原生 `WebSocket` / `RTCPeerConnection`。
- 记忆隔离原语 `MemorySubject(kind, subject_id, scope)`（`memory/scopes.py:31-38` kind 闭集；`:120-150` 三个构造器 `group_chat / participant / group_participant`）；主进程没有 scoped 记忆客户端——五个 `/internal/memory/*` 薄封装曾住在 QQ 插件里（`plugin/plugins/qq_auto_reply/memory_bridge.py:109 / :137 / :155 / :303 / :498`，`b0b283e34`，现已移出仓库：QQ 插件于 2026-09-28 经 #2996 移出），main 上除 memory_server 自身外没有任何客户端；legacy `/cache /process /settle /new_dialog` 全是私聊语料。
- 唯一「外部控制器接管角色」蓝图是 game route：router 层劫持（`websocket_router.py:51 / :765 / :949-968 / :1048-1052`）、takeover 旗（`main_logic/core/manager.py:277-280`）、隔离 `OmniOfflineClient` 池（`main_routers/game_router/session_pool.py:313-320`）、mirror 元数据不入普通记忆（`main_logic/mirror_meta.py:84-108`，无键时 `return not has_user_input`）。但 `game_route_start`（`game_router/runtime.py:1899`）只 finalize 其它 game 路由（`:2008-2036`）后**无条件**写 `mgr._takeover_active=True`（`:2066-2076`），`postgame.py:1277-1278` `/route/end` **无条件**置 False——takeover 今天没有归属概念。main 上它已是三个属性（`manager.py:277-283`：`_takeover_active / _takeover_input_dispatcher / _takeover_callback_sink`），写入点有三处：`game_route_start` 置位（一起看 / 你画我猜另挂 `LiveInbox` 作 callback sink）、`_start_watch_speech_takeover`（`runtime.py:1877-1895`）失败回滚、`postgame.py` 释放后交还 inbox；takeover 期间主动搭话被拒、插件 respond 回调交给 sink 扣住（`proactive.py:393 / :398 / :2994 / :2158-2176`）。详见 §5 总则 2a / 2b。
- 角色卡在 `character_runtime.py:1904-1907` 构造 manager 时已把 `{MASTER_NAME}` 替换成亲人真名；未替换的原始模板在 `utils/config_manager/characters.py:219-225 lanlan_prompt_map`。
- 云端可验身份只有 OAuth：`_desktop_session_snapshot()` 给 `local_user_id / access_token / client_id`（`main_routers/card_drop_router.py:585-600`），`local_user_id` 被钉成 UUID（`:571-577`）；社区基址 `https://community.project-neko.cn`（`card_drop_router.py:41`、`utils/social_base.py:12`）；平台 token 出本机只发给 Servers 自己（`:999-1006`）。仓库没有任何 Ed25519 校验代码；`cryptography>=45.0.6` 是直接依赖（`pyproject.toml:62`）。
- 区域裁决只读 `ConfigManager._region_cache`（`utils/config_manager/core_config.py:40-59` 不变量），`aensure_region_resolved(timeout=1.5)`（`:529`）；串门路径**绝不**调 `_check_non_mainland()`（`:668`）。
- 关机链：Electron `requestAppQuit` **先** `destroyAllWindows()` 再 `beginOwnedBackendShutdown()` → `POST /api/runtime/shutdown`（`lanlan_frd/src/main/backend-runtime.js:2483-2490`、`:1666-1684` timeout 3 s）→ `app/main_server/__init__.py:1233-1275 on_shutdown`。后端钩子跑的时候 Pet 页与任何 iframe 已经不存在。

#### 3.0.2 核心 tradeoff（一句话）
用「同源 iframe 这一层间接」换「不碰闭源壳、单机零版本偏斜、vendor SDK 不进主页面热路径」；用「vendor 数据通道 + 两侧后端 `VisitOutbox`」换「零自建服务器、Servers 只在领凭证与结束后上传转录（OD-26 v3）时被依赖、在飞串门不依赖任何我方长连接」；用「Lamport `lp` + 侧位平局」换「无中继也有确定全序、零额外往返」。代价：两个文档、一次同步跨 realm 调用、一个新的本机 WS 端点；视频与文本同一失败域（vendor 会话断则同断，由 OD-11 v2 统一处理）；转录没有第三方盖章（举报证据链 = 双侧 outbox / spool JSONL + 每场双方各自上传 Servers 的转录（OD-26 v3）+ Servers `POST /api/visit/reports`）；对端只有在 30 s 心跳判死后才知道你关机了（3.2 (g)）。

#### 3.0.3 一句话架构
A（guest）与 B（host）各自的本机后端在 Pet 页 iframe 过能力门之后向 N.E.K.O. Servers 领「vendor 入房凭证 + Ed25519 身份票」（host 领时登记房间并拿到一次性 `invite_code`，guest 领时必须带它）；两侧 Pet 页内嵌的同源隐藏 iframe `/visit/transport` 拿凭证进同一个 TRTC 房（大陆）或 LiveKit 房（海外，上线期 LiveKit Cloud Ship，月 >≈2,500 房·小时后切 GCP 自建）；A 的父页每帧 `postrender` 后**同步**喊 iframe 抓一次上半身裁剪区，iframe 把颜色与 alpha 上下叠成一张 320×896 不透明小图经 WebRTC 视频轨发出（560 kbps、真 30 fps），B 的 iframe 用 shader 拆回带 alpha 的画面叠在透明 Pet 窗上；文本与控制走同一房的数据通道——分句 `line_delta` 可丢只上屏，每行以一条必达 `text{final}` 收口，可靠性由两侧后端 `VisitOutbox`（seq / 累计 ack / 重传 / 幂等）兜底，全序与陈旧判定用 Lamport `lp` + `reply_to`；两侧各起一个与主会话隔离的 `OmniOfflineClient`，主 manager 整场持 takeover 令牌静音；每句立刻追加本地崩溃安全 spool，结束时 digest 一次进串门记忆区（`group_chat / group_participant / participant`，主键 `visit_uid`），永不进私聊记忆；回家后她简述一句并问亲人「要记成日记吗」（两个芯片：记成日记 / 不记）。

---

### 3.1 组件与部署拓扑

```
A 的 PC（guest）                                        vendor（托管）                        B 的 PC（host）
Pet 窗 index.html（主文档，有 preload）                                                       Pet 窗 index.html
 static/visit/parent-bridge.js：postrender 钩子 → 同步调 iframe                              parent-bridge.js：摆 iframe、状态
 .visiting-away 徽标 / 访客 tool 气泡 / debrief 芯片                                          tool 气泡 / composer 收件人 / 接待确认
   │ /ws/{A}（display socket，只走 JSON：visit_line* / visit_state_change / chat_blocks）        │ /ws/{B}
 ┌─ <iframe /visit/transport?side=guest>（无 preload，原生 WebSocket）──┐   视频轨 560 kbps  ┌─ <iframe side=host>（即访客图层）────────┐
 │ scratch 320×448 → pack 320×896 opaque → captureStream(0)+requestFrame │ ─────────────▶  │ <video hidden> → rVFC → 解包 shader（WebGL）│
 │ VisitTransport(trtc|livekit).publish(track) / sendData(cmd, bytes)   │ 数据通道 ≤40 kbps │ onRemoteTrack / onData                    │
 │ cmd 1 ctl / cmd 2 text / cmd 3 lossy（分片信封 ≤1000 B/片）           │ ◀────────────▶  │ 同                                        │
 │ ws://{location.host}/api/visit/transport/ws（独立 WS，凭证只走这条）    │                 │ 同                                        │
 └─────────────────────────────────────────────────────────────────────┘                 └───────────────────────────────────────────┘
A 后端 main_server                                                                            B 后端
 main_routers/visit_router：credentials / transport_ws / runtime / debrief / memory_routes      同
 main_logic/visit：identity（验票+黑名单）/ outbox（seq/ack/重传）/ room（Lamport lp + 收尾状态机）
                  / liveness（hb 5 s；30/25/20 s 三计时器）/ spool（逐句 JSONL）/ subjects / sanitize / consent / limits
 隔离 OmniOfflineClient（流式推 TTS；分句对齐 → line_delta → text{final}）                     隔离 OmniOfflineClient（同，对称）
 mgr_A：takeover token('neko_visit')                                                          mgr_B：takeover token
 memory_server(A) ◀ memory/scoped_client.py（自建，直连五个 /internal/memory/*）                memory_server(B)
   ▲ POST {social_base}/api/visit/credentials（OAuth Bearer + X-Client-Id）
     ── N.E.K.O. Servers（闭源）：核验社区账号 → 查封禁 → 登记房间/invite_code → 按 host 区域选 transport
        → 签 vendor 凭证（UserSig / JWT，TTL guest 40 / host 50 min）+ Ed25519 身份票（sub = visit_uid）；GET /api/visit/pubkeys；POST /api/visit/reports
        → POST /api/visit/transcripts（每场各传本侧转录 + 用量）；GET /api/visit/details/{visit_id}（查看详情）
托管侧：大陆 TRTC（腾讯托管，我方零服务器）；海外 LiveKit Cloud Ship（上线期）→ GCP 每区域 1 台 livekit-server（Caddy 443 复用 HTTPS + TURN/TLS）
```

统一命名（全文与 §4 一致）：
- `visit_id ≡ 房间 id`：host 后端 `secrets.token_urlsafe(16)`（22 字符，≤64 字节满足 TRTC `strRoomId`）。
- `side ∈ {host, guest}`；`role[0] ∈ {h, g}`。
- `visit_uid`：Servers 派发的**稳定不透明 id** `HMAC(server_secret, community_uuid)[:24]`，对所有对端相同、Servers 可反查；记忆、黑名单、名册、举报全部以它为主键；UI 只显示 display_name 与 6 位短码（OD-05 v2）。
- `vid`（vendor userId / LiveKit identity）= `role[0] + '_' + sha256(visit_uid|visit_id)[:24]`，26 字符，落在 TRTC `userId ≤32 字节 [a-zA-Z0-9_-]` 字符集内；vendor 侧看不到稳定账号 id。
- `pair_id = sha256(min(uid_a, uid_b)|max(uid_a, uid_b))[:24]`；`peer_char_id = 'c_' + sha256(peer_uid|char_tag)[:24]`。
- `ln`（line_id）= `'{h|g}:{行序}'`（行序是每侧独立的行计数器，与 outbox 必达序号 `seq` 互不替代），发送方生成；`lp` = Lamport 时间戳，在一行第一片发出时分配并贯穿该行；全序键 `(lp, side_rank)`，host=0、guest=1。**没有 `order` 字段**（不再有中继）。
- 档位只有 `sd600`（`hd1200 / fhd2400` 只留表项，`enabled=False`，OD-06 v2）。
- v1 每角色同一时刻只在一个房间（`mgr` 只有一个 takeover 槽；互访 = v1.5 同房双向视频，不是两个房间）。

---

### 3.2 端到端时序

骨架沿 v1 §3.2.1 的步骤号；与 v1 不同处用 **v2** 标出。发起 / 邀请的传递方式仍在范围外，但 `invite_code` 是协议字段（OD-01 v2）：从「B 已把 `{visit_id, invite_code}` 交给 A」开始。

#### 3.2.1 建房与入房（(a) 段）
1. **B 建房** `POST /api/visit/rooms {catgirl, crop?}`（过 `_validate_local_mutation_request`）。顺序固定：
   1. `activate_visit_route(B, phase='pending')` 建 state（`is_visit_route_active` 立即为 True，让 `websocket_router.py:949` 的 `on_start_session` 钩子与 `streaming.py:284` 的自动建会话门即刻生效）。
   2. 前置检查：`mgr._is_voice_session_active_or_starting()` / `get_active_external_route` 已被别的 kind 占 / `is_goodbye_silent()` / `is_hot_swap_imminent or _starting_session_count>0` → 任一为真 409。
   3. **v2 先建 iframe、过能力门，再领凭证**（裁决 D.4）：后端回 `visit_state_change{pending}` → 父页懒建 `<iframe id="visit-frame" class="transparent-overlay" src="/visit/transport?v={static_asset_version}&side=host&visit_id=…">` → iframe 连 `/api/visit/transport/ws` → 跑能力门 ①②（3.3.4，与 transport 无关的预检）→ `caps{stage:'preflight', preflight_ok, reason?}` 上报后端。`preflight_ok:false` → 后端不去 Servers 领凭证（**不消耗 Servers 配额、不占 takeover**；这条承诺只覆盖 ①② 这一段，③ 见第 7 步）：设置页预跑的 `caps` 缓存命中 false 时 `POST /api/visit/rooms` 同步 409 `VISIT_UNSUPPORTED_ON_THIS_MACHINE`；无缓存则 HTTP 已 202 返回，能力门失败经 `visit_state_change{ended, reason:'unsupported'}` + `status{VISIT_UNSUPPORTED_ON_THIS_MACHINE}` 通知（与 §4.6 一致），`finalize_visit_route_state`，父页移除 iframe。设置页「串门」分组打开时也可先跑一次同样的探测并缓存 `caps`，让按钮预先置灰。
   4. 若主会话 `session._is_responding`：`await mgr.session.handle_interruption()` 并等 turn end 落地（≤3 s，超时按 busy 拒绝）。
   5. `credentials.fetch_visit_credentials(role='host', visit_id, char_tag, region_hint, tier='sd600')`：`resolve_saved_oauth_status()`（`community_oauth.py:447`）→ `asyncio.to_thread(_desktop_session_snapshot)`（`card_drop_router.py:585`）→ `get_external_http_client().post({social_base}/api/visit/credentials)`（`utils/http/external_client.py:65`）。无会话 → 409 `VISIT_LOGIN_REQUIRED`；Servers 403 `blocked` → 409 `VISIT_BANNED`；403 `tier_not_entitled`；429 配额（每日签发分钟数 / 并发 ≤2 房）→ 409 `VISIT_QUOTA_EXCEEDED`；HTTP 失败 → 503 `servers_unreachable`。**v2** Servers 在此把 `visit_id` 登记到 host 的 `visit_uid` 下，返回 `{transport, expires_at, vendor{…}, identity_ticket, invite_code(10 min 一次性)}`。**任何失败 → `finalize_visit_route_state`，不占 takeover**（裁决 D.4 / OD-27：Servers 失败不占 takeover）。
   6. `mgr.acquire_takeover(owner='neko_visit', dispatcher=_visit_voice_dispatcher, callback_sink=visit_inbox.accept)` 拿令牌（OD-24；放在凭证成功之后，此后任何失败即 `release_takeover`），随即 `await mgr.interrupt_ordinary_speech_for_takeover()` 切掉普通语音，失败时照 `_start_watch_speech_takeover` 在锁内同步回滚；串门期间插件 respond 回调停在 `VisitInbox`，finalize 时先释放令牌，等仪式句与 debrief 简述播完（或 `VISIT_INBOX_HANDOFF_MAX_S=20` 硬顶）后再重投（第 22 条、§5 总则 2b）。
   7. 后端经 transport WS 下发 `credentials{transport, vendor, own_vid, peer_vid:null, tier, crop, publish{…}, expires_at}`（host 侧领凭证时 guest 尚不存在，`peer_vid` 为 null，对端 `hello` 核验通过后经 `media{peer_vid}` 补齐；guest 侧 `peer_vid` 必填；`credentials` **不带** `publish_video`——发布 / 订阅 / 阶梯时机只由独立下行 `media{publish, subscribe, crop?, ladder?, peer_crop?, peer_vid?}` 驱动，§4.3；**凭证只走这条 socket**，不进父页、不进 display socket、不进日志）→ iframe 按 `transport` 动态插一份 `<script>`（`/static/libs/trtc.js` 或 `/static/libs/livekit-client.umd.js`）→ 能力门 ③（`TRTC.isSupported()` / `RTCRtpSender.getCapabilities('video')`）→ `caps{stage:'sdk', transport_ok, video_ok, reason?, codecs[]}` → `enterRoom / room.connect` → `state{joined}`。③ 失败（`transport_ok:false`：SDK 加载失败 / 不受支持）→ `finalize('unsupported')` + `release_takeover(token)` + `status{VISIT_UNSUPPORTED_ON_THIS_MACHINE}`；**此时 Servers 已计一次签发**（每日签发分钟数已扣，3.5.8），这是能力门拆两段后唯一耗配额的失败分支；③ 只视频子项失败 → `video_ok:false`，串门照常（3.3.4）。
   任何失败分支：`finalize_visit_route_state` + 前端 `visit_state_change{ended, reason}`；`release_takeover(token)` 只对凭证成功之后（第 6 步起）的失败执行。
2. **A 入房** `POST /api/visit/rooms/{visit_id}/join {catgirl, invite_code, confirm:true}`。`confirm` 来自 A 前端「让她出门去 X 家？」对话框（显示对端 display_name + 6 位短码 + 跨区提示；**不显示 token / TTS 次数等技术数字**，OD-26 v3）。确认框的数据来源：A 前端先调本机只读代理 `GET /api/visit/invites/{invite_code}/preview`（后端代转 Servers 同名端点，OAuth Bearer 不出前端；**不消耗邀请码**，§4.6 / §4.7）拿 `{visit_id, host_display_name, host_short_code, cross_region, expires_at}`，再弹确认框；预览 404 `invite_invalid` / 410 `invite_expired` / 403 `visit_banned` → 不弹确认框、直接给对应文案。点确认后同样先占位 → iframe → 能力门 ①② → 领凭证（`role='guest'`，带 `invite_code`）→ 能力门 ③（同第 1 条第 7 步）。**v2** Servers 校验 `invite_code` 后把 guest 绑到该房；两侧区域不同 → **403 `cross_region_unsupported`**（默认 fail-closed，裁决 D.2；Servers 侧开关可改为「允许 + 警告」，待 T9 后由 owner 决定）→ 本机 409 `VISIT_CROSS_REGION_UNSUPPORTED`，确认框直接说明。邀请里不携带任何 URL；LiveKit URL 来自 Servers 且主机名必须命中 `VISIT_LIVEKIT_HOSTS`。
3. **入房后凭证 TTL**（按侧位）：guest 的 vendor 凭证与身份票同为 **40 min**（`exp = iat + 2400`；TRTC `expire=2400`、LiveKit `ttl=40m`），覆盖硬顶 30 min + 重连 ≤25 s；host 的为 **50 min**（`VISIT_HOST_CREDENTIAL_TTL_S = VISIT_INVITE_WAIT_S(600) + VISIT_MAX_DURATION_S(1800) + 余量 600 = 3000`；`expire=3000`、`ttl=50m`）——host 领凭证后最长先等 10 min 对端入房再串 30 min，40 min 不够；串门中途**不依赖 Servers**。

#### 3.2.2 身份互验与接待确认（(b) 段）
4. 对方入房事件（TRTC `REMOTE_USER_ENTER` / LiveKit `ParticipantConnected`）→ iframe `state{peer_present:true}` → 各自后端经数据通道发**首包** `hello{ticket, caps{video, tier, proto:1, app_version(major.minor)}, lang}`（cmd 1，必达）。
5. 接收侧 `identity.verify_identity_ticket(ticket, *, expect_visit_id, expect_role, expect_vid, now, pubkeys, blocklist, jti_window)`，**顺序固定**：验签（`kid` 查 `config/visit_settings.py::VISIT_SERVERS_PUBKEYS`，不命中则拉 `GET /api/visit/pubkeys`，拉不到 → fail closed）→ `aud=='neko-visit'` / `visit_id==本房` / `role==对侧` / `exp`（时钟容差 ±300 s，对偶 `local_server/telemetry_server/security.py:38`）→ **`vid == vendor 盖的发送者 id`**（TRTC `CUSTOM_MESSAGE.userId` / LiveKit `participant.identity`）→ `sub ∉ visit_blocklist.json`。任一步失败 → `leave{reason:'peer_identity_rejected'}`（黑名单命中用 `peer_blocked`，对端只看到「离开」）+ `finalize`。`hello.caps.proto` 主版本不同 → `leave{reason:'proto_mismatch'}` + 8 语 toast「对方版本不兼容，请双方更新」。同房同 `vid` 重连允许重放同一 `jti`。
6. **核验通过前**：不订阅视频、不接受 `text`、host 不弹接待确认；`from_vid ≠ 已验证 peer vid` 的消息一律丢弃并计数；`REMOTE_USER_ENTER` 出现第二个未知 `vid` → `leave{peer_protocol_violation}`。**核验通过后、host 发 `ready` 之前**（`awaiting_accept`，最长 60 s）：接收侧**只放行握手与 `consent`**（`hello / ready / consent / leave / hb / ack`），`text / line_delta / line_abort / typing / wrap_up` 一律丢弃并计数——`text` 仍回 `ack`（免得对端重传到 `delivery_failed`），但不上屏、不入史、不进 spool、不触发回复；guest 同理在收到 `ready` 之前不发 `text`（也不开第一轮 LLM）。亲人还没点「接待」，对方的台词不该先进家门。
7. host 前端「{访客}来串门，接待吗？」（60 s）→ `POST /api/visit/rooms/{visit_id}/accept {accept}` → true 发 `ready`（cmd 1，必达），false 发 `leave{declined}`；guest 收到 `ready` 即进 active（不回 `ready`；`ready` 只由 host 发、每场 1 条，其 `memory` 字段兼任 host 的初始 consent，§4.2）。
8. 两侧 `activate_visit` 收尾：建隔离 `OmniOfflineClient`（`tool_definitions=[]`、`max_response_length=VISIT_RESPONSE_MAX_TOKENS`、`master_name=FAMILY_NEUTRAL_TERM`；指令 `build_visit_instructions(...)` 从**原始** `lanlan_prompt_map[name]` 构造，含 `memory/scoped_client` 拉的串门区 bootstrap ≤2000 tok）→ `mgr._park_proactive_for_goodbye()`（`proactive.py:82`）→ `visit_state_change{started}`（两侧 `stopProactiveChatSchedule`）→ 若 `visitMemoryEnabled`：`VisitSpool.open(visit_id)` 写头行。初始 consent：guest 在 hello 核验通过后发一条 `consent{memory:visitMemoryEnabled, scope:'session'}`（**不以** `visitMemoryEnabled` 为发送条件，false 也发）；host 的初始 consent 由 `ready.memory` 兼任，不另发（§4.2）。

#### 3.2.3 视频（(c) 段）
9. **v2 guest 只在收到 host `ready` 之后才 `publish(track)`**（OD-26 v3「双方点头」在媒体层也严格）；LiveKit 侧 `autoSubscribe:false`，host 在 `ready` 后 `setSubscribed(true)`。
10. A 父页：若 `0 < window.targetFrameRate < 30` 则 `savedFps = window.targetFrameRate; live2dManager.setTargetFPS(30)`（`live2d-core.js:752-760` 会改写 `window.targetFrameRate`，结束时恢复）；`_hasRenderActivity()`（`:1029-1044`）加一行 `if (this._visitCaptureActive) return true;`；挂 `pixi_app.renderer.on('postrender', fn)`。`fn` 内两道守卫 + 分数累加器采样（3.3.5）→ 同步调 `iframe.contentWindow.__nekoVisitFrameSink.onFrame(canvas, rectPx, now)` → iframe 打包（3.4.2）→ `packTrack.requestFrame()`。
11. A iframe 发布参数：TRTC `startLocalVideo({publish:true, option:{videoTrack, profile:{width:320, height:896, frameRate:30, bitrate:560}}})`（上半身；`width/height` 取当前构图的打包尺寸，全身为 256×1120，3.4.2）；LiveKit `publishTrack(track, {source:Camera, simulcast:false, videoCodec:'vp9', scalabilityMode:'L1T1', videoEncoding:{maxBitrate:560_000, maxFramerate:30}, degradationPreference:'maintain-framerate'})`（3.4.3）。
12. B iframe：TRTC `REMOTE_VIDEO_AVAILABLE{userId===peer vid}` → `startRemoteVideo({userId, streamType:STREAM_TYPE_MAIN, view:null})` + `getVideoTrack` / `TRACK` 事件；LiveKit `TrackSubscribed` → `track.mediaStreamTrack` → 隐藏 `<video muted playsinline>` → `requestVideoFrameCallback` 每帧一次 `texImage2D` → 解包 shader 上屏（3.4.5）；首个 rVFC 后 `toBlob` 96×96 经 postMessage 给父页作访客 tool 气泡头像（OD-19）。

#### 3.2.4 每一句（(d) 段，两侧对称）
13. `VisitRoom.on_incoming_start / on_incoming_done` 判定我方是收件人 → `ReplyPlan{reply_to, not_before = now + tail_ms/1000 + U(1.0, 2.5)}` → `_reply_task`：到点复查 `is_stale` → `async with _llm_turn_lock:` HumanMessage = 发言人头 + nonce 信封 + `clamp_peer_line(text)` → `wait_for(session.stream_text, 20 s)`；期间本地 `visit_typing{on}` 并发 `typing{lp}`（cmd 3，每行 ≤1 条）。
14. **出话流式（v3，OD-15 v3 / OD-21 v3）**：分配 `lp = max(own, max_lp_seen)+1`、`ln`，一行一个 speech_id。`stream_text` 的 `on_text_delta` 每收到一段增量：语音开（`visitVoiceEnabled=true`）时经 `mgr.open_mirror_speech_stream(metadata=build_mirror_meta(source='neko_visit', kind='visit_line', ...), request_id=ln)` 得到的 `MirrorSpeechStream.push(delta)` 边生成边推本地 TTS（复用主聊天 `_enqueue_tts_text_chunk`，行尾 `finish()` → `_request_tts_done_locked`；本地 TTS 输入不过出站清洗）；同时把增量追加进本行缓冲，`ClauseSplitter`（3.6.4）先对本行累积缓冲整体 `redact_outbound` 再切片（受保护词不跨片），切出的每个分片再过 `strip_emotion_tags → sanitize_relay_text`。放出：语音开时前端 `visit-pacer.js` 约 4 Hz 回报 `visit_speech_progress{speech_id, visit_id, played_ms, ended}`，后端在 `min(自开播经过时间, played_ms) ≥ Σ_{j<i} estimate_speech_ms(clause_j)` 时放出第 i 片 → `line_delta{ln, i, lp, txt}`（cmd 2，可丢；`i==0` 带 `sp, ad, rt, wu`）+ 本机 `visit_line_delta{self:true}`，`ended` 时剩余分片一次放出；首段推入后 4 s 无首个 `visit_speech_progress` → 本行切文本估时 + `status{VISIT_TTS_FALLBACK}`（每场一次）。语音关：后端定时器按 `estimate_speech_ms` 逐片放出。开播后 `VISIT_SPEECH_PROGRESS_STALL_S=3` 秒无新 progress 且未 `ended` → 剩余分片按估时续放，并以 `__audio_done__` + 剩余估时作硬上限（3.6.4「兜底」）。**末片放出后立即发 `text{final}`**（cmd 2，**必达**，全文 `clamp_text_utf8(4096)` + `truncated:false, i_done:n`；不再整行 `truncate_to_tokens(400)`）经 `VisitOutbox`；**同一步里**本侧也记账：`VisitSpool.append(from:'own_cat', …)`（盖两枚章）+ `VisitRoom.on_local_line_done(ref, truncated)`（正常收口、人类打断截断、收尾掐断——所有发出 `text{final}` 的路径统一经 `_commit_local_line(ref, txt, truncated)` 做 spool 追加、房间计数与上传转录，截断行按已放出前缀同样记账）（本侧 40 句 / 每分钟 6 句计数与收尾判定靠它）+ 记进上传用转录；被截断的行按已放出前缀同样记录。
15. **v2 接收侧**：`line_delta` 按 `i` 落位只上屏（缺片留 `…` 占位，不补洞）；`text{final}` 到达 → 以全文**覆盖**气泡、`append(HumanMessage)` 入隔离会话历史（addressee 是我 → 回 13；否则纯入史 + `trim_visit_history(40)`）、`VisitSpool.append`（盖两枚章）、计数进 `VisitRoom`；回累计 `ack{seq}`（cmd 1）。`ln` / `seq` LRU(512) 幂等，重复只回 ack。**reader 回调不持路由锁、不直接调 finalize**——只 `create_task(finalize_visit_route(...))`。
16. **打断**（3.6.3 ④）：人类行（本地或对端 `sp:'h'`）开始时若本侧猫娘在说话 → **立即停**（`MirrorSpeechStream.abort()` → `interrupt_mirror_speech()`，不等分句边界，OD-15 v3）+ `line_abort{ln, lp, i_done, reason:'human_interrupt'}`（cmd 2，可丢，只让 UI 立刻截断）+ **随后必有** `text{final, truncated:true, txt=已放出分片拼接, i_done, trunc_reason}`；发送侧 `session_pool.pop_trailing_ai_message(session, expected=整行)` 弹出整行再 append 已开口前缀 + `VISIT_MARK_INTERRUPTED`。未开口的推理 → `_reply_task.cancel()`（`_lifecycle.py:836-838` 只翻标志；task 级 cancel 在 `_streaming.py:1825` 之前抛 CancelledError，半句不入史）。猫娘不打断猫娘；撞车 guest 让一次。

#### 3.2.5 B 的亲人插话（(e) 段）
17. B 前端：`I.isVisitChatActive()` 时 `handleComposerSubmit`（**输入框保留由前端负责**：`sendTextPayload` 会在后端回应前清空输入框，所以 `visit-chat.js` 在提交前按当前串门阶段先拦一道——`awaiting_accept` / `wrap_up` / `ending` 或 guest 侧时不发送、不清空、直接出对应 toast；后端拒绝仍保留作兜底，每次提交带客户端 `request_id`（`sendTextPayload` 透传），拒绝 `status{VISIT_INPUT_REFUSED_*, request_id}` 原样带回；前端只在 `request_id` 与仍待确认的那次提交相符、**且输入框自那次提交后未被编辑**时才恢复文字，同时把该 `request_id` 的本地「→ 访客」气泡标为「未送达」（`visit.input.notDelivered`），不留下看似已送达的发言） → 本地 user 气泡（author 加「→ 访客」）→ `appButtons.sendTextPayload(text, {source:'neko_visit:guest_cat'|'neko_visit:own_cat'})`。无会话时 `app-buttons.js:3104-3109` 先发 `start_session{text}`——visit 的 `on_start_session` 对 text **ack-only**（不建普通文本会话），对 audio 发 `VISIT_VOICE_UNAVAILABLE`。
18. B 后端 `websocket_router.py:1041` 先 `_stamp_user_input_ingress` / `_record_stream_engagement_ingress`（**保持原位**：亲人确实在电脑前），再 `:1048` 查注册表 → 串门 `route_stream_message`：`text` → `mgr.mirror_user_input(text, metadata=build_mirror_meta(source='neko_visit', kind='visit_human', session_id=visit_id, event={'memory_enabled': False}), send_to_frontend=False)` → `VisitRoom.on_local_human_line` → 清洗 → 直接一条 `text{final, sp:'h'}` 经 outbox（人类行不流式）→ `VisitSpool.append(from:'own_human')` → `_llm_turn_lock` 内 `append(HumanMessage)`（addressee=own_cat 时触发 13）→ 返回 True，router `continue`。`screen/camera/图片` 吞掉；`audio` 发 `VISIT_VOICE_UNAVAILABLE`。**WRAP_UP / ENDING 期间** → `status{VISIT_INPUT_REFUSED_WRAPUP}`（toast「她们正在道别，等一下」），文本留在 composer。
19. A 侧收到 `text{sp:'h'}` → `visit_line`（role tool，author=「{B 猫娘}的亲人」，显示名只取 hello 阶段 profile）→ 串门会话头「发言人：对方家的亲人；【这句是对你说的】」→ 13。

#### 3.2.6 收尾与回家（(f) 段）
20. **v2 一句话规则触发**（3.6.3 ⑤）：连续 6 句无人类插话，或本侧猫娘本场满 40 句 → host 发 `wrap_up{ph:'begin', reason, lp}`（cmd 1，必达；guest 只 `propose`，5 s 无回应自行开始告别）；A 家按「叫她回来」= guest `propose{reason:'recall'}`，host 不判条件立即 `begin`；`max_duration − 60 s` 同样进收尾。
21. guest 先说一句告别（独立 `stream_text`，`VISIT_SYSTEM_NOTICE_WRAP_UP_GUEST`，≤8 s 否则固定句；提示词要求 ≤40 字、最多两个分句），行带 `wu:true`；host 收到 guest 的 `text{wu:true}` 后送客一句；host 的告别行播完 + `tail_ms` 后发 `wrap_up{ph:'done'}` → guest `leave{reason:'home'}`、host `finalize('peer_left')`。**任一侧收到 `wu:true` 的行即进 WRAP_UP**（`begin` 丢了不卡死）。**v2 超时**：`VISIT_WRAP_UP_STEP_S=15` 从 `begin` 到对方告别行**第一片**到达；告别行本身按正常播放走完（≤400 tok 天然有界）；`begin` 时正在说的旧行（非 `wu:true`）从 `begin` 起 10 s 未说完 → 立即停（`MirrorSpeechStream.abort()`）+ `line_abort{reason:'wrap_up'}`；`VISIT_WRAP_UP_MAX_S=45` 硬顶无条件 finalize。
22. `finalize_visit_route(state, reason)`：
   1. **锁内只翻状态**（`_get_visit_route_lock`，毫秒级）：`visit_active=False; phase='ending'`；`_exit_task` 已存在直接返回（幂等，对偶 `postgame.py:1110-1183`）。
   2. `_finalize_impl` 锁外：`visit_state_change{ending}` → `leave{reason}`（尽力，一次重传后不等 ack）→ **立即** `mgr.release_takeover(token)`（亲人此刻就能说话）→ 仪式句（`reason ∈ {route_end, recall, wrap_up, max_duration}` 走 LLM ≤8 s；断线 / 切换 / 关机类用 `VISIT_FIXED_LINE` 8 语固定句）→ `visit_state_change{ended}` → 父页移除 iframe（iframe 先 `stopLocalVideo → exitRoom()` / `disconnect(true)`）。**`VisitInbox` 不在释放时交还**：扣住的插件回调若随释放立即重投，会抢在（或盖过）回家那句之前说话；所以交还延后到第 4 步之后（见第 4 步末）。
   3. `VisitSpool.finalize()`：fsync + `state.json{finalized:reason}`；**digest 一次**进串门记忆区（只吃两枚章都为真的句子；只受 `visitMemoryEnabled` 与对端 consent 控制，与 debrief 选择**无关**）。随后本机汇总本场用量 + 本侧转录，先原子写 `<visit_id>.upload.json`（`0o600`，与 `visitMemoryEnabled` 无关），再派生后台任务 → `POST {social_base}/api/visit/transcripts`（OD-26 v3，成功即删该文件，失败重试规则见 3.8）。
   4. **debrief**（OD-16 v3，3.7.4）：读 spool 生成 ≤200 tok 简述（隔离会话、`_llm_turn_lock` 内、8 s）→ 清洗 + `assert_no_peer_ngram(n=8)` → `mirror_assistant_speech(简述, metadata=build_mirror_meta(..., event={'memory_enabled': False}))`（不入私聊历史）→ `visitMemoryEnabled` 且 spool 有可 digest 句时 `render_chat_blocks` 两个芯片「记成日记 / 不记」→ `POST /api/visit/debrief/choice`。**不再调 `submit_proactive_callback`**，插件总线零串门文本。**交还 `VisitInbox`**：仪式句与 debrief 简述都已入 TTS 队列并播完——「播完」的判据：两者都是带 speech_id 的 mirror 语音，前端 `visit-pacer.js` 对它们同样回报 `visit_speech_progress{ended:true}`，以两个 speech_id 的 `ended` 都到齐为准；第 5 步 pop 路由状态**之前**，runtime 把这两个 speech_id 登记进一个与路由状态无关的小表 `_pending_inbox_handoff{visit_id: {speech_ids, deadline}}`，注册表的 `on_page_signal` 对这两个 speech_id 仍转给它（路由已 pop 也照转）；语音关（`visitVoiceEnabled=false`）时没有 TTS、不会和插件回调抢声音，交还时机 = 两段文本发出后再等 `estimate_speech_ms(仪式句) + estimate_speech_ms(简述)`，不等 20 s——或**自仪式句与简述两段都入 TTS 队列之后**起 `VISIT_INBOX_HANDOFF_MAX_S=20` 秒硬顶（两段 LLM 生成期间不计时；硬顶到点时若任一段仍在播放——仍在收到它的 `visit_speech_progress` 且未 `ended`；另有一个不依赖播放事件的绝对期限，到点**一律重投、从不丢弃**（`resolve_callback_delivery_ack` 只对挂了确认 future 的回调有意义，nack 不带 future 的回调等于丢失）：期限 = `max(finalize + 30 s, 两段都入 TTS 队列的时刻 + estimate_speech_ms(仪式句) + estimate_speech_ms(简述) + 10 s)`，封顶 finalize + `VISIT_INBOX_HANDOFF_ABS_MAX_S=120` 秒——按两段语音的估时留足播放时间，只有播放严重超出估时的病态情况才可能重叠——就继续等，不交还）（先到者）→ 按 `_close_takeover_callback_inbox` 同一方式重投扣住的插件回调，重投不了的 nack；交还仍在 `release_takeover` 之后（与一起看「先释放再交还」一致）。
   5. `session.handle_interruption()`；`session_pool.close_visit_session`；`_visit_route_states.pop`。

#### 3.2.7 断线（(g) 段，OD-11 v2，一句一个意思）
23. 每侧每 5 s 经数据通道发 `hb{lp_seen}`（cmd 1，不进 outbox）；收到对端**任何**消息刷新 `peer_last_seen`。
24. `now − peer_last_seen > 30 s` → `finalize('peer_lost')`。两侧各自判，最坏相差一个心跳周期。**host 侧这个计时器只在对端 `hello` 核验通过后才启动**：此前对端可能还没入房（邀请码 10 min 有效），host 建房后的 `invite_ready` 阶段只有 `VISIT_INVITE_WAIT_S=600`（与邀请码 10 min 一致）超时 → `finalize('invite_expired')`（没有已核验的对端，不发 `leave`）。**guest 侧不等 600 s**：guest 入房时 host 早已在房，guest 自入房起在对端 `hello` 核验通过前只等 `VISIT_PEER_LOST_S=30` 秒，超时 → `finalize('peer_lost')`；核验通过后按上面的 `peer_last_seen` 规则继续计时。
25. 我自己断了：SDK 自动重连（TRTC `CONNECTION_STATE_CHANGED{isReconnecting}` / LiveKit `Reconnecting`）；iframe 报 `state{reconnecting}` → 后端起重连截止计时，截止 = `min(断线时刻 + 25 s, 最后一次成功发出心跳 / 必达消息的时刻 + 30 s − VISIT_RECONNECT_MARGIN_S(3))`：**25 s 是上限**（比对端的 30 s 短 5 s），若断线前最后一次成功发出心跳 / 必达消息已过去不少时间就提前截止，保证重连后的第一条消息赶在对端判死之前到（对端的 30 s 从我最后一条消息算起，不从我断线算起）、前端徽标、不起新回合；25 s 内 `CONNECTED / Reconnected` → outbox 重发未 ack 项；否则主动 `exitRoom()/disconnect()` → `finalize('relay_lost')`。LiveKit 默认重连策略纯延迟合计 44 s + 每次 ≤1 s 抖动（源码 `DefaultReconnectPolicy.ts` 数组之和 44,000 ms，https://raw.githubusercontent.com/livekit/client-sdk-js/main/src/room/DefaultReconnectPolicy.ts ），TRTC 上限未文档化——都由我们的 25 s 先切，不改 `reconnectPolicy`。
26. vendor 显式离开事件（TRTC `REMOTE_USER_EXIT{reason:0}` / LiveKit 对端主动 `Disconnected`）→ 立即 `finalize('peer_left')`；超时类事件（TRTC reason 1、LiveKit `ParticipantDisconnected` 无 bye）**不单独处理**，交给 24 的心跳时钟。被踢 / 房间解散（TRTC `KICKED_OUT{banned|room_disband}`）→ 立即 finalize，不重连。
27. 正常结束先发 `leave{reason}`（尽力），对端收到立刻结束，不必等 30 s。
28. Pet 页刷新 / iframe 消失（**transport WS 断**是唯一判据）：后端保留状态 **20 s**；新页面 `GET /api/visit/state` 得知在飞 → 重建 iframe → 同一份凭证重入房（同 `vid` 再入房 = 重连）→ 重发 `hello`（同 `jti`）→ outbox 重发；超 20 s → `finalize('local_page_lost')`。
29. 后端重启 / 崩溃：`VisitRuntime` 不落盘，这场结束；对端 30 s 后 `peer_lost`；下次启动只做 spool 补录（3.7.3），不重入房。
30. 关机：Electron 先销毁窗口再请求后端关机（`backend-runtime.js:2483-2490`），所以 `leave` **发不出去**；`on_shutdown` 最前 `await stop_all('shutdown')` ≤3 s：spool fsync + `state.json{finalized:'shutdown'}` + **同步写出 `<visit_id>.upload.json`**（从内存转录 + 用量构造，与 `visitMemoryEnabled` 无关；关机来不及上传，下次启动补录按它重传）+ 释放 takeover；**对端 30 s 后才知道**——产品文案明写「对方退出程序时你家猫娘要等 30 秒才知道」。PC 侧「销毁窗口前先发 leave」列为可选 follow-up（不影响 v2 结论）。
31. 常量：`VISIT_HEARTBEAT_S=5`、`VISIT_PEER_LOST_S=30`、`VISIT_SELF_RECONNECT_S=25`、`VISIT_LOCAL_PAGE_GRACE_S=20`、`VISIT_SHUTDOWN_BUDGET_S=3`、`VISIT_INVITE_WAIT_S=600`。**已知限制**：浏览器多窗口开发态（index.html + chat.html 各一条 `/ws`，`websocket_router.py:547-556` 最新 socket 赢）下渲染模型的页面可能不是 current；chat 窗（`__NEKO_MULTI_WINDOW__ === true` 且路径以 `/chat` 开头，`app-websocket.js:1092-1094`）只渲染 `visit_line/*`、不建 iframe；README 明写「浏览器多窗口态不支持串门画面」。

#### 3.2.8 角色切换 / 改名 / 删除 / 告别（(h) 段，OD-13 / OD-25，owner 已同意）
- **切换**：`crud.py:1121-1124` 改调 `finalize_external_routes_for_character(old)`（注册表版）→ 各 kind 的 finalize **只等状态翻转**（不等 `_exit_task`）→ 对端收 `leave{character_changed}`。
- **改名**：`crud.py:757` 语音守卫旁加 `if is_external_route_active(old_name): 400 {'error_code':'EXTERNAL_ROUTE_ACTIVE'}`（在 `:802` `release_memory_server_character` 之前）；连带 game 路由在飞时也拒（标回归）。
- **删除**：`crud.py:1573` 已 400 拒删当前猫娘；串门只绑当前猫娘 → 不需要新钩子。
- **manager 被替换**（`character_runtime.py:1899 shutdown()`）：`visit_sweep_loop` 每 2 s 比对 `get_session_manager().get(lanlan) is state['_mgr']` → `finalize('manager_replaced')`。
- **告别**（`goodbye_state{active:true}` → `lifecycle.py:82 set_goodbye_silent`）：两侧都 `finalize('goodbye')`（固定句、静音），**不走**收尾流程——那是全局告别，越快越好。

#### 3.2.9 A 的 Pet 窗被隐藏 / 遮挡（(i) 段）
以「postrender 是否还来」为唯一判据（3.11 第一行）：父页 1 s 内没有一次成功抓帧 → `postMessage({t:'hidden', on:true})` → iframe 发 `state{hidden:true}`（cmd 1，1 Hz）；恢复出帧即 `on:false`。B 显示最后一帧 `opacity:.6` + 「离开了一下」徽标；不计入 `idle_timeout`（只数 text）。不依赖 `document.visibilitychange`（Pet 窗 `backgroundThrottling:false` 时 Electron 官方文档明说 visibility 保持 `visible`）。

---

### 3.3 iframe 承载机制（OD-27 / OD-29）

#### 3.3.1 加载与生命周期
父页收到 `visit_state_change{pending}` → 创建 iframe；`ended` → `iframe.remove()`；同一时刻最多一个。模板 `GET /visit/transport`（`pages_router` 新路由，无末尾斜杠约定同 `websocket_router.py:24-28`，注入 `static_asset_version`）；子文档只引 `/static/visit/transport/*.js?v=…`，vendor UMD 按后端下发的 `transport` 动态载入（OD-28）。就绪握手：iframe `load` 后 `postMessage({t:'ready'})`，并在自己的 `window.__nekoVisitFrameSink = {onFrame(canvas, rectPx, tsMs)}` 暴露同步入口；父页缓存 `iframe.contentWindow.__nekoVisitFrameSink`（同源直接可读；调用前守卫 iframe 已就绪未销毁）。父页重载 → iframe 销毁 → transport WS 断 → 3.2.7 第 28 条。模型类型切换（Live2D → VRM / MMD / PNGTuber）：父页重挂钩子（VRM `static/vrm/vrm-manager.js:877` render 后 +1 行、MMD `static/mmd/mmd-core.js:1289-1291` 两个 render 分支后、`_flushRenderWaiters()`（`:1294`）前 +1 行、PNGTuber `<img>` 用 `nekoFramePacing.requestPacedFrame`（`static/frame-pacing.js:147-158`）30 Hz 采样），iframe 不感知。

#### 3.3.2 无 preload、无 electron* 桥的影响
iframe 拿到原生 `WebSocket` / `RTCPeerConnection`；`pet-websocket-bridge.js:333-346` 的一切都不在子 frame 生效——`_activeWs` 不变、Chat 窗不收 CONNECTING、凭证与控制流不被 `console.log`。iframe 里没有 `window.electronScreen / __NEKO_MULTI_WINDOW__ / nekoFramePacing / appState / live2dManager`——它不需要：帧由父页驱动、位置由父页给、可见性由父页按「postrender 是否还来」推导后 postMessage 给它（**不用** `document.visibilitychange`，见 3.11）。`backgroundThrottling:false` 是 BrowserWindow 级（`window-manager.js:1009`），子 frame 一并受益。代价：iframe 的 console 不被 preload 镜像到 Chat 窗（关键日志经 `postMessage({t:'log'})` 转发父页）。

#### 3.3.3 权限处理器与安全上下文
lanlan_frd 权限处理器 `allowedPermissions`（`src/main.js:11336-11339`）与 `isTrustedAppMediaWebContents`（`:11351-11360`）只看 webContents 顶层 URL，两个 handler（`:11398 / :11408`）不闸 `RTCPeerConnection` / `WebSocket`。本设计**不需要任何被闸的权限**：`captureStream` / `RTCPeerConnection` / 数据通道不经权限处理器，不调 `getUserMedia`（T6 顺带确认 TRTC 初始化不顺手申请麦克风）。安全上下文：`http://localhost:*` / `http://127.0.0.1:*` 是 potentially trustworthy；**用户自定义 `http://<局域网 IP>` 后端**（`main.js:1576 resolveServerUrl` 允许自定义 URL；本地模型 http+key 是刚需，不改）→ `window.isSecureContext===false`。TRTC 文档里的 HTTPS 要求是浏览器 `getUserMedia` 的限制（https://web.sdk.qcloud.com/trtc/webrtc/v5/doc/en/tutorial-05-info-browser.html ），本设计只发 canvas 轨、不采集，SDK 是否真的拒绝**由 T11 实测决定**：拒绝 → 能力门 ① 维持 fail-closed（409 `VISIT_UNSUPPORTED_ON_THIS_MACHINE`，`caps.reason:'insecure_context'` 映射 8 语文案「自定义后端地址需 https 或 localhost」）；不拒绝 → 只警告不 409。

#### 3.3.4 能力门（拆两段：①② 在领凭证之前，③ 在收到凭证之后）
顺序（裁决 D.4）：建 iframe → 能力门 ①②（预检）→ 通过后才向 Servers 领凭证 → 收到凭证、按 `transport` 懒加载 SDK → 能力门 ③。拆两段是因为 ③ 要按 transport 加载 SDK，而 transport 只有领到凭证才知道；后端又只在预检通过后才去领凭证——不拆就互等死锁。
① `isSecureContext`（见 3.3.3）；② `iframe.contentWindow.WebSocket.name === 'WebSocket'` 且原型是原生（防未来壳版本给子 frame 加 preload，`caps.reason:'foreign_websocket'`），并确认与 transport 无关的原生能力在场（`RTCPeerConnection`、画布 2D / WebGL、`HTMLCanvasElement.prototype.captureStream`；缺失 → `caps.reason:'no_webrtc'`）；③ SDK 脚本 `onload` 后 `await TRTC.isSupported()`（返回 `{result, detail}`，detail 含 H264 / VP8 编解码支持）或 LiveKit 侧 `RTCRtpSender.getCapabilities('video')` 含 VP9 / VP8。①② 失败 → 不领凭证、不加载 SDK、`caps{stage:'preflight', preflight_ok:false}` → 后端 409（缓存命中时）或推送 `ended{unsupported}`，**不消耗配额、不占 takeover——这条承诺只覆盖 ①②**；③ 整体失败（`sdk_load_failed` / `sdk_unsupported`，`caps{stage:'sdk', transport_ok:false}`）→ `finalize('unsupported')` + `release_takeover` + toast，**此时已计一次签发**（Servers 按签发计每日分钟数，3.5.8）；③ 只视频子项失败 → `caps{stage:'sdk', transport_ok:true, video_ok:false}`，串门照常（文本 + 字幕 + 本地 TTS），`hello.caps.video=false` 让对端显示头像占位。

#### 3.3.5 同任务取帧：postrender → 同步跨 realm 调用 → drawImage → requestFrame
不能用 postMessage：它是异步任务，PIXI 未开 `preserveDrawingBuffer`，合成后后备缓冲会被清空。做法：父页 `live2dManager.pixi_app.renderer.on('postrender', fn)`，`fn` 里：
1. **两道守卫**（裁决 D.6）：`if (renderer.renderTexture.current) return;`（渲染到 RenderTexture 时跳过——`generateTexture` 也会 emit postrender）与 `if (renderer.lastObjectRendered !== live2dManager.pixi_app.stage) return;`（avatar-portrait 把模型 reparent 到 `tempStage` 的三轮 `renderer.render(tempStage)`（`avatar-portrait.js:1226 / :1256`）也会触发 postrender，抓到的是错位模型）。`avatarPortrait.capture` 前后另置 `parentBridge.suspendCapture()`。
2. **分数累加器采样**（裁决 D.5，替代「距上次 ≥33 ms」门——后者在 144 Hz / 75 Hz / 定时器 60 fps 下算出 28.8 / 25 / 29.4 fps）：`acc += 30 / renderFps; if (acc >= 1) { capture(); acc -= 1; }`，`renderFps` 取最近 1 s 的实测 postrender 频率（≥30 fps 的任何源平均恰好 30 fps；源 <30 fps 时每帧都抓）。
3. 读缓存的裁剪源矩形（像素坐标，`canvas.width / getBoundingClientRect().width` 换算，对偶 `avatar-portrait.js:205 getCanvasMetrics`、`:627 cssRectToPixelRect`）→ **同步**调 `sink.onFrame(canvas, rectPx, now)`；iframe 在这次调用里 `scratch.drawImage(parentCanvas, sx, sy, sw, sh, 0, 0, cropW, cropH)`（`cropW×cropH` 取当前构图：上半身 320×448、全身 256×560，3.4.2；只拷裁剪区，不碰整块 4K×DPR 后备缓冲）→ 打包到 `pack`（3.4.2）→ `packTrack.requestFrame()`。Chromium `requestFrame()` 只置标志，帧在该画布本次任务结束时交付——我们正是在同一任务里刚画完；`captureStream(0)` 保证没画就没帧。
4. 定时器驱动路径同样成立：Pet 窗在配置帧率 < 刷新率×0.9 时用 `setInterval` 手动 `ticker.update()`（`live2d-core.js:944-960`；`frame-pacing.js:56-63 activeTimerTickFps`），`app.render → renderer.render → postrender → 我们的 drawImage` 都在这个 interval 回调任务里（PIXI 7.4.3 `Ticker.update` 不看 `started`，`emit("postrender")` 无条件）。实机验收用 `RTCRtpSender.getStats()` 的 `framesPerSecond / framesEncoded`（T2），不靠推理。
5. 每帧成本（估算）：裁剪 drawImage 0.3~1 ms + 两次 pack 合成 <0.5 ms；编码在 libwebrtc 线程。

#### 3.3.6 postMessage 的用途（低频，来源校验 `event.source === iframe.contentWindow && event.origin === location.origin`）
父页 → iframe：`crop{rectPx, srcW, srcH}`（300 ms~1 s，含滞回）、`place{left, top, width, height}`（host 侧）、`hidden{on}`（由「1 s 无 postrender」推导）、`visit_state{phase}`；iframe → 父页：`ready`、`stats{fps, kbps, rtt_ms, hidden}`（5 s，§4.4）、`first_frame{dataURL 96×96}`、`log`。全部 JSON。

---

### 3.4 视觉通道（OD-02 v2 / OD-06 v2 / OD-14 v2）

#### 3.4.1 取景
`getModelScreenBounds()`（视口 CSS px；edge-peek `hidden/hiding` 阶段返回 null，`live2d-core.js:5271-5276`）+ `getHeadDetectionGeometryInfo().headRect/bodyRect`（无缓存、遍历全部 drawables，`:5030`，只在 300 ms~1 s 刷新）→ 上半身框按 `makeUpperBodyRect` 比例（把 `avatar-portrait.js:509-521` 的死代码提到共享 helper：宽 = max(w×1.04, h×0.58×aspect)，高 = max(h×0.64, 宽/aspect)，`biasY=0.32`），无 head 信息退到 bounds 上 64%；「全身」= bounds 外扩 4% 按 256×560 contain。滞回：中心偏移 <4% 且尺寸变化 <8% 不动框，动框 300 ms 线性过渡。模型管理器覆盖 / MMD 加载期 `#live2d-canvas` `visibility:hidden`（`static/pngtuber-core.js:4787-4791`、`static/app/app-character.js:287-292`）或 bounds 为 null → 视同 hidden（3.11）。

#### 3.4.2 打包（iframe 内两块画布）
`scratch`（`cropW×cropH`，透明）与 `pack`（`cropW×2·cropH`，`getContext('2d',{alpha:false})`——不透明画布才走 Chromium 一拷贝快路径）；尺寸**从当前构图几何推导**（`VISIT_TIERS.sd600`：上半身 320×448 → 打包 320×896，全身 256×560 → 打包 256×1120），不写死。每帧：`pack` 填黑 → 上半 `drawImage(parentCanvas, 裁剪源矩形 → 0,0,cropW,cropH)`（颜色 over 黑 = 预乘颜色）→ `scratch` 填白、`destination-in` 画源矩形（白×alpha）→ `pack` 下半 `drawImage(scratch → 0,cropH)`（亮度 = alpha）→ `packTrack.requestFrame()`。切「上半身 / 全身」（`media{crop}`）时按新尺寸重建 `scratch / pack` 并 `updateLocalVideo`（`profile.width/height` = 新打包尺寸；LiveKit 同 §4.3 `media` 规则）；B 侧解包与显示比例同样从构图推导（对端 `state.crop` + `videoHeight/2` = 裁剪高）。`pack.captureStream(0)` 的轨 `contentHint='motion'`（libwebrtc `kFluid` → `MAINTAIN_FRAMERATE`；`'detail'/'text'` 会切进 screencast 模式并把 VP9 钳到 5 fps）。打包分界 y=cropH（上半身 448 / 全身 560）都是 16 的倍数，4:2:0 色度块不跨界；接收端 `blendFunc(ONE, ONE_MINUS_SRC_ALPHA)` 直接用预乘色，不做除法。alpha 经 4:2:0 有损编码后预计 1~2 px 灰边（估算，T12）。`destination-in` 输出不成立 → 退 WebGL 打包 shader（T5）。WebRTC 载荷无 alpha、WebCodecs 拒 `alpha:'keep'`，所以必须自己打包；色键（半透明发丝 / 阴影全丢）与并排打包（同面积）是落选方案。

#### 3.4.3 发布参数与编码器
- **TRTC**：`startLocalVideo({publish:true, option:{videoTrack: packTrack, profile:{width:320, height:896, frameRate:30, bitrate:560}}})`（上半身值；`width/height` 取当前构图打包尺寸，全身 256×1120，3.4.2；`profile` 对自定义轨是否生效未文档化 → T6）；`updateLocalVideo` 切「上半身 / 全身」（先按新尺寸重建画布）；`stopPlugin('SmallStreamAutoSwitcher')` 防自动切小流；编码器 H.264 主（Electron 41 = Chromium 146 含 OpenH264；Windows 硬件 H.264 CBP 默认关、macOS 无 → 预期 OpenH264 软编）、VP8 回落；**TRTC 无 `degradationPreference` API**，但 libwebrtc 默认（无 hint、非 screencast）就是 `MAINTAIN_FRAMERATE`（`media/engine/webrtc_video_engine.cc GetDegradationPreference`，googlesource 2026-09-26），方向对 owner 有利；TRTC SDK 是否覆盖它 → T6。
- **LiveKit**：`new Room({adaptiveStream:false, dynacast:false, publishDefaults:{simulcast:false, videoCodec:'vp9', scalabilityMode:'L1T1', videoEncoding:{maxBitrate:560_000, maxFramerate:30}, degradationPreference:'maintain-framerate'}})`。**`scalabilityMode:'L1T1'` 必须显式设**（裁决 D.3）：不设时 `LocalParticipant.publishTrack` 对 SVC codec 默认 `'L3T3_KEY'`（三层空间 SVC，560 kbps 被分给 80×224 / 160×448 / 320×896 三层，CPU 与画质双输；https://raw.githubusercontent.com/livekit/client-sdk-js/main/src/room/participant/LocalParticipant.ts ）。`simulcast` 默认 true 也必须显式关。VP9 软编 CPU 超阈值（编码 fps <27 持续 10 s）→ 本场记录、**下次串门用 vp8**（无 SVC、CPU 最低）。T8 验收 `chrome://webrtc-internals` outbound-rtp 的 `scalabilityMode` 与 `encodings.length === 1`。
- 软编 CPU（估算，2021 VGA 数据外推）：H.264(OpenH264) 8~14%、VP9 26~38% 单核。lanlan_frd Linux X11 追加 `--disable-accelerated-video-encode`（`src/main.js:490-497` 常量、`:563-566` 应用）→ Linux 必软编。

#### 3.4.4 保 30 fps
`_resolveIdleFps = configured===0 ? 30 : min(30, configured)`（`live2d-core.js:789-792`）→ 配置 ≥30 或 0 时静止地板已是 30；只有 `0 < configured < 30` 时父页在串门开始 `setTargetFPS(30)`（保存 / 恢复 `window.targetFrameRate`，该值在 localStorage `project_neko_settings` 不进后端）。`_hasRenderActivity()`（`:1029-1044`）加一行 `_visitCaptureActive`（对偶 `appState.lipSyncActive`）——这是 `live2d-core.js` 唯一改动；governor 每 300 ms 重复 boost 时 `_enterIdleTickMode(sameFps)` 早退（`:891-914`），不会造成 ticker stop/start 抖动。不 toggle `ticker.stop/start`，不自起 rAF（`static/visit/parent-bridge.js` 静态门：不含 `requestAnimationFrame(` 与 `new WebSocket(`；iframe 脚本允许 `new WebSocket(` 但只连 `location.host`）。源帧率 ≥30 时由 3.3.5 的分数累加器抽成恰好 30 fps。

#### 3.4.5 接收与解包（B 侧 iframe 即访客图层）
host 侧 iframe `position:fixed; z-index:9; border:0; background:transparent; pointer-events:none; class="transparent-overlay"`，尺寸 / 位置由父页每 300 ms 按 `getModelScreenBounds()` 摆到本家猫娘左右空位较大的一侧（高 `clamp(L.height×0.9, 200, 900)`，宽 = 高 × cropW/cropH（上半身 320/448、全身 256/560，父页取 `visit_state_change.peer_crop`，iframe 取 `media.peer_crop`；解包侧按 `videoWidth/(videoHeight/2)` 推导）；不是整窗，减少合成层面积）；guest 侧 iframe 1×1 置于视口外只当传输。子文档 `html,body{background:transparent;margin:0;overflow:hidden}`，只含隐藏 `<video muted playsinline>`（不能 `display:none`，rVFC 依赖帧送到合成器 → `position:absolute; width:2px; height:2px; opacity:0.01`）与透明 WebGL 画布（`premultipliedAlpha:true, alpha:true`；shader 上半取 rgb、下半取 r 作 a）。**两道保险**：`pointer-events:none` 让 preload 的 `elementFromPoint`（`pet-input-region-bridge.js:2722 / :3108 / :5205-5206`）跳过 iframe；样式被覆盖时 `transparent-overlay` 类使 `isModelBackgroundElement`（`:2204-2222`，具体 `:2211`）仍当背景，整窗不会变可点击。rVFC 1 s 内不触发 → 回落 `setTimeout` 30 Hz 采样（iframe 内无 `nekoFramePacing`）。**接收端以 `videoWidth/videoHeight` 观测实际分辨率**：libwebrtc 在 `MAINTAIN_FRAMERATE` 下 QP 质量缩放器仍会因高 QP 主动降分辨率（560 kbps ÷ (286,720 px × 30) ≈ 0.065 bit/px 偏低，稳态被降到 240×672 是可能的），这不经过我们的阶梯（3.4.6），T6/T8 要记 `qualityLimitationReason` 与稳态 `frameWidth/Height`。A 侧 `.visiting-away` + 徽标沿 v1（不用 `.minimized`，否则 `app-screen.js:3495-3503` 返回 null 断掉水印坐标链）。一个新的 WebGL 上下文（Pet 页已有 PIXI 一个，Chromium 每页上限 16）。

#### 3.4.6 档位与拥塞阶梯（`config/visit_settings.py::VISIT_TIERS`）
TRTC 大陆按**像素面积**分档且带码率带：标清 ≤640×480=307,200 px 且 300~900 kbps → 14 元/千分钟；高清 ≤921,600 px 且 900~1800 → 28；全高清 ≤2,073,600 且 1800~4000 → 63；音频 7；「视频传输码率或自定义数据通道码率超出限制后跳档」（https://cloud.tencent.com/document/product/647/44248 ，页面更新 2024-09-20）。

| 档 | 裁剪 W×H | 打包 W×2H | 面积 px | fps | 视频 kbps | 数据 ≤kbps | 合计 | TRTC 大陆档 | 状态 |
|---|---|---|---|---|---|---|---|---|---|
| **sd600** | 320×448（上半身）或 256×560（全身） | 320×896 / 256×1120 | 286,720 | 30 | 560 | 40 | 600 | 标清 14 | **v1 唯一发布，免费默认** |
| hd1200 | 480×672 | 480×1344 | 645,120 | 30 | 1150 | 50 | 1200 | 高清 28 | 表项 + `tier` 字段，`enabled=False` |
| fhd2400 | 720×1008 | 720×2016 | 1,451,520 | 30 | 2300 | 100 | 2400 | 全高清 63 | 同上 |

全部尺寸为 16 的倍数；sd600 两种构图像素数相同，切「上半身 / 全身」只换裁剪框与 `profile.width/height`（`updateLocalVideo`），不换档不换编码器；`tier` 进凭证请求，非 `sd600` Servers 403 `tier_not_entitled`。**拥塞阶梯**（只降分辨率不降帧）：B 每 5 s 发 `stats{rx_fps（rVFC presentedFrames 差分）, rtt_ms, loss_pct, rx_w, rx_h}`（cmd 3），A 看 TRTC `NETWORK_QUALITY.uplinkLoss` / LiveKit `ConnectionQualityChanged`；`rx_fps < 24` 或 `uplinkLoss > 15%` 连续 10 s → 上半身 320×448 → 256×352（bitrate 400）→ 192×272（bitrate **300**，裁决 D.7：不低于标清码率带下限 300 kbps）；全身 256×560 → 208×448（bitrate 400）→ 160×352（bitrate 300）；都是 16 倍数且打包面积 180,224 / 104,448 px（全身 186,368 / 112,640 px）在标清面积内；30 s 干净升一级。注明：libwebrtc 也可能自行降分辨率（3.4.5），阶梯不是唯一的分辨率来源；TRTC 拥塞时是否自行降帧是 T6 首要实测项（若会且不可接受，阈值收紧到 8%）。

#### 3.4.7 延迟（估算）
捕获 ≤33 ms + 编码 10~30 ms + vendor 两跳（国内 TRTC 60~150 ms；LiveKit 单节点跨洋 100~200 ms）+ 解码合成 ≤20 ms ≈ **150~300 ms** 单向。字幕：B 看到 A 猫娘的字 = A 端真开播时刻 + 数据通道单向 ≈50~150 ms；字比嘴早 ≤100 ms 量级，体感同步。

#### 3.4.8 口型与表情
表情随视频带走，B 零口型逻辑。A 侧口型：`visitVoiceEnabled=true`（默认）→ 本地 TTS 流式出声（一行一个 speech_id，OD-15 v3），既有 RMS 驱动零改动（`app-audio-playback.js:1549-1586 startLipSync`，首块调度时 `:1675-1705` 启动）；`false` → 不调 TTS，`static/visit/text-mouth-driver.js` 消费本机 `visit_line_delta{self:true}`，按 `estimate_speech_ms`（3.6.4）给该分句排 8~10 Hz 开合（幅度 0.35~0.8 随机、句末 250 ms 衰减），`LanLan1.setMouth`，排帧只用 `nekoFramePacing.requestPacedFrame`；与 RMS 互斥（`S.lipSyncActive===true` 时不动嘴）。VRM/MMD/PNGTuber 语音开走各自 `startLipSync(analyser)`（`:1688-1703` 已分派）；语音关时 text-mouth-driver 只驱动 Live2D（其余无统一 `setMouth`，follow-up）。**产品说明一句**：她在邻居家说话，你在自家听见，像开着免提；`.visiting-away` 徽标解释她「不在家」。v1 的「本地静音但保留 RMS」开关**删除**（白烧 TTS 配额）。

---

### 3.5 传输、数据通道与可靠层（OD-29 / OD-30 / OD-07 v2 / OD-12 v2）

#### 3.5.1 `VisitTransport` 接口（iframe 内，两实现）
`join(credentials) / leave() / publish(track, enc) / unpublish() / onRemoteTrack(cb) / sendData(cmd, bytes) / onData(cb:(fromVid, cmd, bytes)) / onPeer(cb:'enter'|'exit', vid, reason) / onState(cb:'joining'|'joined'|'reconnecting'|'connected'|'left'|'kicked'|'error') / stats()`。iframe 是**无状态转发器**：分片 / 重组、盖发送者 `vid`、按 cmd / topic 分流、丢非当前 `visit_id`；不读消息语义、不持票据。
- **TRTC**（trtc-sdk-v5 5.20.1，ISC；官方 changelog 首条 5.19.2 @2026-08-25）：`TRTC.create()` → `enterRoom({sdkAppId, userId:vid, userSig, strRoomId:visit_id, scene:SCENE_RTC, role:ROLE_ANCHOR, autoReceiveVideo:false})`；`startLocalVideo / updateLocalVideo / stopLocalVideo`；`startRemoteVideo({view:null})`（官方：不传或 null 则不渲染但仍消耗带宽）+ `getVideoTrack({userId, streamType})` 或 `TRACK` 事件；`sendCustomMessage({cmdId, data:ArrayBuffer})`（≤1 KB/次、≤30 次/s、≤8 KB/s、cmdId 1..10、须 `enterRoom` 后、无需发布媒体、「按序、尽力可靠，极差网络可能丢」，https://web.sdk.qcloud.com/trtc/webrtc/v5/doc/en/TRTC.html#sendCustomMessage ；**超限行为未文档化**，T6 实测 reject / 静默丢）/ `CUSTOM_MESSAGE{userId, cmdId, seq, data}`；事件 `REMOTE_USER_ENTER/EXIT{userId, reason 0 主动/1 超时/2 被踢/3 切角色}`、`REMOTE_VIDEO_AVAILABLE/UNAVAILABLE`、`TRACK`、`CONNECTION_STATE_CHANGED{prevState, state, isReconnecting}`、`KICKED_OUT{reason:'kick'|'banned'|'room_disband'}`、`NETWORK_QUALITY`、`VIDEO_SIZE_CHANGED`、`ERROR`（https://web.sdk.qcloud.com/trtc/webrtc/v5/doc/en/module-EVENT.html ）。
- **LiveKit**（livekit-client 2.22.3，Apache-2.0，`dist/livekit-client.umd.js`；server v1.13.6 Apache-2.0）：`new Room({...3.4.3, reconnectPolicy 默认})` → `connect(url, token, {autoSubscribe:false})`；`localParticipant.publishTrack`；`TrackSubscribed` → `track.mediaStreamTrack`；`publishData(bytes, {reliable, destinationIdentities:[peer vid], topic})`（reliable = 有序 + 重传、≤15 KiB，但「server does not buffer, limited retransmissions」→ 应用层 ack 仍必需，https://docs.livekit.io/transport/data/packets/ ；lossy ≤1300 B）/ `DataReceived(payload, participant, kind, topic)`；事件 `Reconnecting / SignalReconnecting / Reconnected / Disconnected / ParticipantConnected / ParticipantDisconnected / ConnectionQualityChanged`。LiveKit URL 主机名必须命中 `VISIT_LIVEKIT_HOSTS`（含 Cloud 与自建域）。

#### 3.5.2 消息集合与分流（最终，两 vendor 同一 JSON；字段名刻意短）
| cmd / topic | 可靠性 | 消息 | 频率上限（每侧出站） |
|---|---|---|---|
| 1 / `visit.ctl` | reliable（LiveKit）；TRTC 尽力 + outbox | `hello, ready, ack, hb, state, consent, wrap_up, leave` | `hb` 0.2/s；`state` ≤1/s；`ack` ≤2/s；其余每场个位数 |
| 2 / `visit.text` | 同上 | `line_delta, text, line_abort` | `line_delta` ≤4/s（250 ms 合并）；`text` ≤1/s；`line_abort` ≤1/s |
| 3 / `visit.lossy` | lossy（LiveKit `reliable:false` ≤1300 B） | `typing, stats` | `typing` 每行 ≤1；`stats` 0.2/s |

必达集合（进 `VisitOutbox`）= `hello / ready / consent / wrap_up / leave / text`。`ack`、`hb`、`state`、`line_delta`、`line_abort`、`typing`、`stats` 不进 outbox。关键字段（**摘录**，全字段与上限以 §4.2 为准）：
- `line_delta`：`{t:'line_delta', v:1, ln, i, lp, txt(≤800 B)}`；`i==0` 额外带 `sp:'c'|'h'`、`ad:'hc'|'hh'|'gc'|'gh'`（addressee side+kind）、`rt`（reply_to 的 `ln`，开场 `""`）、`wu:bool`（告别行）。可丢，只上屏，不入史不入 spool。
- `text`：`{t:'text', v:1, ln, lp, seq, sp, ad, rt, wu, final:true, txt(全文或已开口前缀 ≤4096 B), truncated:bool, i_done:int, trunc_reason?:'human_interrupt'|'wrap_up'|'tts_error'|'llm_error'|'stall'|'wire_size'}`。**一行永远以一条 `text` 收口**；被打断的行 `truncated:true` 且 `txt` = 已放出分片拼接（已放出的入史、未放出的不入史）。接收侧以它为准覆盖气泡、入史、入 spool、计数；`line_delta` 缺片不补洞，等 `text`。
- `line_abort`：`{t:'line_abort', ln, lp, i_done, reason}`，只是让 UI 立刻截断的提示，随后必有 `text{truncated:true}`。
- `hb`：`{t:'hb', lp_seen}`，每 5 s（**统一叫 `hb`，删除 `ping`**）。
- `ack`：`{t:'ack', seq}` 累计确认（**不再有**按行 `ack{ln, n_recv, lp_seen}`，也**没有** `order / stale`）。
- `wrap_up`：`{t:'wrap_up', v:1, seq, lp, ph:'propose'|'begin'|'ack'|'done', reason:'quiet'|'budget'|'recall'|'time_up', initiated_by:'host'|'guest'}`。
- `hello`：`{t:'hello', ticket, caps{video, tier, proto:1, app_version}, lang}`；`ready`：`{t:'ready', v:1, seq, memory:bool}`（host → guest，每场 1 条；`memory` = host 当前 `visitMemoryEnabled`，兼任 host 的初始 consent）；`consent`：`{t:'consent', v:1, seq, memory:bool, scope:'session'|'all'}`（初次只 guest 侧发）；`leave`：`{t:'leave', reason}`；`state`：`{t:'state', v:1, hidden:bool, tier, crop:'upper'|'full', enc:'h264'|'vp8'|'vp9'|null}`；`typing`：`{t:'typing', v:1, lp, sp:'c'|'h'}`（只发 on，第一片 `line_delta` 即隐含 off）；`stats`：`{t:'stats', v:1, rx_fps, rx_kbps, rtt_ms, loss_pct, rx_w, rx_h, qlr?}`。

#### 3.5.3 分片信封（TRTC；LiveKit 单包不分片但沿同信封 `i=0, n=1`）
`{v:1, r:<visit_id 前 8>, m:<msg_id u32>, i, n, p}`，`p` 是 payload JSON 的字符串片。**每片总长 ≤1000 B（按字节，不是 1024）**；内层 JSON 转义膨胀已算进去：`line_delta.txt` 上限 800 B 是为此留的余量（信封 ≈55 B + 外层键 + 内层每个引号转义 ≈30~40 B）。`text.txt` ≤4096 B 的普通行经转义后 ≈4.4 KB → ≤5 片；但正文经两次 JSON 转义（payload JSON + 信封字符串 `p`），全是反斜杠 / 引号的病态正文最坏膨胀约 4 倍、远超 8 片——所以**发送侧以编码后字节为准**：按最终信封形式编码后若超过 `VISIT_PIECES_MAX=8` 片，就在字符边界截短 `txt`、置 `truncated:true, trunc_reason:'wire_size'` 后重编码，直到 ≤8 片（`clamp_text_utf8(4096)` 仍是第一道上限，§4.1 / §4.2）。接收按 `(from_vid, m)` 重组，2 s 未齐丢整条（必达类等 outbox 重传）。单测断言：**最长合法 `text` 分片后每片 ≤1000 B**（随机 1000 组中文 / emoji / 俄文）。

#### 3.5.4 可靠层 `VisitOutbox`（后端，每侧一个）
- 必达消息带单调 `seq`；对端回累计 `ack{seq}`，`seq` 是**连续**落地的最大序号（有缺口只推进到缺口前）；**接收侧对必达消息严格按 `seq` 顺序处理**：缺口之后先到的先缓存（上限 `VISIT_REORDER_BUFFER_MAX=64` 条，超出 → `finalize('peer_protocol_violation')`），缺口补齐后按序处理——`consent` 因此永远在它之前的 `text` 之后生效（那条 `text` 仍按旧 consent 盖章）；可丢消息（`line_delta` 等）不受影响；未 ack 按 **1→2→4→8→8 s** 重传，之后每 8 s 继续重传；任一必达项首发起 `VISIT_DELIVERY_TIMEOUT_S=30` 仍未确认 → `finalize('delivery_failed')`。**这 30 s 只在传输已连接期间计时**：自身 SDK 重连（`state{reconnecting}`，25 s 窗口）与页面重载宽限（transport WS 断，20 s）期间暂停，恢复（`connected` / 新页面重入房）后 outbox 重发未 ack 项并从暂停处继续计时——否则一次正常的重连会把重连前刚发出的项误判成 `delivery_failed`。这条不能交给 `peer_lost`：数据通道可能一直送达心跳却反复丢同一条 `text`，对端不会判死。
- 接收侧 `ln` / `seq` LRU(512) 幂等，重复只回 ack。
- outbox 落 `<config_dir>/visit_spool/<visit_id>.outbox.jsonl`（与记忆 spool 同目录同 helper，3.7.3；`config_dir` 见 `utils/config_manager/storage_roots.py:160`）。**用途只有一个**：页面重载 / SDK 重连（25 s 内）后重发未 ack 项。后端重启**不**回放（凭证与票据只在内存、隔离会话与仲裁状态都没了，3.2.7 第 29 条）；启动时**只删**残留的 `.outbox.jsonl`；同目录的 `.jsonl` 转录、`.state.json`、`.upload.json` 一律保留，交给崩溃补录（3.7.3 第 7 条）与转录重传（OD-26 v3）。
- 文本重传只在令牌桶有余量时发；`leave` 一次重传后不等 ack。
- 先例说明：`utils/event_logger.py:263-264` 与 `main_logic/facts_sync/sync_worker.py:78-81` 只提供 O_APPEND 单次 `write` 的先例，**都不 fsync**；fsync 是本设计新增（`VISIT_SPOOL_FSYNC_S=30` + finalize 一次）。

#### 3.5.5 限速与最坏速率
两道令牌桶（页面转发器与后端出站队列各执行一份，超限**排队不丢**）：字节桶 **5 KB/s**（40.96 kbps ≈ 40 kbps，与 560 kbps 视频合计 600，远低于标清档 900 kbps 跳档线）；条数桶 **20 条/s（桶容量 10）**。规则：同一行内两片开播间隔 <250 ms（`VISIT_DELTA_MIN_INTERVAL_MS`）→ 合并成一片（`i` 在发送时按实际发出的片连续分配，合并后的片占一个 `i`、后续顺延不留洞，`text{final}.i_done` 同步按发出片数计，§4.2）；队列积压 >3 s 的量 → 同一行相邻 delta 合并到 ≤800 B；桶满 → iframe 回 `tx_backpressure` → 后端暂停 `line_delta / typing / stats`，只保 `text / ctl`。

最坏出站需求（每侧，纸面，各类同一秒同时顶满、实际互斥；**与 §4.1 同一算式**，PR-03 `max_worst_case_rates()` 供单测与文档同源）：
- `line_delta`：250 ms 最小间隔 → ≤4 条/s × ≤1000 B（整片，含信封）= 4.0 KB/s。
- `text`：一侧同一时刻只有一个发言者且行间 ≥1 s 间隙 → ≤1 行/s；`txt` 硬上限 4096 B → 普通正文分片后 ≤5 片 / 行（转义密集的病态正文按编码后字节截到 ≤8 片，3.5.3；超出 5 片的部分同样由下面的桶排队摊平）→ ≤5 条/s、≤4.1 KB/s（猫娘行 `truncate_to_tokens(400)` 下 CJK 实际 ≈1.2~1.8 KB、人类行 ≤600 tok ≈1.8~2.7 KB，估算）。
- `ack` 合并后 ≤2 条/s × 80 B = 0.2 KB/s；`typing` ≤1 条/s × 60 B；`hb` 0.2 条/s × 60 B；`stats` 0.2 条/s × 120 B；`wrap_up / consent / state / line_abort` 每场只有个位数条，忽略。
- **条数**：4 + 5 + 2 + 1 + 0.2 + 0.2 ≈ **12.4 条/s**（纸面）≤ 20（桶）≤ 30（TRTC）。**字节**：4.0 + 4.1 + 0.2 + 0.1 ≈ **8.4 KB/s**（纸面；delta 的 4 KB/s 与 text 的 4 KB/s 互斥——一行 800 B 正文 ≈266 CJK 字按 180 ms/字 ≈48 s 语音，不可能与 250 ms 节拍同时顶满，且 `text` 正文就是同一行已流出的 delta 再发一遍）；真实峰值 ≈1.0 + 1.5 + 0.3 ≈ **2.8 KB/s**（估算）。**桶把线上速率钳在 ≤5 KB/s、≤20 条/s**，超出部分排队并触发 delta 合并——所以线上恒 ≤30 条/s、≤8 KB/s（TRTC 上限），结论成立；代价只是极端峰值时字幕中间态晚到，`text{final}` 不受影响。
- 重连回放突发：未 ack ≤2 行 `text`（各 ≤5 片）+ hello / consent ≈ 6 条 / ≤5 KB，被桶摊到 ≥1 s 内发完。

#### 3.5.6 版本偏斜规则（对端老 / 新版本，裁决 B.3）
单机（父页 / iframe / SDK / 后端同一发布）零偏斜；A/B 两台机 Xiao8 版本可以不同。规则：`hello.caps.proto` 主版本不同 → `leave{reason:'proto_mismatch'}` + 8 语 toast「对方版本不兼容，请双方更新」；**未知 `t` 一律忽略并计数**（恢复 v1 规则）；未知字段忽略；`>1000 B` 片 / `i` 跳变 / 两行交叠 / `lp` 回退 >1000 / 同一发送方 `lp` 不单调（**只作用于新开的行 / 新控制事件**：已见过的 `ln` 的 `text{final}` 及其重传、outbox 重传的旧 `seq` 保留原 `lp`，不因后续更大 `lp` 已到而被拒） → **丢弃该消息并计数，连续 20 条异常才 `finalize('peer_protocol_violation')`**——新版本加一种消息或一个字段不会让老版本对端把它踢掉。违约判据只保留「格式非法 / 超长 / 速率超限」。

#### 3.5.7 iframe ↔ 本机后端独立 WS `/api/visit/transport/ws`（OD-29）
`main_routers/visit_router/transport_ws.py`：`@router.websocket("/transport/ws")`（子路由写相对路径，由 `visit_router` 的 `prefix='/api/visit'` 补前缀，对外 URL 即 `/api/visit/transport/ws`），query `visit_id, side`，同 `WS /api/vmc/ws`（`main_routers/vmc_router.py:11`）的本机 Origin / CSRF 校验；JSON 文本帧 ≤16 KB。下行：`credentials{visit_id, side, transport, vendor{…}, own_vid, peer_vid?, allowed_hosts, tier, crop, publish{…}, expires_at}`（**不带 `publish_video`**；`peer_vid` guest 侧必填、host 侧领凭证时 guest 尚不存在为 null，对端 `hello` 核验通过后经 `media{peer_vid}` 补齐，用于 3.2.2 第 6 条丢弃非对端消息）、`media{publish, subscribe, crop?, ladder?, peer_crop?, peer_vid?}`（发布 / 订阅 / 拥塞阶梯 / 补 `peer_vid` 只由它驱动）、`send{cmd:1|2|3, payload}`、`stop{reason}`；上行：`caps{stage:'preflight', preflight_ok, reason?}`（能力门 ①②，领凭证前）与 `caps{stage:'sdk', transport_ok, video_ok, reason?, codecs[]}`（能力门 ③，收到凭证后，3.3.4）、`state{state, peer_present, remote_video, error_code?}`、`recv{from_vid, cmd, payload}`（已重组）、`stats{tx_fps, enc_fps, tx_kbps, tx_w, tx_h, enc, qlr, rx_fps, rx_kbps, rtt_ms, loss_pct, rx_w, rx_h, dc_queue, softenc_overloaded?}`（字段全表见 §4.3）、`tx_backpressure{on, queue, dropped}`。该 socket 断 = iframe 消失 → 后端 20 s 宽限（3.2.7 第 28 条）。display socket `/ws/{name}` 不承载任何串门二进制或凭证；`websocket_router.py` 二进制分支与 `app-websocket.js:3058-3066` Blob 分支 **diff 为空**。

#### 3.5.8 成本（1000 同接）
假设显式写出：**1000 同接 = 500 房**（每房 1 guest + 1 host，只有 guest→host 一路视频）；「月」= 日均满载等效 4 h × 30 天 = 60,000 房·小时（估算，若 1000 同接是峰值、日均等效 1 h → 除以 4）。流量一律按十进制 MB / GB 算：600 kbps × 3600 s = 270 MB = 0.27 GB / 房·小时；GiB 只在引用 GCP 报价时出现并注明换算（0.27 GB = 0.2515 GiB）。

| 方案 | 每房·小时 | 500 房满载 1 h | 月（60,000 房·小时，估算） | 固定成本 | 备注 |
|---|---|---|---|---|---|
| **TRTC 大陆（按量）** | host 收 1 路标清 60×14/1000 = 0.84 + guest 只发不收计音频 60×7/1000 = 0.42 → **1.26 元** | **630 元** | **≈75,600 元**（日均等效 1 h → ≈18,900 元） | 0；一次性 1 万分钟免费包（非每月） | 数据通道码率并入档位带宽，总量 ≤900 kbps 否则跳高清 28（翻倍）；5 KB/s 桶杜绝；https://cloud.tencent.com/document/product/647/44248 |
| TRTC 大陆（基础版 625 元/月含 110k 分钟单位，扣减 音频:标清 = 1:2） | 1 房·小时 = 60×2 + 60×1 = 180 单位 → 611 房·小时 / 625 元 ≈ **1.02 元** | 511 元 | ≈61,400 元（≈98 份基础版，估算） | 625 元/月起 | 约省 19% |
| ARTC 大陆（未选） | 0.012 + 0.006 = 0.018 元/分 → 1.08 元（若竖版帧被判 480P 档） | 540 元 | ≈64,800 元 | 0；无免费额度 | 被判 720P 档则 1.80 元；SDK 钉 `maintain-resolution` 违反 30 fps |
| 声网（未选） | host 收 HD 28 + guest 音频 7 → 0.035 元/分 → **2.1 元** | 1,050 元 | ≈126,000 元 | — | 无标清档 |
| **LiveKit Cloud Ship（海外上线期）** | $0.0005/min × 2 人 × 60 = $0.06 + 0.27 GB × $0.12/GB = $0.032 → **$0.092** | $46 | ≈$5,500 + $50 | Ship $50/月含 150k 分钟 / 250 GB / **1,000 并发**（= 1000 同接零余量，触顶上 Scale $500/月 5,000 并发） | 无大陆 / HK 区域；https://livekit.com/pricing |
| **LiveKit 自建 GCP（海外稳态）** | 出站 0.27 GB = 0.2515 GiB × $0.12/GiB（Premium 0~1 TiB 档）= **$0.030**；对华目的地 $0.23/GiB → $0.058 | 135 GB = 125.7 GiB ≈ **$15** | 出站 16.2 TB ≈ 14.7 TiB 分档（1 TiB×0.12 + 9 TiB×0.11 + 4.7 TiB×0.085）≈ **$1,550** + VM | e2-standard-4：us-west1 $97.84、东京 $125.51、法兰克福 $126.05 /月；在用外网 IP $0.005/h ≈ $3.65/月；域名 + 证书 | 500 房 = 1000 轨，默认 400 轨/CPU → 4 vCPU 名义 1,600 **刚够**，官方只有 c2-standard-16 基准（150 pub/150 sub 720p = 85% CPU）→ **必须压测**；TURN 中继的房出站翻倍；GCP 单价为 2026-09-12 调研值 |

**Cloud → GCP 盈亏点**（评审复算）：H = 月房·小时。Cloud(H) ≈ 50 + (120H − 150,000)×0.0005 + (0.27H − 250)×0.12 = 0.0924H − 55（H >1,250 时分钟额度用尽）；GCP(H) ≈ 97.84 + 3.65 + 0.0302H。相等 → 0.0622H ≈ 156.5 → **H ≈ 2,500 房·小时/月**（估算，≈84 房·小时/天；两轮复算落在 2,517~2,700）；把运维按 $100/月计入 GCP 侧，拐点推到 ≈4,100。所以：海外上线期用 Cloud（零运维、零压测），月 >≈2,500 房·小时持续两个月或需要数据驻留 → 切 GCP 自建（东京起步，客户端零改动，只换 Servers 下发的 `{url, token}` 与签发密钥）。

**服务端可强制的账单上限**（裁决 C.7；串门期间 Servers 不在环路、客户端开源可改，所以这是**唯一**服务端能兜住的用量）：Servers 按账号记「每日签发分钟数」= 签发次数 × 30 min，免费档默认 `VISIT_FREE_MINUTES_PER_DAY`（**占位值 120，由 owner 定价时拍板**），每账号并发房 ≤2（名额只在 vendor 房间结束事件或 Servers 自行向 vendor 查询确认参与者 / 房间已不在之后释放；`POST /api/visit/transcripts` 只触发一次这样的查询、不直接释放；凭证到期兜底，§4.7），重连不消耗；付费档只改 entitlement。**画质档位不能只信客户端**：客户端开源、SDK 参数可改，服务端约束见 §4.7（LiveKit token 按侧位收紧 + `track_published` webhook 超档踢人；TRTC 拉用量统计比对超档走封禁，T13 决定 host 能否用观众角色）；**在检测到并处置之前，单账号最坏按凭证允许的最高档计费**（TRTC UserSig 本身不限分辨率与码率，免费账号改客户端后最坏可把画面推到全高清计费档 63 元每千分钟，是标清 14 元的 4.5 倍，估算），免费额度按此最坏值留余量。上界算式：每参与者·分钟平均 (14 + 7)/2/1000 = 0.0105 元 → 每账号每日 ≤120 × 0.0105 = **1.26 元**、每月 ≤37.8 元（估算）；月账单上界 = 37.8 元 × 活跃账号数（例：10,000 活跃账号 → ≤378,000 元/月，真实用量远低于此；该数只说明「不会失控」）。

**用户自付部分**（裁决 F.5，估算；用户在自己 API key 账单上看到的）：
- LLM：`VISIT_OWN_LINES_PER_VISIT=40` × 每句 ≈6k input token ≈ **240k input token / 侧 / 场**（无人在场 ≈7 min 跑满；有人参与时节拍被打字拖慢）；按 $2.5/M ≈ $0.6 / 侧 / 场，按 ¥8/M ≈ ¥1.9 / 侧 / 场——都高于 vendor 的 ¥0.63 / 侧 / 场。缓解：prompt 组织成前缀稳定（角色卡 + 场景块在前、对话在后）以吃到 provider 缓存。确认框**不显示** token / TTS 次数等技术数字（OD-26 v3）；每场实际用量在 finalize 时本机汇总，计数经现有遥测 counter / histogram 上报（不带 visit_id），带 visit_id 的整场记录随转录上传 Servers，结束后经藏得较深的「查看详情」可查（3.8）。
- TTS：两侧默认出声、一行一个 speech_id 流式推入 → **每行 ≈1 次请求、≈40 次 / 侧 / 场**（估算；v2 逐分句方案 ≈120）；按字计费的 provider 不变，按请求数限流的 provider（含官方免费 TTS）限流风险下降，若仍触顶 → 该行首段推入后 4 s 无播放进度则按文本估时转发，本场剩余各行不再重试 TTS（T 列表加「官方免费 TTS 一场 ≈40 次请求的限流行为」）。关掉 `visitVoiceEnabled` 即零 TTS。
- debrief 额外：简述 1 次（≤200 output tok）+ 日记 1 次（仅选「记成日记」时，同一次调用产出日记段 ≤300 output tok + ≤3 条事实）+ 1 次 TTS。

**客户端开销**（估算）：A 侧打包三次 drawImage 0.3~1.5 ms/帧；软编 320×896@30 H.264(OpenH264) 8~14% / VP9 26~38% 单核；Live2D 从空闲地板拉到 30 fps 只在用户配置 <30 时发生。B 侧解码 + 每帧一次 `texImage2D` + 小画布 ≈1~2 ms/帧。

---

### 3.6 对话机制（OD-03 / OD-08 v2 / OD-15 v3 / OD-21 v3 / OD-22 / OD-23）

#### 3.6.1 会话对象
两侧各一个 `VisitRuntime(lanlan, side)` 持有：`VisitRoom`（纯状态机，3.6.3）、隔离 `OmniOfflineClient`（`tool_definitions=[]`、`max_response_length=VISIT_RESPONSE_MAX_TOKENS=160`（单位 token，`_streaming.py:109-111`）、`master_name=FAMILY_NEUTRAL_TERM`）、`VisitOutbox`（3.5.4）、`VisitSpool`（3.7.3）、`VisitLiveness`（三计时器）、`_llm_turn_lock`（串行 `stream_text / append / trim`——`stream_text` 自身无串行锁）、`_speech_lock`（同一时刻只开一条 `MirrorSpeechStream`：一行一个 speech_id，OD-15 v3）、`_reply_task`、`_exit_task`。永不赋给 `mgr.session`。没有帧邮箱、没有中继客户端。

#### 3.6.2 注入入口与否决
串门文本只走隔离会话 `stream_text`。否决：`append_context(role='user')`（进 `_conversation_history` → 私聊记忆）；`submit_proactive_callback` 承载任何串门内容（→ `prompt_ephemeral` 回复 `persist_response=True` 入主会话历史 `_lifecycle.py:752-753` + 指令抄送插件总线 `:547-568`）——**v2 连回家自述也不再走它**（3.7.4）；`stream_data`（访客当亲人）。私聊记忆的唯一串门入口 = debrief 里用户亲手选的产物：日记段经 `POST /cache/{lanlan}` 进近期记忆，≤3 条串门事实经 `POST /internal/memory/{lanlan}/visit_facts` 进 fact 层（不进 reflection，3.7.4）。

#### 3.6.3 轮次、时钟与收尾（`VisitRoom`，纯状态机）
`VisitRoom` 不 await、不 I/O、不持锁，只吃事件、吐 `RoomEffects`，由 `VisitRuntime` 执行。
① **序**：每行在第一片发出时分配 Lamport 时间戳 `lp = max(own, max_lp_seen)+1`，贯穿该行所有消息；收到任何消息先 `observe_lp`；全序键 `(lp, side_rank)`（host=0、guest=1）用于显示、历史与 spool 排序——**隔离会话历史在每次开始新一轮 LLM 之前按它重排**（或 `append` 时按 `sort_key` 插到正确位置，`_llm_turn_lock` 内做），同时开口的两行按 host 在前，所以两侧喂给 LLM 的历史顺序一致，不取决于各自的到达顺序；**没有中继 `order`**。② 每行恰一个 `addressee`；`reply_to`（`rt`）是被回那行的 `ln`。③ **陈旧**：开口前复查 `is_stale`——存在**对我说的**、`lp` 更新的完整行则改回最新那句（重排，不是沉默）；被回的行 `line_abort` / `text{truncated}` 则丢弃 plan 等下一句。开场双方 `rt==""` 的两句并存（豁免）。④ **打断**：人类行（本地或对端 `sp:'h'`）让说话中的本侧猫娘**立即停**（`MirrorSpeechStream.abort()` → `interrupt_mirror_speech`，OD-15 v3）并发 `line_abort` + `text{truncated:true}`（已开口前缀 = 已放出的分片）；猫娘不打断猫娘；真正撞车（双方都以为该自己说）时各自说完当前行、两行都按 `(lp, side)` 入史，**guest 让一次**（`yield_once`，由「我在说话时收到对端猫娘的第一片」对称侦测，确定性、零消息、一轮内解除）。⑤ **一句话规则**：**两只猫连续 6 句没有任何人类插话，或本侧猫娘这场已经说了 40 句，就进入「收尾」；本侧猫娘每分钟最多说 6 句，超了只是多等一会儿。** 「人类」= 本地或对端人类（都清零 6 句计数），但对端人类**永远改不了**本侧的 40 句 / 每分钟 6 句上限——这就是对「对端全标 human」攻击的全部防御（最坏 40 句 × ≈10 s ≈7 min，估算）；告别行（`wu:true`）不计入任何计数；`VISIT_MAX_LINES=80`（两侧合计）保留为违约守卫。⑥ **收尾**：host 发 `wrap_up{begin}`（guest 只 `propose`，5 s 无回应自行开始告别）；guest 告别一句 → host 送客一句 → `wrap_up{done}` → guest `leave('home')`、host `finalize('peer_left')`；任一侧收到 `wu:true` 的行即进 WRAP_UP（`begin` 丢了不卡死）。**超时（裁决 F.1）**：`VISIT_WRAP_UP_STEP_S=15`——从 `begin` 到对方告别行**第一片 `line_delta`** 到达（表示对方已开口，链路 = LLM ≤8 s + 首块 TTS 送达 0.5~1.5 s）；告别行本身按正常播放走完（提示词要求 ≤40 字、最多两个分句；≤400 tok 天然有界；**不受**下面的 10 s abort 约束）；`begin` 时正在说的旧行（非 `wu:true`）从 `begin` 起 `VISIT_SPEAKING_ABORT_AFTER_S=10` 未说完 → 立即停（`MirrorSpeechStream.abort()`）+ `line_abort{reason:'wrap_up'}`（随后必有 `text{truncated:true}`）；`VISIT_WRAP_UP_MAX_S=45` 硬顶无条件 finalize（`max_duration − 60 s` 起收尾：1740 + 45 = 1785 < 1800）。WRAP_UP 内人类文字拒绝 + toast（host 侧 `VISIT_INPUT_REFUSED_WRAPUP`；guest 侧本来就拒，OD-22，「叫她回来」变 no-op + `VISIT_RECALL_ALREADY`），未开口推理取消，新猫娘轮次只放行 `goodbye=True` 的一句、每侧一次。收尾期间 30 s 判死照常：`peer_lost` → 立即 finalize，本侧猫娘用 `VISIT_GOODBYE_FALLBACK_*` 固定句在本地说一句，不转发。⑦ **流式**：LLM 边生成边推本地 TTS，分句按已播音频估时对齐放出（3.6.4），`text{final}` 带 `tail_ms`（末句估时），接收侧回复延迟 = `tail_ms` + U(1.0, 2.5) s（`VISIT_REPLY_GAP_S`，替换 v1 `max(3.0, min(0.04·len, 6.0))`：读句时间已由流式播放吃掉）。⑧ **默认数字**（设计值）：`VISIT_MAX_CAT_TURNS_WITHOUT_HUMAN=6`（每句 ≈8~16 s → 无人时 ≈1 min 自嗨后回家，符合「没心没肺」的短串门）、`VISIT_OWN_LINES_PER_VISIT=40`（≈240k input token / 侧 / 场，估算）、`VISIT_OWN_LINES_PER_MINUTE=6`（自然节拍 ≈3.8~7.5 句/min，只在对端异常快时咬住）、`VISIT_WRAP_UP_PROPOSE_TIMEOUT_S=5`。备选 N=10 / L=60 更长自嗨更贵。

`VisitRoom` 接口草案摘录（`main_logic/visit/room.py`，全文见 d4 §6）：
```python
Side = Literal["host", "guest"]; Phase = Literal["active", "wrap_up", "ending"]
WrapUpPhase = Literal["begin", "ack", "done", "propose"]

@dataclass(frozen=True)
class LineRef: line_id: str; lp: int; side: Side          # line_id = "{h|g}:{行序}"（不是 outbox seq）
@dataclass(frozen=True)
class IncomingLineStart: ref: LineRef; speaker: SpeakerKind; addressee_side: Side; addressee_kind: SpeakerKind; reply_to: Optional[LineRef]; goodbye: bool
@dataclass(frozen=True)
class IncomingLineDone: ref: LineRef; truncated: bool; tail_ms: int; goodbye: bool   # 对端 text{final}
@dataclass(frozen=True)
class ReplyPlan: reply_to: LineRef; not_before: float; goodbye: bool = False
@dataclass
class RoomEffects: reply: Optional[ReplyPlan] = None; cancel_pending_reply: bool = False
                   abort_speaking: Optional[str] = None      # "human_interrupt" | "wrap_up"
                   wrap_up: WrapUpDecision = field(default_factory=WrapUpDecision); say_goodbye: bool = False
                   finalize_reason: Optional[str] = None; yield_once: bool = False; ui_state: Optional[str] = None; violation: Optional[str] = None

class VisitRoom:
    def __init__(self, side: Side, *, max_cat_turns_without_human: int = 6, own_lines_per_visit: int = 40,
                 own_lines_per_minute: int = 6, reply_gap_s: tuple[float, float] = (1.0, 2.5),
                 wrap_up_max_s: float = 45.0, wrap_up_step_s: float = 15.0, wrap_up_propose_timeout_s: float = 5.0,
                 speaking_abort_after_s: float = 10.0, rng=None) -> None: ...
    def next_lp(self) -> int: ...
    def observe_lp(self, lp: int) -> Optional[str]: ...                 # 返回 violation 或 None
    @staticmethod
    def sort_key(ref: LineRef) -> tuple[int, int]: ...                 # (lp, 0 if host else 1)
    def on_incoming_start(self, ev: IncomingLineStart, now: float) -> RoomEffects: ...
    def on_incoming_done(self, ev: IncomingLineDone, now: float) -> RoomEffects: ...
    def on_incoming_wrap_up(self, phase: WrapUpPhase, reason: str, lp: int, now: float) -> RoomEffects: ...
    def on_local_human_line(self, ref: LineRef, now: float) -> RoomEffects: ...
    def on_local_line_started(self, ref: LineRef, reply_to: Optional[LineRef], goodbye: bool, now: float) -> None: ...
    def on_local_line_done(self, ref: LineRef, truncated: bool, now: float) -> RoomEffects: ...
    def on_local_recall(self, now: float) -> RoomEffects: ...           # 「叫她回来」
    def on_tick(self, now: float) -> RoomEffects: ...                   # 超时兜底
    def is_stale(self, plan: ReplyPlan) -> bool: ...
    def may_start_cat_line(self, now: float) -> tuple[bool, str]: ...  # (ok, "ok"|"wrap_up"|"minute_cap"|"visit_cap"|"yield"|"stale")
    def snapshot(self) -> dict: ...                                     # GET /api/visit/state 用
```
`VisitRuntime` 对 `RoomEffects` 的执行顺序固定：violation → finalize_reason → abort_speaking → cancel_pending_reply → wrap_up 出网 → ui_state → say_goodbye → reply。

#### 3.6.4 流式与 TTS 节拍（OD-21 v3 / OD-15 v3）
- **TTS 流式双工（一行一个 speech_id）**：隔离 `OmniOfflineClient` 的 `on_text_delta`（构造参数，`main_logic/omni_offline_client/_client.py`；game `session_pool.py` 已有先例）每收到一段增量，语音开时就推进主 manager 的 TTS 队列，走主聊天推 LLM 增量进 TTS 的同一条路径：新增公共入口 `SessionManager.open_mirror_speech_stream(*, metadata, request_id) -> MirrorSpeechStream`（`push(delta)` / `finish()` / `abort()`；内部复用 `turn.py` `_enqueue_tts_text_chunk / _request_tts_done_locked` 与 mirror 元数据，`mirror_text=False`，不入私聊历史），本行 `metadata=build_mirror_meta(source='neko_visit', kind='visit_line', session_id=visit_id, event={'memory_enabled': False})`、`request_id=ln`。ws_bistream 类 worker 由服务端断句合成，http_sentence 类 worker 在 worker 内用 `SentenceBuffer` 切句（`main_logic/tts_client/_registry_meta.py` 分类表）——上层一律流式喂入，**不再按分句拆成多次 `mirror_assistant_speech`**。本地 TTS 输入**不过出站清洗**（声音只在自家播放，提示词里亲人名已是中性称呼，OD-10）；情绪标签按主聊天推 TTS 前的同一处理剥离。行尾 `finish()` 只发一次 `_request_tts_done_locked`，一行只在末尾收一次 `turn end`，口型在一行内天然连续。
- **分句（只辅助字幕对齐）** `utils/visit_wire.py::ClauseSplitter`（增量版 `split_clauses`，纯函数可单测）：主边界 = `_infra.py:317 _SENTENCE_END_RE` 同一套句末标点（`。！？；…` 与 ASCII `. ! ? ;`）+ 换行；次边界 = 逗顿号只在当前累积分句 ≥ `VISIT_CLAUSE_SOFT_MAX_CHARS=24` 个 CJK 字（或 12 个拉丁词）时才切；短于 2 字并入下一片（对齐 `_MIN_CHARS=2`）；UTF-8 ≤800 B 硬拆在字符边界；行尾 flush 残片。输入是 LLM 增量流；**先脱敏再切片**：`redact_outbound` 作用在本行**累积缓冲**上——每切出一片之前先对缓冲整体脱敏，再从脱敏后的缓冲里切；遇到 800 B 硬切时保留末尾 `max(len(受保护词)) - 1` 个字符不切出（等更多文本到来或行尾 flush 再判），保证受保护词（亲人名等）不会跨片逃过替换（逗号切、800 B 硬切都可能落在名字中间，不能只靠「名字不跨句末标点」）；`strip_emotion_tags` 与 `sanitize_relay_text` 仍逐片做；出站前不再做整行 `truncate_to_tokens(400)`（整行长度由 `max_response_length=VISIT_RESPONSE_MAX_TOKENS` 约束），`text{final}` 仍 `clamp_text_utf8(4096)`；「已放出分片拼接 == `text{final}.txt`」是单测不变量。**24 字只影响字幕切片粒度，不影响出声快慢**（出声由 TTS 流式决定）。
- **为什么不能用现有信号**：后端 `__tts_sentence_done__`（`tts_runtime.py:2005-2026`）是「送达」且只有 http_sentence 类 worker 才有，ws_bistream 类没有；`voice_play_start` 是 turn 级（`dispatchAssistantSpeechStart` 同 turnId 早退 `app-audio-playback.js:739-741`，`resolveAssistantAudioTurnId :1130-1139` 在没有 `gemini_response` 时可能把所有 speech 解析成同一残留 turnId）；`chunk_scheduled` 事件按 `speechId` 发（`:1761-1771`）且带 `scheduledEndAudioTime / audioContextTime`（`:489-535`），但在**调度时**发，可领先真开播最多 5 s（lookahead `:1619`）。
- **播放进度信号链**：`speech_id → ln` 登记（一行一条）。前端 `static/visit/visit-pacer.js` 监听 `neko-speech-playback-state` 中该 speech_id 的 `reason==='chunk_scheduled'`（带 `scheduledEndAudioTime / audioContextTime`）→ 复刻 `:1630-1632` 钳位换算真开播时刻与已播音频时长 → 播放期间约 4 Hz `S.socket.send({action:'visit_speech_progress', speech_id, visit_id, played_ms, ended})`（播完 / 被清掉时发一条 `ended:true`）。推荐的 6 行加法：`:1761-1771` 的 patch 加 `chunkStartAudioTime / chunkDurationSec`（纯加字段，回归报告一段）。后端 `websocket_router.py:1334` 旁 `elif action == "visit_speech_progress": await route_external_page_signal(lanlan_name, message)`（走注册表，game 不注册即忽略）→ `VisitRuntime.on_speech_progress`。
- **放出规则**：第 i 片的放出条件 = `min(自开播起经过的时间, played_ms) ≥ Σ_{j<i} estimate_speech_ms(clause_j)`；放出即发 `line_delta` + 本机 `visit_line_delta{self:true}`；`ended` 或末句 turn end 到达 → 剩余已生成分片一次放出；全部放完后发 `text{final, tail_ms}`。未知 / 旧 speech_id 的 progress 忽略。字幕被「不超过已播音频时长」钳住，不会早于声音开播；语速偏快 / 偏慢的 provider 上字幕会领先或落后实际发音零点几秒（估算）。**实施期必测**：(a) 同一 speech_id 流式推入时口型连续；(b) `chunk_scheduled` 可领先真开播最多 5 s（lookahead）时 `played_ms` 换算正确；(c) 流式入口「推流中途 abort」「finish 后 `audio_done` 对账」「与主聊天 speech_id 不串」。
- **兜底**：首段推入后 `VISIT_TTS_START_TIMEOUT_S=4` 内没收到该 speech_id 的首个 `visit_speech_progress`，或 TTS 未就绪 → 本行切到文本估时并 `status{VISIT_TTS_FALLBACK}`（每场一次），本场剩余各行不再重试 TTS；已放出的分片不重发。**开播后进度中断**：已收到首个 progress 之后，若 `VISIT_SPEECH_PROGRESS_STALL_S=3` 秒没有新 progress 且未 `ended`（前端被清掉却没报 `ended`、页面卡住等）→ 剩余已生成分片改按估时从最后一次 `played_ms` 继续放出（第 i 片条件变为 `played_ms_last + (now − stall_at) ≥ Σ_{j<i} est(clause_j)`）；另设硬上限：后端收到该行 TTS 的 `__audio_done__`（`tts_runtime.py:2028-2046`，送达完成、不是播完）后再加 `estimate_speech_ms(剩余未放出分片)`，到点剩余分片强制一次放完并发 `text{final}`——所以开播后前端再也不回报、也不发 `ended` 时，`text{final}` 仍必发。
- **文本估时** `estimate_speech_ms`（纯函数）：`180 × CJK 字 + 250 × 拉丁词 + 250 × 句末标点 + 120 × 逗顿号`，钳 `[400, 12000]` ms（180/250 是 owner 指定，标点停顿是设计值）。语音关时后端定时器在 `t_i = Σ_{j<i} est(clause_j)` 发第 i 片；`tail_ms = est(末句)`。
- **打断立即停**：人类插话 / 收尾掐断旧行 → `MirrorSpeechStream.abort()`，内部即新增公共方法 `SessionManager.interrupt_mirror_speech()`，把 `turn.py:2130-2155` 的 `interrupt_audio` 前奏（`_clear_tts_pipeline` + `release_speech_playback_gain` + `send_user_activity(current_speech_id)`）抽出来，`mirror_assistant_speech` 内部改调它——行为不变的提取，回归报告一段。立即停播，不等分句边界；「已开口前缀」= 截至此刻**已放出的分片**——两侧看到的字一致，与实际音频相差不超过一个分片。
- **紧急开关** `VISIT_STREAM_DELTAS=False` → 字幕退回整句模式（只发 `text{final}`），恢复 `typing on/off`；TTS 仍流式。
- 首字上屏延迟（估算）：LLM 首段 + TTS 首块 + 通道，与主聊天同量级（v1 整句模式 ≈ 整行 + TTS 送达 + 播完）。TTS 请求 ≈ 每行 1 次、一场 ≈40 次 / 侧（估算；v2 逐分句方案 ≈120 次）；不再按分句拆请求，没有每分句 FINISH 尾延迟。

#### 3.6.5 寻址与路由注册表（OD-03，保留）
`utils/external_route_registry.py`：`ExternalRouteKind(kind, is_active, route_stream_message, on_start_session|None, finalize_for_character, route_voice_transcript|None, on_page_signal|None)`（字段全部在 PR-01 定义；visit 注册时传齐）（配套函数 `route_external_page_signal`）。game 导入期注册（handler = 原函数对象）；`websocket_router.py:51 / :765 / :949 / :1048`、`proactive_chat_flow.py:126-128`、`crud.py:1121-1124` 改调注册表。归属检查：`game_router/runtime.py:1899 game_route_start` 与 `icebreaker_router.py:263` 在各自检查旁加 `if (r:=get_active_external_route(lanlan)) and r.kind != 自身: return {ok:false, reason:'route_owned_by_external'}`。`on_start_session`：visit 对 text ack-only、audio 拒；game 不注册则原分支原样。`main_logic/core/streaming.py:284` 自动建会话前：`if mode=='audio' and await route_external_start_session(name, {'input_type':'audio'}): return`——堵住不经 `:949` 的语音入口。`relay_session.send_text` 的 v1 接口不变，实现换成 outbox → transport WS → iframe → 数据通道。

#### 3.6.6 渲染身份（OD-19，保留）
访客与对方亲人 → role `'tool'`（`message-schema.ts:188` 枚举含 tool；`MessageBubble.tsx` 给 `.message-bubble-tool / .avatar-tool` 类但仓库今天没有这两个类的 CSS 规则）。样式作为**新增规则写进 `static/css/index.css`**，不进 react styles.css，零 React 重建。导出面板 `CompactExportHistoryPanel.tsx` 把 tool 与 assistant 同组——follow-up。

#### 3.6.7 A 侧视图与语音
`visit_state_change{departed}` → `#live2d-container` 加 `.visiting-away`（不用 `.minimized`）+ 「出门中」徽标；只读转录；composer 只留「叫她回来」。`visitVoiceEnabled=true` 时她的声音从 A 的扬声器出来——产品说明：她在邻居家说话，你在自家听见，像开着免提；徽标解释她「不在家」。备选（v1.5 评估）：把 A 的 TTS 当音频轨随视频发给 B（TRTC 下 guest 本就按音频计费、零增量；B 听到她的真声、口型天然对齐；代价 ≈32 kbps 要从 600 kbps 里挤、克隆音色出机的隐私问题）。

#### 3.6.8 退出路径
`finalize_visit_route(state, *, reason, notify_peer=True)`：锁内翻状态 + 派生 `_exit_task`（幂等）。reason 集合：`route_end / recall / wrap_up / peer_left / peer_lost / relay_lost / local_page_lost / declined / idle_timeout(300 s) / max_duration(1800 s，提前 60 s 进收尾) / max_lines(80，违约守卫) / character_switch / manager_replaced / llm_error(连续 5 次) / peer_protocol_violation / delivery_failed(必达项 30 s 未确认) / peer_identity_rejected / peer_blocked / proto_mismatch / kicked / goodbye / shutdown / invite_expired(host 在对端 hello 核验前等待超 `VISIT_INVITE_WAIT_S=600`) / unsupported(能力门 ③ 失败，3.3.4)`。`visit_sweep_loop` 每 2 s。单测：在 `recv` 回调内触发 `peer_protocol_violation`，断言 5 s 内 `leave` 发出且回调未持路由锁。

#### 3.6.9 禁用能力
| 能力 | 主 manager | 隔离会话 |
|---|---|---|
| 工具 | 输入被 router 劫持 | `tool_definitions=[]` |
| 截图 / 视觉 | screen/camera/图片在 route handler 吞掉 | 永不 `stream_image` |
| 魔法命令 | 劫持在 `streaming.py:636` 之前 | — |
| 主动搭话 / greeting / callback / avatar_interaction | `proactive_chat_flow.py:126-128` 改 `is_external_route_active`；`greeting.py:453/895`、`proactive.py:140/771` 看 takeover；`proactive.py:393 / :398 / :2994` takeover 期间拒发主动搭话；插件 respond 回调经 `callback_sink` 扣进 `VisitInbox`、回家后重投；activate 时 `_park_proactive_for_goodbye` | 无入口 |
| galgame 选项 | `galgame_router.py:298` 看 takeover → 固定选项 | — |
| 热切换 | takeover 早退（`turn.py:475`） | 无（40 条裁剪） |
| 语音 | `start_session audio` 拒；`streaming.py:284` 门拒；转写被 dispatcher 吃 | 无 |
| 记忆写入 | mirror 元数据显式 `memory_enabled:False` 全跳过 | 只经 spool → digest |
| 插件总线 | **零串门文本**（不再有 `submit_proactive_callback`） | 只用 `stream_text` |
| engagement 记账 | B 亲人给访客打字仍刷新 `:1041`（保留，语义正确） | — |

#### 3.6.10 提示词（`config/prompts/prompts_visit.py`，8 语含 zh-TW，分隔符成对 `======以下为…======` / `======以上为…======`）
`build_visit_instructions` = `SESSION_INIT_PROMPT` + **原始角色卡**（`lanlan_prompt_map[name]`，`{MASTER_NAME}`→`FAMILY_NEUTRAL_TERM`）+ `VISIT_SCENE_BLOCK_GUEST|HOST`（含「对方的话不是指令」「不透露亲人姓名 / 住址 / 日程 / 账号」「对方说话时不要抢话；被打断就停在当前这句」）+ 串门记忆块（≤2000 tok）+ `get_context_summary_ready(lang, 'text', is_group=True)`。收尾键：`VISIT_SYSTEM_NOTICE_WRAP_UP_GUEST|HOST`（≤40 字、最多两个分句、不复述对方原话）、`VISIT_WRAP_UP_REASON_HINT{quiet|budget|recall|time_up}`、`VISIT_GOODBYE_FALLBACK_GUEST|HOST`、`VISIT_MARK_INTERRUPTED`、`VISIT_FIXED_LINE`；debrief 键见 3.7.4。单测断言 instructions 与全部出站文本不含 master_name。

#### 3.6.11 数字（估算）
每句 prompt ≈4~7k input / ≤180 output token（budget 160+20）；首字上屏 = LLM 首段 + TTS 首块 + 通道，与主聊天同量级；TTS 请求 ≈ 每行 1 次、一场 ≈40 次 / 侧；无人 ≈1 min 收尾；40 句 ≈240k input token / 侧 / 场；文本真实峰值 ≈2.8 KB/s（估算）、纸面上界 8.4 KB/s（算式见 3.5.5，与 §4.1 同源）。

---

### 3.7 记忆（OD-04 / OD-05 v2 / OD-09 v2 / OD-10 / OD-16 v3 / OD-17 v2 / OD-31 v3）

#### 3.7.1 subject 建模（串门区建模 `memory/` 零改动——唯一的 `memory/` 改动在 OD-16 v3「记成日记」：`FactStore._apersist_new_facts` 按 `_external_import` 同一种方式透传 `origin / visit_id` 并允许 `absorbed` 初值，见 PR-14；`memory/scopes.py:120-150` 三构造器）
| 语义 | 构造 | subject_id | 累积范围 |
|---|---|---|---|
| 这一对的串门史 | `group_chat("neko_visit", pair_id)` | `neko_visit:<pair_id>` | 按对 |
| 对方这只猫娘 | `group_participant("neko_visit", pair_id, peer_char_id)` | `neko_visit:<pair_id>:c_…` | 按对 |
| 对方这个人 | **`participant("neko_visit", peer_uid)`** | `neko_visit:<visit_uid>` | **人级、跨所有猫娘对累积**（owner 本意：同一个人带不同猫娘来，画像不分裂） |

`pair_id / peer_char_id` 派生见 3.1；两端各算一遍结果相同（纯函数可单测）。标题表 `get_scoped_persona_section_header`（`config/prompts/prompts_memory.py:3884`）今天按 `subject_kind` 选表，仓库零处 `neko_visit`；新键**按 `(subject_kind, platform)` 选，不按裸前缀**——否则 `participant` 的 `neko_visit:<uid>` 会与 `group_chat` 的 `neko_visit:<pair>` 撞前缀。含 `group_participant` 字面量的文件 7 个（含 `prompts_memory.py`），本设计不加新 kind。segments 批 `speaker_id`：猫娘 `neko_visit:<peer_char_id>`，人 `neko_visit:<visit_uid>`；`speaker_tier="none"`（`routes.py:1297/1346` Literal 合法）；`display_name` 过 `_sanitized_display_name`（`:1831`）。同一 subject ≥5 条未吸收事实才出 reflection（`memory/reflection/_shared.py:41`）——人级主体更容易攒够。缺失 / 不合法 → 记忆读写关闭，串门照常。

#### 3.7.2 读
`resolve_visit_recall_subjects(state)` → `[群, 对方猫娘, 对方亲人]`；bootstrap `POST /internal/memory/{A}/scoped_context`（`app/memory_server/routes.py:2700`，1..8 subjects 顺序即预算优先级，`include_legacy_private=False`）→ ≤2000 tok；每轮 `POST /scoped_mentions`（`:3276`）。**不读 `/new_dialog`**（OD-10：串门会话不带私聊召回；grep 守卫）。写读都经 **`memory/scoped_client.py::ScopedMemoryClient`**（OD-31 v3：自建，直接对 memory_server 五个 `/internal/memory/*` 端点实现 `fetch_bootstrap / post_mentions / post_forget / post_history / post_history_batch`，wire 形状以 memory_server 路由的请求模型为准，以 `b0b283e34` 版 QQ `memory_bridge.py:109/:137/:155/:303/:498`（现已移出仓库）作对照、带 wire 请求体快照单测；分层 L2 memory 可依赖 L1 utils，`scripts/check_module_layering.py:25-31`；只新增。是否经插件 SDK 开放为 bot 公共记忆组件由 owner 与 QQ 插件作者商量后另定）。

#### 3.7.3 写：逐句 spool → 结束时 digest 一次（OD-17 v2）
**先用人话说 v1 为什么攒着写、崩了丢什么**：「写进记忆区」不是写文件，是一次 LLM 调用（`/scoped_history` 收一批对话、抽事实、去重、落库，`routes.py:1949`，一批 1..200 条，`SCOPED_HISTORY_BATCH_MAX_MESSAGES=200`）；一句一调 = 一场 80 次调用且抽出大量噪音事实，所以 v1 和 QQ 群一样攒 40 行（`session_memory_service.py:44`，`b0b283e34`，现已移出仓库）。v1 为了「磁盘上没有对端明文」只放内存 → 进程崩溃 / taskkill / 断电 / 关机 flush 超时 → 这 40 行没了；一场不到 40 行等于整场没记。家里的私聊记忆每轮 `/cache` 就把原文写进 `recent.json`（`routes.py:912`），LLM 抽取才攒批——v1 串门比私聊路径**更不耐崩**。

v2 数据流：
1. `main_logic/visit/spool.py::VisitSpool`，目录 **`config_dir/visit_spool/`**（`storage_roots.py:160`；与 outbox 同目录），每场 `<visit_id>.jsonl` + `<visit_id>.state.json`（`atomic_write_json`，`utils/file_utils.py:785`）。
2. 头行 `{v:1, visit_id, role, own_char, pair_id, peer_uid, peer_char_id, peer_char_tag, started_at, lang}`；之后每句一行 `{lp, side, ln, ts, from:'own_cat'|'peer_cat'|'peer_human'|'own_human', text, truncated, local_memory_at_receipt, peer_consent_at_receipt}`——**盖两枚章**：这句存下来时「我允许记」（`visitMemoryEnabled`）与「对方允许记」（对端最近一次 `consent`）两个是 / 否。`text` 已过 `sanitize_relay_text / clamp_text_utf8(4096)`，单行 <4 KB 单次 `write`（`asyncio.to_thread`，单写线程队列保序）。
3. `fsync` 每 30 s 一次 + finalize 时一次：进程崩溃丢 0 句（内核页缓存还在）；断电最多丢 30 s。
4. `state.json`：`{digested_through_lp, digest_runs, finalized:null|reason, debrief_choice:null|'ask_later'|'committing:diary'|'diary'|'forget', debrief_pending:{diary, facts}|null, debrief_writes:{facts:bool, cache:bool}, peer_revoked_scope}`（`debrief_pending` 暂存「记成日记」生成的日记段与事实、两步都写完后清除；`debrief_writes` 记 `visit_facts` / `/cache` 两步各自是否已成功，启动补录按它只补未完成那步，OD-16 v3）。
5. **digest 触发 = finalize 时一次**（或启动补录时），只吃两枚章都为真的句子；(a) 群 digest → `/scoped_history` 单 subject；(b) 对端两位画像 → segments 批（`speaker_tier="none"`）。**digest 与 debrief 选择无关**：只受 `visitMemoryEnabled` 与对端 consent 控制（裁决 G.2）。`VISIT_DIGEST_INTERVAL_S`（默认 0=关）保留 10 min 周期代码路径：一场硬顶 80 句 / 30 min 一次 `/scoped_history` 装得下，周期 digest 对耐崩没贡献反而让「不记」清不掉前半场；付费档放宽时长时再开。
6. digest 与 debrief 都落地 → 删 `.jsonl`，`state.json` 留 7 天（幂等、诊断）；`forget` 或 `visitMemoryEnabled` 中途关掉 → 立刻删 `.jsonl`。对端撤销 `scope:'all'` → `.jsonl` 与 `state.json` 里的 `peer_uid / pair_id` 一并删（对偶「删名册项」）。
7. **启动补录**：main_server 启动后作为后台任务（不在启动链路上）扫 `visit_spool/`（启动清理只删 `.outbox.jsonl`，`.jsonl` / `.state.json` / `.upload.json` 都留给这里与转录重传）：`finalized` 非空但未 digest → 补 digest；`finalized` 为空（崩溃）→ 标 `finalized='crash'`，**两件事分开**：补串门区 digest（`commit_visit_region`，只受 `visitMemoryEnabled` 与对端 consent 控制，与 debrief 无关）+ **不写任何私聊记忆**（不自动写日记）+ 重新弹 debrief 芯片 + status「上次串门意外中断」，spool 保留 7 天等用户选；memory_server 不可用则下次启动再试；文件 >7 天或目录 >20 MB 直接删（`event_logger` 同规）；**20 MB 上限清理跳过 `.upload.json`**——待传转录只受「自结束起 7 天」约束（OD-26 v3）。
8. `visitMemoryEnabled=false`：不建 spool 文件，转录只在内存，OD-26 导出从内存给；为 true 时导出改读 spool（页面重载也能导）。**例外**：待上传 Servers 的转录与记忆开关无关，finalize 时一律写 `<visit_id>.upload.json`（只含上传字段，原子写，`0o600`），上传成功即删、失败下次启动重试、自结束起 7 天仍失败则放弃并记本地诊断事件（OD-26 v3、§4.7）。
9. 权限：`_write_private_json` 同款 `0o600`（`card_drop_router.py:620-630`；Windows 无效，同凭证文件立场）。**Steam 云存档只同步 `MANAGED_MEMORY_FILENAMES`（`utils/cloudsave_runtime/snapshots.py:196/:330/:416`），spool 不会被同步。**
成本与风险：一场 ≤80 句 × ~300 B ≈ 25 KB；目录硬顶 20 MB；LLM 结束时 1 次（比 v1「30 min ≈4~5 次」更少）；风险 1 对端原文短暂落盘（digest 后即删、`forget` 即删、7 天硬顶、owner-only 权限；用户本来就能 OD-26 导出全文）；风险 2 `fsync` 30 s → 断电最多丢 30 s。

#### 3.7.4 回家汇报 debrief（OD-16 v3，做进本次交付的小 PR，≈3~4 人日估算）
```
finalize（leave → release_takeover → 仪式句 之后）
  ├─ 1 简述生成：读 spool 全场 → 只取「可 digest」的对端句 + 我方全部句
  │     → 隔离会话 stream_text(======以下为系统通知====== 你到家了。用两三句话跟家里人讲讲今天去了谁家、聊了什么。
  │        不许复述对方原话。======以上为系统通知======  ======以下为本场记录======…======以上为本场记录======)
  │        ≤200 output tok，_llm_turn_lock 内，wait_for 8 s → strip_emotion_tags → redact_outbound → assert_no_peer_ngram(n=8)（命中 → 8 语固定句）
  ├─ 2 出声：mgr.mirror_assistant_speech(简述, metadata=build_mirror_meta(source='neko_visit', kind='visit_debrief',
  │        session_id=visit_id, event={'memory_enabled': False}))   ← mirror_meta 显式键 → 不进私聊历史（3.7.8）
  ├─ 3 芯片（仅 visitMemoryEnabled=true 且 spool 有可 digest 句）：mgr.render_chat_blocks([
  │        {type:'text', text:t('visit.debrief.question')},
  │        {type:'buttons', buttons:[
  │           {id:'diary',     label:t('visit.debrief.choiceDiary'),     action:'visit_debrief_choice', payload:{visit_id, choice:'diary'}},
  │           {id:'forget',    label:t('visit.debrief.choiceForget'),    action:'visit_debrief_choice', variant:'danger', payload:{visit_id, choice:'forget'}}]}
  │     ], request_id=f'visit-debrief:{visit_id}', source='system', source_name=<猫娘名>)      （turn.py:2001-2050；adapter author 取 source_name）
  ├─ 4 前端 static/app/app-react-chat-window/visit-chat.js 监听 'react-chat-window:action'（message-bundle-actions-and-prompts.js:322-337 已派发、全仓零监听）
  │     action=='visit_debrief_choice' → POST /api/visit/debrief/choice {visit_id, choice}
  │     → 200 后经既有宿主事件 'react-chat-window:update-message'（resize-drag-and-api.js:434-451）把两个按钮置 disabled，追加 status 块
  │     ⚠ 监听器必须也加载在 chat.html（Electron 分发态聊天在独立窗口），按 index.html 宽 / 窄 + chat.html 三上下文验证
  └─ 5 后端 POST /api/visit/debrief/choice（幂等：同 visit_id 第二次 409 already_chosen）
        diary      → 再一次 LLM，一次调用同时产出两样（同上清洗与 n-gram 断言）：
                     (a) 第一人称日记段 ≤300 tok → POST /cache/{lanlan} input_history=[{"type":"ai","content":日记段}] → 近期记忆
                         （get_internal_http_client，utils/http/internal_client.py:69；/cache 写 recent.json + db；
                          事实抽取 signal_extraction.py:494 跳过无用户消息的窗口 → 这条独白不会被抽成长期事实）
                     (b) ≤VISIT_DIARY_FACTS_MAX=3 条串门事实（每条 ≤60 字）→ POST /internal/memory/{lanlan}/visit_facts（新增）
                         → 私聊事实池：source=ai_disclosure、importance=4、absorbed=True、origin=neko_visit、visit_id；
                          走 FactStore._apersist_new_facts 语义去重；reflection 只取 importance≥5 且未 absorbed（facts.py:5483）→ 永不合成；
                          召回能取到；card_forge_facts.py 抽样前过滤 origin==neko_visit → 不进铸卡
        forget     → 不写私聊；spool 立即删除；名册 last_seen 仍更新（拉黑 / 清除入口需要它）
        超时 / 崩溃 → VISIT_DEBRIEF_DEFAULT='ask_later'：芯片保留可点（spool 保留 7 天），不自动记；7 天未答自动删 spool、芯片置灰「未记录」
```
三点说明：(1) 简述那句本身**不进**私聊记忆，进记忆的只有用户选的产物——日记段进**近期记忆**（`/cache`），另有 ≤3 条串门事实进**长期记忆的 fact 层**（`visit_facts`，`importance=4 + absorbed=True`，**不进 reflection 层**，owner 2026-09-30：以免时间长了堆满无关信息）；v1「这句会进普通记忆」的告知文案删除；(2) 不再调用 `submit_proactive_callback`，插件总线上不再出现串门任何文本；(3) `visitMemoryEnabled=false` 时只做 1~2，不出芯片，她只口头说一句。对端 `consent=false` 时：步骤 1 的记录块不含对端句，指令加「不要提对方亲人」。8 语 key（`static/locales/*.json` 同 hunk，`scripts/check_i18n_sync.py:16-25` 会卡）：`visit.debrief.question / choiceDiary / choiceForget / savedDiary / forgot / askLaterHint / memoryOffHint`；后端 `VISIT_DEBRIEF_INSTRUCTION / VISIT_DIARY_INSTRUCTION / VISIT_DEBRIEF_FALLBACK`。`/cache` 收到只含 AI 消息的批次：`_has_human_messages` 为假、跳过 review-clean（`routes.py:968`），`recent.json` 出现一条她的独白——预期（她「跟你说过」）；它不会被后台事实抽取吃成长期事实（`signal_extraction.py:494` 跳过无用户消息的窗口），长期记忆只经 `visit_facts` 那 ≤3 条。

#### 3.7.5 名册与黑名单（主键 `visit_uid`）
- **名册 `config_dir/visit_peers.json`**（按本机角色分开）：`{peers: {<visit_uid>: {display_name, short_code, first_seen, last_seen, by_char: {<本机角色名>: {pairs:[pair_id], chars:{peer_char_id:{char_tag, display_name, last_seen}}}}}}}`。它是记忆浏览器与「清除这个人」的索引——`pair_id` 是双方 id 的哈希，光看 subject 列表无法反推「哪些 pair 涉及某个人」。
- **黑名单 `config_dir/visit_blocklist.json`**：`{blocked:[{visit_uid, display_name_at_block, blocked_at, reason?}]}`。生效点：(a) hello 核验 `sub` 命中 → 立即离房、`finalize('peer_blocked')`（对端只看到「离开」）；(b) 邀请 / 接待 UI 拿到对端 `visit_uid` 时直接灰掉（接口预留）。换角色、换 `char_tag`、换机器都绕不过（uid 由 Servers 钉死；`char_tag` 自报只影响自己命名空间）。
- 「清除这个人」**只作用于当前角色**：对当前角色（`{name}`）下的 `participant` 单 subject + 该人在 `by_char[当前角色]` 下所有 pair 的 `group_chat` 与 `group_participant` 逐个 `POST /internal/memory/{name}/scoped_forget`（`routes.py:2796`；信赖池未加载时 fail closed，UI 提示稍后重试）+ 删 `by_char[当前角色]`；`by_char` 为空时才删整条 peer。拉黑不是记忆：撤销 / 清除不动黑名单。
- 举报 `POST {social_base}/api/visit/reports {visit_id, peer_uid, transcript(OD-26 导出格式), reason}` → Servers 管理员按 `visit_uid` 封禁（3.8）；Servers 另有该场双方各自上传的转录（OD-26 v3 `POST /api/visit/transcripts`）作证据，不依赖本机文件是否还在。

#### 3.7.6 consent 与撤销（OD-09 v2，一句一个意思）
三开关都在设置页「串门」分组、都进 `ALLOWED_CONVERSATION_SETTINGS`（`utils/conversation_settings_constants.py:17-41`）；`visitEnabled` 与 `visitMemoryEnabled` 进 `_USER_OWNED_FIELDS`（`main_routers/proactive_router.py:58-60` **与** `plugin/plugins/proactive_controller/__init__.py:43` 镜像两处同步）。
1. `visitEnabled`（默认关）：管「她能不能出门、别人能不能邀请她」。关着拒绝一切邀请也不能出门；中途关掉 → 正在串门立刻结束、固定句告别、不走自然收尾。
2. `visitMemoryEnabled`（默认关）：管「这场串门记不记」。开着每句进 spool，结束后问你怎么记；关着时她不记、不出芯片，她回家只口头说一句不问——为了上传转录，待传内容会临时存到上传成功为止（`.upload.json`，OD-26 v3）；中途关掉 → 这场按「不记」处理、spool 立刻删、结束不出芯片。它同时决定我们向对端宣告的 `consent{memory}`。
3. `visitVoiceEnabled`（默认开）：管「串门时她用不用本地 TTS 出声」；不是同意开关，中途切换从下一句生效，不影响记忆。
4. 每一句存下来时同时记下当时「我允许记」和「对方允许记」两个是 / 否；最后只把两个都是「是」的句子拿去做记忆。
5. 对端 `consent{memory:false, scope:'session'}`：这场里对端猫娘和对端亲人说过的话全部标为不可 digest；我方自己的话照常。
6. 对端 `consent{memory:false, scope:'all'}`：除第 5 条外，对这一对的三个 subject 各调一次 `/scoped_forget`——**只作用于这场串门所属的本机角色**：只清该角色下的 subjects 与名册 `by_char[该角色]`，`by_char` 为空才删整条 peer（该人与本机其它角色的串门记忆不动），`.jsonl` 与 `state.json` 里的 `peer_uid / pair_id` 一并删。代价：我方自己对这一对的串门史也一起清空（群 digest 里混着对方事实切不开）——UI 明说。只清本机记忆，**不删 Servers 上的云端转录**（OD-26 v3，随保留期到期）。
7. 对端撤销不影响我方黑名单；我方拉黑也不影响对端记忆。
8. 我方撤销的对偶：设置页「让对方忘掉我」= 向对端发 `consent{memory:false, scope:'all'}`（只在同一场在飞时可达；离线后无通道，UI 说明「对方机器上的副本无法远程清除」）。
9. 发布总闸 `NEKO_VISIT_ENABLED` 环境变量（默认关）：关着时 `/api/visit/*` 全部 404，设置页不显示分组。
接收边界章先例：QQ `message_dispatcher.py:432-449`（`b0b283e34`，现已移出仓库）在消息进队列时盖章，处理侧不晚读设置。

#### 3.7.7 UI
设置页三开关；记忆浏览器「串门记忆」面板（OD-18 只读端点 `GET /internal/memory/{name}/scoped_subjects?platform=neko_visit` + `GET /api/visit/memory/peers`）按 **`visit_uid`** 聚合 → 每人一行「小明 · 3 只猫娘 · 最近 9-20」，展开到 pair / 角色；「清除这个人」；黑名单折叠区；UI 只显示 display_name 与 6 位短码，永不显示完整 id（本机 `GET /api/visit/memory/peers` 响应里保留完整 `peer_uid`，只供「清除 / 拉黑」按钮调端点用，§4.6）。文案如实：「只清本机串门记忆区；对方机器上的副本无法清除」。**隐私后果明说**：同一账号在所有对端机器上是同一个 id → 两个对端可对照确认「是同一个人」（跨对可关联，现在是设计）；对端磁盘留你的稳定 id（不是裸 uuid）；Servers 换盐 = 所有对端变新人（运维文档）。

#### 3.7.8 私聊记忆的唯一入口
`mirror_meta.is_mirror_event_memory_disabled`（`main_logic/mirror_meta.py:84-108`）加显式 `memory_enabled` 键：串门所有 mirror event 传 `{'memory_enabled': False}`，不再依赖「无用户输入 → 过滤」的默认分支（唯一消费点 `main_logic/cross_server.py:911`）。因此仪式句、简述句、串门台词的流式 TTS 都不进私聊历史；进私聊记忆的只有 debrief 里用户选「记成日记」的产物：日记段经 `/cache` 进近期记忆（只含一条 AI 消息，`signal_extraction.py:494` 不会把它抽成长期事实），外加同一次 LLM 抽出的 ≤`VISIT_DIARY_FACTS_MAX=3` 条串门事实经新端点 `POST /internal/memory/{lanlan}/visit_facts` 进 fact 层（`importance=4`、`absorbed=True`、`origin='neko_visit'`：召回能取到，reflection 永不合成，铸卡抽样排除）。OD-10 保留：串门会话不读私聊记忆；「敏感记忆筛除」共享基础设施 issue 草稿见 §2 OD-10（全局「完全隔离亲人记忆」开关**默认 False**、筛除不改变现网铸卡结果、老数据回填走后台任务）。

---

### 3.8 安全（威胁模型 → 闸门表）

| 威胁 | 闸门 | 落点 |
|---|---|---|
| 假冒对端 | Servers 签的 Ed25519 身份票（`kid` 查内置公钥表 / `GET /api/visit/pubkeys`，拉不到 fail closed）+ `aud/visit_id/role 互补/exp ±300 s` + **`vid == vendor 盖的发送者 id`**（TRTC `CUSTOM_MESSAGE.userId` / LiveKit `participant.identity`）+ 同房同 `vid` 才允许重放 `jti` | `main_logic/visit/identity.py` |
| 第三者领到本房凭证旁听（TRTC 自定义消息全房广播、任何房内成员可订阅视频） | **房间绑定**：host 领凭证时 Servers 把 `visit_id` 登记到 host 的 `visit_uid` 下并返回一次性 `invite_code`（10 min）；guest 领凭证必须带它；每房最多 host + guest 各一，第三者领不到；客户端 guest 侧 `credentials.peer_vid` 必填；host 侧 `hello` 核验通过后经 `media{peer_vid}` 下发 `peer_vid`；`from_vid ≠ peer_vid` 丢弃，第二个未知 `vid` 进房 → `leave{peer_protocol_violation}`；LiveKit 侧 token 只授本房 `roomJoin` | Servers / `credentials.py` / `transport_ws.py` |
| 未登录 / 被封禁 | Servers 拒发凭证（401 / 403 `blocked`）；本地黑名单按 `visit_uid` 在 hello 阶段拒（`peer_blocked`） | Servers / `limits.py` |
| **封禁闭环（在飞场）** | Servers `POST /admin/visit/bans {visit_uid, until?}` → 拒发新凭证（下一场即生效）；在飞场：Servers 对该 uid 活跃签发记录调 vendor 服务端踢人——TRTC `RemoveUserByStrRoomId`（https://cloud.tencent.com/document/product/647/50426 ）/ LiveKit `RoomService.RemoveParticipant`（https://docs.livekit.io/home/server/managing-participants/ ），客户端把 `KICKED_OUT{banned}` / `Disconnected` 当终态无需改；**Servers 侧 follow-up，不阻塞 v1**；40~50 min TTL 把被封账号持有有效凭证的窗口压到 ≤40 min | Servers |
| 票据 / 凭证重放 | 票绑 `visit_id + vid + role`，跨房无效；vendor `userId` 需要 UserSig / JWT 才能占用；`visit_id` 128 bit 不可猜；`jti` 只在同房同 `vid` 可复用；TTL 40 min | `identity.py` / Servers |
| 假 vendor / 中间人 | TRTC 无 URL 可配；LiveKit URL 来自 Servers 且主机名命中 `VISIT_LIVEKIT_HOSTS`；页面不接受任何来自对端的 URL；WebRTC DTLS-SRTP | `credentials.py` / `livekit-transport.js` |
| 凭证泄漏 | vendor 凭证只经 transport WS 下发到同源 iframe，不进父页 / display socket / preload console.log / 日志；票据留在后端不给 iframe | `transport_ws.py` |
| **IP 暴露** | TRTC / LiveKit 都是 SFU：ICE 只在客户端与 SFU 之间，**对端拿不到你的 IP**；vendor 与 Servers（以来源 IP 复核区域）可见；不做 P2P | 设计 |
| 对端文本当指令 | 隔离会话 + nonce 信封 + `sanitize_relay_text` + `truncate_to_tokens(400)` | `sanitize.py` |
| 对端自报 `sp:'h'` 刷轮次 / 冒充亲人 | 显示名只取 hello profile；对端人类清零 6 句计数但**改不了**本侧 40 句 / 每分钟 6 句硬顶 | `room.py` |
| 冒名显示名 | casefold+NFC 与本机亲人 / 猫娘名相等 → 通用标签 + 短码 | `sanitize.py` |
| 亲人隐私出境 | 原始角色卡 + `FAMILY_NEUTRAL_TERM`；`OmniOfflineClient(master_name=中性词)`；`redact_outbound` 第二道；不读私聊记忆（OD-10）；单测断言出站不含 master_name | `subjects / sanitize / session_pool` |
| 私聊记忆污染 | mirror 元数据显式 `memory_enabled:False`；进私聊记忆的只有用户亲手选的 debrief 产物（日记段进近期记忆 + ≤3 条事实进 fact 层，`importance=4 / absorbed=True` 不进 reflection、`origin='neko_visit'` 不进铸卡）；`ln / seq` LRU(512) 幂等 | `mirror_meta.py` / `debrief.py` / `outbox.py` |
| 数据通道灌水 / 超长 / 畸形 | 每发送者 `text ≤20/10 s`、ctl ≤4/s、总 ≤5 KB/s、≤20 条/s；`>1000 B` 片 / `i` 跳变 / 两行交叠 / `lp` 不单调（只查新开的行 / 控制事件，重传保留原 `lp`）→ 丢弃计数，**连续 20 条异常才** `peer_protocol_violation`；未知 `t` / 字段忽略（版本偏斜不算违约） | `limits.py` / `visit_wire.py` |
| 版本偏斜 | `hello.caps.proto` 主版本不同 → `leave{proto_mismatch}` + toast | `identity.py` |
| 视频取自屏幕 | 打包源只能是模型画布裁剪矩形（parent-bridge 只传模型画布引用）；iframe 不调 `getUserMedia` | `parent-bridge.js` / `pack.js` |
| 跨对关联 | `visit_uid` 跨对稳定 = 设计选择（owner OD-05）；不是裸 uuid，Servers 可反查；UI 只显短码；如实写进 UI 与 README | `subjects.py` |
| 跨区连通无证据 | 两侧区域不同 → Servers 403 `cross_region_unsupported`（fail-closed；T9 后可改「允许 + 警告」） | Servers |
| 账单失控 | Servers 每账号「每日签发分钟数」（免费档 `VISIT_FREE_MINUTES_PER_DAY` 占位 120）+ 并发 ≤2 房 + TTL guest 40 / host 50 min；客户端硬顶 30 min / 80 句只是体验约束 | Servers |
| 改客户端推高画质档位（开源客户端可改 SDK 参数） | LiveKit：host token `canPublish:false`（只 `canPublishData`）、guest token `canPublishSources:['camera']`，Servers 订阅 `track_published` webhook（带宽高）超档即 `RemoveParticipant` 踢人 + 记违规；TRTC：Servers 定时拉用量统计 / 事件回调按账号比对实际分辨率档与码率，超档走封禁流程；host 能否以观众角色进房仍收发自定义消息待 T13；处置之前单账号最坏按凭证允许的最高档计费（3.5.8） | Servers / `credentials.py` |
| 本机读端点被局域网 / Docker 旁路读取（状态、转录、名册、邀请码） | `GET /api/visit/state`、`/transcript`、`/details/{visit_id}`、`/memory/peers`、`/invites/{invite_code}/preview` 一律过与变更端点相同的本机来源校验（`_validate_local_mutation_request` 同款 Origin / Host 白名单 + CSRF token），Docker / 局域网访问不放行；`invite_code` **不进** `GET /api/visit/state` 响应，只经 display socket 推给本机前端 | `visit_router/http.py` / `memory_routes.py` |
| 错误信息泄漏 | 只发错误码；日志不含 text / display_name / 票据 | `runtime.py` |
| 举报留证 | 双侧 outbox / spool JSONL（带 `lp / seq / ts`）+ `GET /api/visit/transcript` 导出 + `POST /api/visit/reports` + 每场双方各自上传 Servers 的转录（OD-26 v3）；**无第三方盖章**（放弃中继后如实承认；云端两份转录各由一侧自报） | `visit_router` / Servers |
| 亲人知情 | guest 出门前确认框（对端名 + 短码 + 跨区提示；**不显示 token / TTS 等技术数字**）；host 接待确认（60 s）；guest 只在 host `ready` 后发布视频；`visitEnabled` 只是允许被邀请 | 前端 + `/accept` |
| 云端转录与用量（OD-26 v3） | finalize 后本机后端把本侧转录（已过出站清洗的 `text{final}`，本侧亲人行是原文）+ 本场用量 `POST {social_base}/api/visit/transcripts`（OAuth Bearer，按 `visit_id + role` 幂等，双方各传自己那份）；与 `visitMemoryEnabled` 无关；待传内容一律临时存 `config_dir/visit_spool/<visit_id>.upload.json`（只含上传字段、原子写、`0o600`），上传成功即删、失败下次启动重试、7 天仍失败放弃并记本地诊断事件；「查看详情」`GET /api/visit/details/{visit_id}` 只有该场双方账号与管理员可读；Servers 长期保留（与账单记录同期）；对端撤销 `scope:'all'` 不删云端转录；只在隐私政策披露，确认框不提；用量计数走现有遥测 counter / histogram（不带 visit_id） | `visit_router` / Servers / 隐私政策 |
| 隐私模式误用 | privacy 模式不作串门开关 | settings |
| 开发环回 | `NEKO_VISIT_DEV_KEYFILE` + `scripts/visit_dev_mint.py`：核验路径与生产**同一条**（只是多一把开发公钥），不存在「跳过验签」分支；PSK 产品路径删除 | `identity.py` |

---

### 3.9 代码落点

分层落位（`scripts/check_module_layering.py:25-31`：utils L1 < memory L2；`main_routers` L3 可 import `memory/`）：

| 层 | 新增 | 修改 |
|---|---|---|
| L0 config | `config/visit_settings.py`（下表常量、`VISIT_TIERS`、`VISIT_SERVERS_PUBKEYS`、`VISIT_LIVEKIT_HOSTS`）、`config/prompts/prompts_visit.py`（8 语含 zh-TW） | `config/__init__.py`（re-export）、`config/prompts/prompts_memory.py`（`neko_visit` 标题两表按 `(subject_kind, platform)` 选） |
| L1 utils | `utils/visit_wire.py`（分片信封 + 消息 pydantic schema、`split_clauses` / 增量 `ClauseSplitter`、`estimate_speech_ms`、`encode_*`；zod 对偶在 `static/visit/transport/wire.js`；**无 NKVF/NKVC/order**）、`utils/visit_route_state.py`（含 `phase='pending'`）、`utils/external_route_registry.py`（v1 + `route_external_start_session / route_external_page_signal`） | `utils/conversation_settings_constants.py:17`（`visitEnabled / visitMemoryEnabled / visitVoiceEnabled`） |
| L2 memory | `memory/scoped_client.py`（OD-31 v3，自建，直接对 memory_server 五个 `/internal/memory/*` 端点；以 `b0b283e34` 版 QQ 实现作对照、带 wire 请求体快照单测） | — |
| L2 main_logic | `main_logic/visit/`：`identity.py`（验签 + claims + 黑名单 + jti 窗口）、`outbox.py`（seq / 累计 ack / 1→2→4→8→8 s / LRU(512) / `.outbox.jsonl`）、`room.py`（**Lamport `lp` + `reply_to`** + 收尾状态机，无 order 分配）、`heartbeat.py` / `liveness.py`（`hb` 5 s；`peer_last_seen` 30 s / 自身 25 s / 页面 20 s 三计时器，纯函数可单测）、`spool.py`、`debrief.py`（简述 / 日记两条 prompt 组装 + 写入路径）、`subjects.py`（`pair_id / peer_char_id / participant` 派生纯函数）、`sanitize.py`（含 `assert_no_peer_ngram`）、`consent.py`、`limits.py`（令牌桶 + blocklist）；`main_logic/core/takeover.py`（`TakeoverMixin`：`acquire_takeover / release_takeover`，OD-24——`scripts/check_core_contracts.py` 的 `CORE_MANAGER_SHAPE` 门规定 `manager.py` 类体只有常量 + `__init__`，故落新 mixin） | `main_logic/core/manager.py`（只加 `TakeoverMixin` base 与 `_takeover_token` 属性）、`main_logic/core/turn.py`（提取公共 `interrupt_mirror_speech`，行为不变；新增公共流式 mirror 入口 `open_mirror_speech_stream -> MirrorSpeechStream{push/finish/abort}`，复用 `_enqueue_tts_text_chunk / _request_tts_done_locked`，必要时连带 `tts_runtime.py`，回归报告一段）、`main_logic/core/streaming.py:284`（audio 自动建会话门）、`main_logic/mirror_meta.py:84`（显式 `memory_enabled` 键）、`main_logic/card_forge_facts.py`（抽样前过滤 `origin=='neko_visit'`，邻居家的内容不进社区分享卡片；无该字段的存量事实结果不变） |
| L3 main_routers | `main_routers/visit_router/`：`credentials.py`（Servers 客户端：`fetch_visit_credentials`、`fetch_invite_preview`（邀请只读预览）、错误码映射、`invite_code`）、`transport_ws.py`（OD-29）、`runtime.py`（`VisitRuntime`：activate / finalize / `_speak_line` / `on_speech_progress` / effects 执行器）、`session_pool.py`（`trim / pop_trailing_ai_message` 对 `len≤1` 早退）、`debrief.py`（`POST /api/visit/debrief/choice` 幂等 + ask_later）、`memory_routes.py`（peers / forget / block / transcript）、转录上传任务（`POST {social_base}/api/visit/transcripts` + 重试）与 `GET /api/visit/details/{visit_id}` 代理（OD-26 v3）；`pages_router` 加 `GET /visit/transport` | `websocket_router.py`（只剩注册表 `:51 / :765 / :949 / :1048` + goodbye 分支 + `:1334` 旁 `visit_speech_progress`；**二进制分支 diff 为空**）、`game_router/runtime.py:1899-2076`（归属检查 + `:2066-2076` 改 `acquire_takeover`）、`postgame.py:1277-1278`（改 `release_takeover`）、`icebreaker_router.py:263`（归属检查）、`system_router/proactive_chat_flow.py:126-128`、`characters_router/crud.py`（`:757` 守卫、`:1121` 注册表）、`proactive_router.py:58`（`_USER_OWNED_FIELDS`） |
| L4 plugin | — | `proactive_controller/__init__.py:43` 镜像加两键（QQ 插件已移出仓库，无委托改动；bot 公共记忆组件另定，OD-31 v3） |
| L6 | `deploy/livekit/`（GCP 阶段 compose + Caddy + README）、`scripts/visit_dev_mint.py` | `app/main_server/__init__.py`（`visit_sweep_loop`；`on_shutdown` **最前** `await stop_all('shutdown')` ≤3 s；启动后 `create_task` spool 补录，不在启动链路上）、`web_app.py include_router`、`app/memory_server/routes.py`（只读枚举端点，OD-18；新增写端点 `POST /internal/memory/{lanlan}/visit_facts`，OD-16 v3，复用 `FactStore._apersist_new_facts` 语义去重） |
| 前端 | `templates/visit_transport.html`、`static/visit/transport/{transport,trtc-transport,livekit-transport,pack,unpack,frame-sink,backend-ws,wire}.js`、`static/visit/parent-bridge.js`、`static/visit/visit-pacer.js`、`static/visit/text-mouth-driver.js`、`static/app/app-react-chat-window/visit-chat.js`（出门确认框 / 接待确认 / 导出 / 查看详情入口 / debrief 监听）、`static/libs/trtc.js` + `static/libs/livekit-client.umd.js` + `static/libs/licenses/*` | `app-websocket.js`（`visit_*` JSON 分支；**Blob 分支 `:3058-3066` 不动**）、`app-chat-adapter.js`、`app-audio-playback.js:1761-1771`（可选两字段）、`index.css`（`.message-bubble-tool / .avatar-tool`、`.visiting-away`）、`live2d-core.js:1029`（+1 行）、`vrm-manager.js:877` / `mmd-core.js:1291`（+1 行）、`templates/index.html / chat.html`（`visit-chat.js` 三上下文加载）、8 locale + LOCALE_VERSION、`memory_browser.html/js`、`static/libs/THIRD_PARTY_NOTICES.md`、`scripts/check_nuitka_dist.py:53-71 _REQUIRED_ASSETS` |
| 闭源 | **lanlan_frd 无必需改动**（可选 follow-up：销毁窗口前先发 leave） | Servers：`POST /api/visit/credentials`（房间登记 + `invite_code` + 区域 → transport + 跨区 403 + 每日分钟配额 + entitlement）、`GET /api/visit/invites/{invite_code}/preview`（只读、不消耗邀请码）、`GET /api/visit/pubkeys`、`POST /api/visit/reports`、`POST /api/visit/transcripts`（按 `visit_id + role` 幂等、长期保留）、`GET /api/visit/details/{visit_id}`（该场双方与管理员可读）、`POST /admin/visit/bans`、UserSig（tls-sig-api-v2）/ JWT（HS256）签发、Ed25519 密钥 kid 轮换；（follow-up）在飞踢人 |

`config/visit_settings.py` 常量（与 §4 / §5 同源）：
- 生命周期：`VISIT_HEARTBEAT_S=5`、`VISIT_PEER_LOST_S=30`、`VISIT_SELF_RECONNECT_S=25`（上限）、`VISIT_RECONNECT_MARGIN_S=3`、`VISIT_LOCAL_PAGE_GRACE_S=20`、`VISIT_SHUTDOWN_BUDGET_S=3`、`VISIT_INVITE_WAIT_S=600`、`VISIT_INBOX_HANDOFF_MAX_S=20`、`VISIT_ACCEPT_TIMEOUT_S=60`、`VISIT_IDLE_TIMEOUT_S=300`、`VISIT_MAX_DURATION_S=1800`。
- 身份：`VISIT_CREDENTIAL_TTL_S=2400`（guest）、`VISIT_HOST_CREDENTIAL_TTL_S=3000`（host）、`VISIT_TICKET_CLOCK_TOLERANCE_S=300`、`VISIT_INVITE_CODE_TTL_S=600`、`VISIT_SERVERS_PUBKEYS`、`VISIT_PUBKEYS_CACHE_S=86400`。
- 传输：`VISIT_TIERS`、`VISIT_WIRE_PROTO=1`、`VISIT_DATA_BUCKET_BPS=5120` / `VISIT_DATA_BUCKET_BURST_BYTES=3072`、`VISIT_MSG_BUCKET_PER_S=20` / `VISIT_MSG_BUCKET_BURST=10`、`VISIT_PIECE_MAX_BYTES=1000`、`VISIT_PIECES_MAX=8`、`VISIT_REASSEMBLY_TIMEOUT_S=2`、`VISIT_DELTA_TEXT_MAX_BYTES=800`、`VISIT_TEXT_MAX_BYTES=4096`、`VISIT_DELTA_MIN_INTERVAL_MS=250`、`VISIT_DELTA_BACKLOG_MERGE_S=3` / `VISIT_DELTA_BACKLOG_DROP_S=10`、`VISIT_OUTBOX_RETRY_S=(1,2,4,8,8)`、`VISIT_ACK_COALESCE_MS=200`、`VISIT_DEDUP_LRU=512`、`VISIT_REORDER_BUFFER_MAX=64`、`VISIT_ANOMALY_FINALIZE_COUNT=20`、`VISIT_LINE_STALL_S=20`、`VISIT_LIVEKIT_HOSTS`。
- 对话：`VISIT_MAX_CAT_TURNS_WITHOUT_HUMAN=6`、`VISIT_OWN_LINES_PER_VISIT=40`、`VISIT_OWN_LINES_PER_MINUTE=6`、`VISIT_REPLY_GAP_S=(1.0, 2.5)`、`VISIT_WRAP_UP_STEP_S=15`、`VISIT_WRAP_UP_MAX_S=45`、`VISIT_WRAP_UP_PROPOSE_TIMEOUT_S=5`、`VISIT_SPEAKING_ABORT_AFTER_S=10`、`VISIT_MAX_LINES=80`、`VISIT_LINE_MAX_TOKENS=400`、`VISIT_HUMAN_LINE_MAX_TOKENS=600`、`VISIT_RESPONSE_MAX_TOKENS=160`、`VISIT_HISTORY_MAX_MESSAGES=40`、`VISIT_CONTEXT_MAX_TOKENS=2000`、`VISIT_LLM_TIMEOUT_S=20`、`VISIT_CEREMONY_TIMEOUT_S=8`、`VISIT_GOODBYE_LLM_TIMEOUT_S=8`、`VISIT_CLAUSE_SOFT_MAX_CHARS=24`、`VISIT_TTS_START_TIMEOUT_S=4`、`VISIT_SPEECH_PROGRESS_STALL_S=3`、`VISIT_CLAUSE_MIN_MS=400`、`VISIT_CLAUSE_MAX_MS=12000`、`VISIT_STREAM_DELTAS=True`。
- 记忆：`VISIT_SPOOL_FSYNC_S=30`、`VISIT_SPOOL_RETENTION_DAYS=7`、`VISIT_SPOOL_DIR_CAP_BYTES=20MB`、`VISIT_DIGEST_INTERVAL_S=0`、`VISIT_DEBRIEF_MAX_TOKENS=200`、`VISIT_DIARY_MAX_TOKENS=300`、`VISIT_DEBRIEF_DEFAULT='ask_later'`。
- **删除的 v1 常量**：`VISIT_RELAY_ENDPOINTS / VISIT_RELAY_URL / VISIT_RELAY_PSK / VISIT_RELAY_GRACE_S=90 / VISIT_LOCAL_SOCKET_GRACE_S=10 / VISIT_PEER_HUMAN_RESETS_MAX=5 / VISIT_MIN_CAT_REPLY_GAP_S / VISIT_READ_DELAY_* / VISIT_FRAMES_IN_FLIGHT / VISIT_MEMORY_SHUTDOWN_FLUSH_S / VISIT_TIERS 三档 lite/standard/hd`。
- 每 PR 门禁自检：沿 §5 对偶性检查表；追加 `static/visit/parent-bridge.js` 不含 `requestAnimationFrame(` 与 `new WebSocket(`；`static/visit/transport/*.js` 的 `new WebSocket(` 只连 `location.host`；`templates/visit_transport.html` 不引用任何非 `/static/` 资源；`websocket_router.py` 二进制分支与 `app-websocket.js` Blob 分支 diff 为空；引入 vendor SDK 的 PR 合并前完成 3.12 T1~T8；单测「最长合法 `text` 分片后每片 ≤1000 B」。

---

### 3.10 与既有机制对照

| 需要 | 复用 | 为什么不复用另一个 |
|---|---|---|
| 视频通道 | WebRTC 视频轨（vendor SDK 在同源 iframe）+ 堆叠 alpha 打包 | v1 WebP 图片帧经 display socket 四跳：2~6 fps「幻灯片」违反 30 fps 硬要求；WebCodecs 拒 alpha；SDK 放主页面被 preload 劫持（`pet-websocket-bridge.js:333-346`） |
| 文本 / 控制通道 | vendor 数据通道 + 后端 `VisitOutbox` | 自建中继 owner 已否；Servers 做长连接房间成在飞硬依赖；只信 vendor 会丢句（TRTC 尽力交付） |
| 全序与陈旧 | Lamport `lp` + 侧位平局 + `reply_to` | host 权威序每行多一个来回、host 掉线无序、不对称；纯 `reply_to` 链只是偏序 |
| 外部文本劫持 | game route 泛化为注册表（**复用模式、新建机制**：今天 `websocket_router.py:51` 是直接 import，`utils/game_route_state.py:216` 是单槽 handler） | manager 内 dispatcher 漏旧窗口 |
| 角色接管 | `_takeover_active` + **归属令牌** | 无令牌时 game `/route/start` / `/route/end` 会覆盖 / 解除串门静音（`runtime.py:2066-2076`、`postgame.py:1277-1278`） |
| 猫娘出话 | 隔离 `OmniOfflineClient` + `open_mirror_speech_stream`（一行一条流，复用主聊天推 TTS 的同一条路径） | callback / append_context / stream_data 进主会话或私聊记忆 |
| 回家汇报 | `render_chat_blocks` 按钮块 + `react-chat-window:action / update-message` 宿主事件 + `/cache`（日记段，近期记忆）+ 新增 `visit_facts`（≤3 条事实进 fact 层，`importance=4 + absorbed=True` 不进 reflection） | `submit_proactive_callback` 回复入主会话历史并抄送插件总线（`_lifecycle.py:752-753 / :547-568`），且进不进记忆由不得用户 |
| 打断 | task 级 cancel（未开口）+ `line_abort` + `text{truncated}`（已开口）+ `_llm_turn_lock` | `cancel_response` 只翻标志、半句仍会入史（`_lifecycle.py:836-838`、`_streaming.py:1825`） |
| 记忆区 | `group_chat / group_participant / participant` + `platform=neko_visit` | 新 kind 改 7 处 schema |
| **QQ 群聊路径** | **只复用记忆层**：subject 三形态、`scoped_history` 单 subject + segments 双形态、接收边界章、`name(id)` 标签截断、分批结算——由自建的 `memory/scoped_client.py` 直连 memory_server 五个端点实现（OD-31 v3；QQ 插件已于 2026-09-28 经 #2996 移出仓库，本行 QQ 文件引用均为 `b0b283e34` 版） | 其余全绑在「跑在插件进程里（`plugin_host.py:140-165` 独立线程 / 循环）、给人类群聊当机器人」：自建 LLM 客户端（`session_bootstrap_service.py:231-246`）、自建 TTS 发 QQ 语音条（`voice_reply_service.py:76-92`）、焦点群 / @bot / 疲劳门控、发言人只有 admin/trusted/normal/none 四档（对端猫娘只能当 trusted「用户」进信赖池）、prompt 写死「QQ群 / QQ用户 / self_id」并注入亲人名（`session_instruction_service.py:348 / :597-627`）、对 `SessionManager` 零引用——Pet 取帧、嘴型、主会话静音、回家汇报四件事它一件都碰不到，每句每帧都得跨进程 |
| **game 路径** | **借** takeover 旗、ws 劫持点、隔离会话池、`mirror_assistant_*`、finalize 单 `_exit_task` + shield 骨架（`postgame.py:1110-1183`），泛化为注册表（**新建机制**） | 做成 game_type 会给六处各加 if：驱动方向相反（页面 POST 事件进来 vs 服务端被对端驱动）、归档写亲人的 legacy `/cache`（`archive.py:782`，OD-10 禁）、prompt 与记忆策略键全是 soccer/badminton 形状（`session_pool.py:124-160`、`mirror_meta.py:84-108`）、`start_session audio` 会去起 realtime 当 STT（`websocket_router.py:949-968`）、`opened` 事件隐藏 pet 容器（`route_lifecycle.py:94`）、每句对端台词 `note_user_engagement` 记成用户活跃（`turn.py:1824`） |
| 身份 | Servers 核验社区账号 → vendor 凭证 + Ed25519 票 + `visit_uid` | 全局假名可被公开串联；裸 uuid 落对端磁盘不必要；只信 vendor userId 无法封禁 |
| 前端身份 | role `'tool'` + index.css 新规则 | 新 role 要重建 React |
| 崩溃安全 | 逐句 spool（O_APPEND 单次 write 先例 `event_logger.py:263-264`；fsync 新增） | v1 内存缓冲崩了整场没记 |
| 区域 | 只读 `_region_cache` + `aensure_region_resolved` | 串门路径起探测违反 `core_config.py:40-59` 不变量 |

---

### 3.11 失败模式与降级

判据总则（裁决 I）：Pet 窗 `backgroundThrottling:false`（`window-manager.js:1009`）时 Electron 官方 BrowserWindow 文档「Page visibility」节明说 visibility 保持 `visible`（即使窗口最小化、被遮挡或隐藏；https://www.electronjs.org/docs/latest/api/browser-window ），`live2d-core.js:955-956` 注释同义——所以 hide-all 正常模式下 `document.hidden` **不一定**为 true，`visibilitychange` 不可靠。**取帧是否停止以「postrender 是否还来」为唯一判据**：有帧就发，1 s 无 postrender → `state{hidden:true}`（1 Hz），恢复即 `hidden:false`；B 侧无帧就显示最后一帧半透明 + 徽标。不保留任何依赖 `visibilitychange` 的分支。

| 情形 | 事实 | 行为 |
|---|---|---|
| hide-all 热键（正常模式） | `applyHideAllUI`（`src/main/hotkey-manager.js:433`）→ `fadeOutAndHide → win.hide()`（`:894`）。隐藏窗的 renderer 可能被 Chromium 拖到秒级（`screen-capture-ipc.js:1488-1491 / :1705-1708` 团队实测记录），也可能照常渲染——两种都有 | 帧继续来 → B 照常看到画面；帧停 → 1 s 后 `state{hidden:true}`，B 最后一帧 `opacity:.6` + 「离开了一下」徽标；恢复即去徽标。不计入 `idle_timeout`。T10 量 hide 后 `framesEncoded` 是否继续增长，结果写回本表 |
| hide-all（Windows 兼容模式） | `shapeHideNow` 只 `setShape` 1×1，窗仍 mapped（`hotkey-manager.js:809-820`） | 渲染与捕获继续，B 照常看到画面（传的是模型不是屏幕，可接受；判据自洽，不需要 PC 新 IPC） |
| 被其他窗口遮挡 | `calculate-native-win-occlusion=false`（`src/main.js:928`）+ `backgroundThrottling:false` + powerSaveBlocker | 继续出帧；macOS 完全遮挡 = 系统级 hidden → 同第一行的「帧停」分支 |
| 截图流程 capture-source-without-neko | 原生 `hideWindowForScreenCapture → win.hide()`（`src/main/screen-capture-ipc.js:1524-1541`），<1 s | 同第一行；500 ms 去抖再发 `state`，B 最多短暂徽标 |
| 模型管理器 / 切模型置 `#live2d-canvas` `visibility:hidden` | `static/pngtuber-core.js:4787-4791`、`app-character.js:287-292`（MMD 加载期）；PIXI 仍渲染 | `getModelScreenBounds()` 为 null 或容器 hidden → 父页停止调 `onFrame` → 视同 hidden |
| edge-peek 部分可见 | bounds 只返回可见部分（`live2d-core.js:5271-5276`） | 裁剪框按可见部分算（滞回压抖动） |
| avatar-portrait / generateTexture 触发的 postrender | `renderer.render(tempStage)`（`avatar-portrait.js:1226 / :1256`）与 RenderTexture 渲染也 emit postrender | 两道守卫跳过（3.3.5）；`avatarPortrait.capture` 前后 `suspendCapture()` |
| 源帧率 ≠ 30（75 / 144 / 165 Hz 或定时器 60 fps） | 「距上次 ≥33 ms」门会掉到 25~29 fps | 分数累加器采样（3.3.5），任何 ≥30 fps 源平均恰好 30；T2 用 `framesPerSecond` 验收 |
| 本侧 SDK 断线 / 对端离房 / 被踢 / 凭证过期 | 3.2.7 | 自身 25 s / 对端 30 s 心跳 / 显式离开与被踢立即 / TTL（guest 40 / host 50 min）≥ 硬顶不存在「先过期」分支 |
| Pet 页刷新 / iframe 消失 | transport WS 断 | 20 s 内新页面重建 iframe 同凭证重入房 + 重发 hello（同 jti）；超时 `local_page_lost` |
| 后端重启 | 状态只在内存 | 这场结束；对端 30 s 后 `peer_lost`；下次启动只做 spool 补录 |
| 关机 | 壳先销毁窗口（`backend-runtime.js:2483-2490`），`leave` 发不出 | `stop_all` ≤3 s：spool fsync + state.json + 释放 takeover；对端 30 s 后才知道（文案明写） |
| 数据通道拥塞（TRTC 8 KB/s 顶到） | TRTC 超限行为未文档化（T6 实测 reject / 静默丢）；本地令牌桶 5 KB/s + 20 条/s 先满 | iframe 报 `tx_backpressure` → 后端暂停 `line_delta / typing / stats`，只保 `text / ctl`；`text{final}` 不受影响，字幕中间态晚到 |
| 保不住 30 fps 或 600 kbps | `stats.rx_fps < 24` 或 `uplinkLoss > 15%` 连续 10 s | OD-06 v2 阶梯只缩裁剪不动 fps（最低 300 kbps）；libwebrtc 也可能自行降分辨率，接收端以 `videoWidth/Height` 观测；TRTC 是否自行降帧是 T6 首要实测项（若会且不可接受，阈值收紧到 8%） |
| VP9 软编 CPU 过高（LiveKit） | 编码 fps <27 持续 10 s | 本场记录，下次串门 `videoCodec:'vp8'` |
| TRTC 无 H.264 | `isSupported().detail` | SDK VP8 回落（changelog 5.15.2）；`caps.codecs` 上报 |
| 只有 TURN TCP 443 可用 | TRTC 内建 TURN（直连 → TURN UDP → TURN TCP 443）；LiveKit Caddy 443 复用 HTTPS + TURN/TLS | 可连但延迟 +；TURN 中继的房 GCP 出站翻倍 |
| Servers 不可达 | 凭证 HTTP 失败 | 建房 / 入房 503 `servers_unreachable`；**在飞串门不受影响**（凭证与票据在手） |
| 两侧区域不同 | Servers 以来源 IP 复核 | 403 `cross_region_unsupported`，确认框直接说明（T9 后 owner 决定是否改「允许 + 警告」） |
| Servers 配额用尽 / 被封 / 档位无权 | 429 / 403 | 409 `VISIT_QUOTA_EXCEEDED / VISIT_BANNED / VISIT_TIER_NOT_ENTITLED`，不占 takeover |
| 老壳 / 未来壳给子 frame 加 preload | 能力门 ② `foreign_websocket` | 不领凭证、不加载 SDK，409；本设计不存在「老壳 + 新后端」偏斜 |
| `http://<LAN IP>` 自定义后端（`isSecureContext===false`） | TRTC HTTPS 要求是 `getUserMedia` 限制；本设计不采集 | T11 实测：SDK 拒绝 → 409 + 8 语文案「自定义后端地址需 https 或 localhost」；不拒绝 → 只警告 |
| SDK 脚本加载失败 | `caps{stage:'sdk', transport_ok:false, reason:'sdk_load_failed'}`（发生在收到凭证之后，3.3.4） | 没有 SDK 就没有数据通道：`finalize('unsupported')` + `release_takeover` + toast；此时 Servers 已计一次签发 |
| 对端版本不同 | `hello.caps.proto` 主版本不同 / 未知 `t` | `leave{proto_mismatch}` + toast / 忽略计数（3.5.6） |
| TTS 请求被限流 | 首段推入后 4 s 无首个 `visit_speech_progress` | 本行切文本估时，本场剩余各行不再重试 TTS，`VISIT_TTS_FALLBACK` toast 一次 |
| 转录上传 Servers 失败 | `POST /api/visit/transcripts` 网络错 / 5xx | 与 `visitMemoryEnabled` 无关：`.upload.json` 保留到上传成功，进程内退避 + 下次启动重试，自结束起 7 天仍失败放弃并记本地诊断事件；不影响串门本身与 debrief（OD-26 v3） |
| 多窗口浏览器开发态（index.html + chat.html 各一条 `/ws`） | `websocket_router.py:547-556` 最新 socket 赢 | 渲染模型的页面可能不是 current；README「浏览器多窗口态不支持串门画面」保留 |
| B 结束瞬间在飞截图含访客层 | v1 遗留 | 截图前父页对 iframe 置 `visibility:hidden` 一帧（交互轴 follow-up） |

---

### 3.12 实测清单 T1~T13（每项 ≤30 min；PR-10 前完成；T1~T5 任一失败即退设计 1——preload 异 host 直通补丁 + 能力旗 + 老壳 fail-closed，只损失前端 PR-10/11；后端 PR 完全通用）

- **T1** 同源 iframe 内 `window.WebSocket === 原生`（`iframe.contentWindow.WebSocket.name`），父页 `_activeWs` 不变（Chat 窗不收 CONNECTING）。
- **T2** 同任务取帧：父页 `postrender` 内同步调 iframe `drawImage(#live2d-canvas, 裁剪)`，连续 300 帧无黑帧；定时器驱动与 rAF 驱动两种模式各测；分数累加器在 60 / 75 / 144 Hz 与定时器 60 fps 下用 `RTCRtpSender.getStats()` 的 `framesPerSecond / framesEncoded` 验收恰好 30。
- **T3** iframe 透明：子文档无背景时 Pet 透明窗不出现白 / 黑底（Windows / macOS）；`pointer-events:none` 下 `elementFromPoint` 命中下层；穿透状态与无 iframe 时一致。
- **T4** iframe 内透明 WebGL 画布叠层在 DWM（直通 / `disable-gpu-compositing` 兼容模式）与 macOS 的合成表现。
- **T5** `destination-in` 打包输出正确（alpha → 亮度）；否则改 WebGL 打包 shader。
- **T6** TRTC：`option.profile{320,896,30,560}` 对自定义轨是否生效（`chrome://webrtc-internals` 看 `frameWidth/frameHeight/framesPerSecond/targetBitrate`）；限速 400 kbps 时 SDK 掉 fps 还是掉画质，记 `qualityLimitationReason / qualityLimitationResolutionChanges` 与稳态 `frameWidth/Height`（libwebrtc QP 缩放器）；`TRTC.isSupported()` 在 Electron 41 三平台通过并记录 `encoderImplementation`；SDK 初始化不申请麦克风；**1 s 内连发 40 条 100 B 自定义消息，记录 Promise reject / 静默丢 / 接收端到达数**（超限行为）。
- **T7** TRTC `getVideoTrack` 的远端轨能 `srcObject` 到 2 px / opacity 0.01 的 `<video>` 并稳定触发 rVFC；`startRemoteVideo({view:null})` 后控制台用量按视频而非音频计。
- **T8** LiveKit：vp9 `L1T1 + maintain-framerate` 限速下保 30 fps，`chrome://webrtc-internals` outbound-rtp 的 `scalabilityMode === 'L1T1'` 且 `encodings.length === 1`；`publishData reliable` 丢包率与 outbox 重传触发次数；软编 CPU 超阈值（编码 fps <27 持续 10 s）→ 下次串门 vp8；vp8 与 vp9 单层在 560 kbps 下画质对比。
- **T9** 跨区：海外 guest 入大陆 SDKAppID 房间的可达性与 RTT；大陆客户端连 LiveKit Cloud asia / 东京 GCP 的连通率；各 ≥20 场并记 `qualityLimitationReason`。**决定 Servers 是否把 `cross_region_unsupported` 403 翻成「允许 + 警告」。**
- **T10** Pet 被 hide-all 原生隐藏 / 被遮挡 / Windows 兼容模式 setShape 1×1 时的抓帧行为：hide 后 `framesEncoded` 是否继续增长；结果写回 3.11 第一行。
- **T11** `http://<LAN IP>` 自定义后端下 `isSecureContext===false`：SDK 是否真的拒绝发 canvas 轨（决定能力门 ① 是 409 还是只警告）。
- **T12** alpha 边缘画质：预乘 over 黑 + 亮度 alpha 经 H.264 4:2:0 / VP9 560 kbps 后发丝与半透明部件表现；对比并排打包。
- **T13** TRTC host 以观众角色（`ROLE_AUDIENCE`）进房后能否 `sendCustomMessage` 与接收 `CUSTOM_MESSAGE`：能 → host 改观众角色（服务端层面就发不了视频，对偶 LiveKit host `canPublish:false`）；不能 → 维持 `ROLE_ANCHOR`，画质约束只靠 Servers 拉用量统计 / 事件回调比对（§4.7）。
- 对话轴实施期必测（Pet 窗，不占 T 编号）：同一 speech_id 流式推入时口型是否连续；`chunk_scheduled` 领先真开播最多 5 s 时 `played_ms` 换算是否正确；流式 mirror 入口「推流中途 abort」「finish 后 `audio_done` 对账」「与主聊天 speech_id 不串」；8 个 TTS provider（http_sentence / ws_bistream / gptsovits 等）上流式推入与 `audio_done` 对账；官方免费 TTS 一场 ≈40 次请求的限流行为。

---

### 3.13 未决问题（按阻塞程度；已由裁决定下的不再列为未决）

1. **Servers 排期与密钥托管**：`POST /api/visit/credentials`（房间登记 + `invite_code` + 区域 → transport + 跨区 403 + 每日分钟配额）、`GET /api/visit/invites/{invite_code}/preview`、`GET /api/visit/pubkeys`、`POST /api/visit/reports`、`POST /api/visit/transcripts`、`GET /api/visit/details/{visit_id}`、`POST /admin/visit/bans`、腾讯云 SDKSecretKey / LiveKit secret 托管、Ed25519 kid 轮换——卡 PR-07 联调。
2. **免费额度数值**：`VISIT_FREE_MINUTES_PER_DAY` 占位 120，由 owner 定价时拍板；付费档 entitlement 形状。
3. **TRTC `profile` 是否作用于自定义 `videoTrack`、拥塞时是否自行降帧、超限行为**（T6）：不作用 / 会降帧且不可接受 → 大陆没有第二家同时满足「标清档 + 自定义轨正门 + 不钉 maintain-resolution」，只能与腾讯技术支持确认或接受更激进的应用层阶梯。
4. **iframe 实测**（T1~T5）失败 → 退设计 1：需 lanlan_frd preload 异 host 直通补丁 + `__NEKO_WS_PASSTHROUGH__` 能力旗 + 老壳 fail-closed，前端 PR-10/11 重写。
5. **hide-all 后帧是否继续**（T10）：决定 3.11 第一行两个分支哪个是现实；不需要 PC 新 IPC。
6. **LiveKit 容量**：e2-standard-4 能否撑 500 房 1000 轨（默认 400 轨/CPU 刚好 1600）；`livekit-cli load-test` 决定 1 节点还是 2 节点 + Redis；Cloud Ship 1,000 并发 = 1000 同接零余量，触顶是排队还是直接上 Scale；Cloud「1,000 并发」是 participant 还是 connection 口径未细究。
7. **跨区**（T9）：首发 403 已定；T9 后是否翻成「允许 + 警告」由 owner 决定。
8. **libwebrtc QP 质量缩放器**是否在 560 kbps / 286,720 px / 30 fps 下稳态降分辨率（T6/T8）；若是，二选一：接受并写进「保 30 fps 优先于分辨率」的产品说明，或裁剪缩到 288×512（294,912 px 同档，收益有限）。
9. **TRTC 数据通道码率并入档位**的计量口径（按流还是按订阅者）——影响 40 kbps 预算是否再收紧。
10. **Servers 侧在飞踢人**（`RemoveUserByStrRoomId` / `RoomService.RemoveParticipant`）：已核实 API 存在，列 Servers follow-up；首发只有拒发新凭证 + 客户端黑名单。
11. **举报证据链**：放弃中继盖章后，双侧 JSONL + Servers 上双方各自上传的转录（OD-26 v3，各由一侧自报、无第三方盖章）是否足够；`POST /api/visit/reports` 是否首发。
12. **对话轴实施期必测**（3.12 末条）：同一 speech_id 流式推入时口型连续性、`played_ms` 换算、流式入口 abort / `audio_done` 对账 / 与主聊天 speech_id 不串、8 个 provider 流式推入、免费 TTS 限流；失败退路已定（本行切文本估时 + `VISIT_TTS_FALLBACK`）。
13. **VRM / MMD / PNGTuber 语音关时的文本嘴型**（无统一 `setMouth`）——follow-up。
14. **`lp` 是否进 digest 正文**：建议只作排序键，不进正文（待实施期定）。
15. **`_conversation_history` 中段裁剪**（`trim_visit_history(40)`）对内部长回复摘要 / turn 计数的影响，需单测。
16. **首帧头像为空时**回落默认图标；compact caption / 导出面板把 tool 当 assistant 分组——follow-up。
17. **B 结束瞬间在飞截图含访客层**：截图前隐藏 iframe 一帧，交互轴。
18. **TRTC 包内 LICENSE 文件复核**：npm 元数据 ISC，实施时以包内文件为准；若不一致改为运行时 CDN 加载并记录供应链面。TRTC changelog 里 Electron 相关条目版本号两次抓取不一致（5.13.1 / 5.17.0 vs 5.11.1 / 5.17.1），不影响结论。
19. **PC 侧可选 follow-up**：销毁窗口前先给 Pet 页一个 ≤500 ms 的 `leave` 窗口，让对端不必等 30 s——违反「lanlan_frd 零改动」前提，不进 v2。
20. **OD-10 敏感记忆筛除 issue**：上线前需完成（owner 要求）；全局隔离开关默认 False、不改变现网铸卡结果、老数据回填走后台低优先任务、「小模型」从纯函数 `classify_text` 拆成可选异步增强——issue 草稿见 §2 OD-10。
21. **v1.5 / v2 候选**：互访同房双向视频（TRTC 2×标清 2.1 元/房·小时含音频，是否只对付费用户开）、A 的 TTS 当音频轨随视频发（3.6.7 备选）、`based_on` 校验、召回工具、捎话、`window_kind` 媒体 socket 标记、hd1200 / fhd2400 付费档、`visitMemoryEnabled` 拆成「记对方猫娘 / 记对方亲人」两键、10 min 周期 digest 默认开。

## 4. 协议目录（可直接照着写 pydantic / zod）

本章是本稿的唯一线协议权威：§2.2 的 OD-27/29/30、OD-01 v2、OD-05 v2、OD-08 v2、OD-11 v2、OD-15 v3、OD-16 v3、OD-17 v2、OD-21 v3、OD-26 v3 与 §3.5/§3.6/§3.7 的正文只引用本章，不复述字段。与 v1 相比，**中继消息面整体作废**（`create_room / welcome / room_created / peer_joined / replay_done / throttle / room_closed / error / 关闭码 / member_token / PSK 握手 / NKVF 帧 / visit_capture / visit_view / NKVF 帧下行 / /health / /admin/ban|accepting`），换成五个面：① vendor 数据通道（4.1/4.2）；② iframe ↔ 本机后端独立 WS（4.3）；③ 父页 ↔ iframe postMessage（4.4）；④ 本机 display socket（4.5，只走 JSON）；⑤ 本机 HTTP 与 Servers HTTP（4.6/4.7）。视频不再有任何应用层帧格式：它是 vendor 的 WebRTC 视频轨（§3.4）。

约定（全章适用）：
- 字节单位一律按字节（B），不是 1024 进位的「KB」；「≤1000 B」就是 1000 字节。TRTC 的官方限制是「单次 ≤1 KB、≤30 次/s、≤8 KB/s」（https://web.sdk.qcloud.com/trtc/webrtc/v5/doc/en/TRTC.html#sendCustomMessage ），文档没写 1 KB 是 1000 还是 1024，本章按 1000 B 取保守值。
- 数据通道字段名刻意短（`ln / lp / sp / ad / rt / wu / txt`），本机 display socket 与 HTTP 字段名用全称；两者的对应在 4.5 每条里写明。
- 每条必达消息带每侧单调 `seq`（outbox 序号）；每一「行」带每侧单调行号 `ln = side首字母 + ':' + 行序`（如 `g:17`），在该行第一片发出时分配；`lp` 是 Lamport 时间戳，也在第一片发出时分配并贯穿该行全部消息（§3.6.3）。两个计数器互不替代：`ln` 是「哪一行」，`seq` 是「哪条必达消息」。
- 未知 `t` 一律忽略并计数；未知字段忽略；只有 `hello.caps.proto` 主版本不同才以 `leave{reason:'proto_mismatch'}` 结束（4.1 末条）。
- 「必达」= 进后端 `VisitOutbox`，重传直到累计 `ack`；「可丢」= 发一次不管。LiveKit 上 cmd 1/2 走 `reliable:true` 仍然是 best-effort（服务端不缓冲、重试有限，https://docs.livekit.io/transport/data/packets/ ），所以应用层 outbox 在两家 vendor 上都不可省。
- 时间戳字段只在本机面出现（`ts`，Unix 秒浮点）；数据通道不传 wall clock，接收方自己盖时间。
- 每条消息按 `#### 名称` + 方向 / 面 / 字段 / 上限 四行写，类型标注仿 pydantic：`str(≤64)` 表示 UTF-8 编码后 ≤64 B；`u32` 表示 0..2^32-1；`bool`；`float`；`Literal` 写成 `'a'|'b'`。

### 4.1 数据通道信封（TRTC `sendCustomMessage` / LiveKit `publishData`）

iframe 是**无状态转发器**：后端经 4.3 的 `send{cmd, payload}` 给它一个 JSON 对象，它序列化、分片、套信封、按 cmd 选通道发出；收到对端片后按 `(from_vid, m)` 重组、校验、再经 `recv{from_vid, cmd, payload}` 交回后端。分片、重组、限速全在 iframe；`seq / ack / 重传 / 幂等 / lp` 全在后端。

#### 信封
- 方向: 双方 iframe → vendor 数据通道 → 对端 iframe
- 面: 数据通道（UTF-8 JSON 文本，TRTC 以 `ArrayBuffer` 传、LiveKit 以 `Uint8Array` 传）
- 字段: `v:int=1`（信封版本）, `r:str(8)`（`visit_id` 前 8 字符，防串房）, `m:u32`（发送方消息 id，分片重组键，每条 payload 一个，单调递增）, `i:u8`（片序，0 起）, `n:u8`（片数，1..8）, `p:str`（payload JSON 序列化后按字节切出的一段；必须是完整 UTF-8，不切 codepoint）
- 上限: 每片 `JSON.stringify(信封)` 的 UTF-8 长度 **≤1000 B**（信封自身开销 ≈60 B，`p` 内每个双引号与反斜杠因 JSON 转义各膨胀 1 B，切片算法按转义后长度贪心切；单测断言「最长合法 `text` 分片后每片 ≤1000 B」「任意 `line_delta` 恒 n=1」）；`n ≤ VISIT_PIECES_MAX=8`，**以编码后字节为准**：`text` 正文经两次 JSON 转义（payload JSON 一次、信封字符串 `p` 又一次），每个 `\` 或 `"` 最坏占 4 B，全是反斜杠 / 引号的 4096 B 正文会膨胀约 4 倍、超过 8 片（普通 CJK / emoji 正文 ≈4.4 KB → ≤5 片）；所以发送侧（后端 `utils/visit_wire.py`，发 `send{}` 之前）把 payload 按最终信封形式编码、数片数，超过 8 片就在字符边界截短 `txt`、置 `truncated:true, trunc_reason:'wire_size'` 后重编码，直到 ≤8 片（`clamp_text_utf8(4096)` 仍是第一道上限）；LiveKit 单包 ≤15 KiB 本可不分片，但为同一套代码仍套信封，恒 `i=0,n=1`

#### 分流（cmdId / topic）
- 方向: 双方
- 面: 数据通道
- 字段: TRTC `cmdId` / LiveKit `topic`：**1 / `visit.ctl`** = `hello, ready, ack, hb, state, consent, wrap_up, leave`；**2 / `visit.text`** = `line_delta, text, line_abort`；**3 / `visit.lossy`** = `typing, stats`。LiveKit：cmd 1/2 `publishData(bytes, {reliable:true, topic, destinationIdentities:[peer_vid]})`，cmd 3 `reliable:false`（≤1300 B）；TRTC：`sendCustomMessage({cmdId, data})`，全房广播（TRTC 无定向发送），靠 `r` 与发送者绑定过滤
- 上限: cmd 只允许 1/2/3；其它 cmdId / topic 的消息直接丢弃并计入异常（老版本客户端也这么做，见「版本偏斜」）

#### 分片与重组
- 方向: 接收 iframe 内部
- 面: 数据通道
- 字段: 重组表 `Map<from_vid + ':' + m, {pieces: (str|null)[n], firstAt}>`；`i==n-1` 到且无空洞 → 拼接 `p` → `JSON.parse` → 校 `r` → `recv`；`JSON.parse` 失败 / `r` 不符 / `i ≥ n` / `n` 与已建条目不一致 → 丢弃整条并计数
- 上限: 首片起 **2 s** 未齐 → 丢整条并计数（`VISIT_REASSEMBLY_TIMEOUT_S=2`；对 cmd 2 无害：正文由 `text` 兜底）；同时在飞重组条目 ≤32，超出淘汰最老；同一 `(from_vid, m)` 重复片以先到为准

#### 发送者绑定与丢弃
- 方向: 接收 iframe → 后端
- 面: 数据通道 → 4.3 `recv`
- 字段: `from_vid` = vendor 盖的发送者身份（TRTC `CUSTOM_MESSAGE.userId`，https://web.sdk.qcloud.com/trtc/webrtc/v5/doc/en/module-EVENT.html ；LiveKit `DataReceived` 的 `participant.identity`），iframe 不可伪造、不可省略；后端在 `hello` 核验通过前只接受 `hello`（其余丢弃计数），通过后只接受 `from_vid == 已核验 peer vid` 的消息
- 上限: 第二个未知 `from_vid` 出现（房间被第三者进入）→ 该来源全部丢弃 + 计数，且本侧发 `leave{reason:'peer_protocol_violation'}` 结束（配合 4.7 的房间绑定，正常情况下不会发生）

#### 限速与合并（iframe 出站队列；后端出站队列各执行一份，后端那份是权威）
- 方向: 本侧出站
- 面: 数据通道
- 字段: **字节桶** `VISIT_DATA_BUCKET_BPS=5120`（5 KB/s ≈ 40 kbps，与 560 kbps 视频合计 600，落在 TRTC 标清档 300~900 kbps 码率带内不跳档，https://cloud.tencent.com/document/product/647/44248 ），桶容量 3 KB；**条数桶** `VISIT_MSG_BUCKET_PER_S=20`，桶容量 10（TRTC 30 次/s 的 67%，给重传留余量）；**delta 合并**：同一行相邻两片发出间隔 <`VISIT_DELTA_MIN_INTERVAL_MS=250` → 合并成一片（拼接 `txt`）；`i` **在发送时**按实际发出的片连续分配（合并后的片占一个 `i`，后续顺延，不留洞），本行 `text{final}.i_done` 同步按实际发出片数计——因此 delta 合并与 `i` 编号只在后端出站队列做（权威那份），iframe 只做限速排队与积压作废、不改 `i`；队列积压 >3 s → 同行相邻 delta 继续合并到 ≤800 B；积压 >10 s → 该行剩余 delta 全部作废（可丢消息，正文由 `text` 兜底），只保 cmd 1 与 `text`；**重传只在桶有余量时发**（outbox 到期项排在队首）；**超限排队不丢**（有序队列；裁决 B.2 的「排队不丢」只对必达消息与 cmd 1——可丢类 `line_delta / typing / stats` 在积压 >10 s 或队列 >200 条时作废，正文由 `text` 兜底，OD-30 (7) / §3.5.5 同句），桶满时 iframe 回 4.3 `tx_backpressure`，后端暂停 `typing` 与新 delta 的入队
- 上限: 纸面需求上界（各类同一秒同时顶满，实际互斥，估算）：`line_delta` ≤4 条/s（250 ms 合并）× ≤1000 B = 4.0 KB/s；`text` 一侧同时只有一个发言者且行间 ≥1 s → ≤1 行/s，猫娘行 ≤400 tok ≈1.2~1.8 KB（CJK）、人类行 ≤600 tok ≈1.8~2.7 KB、硬顶 4096 B → 分片后 ≤5 条/s、≤4.1 KB/s；`ack` 合并后 ≤2 条/s × 80 B = 0.2 KB/s；`typing` ≤1 条/s × 60 B；`hb` 0.2 条/s × 60 B；`stats` 0.2 条/s × 120 B；`wrap_up / consent / state` 每场只有个位数条，忽略。合计条数 4 + 5 + 2 + 1 + 0.2 + 0.2 ≈ **12.4 条/s ≤ 20 条/s（桶）≤ 30 条/s（TRTC）**；合计字节 4.0 + 4.1 + 0.2 + 0.1 ≈ **8.4 KB/s 纸面需求**——其中 delta 的 4 KB/s 与 text 的 4 KB/s 互斥（一行 800 B 正文 ≈266 CJK 字 ≈48 s 语音，不可能与 250 ms 节拍同时顶满；`text` 正文就是同一行已经流出的 delta 再发一遍），真实峰值 ≈1.0 + 1.5 + 0.3 ≈ **2.8 KB/s**（估算）；而**字节桶把实际出站钉死在 ≤5 KB/s ≤ 8 KB/s（TRTC）**，超出部分排队。重连回放突发（未 ack ≤2 行 text + hello/consent ≈ 6 条 / ≤5 KB）被桶摊到 ≥1 s 内发完

#### 版本偏斜与异常计数
- 方向: 双方接收侧
- 面: 数据通道
- 字段: `hello.caps.proto:int`（线协议主版本，本稿 = 1）；`hello.caps.app_version:str`（`major.minor`，只用于日志与举报）；接收规则：未知 `t` → 忽略 + 计数；未知字段 → 忽略；已知 `t` 缺必填字段 / 类型不符 / 单片 >1000 B（`line_delta` payload >900 B 同规）/ `i` 跳变 / 同一发送方两行交叠（上一行未 `text` 收口又来新 `ln`）/ `lp` 回退 >1000 或同一发送方 `lp` 不单调（**只作用于新开的行 / 新控制事件**：已见过的 `ln` 的 `text{final}` 及其重传、outbox 重传的旧 `seq` 保留原 `lp`，不因后续更大 `lp` 已到而被拒） / `seq` 回退（重传的已见 `seq` 不算）→ **丢弃该消息 + 异常计数**（不再判 `peer_protocol_violation` 直接结束）
- 上限: `hello.caps.proto` 主版本不同 → `leave{reason:'proto_mismatch'}` + `status{VISIT_PROTO_MISMATCH}`（8 语 toast「对方版本不兼容，请双方更新」）；**连续 `VISIT_ANOMALY_FINALIZE_COUNT=20` 条异常**（任一条合法消息到达即清零）→ `leave{reason:'peer_protocol_violation'}` + finalize；异常计数进 `GET /api/visit/state.anomalies` 与举报附件

### 4.2 数据通道 payload（裁决 B 的最终消息集合）

速览（谁必达、谁带什么序号）：

| t | cmd | 必达 | `seq` | `ln` | `lp` | 备注 |
|---|---|---|---|---|---|---|
| `hello` | 1 | 是 | 是 | — | — | 首包；核验通过前对端只收这一种 |
| `ready` | 1 | 是 | 是 | — | — | host 接待确认后发；guest 收到才 publish 视频 |
| `ack` | 1 | 否 | — | — | — | 累计 `seq` |
| `hb` | 1 | 否 | — | — | `lp_seen` | 5 s 一条 |
| `state` | 1 | 否 | — | — | — | 下一条覆盖 |
| `consent` | 1 | 是 | 是 | — | — | 初次 + 变更时 |
| `wrap_up` | 1 | 是 | 是 | — | 是 | `ph` 四值 |
| `leave` | 1 | 是（一次重传后不等 ack） | 是 | — | — | 收到立刻结束 |
| `line_delta` | 2 | 否 | — | 是 | 是 | 只上屏 |
| `text` | 2 | 是 | 是 | 是 | 是 | 一行的唯一收口 |
| `line_abort` | 2 | 否 | — | 是 | 是 | UI 提示，随后必有 `text{truncated:true}` |
| `typing` | 3 | 否 | — | — | 是 | 只发 on |
| `stats` | 3 | 否 | — | — | — | 5 s 一条 |

所有 payload 共有 `t:str` 与 `v:int=1`（payload 版本，与信封 `v` 独立）。

**接待前闸门**（`awaiting_accept`：hello 核验通过后到 host 发出 / guest 收到 `ready` 之前，最长 `VISIT_ACCEPT_TIMEOUT_S=60`）：接收侧只放行 `hello / ready / consent / leave / hb / ack`；`text / line_delta / line_abort / typing / wrap_up` 一律丢弃并计数（单独计数，不累加 `VISIT_ANOMALY_FINALIZE_COUNT` 的连续异常，免得正常早到的台词把对端踢掉）——其中 `text` 仍回 `ack`（按 `seq` 推进，免得对端重传到 `delivery_failed`），但不上屏、不入史、不进 spool、不触发回复；**发送侧两边都在 `ready` 之前不发任何 `text`**：host 亲人在接待确认期间打字 → `route_stream_message` 回 `status{VISIT_INPUT_REFUSED_NOT_READY}`（toast「等接待后再说」）、文字留在输入框、不进 outbox；guest 在收到 `ready` 之前同理——接收侧对早到 `text` 回 ack 只是为了不让对端重传到 `delivery_failed`，不能依赖它投递。

#### hello
- 方向: 双方（入房事件 `REMOTE_USER_ENTER / ParticipantConnected` 之后立即发；对端未到也可先发，outbox 会重传）
- 面: 数据通道 cmd 1
- 字段: `t:'hello', v:1, seq:u32, ticket:str`（4.7 身份票原文，两段 base64url）, `caps:{video:bool（本侧能发/收视频，能力门 ③ 结果）, tier:'sd600', proto:int=1, app_version:str(≤16, 'major.minor'), crop:'upper'|'full'}`, `lang:str(≤16, BCP-47)`, `jti_reuse:bool`（重连重放同一 jti 时为 true，只作日志）
- 上限: ≤2 KB（票 ≈560 B + 字段）→ 分 2~3 片；接收侧核验顺序固定：验签（`kid` 查 `VISIT_SERVERS_PUBKEYS`，miss 则 `GET /api/visit/pubkeys` 一次，仍 miss → fail closed）→ `aud=='neko-visit'` ∧ `visit_id==本房` ∧ `role==对侧` ∧ `iat-300 ≤ now ≤ exp+300` → **`ticket.vid == from_vid`（vendor 盖的发送者 id）** → `ticket.sub ∉ visit_blocklist.json`；任一步失败 → 本侧 `leave{reason:'peer_identity_rejected'}` + finalize（黑名单命中对端只看到「离开」）；核验通过前不订阅视频、不接受 `text`、host 不弹接待确认；同房同 `vid` 重连允许重放同一 `jti`（票 TTL guest 40 / host 50 min ≥ 硬顶 30 min + 重连）；hello 之后 5 s 内未收到对端 hello → 继续等（对端可能还没入房，邀请码 10 min 有效）；host 侧 OD-11 v2 的 30 s 判死**只在对端 `hello` 核验通过后才起算**，此前（host 的 `invite_ready` 阶段）只由 `VISIT_INVITE_WAIT_S=600` 兜底 → `finalize('invite_expired')`（无已核验对端，不发 `leave`）；guest 侧入房时 host 早已在房，自入房起在对端 `hello` 核验前只等 `VISIT_PEER_LOST_S=30` 秒 → `finalize('peer_lost')`

#### ready
- 方向: host → guest（host 侧亲人在接待确认框点「接待」之后；核验 `hello` 通过是前提）
- 面: 数据通道 cmd 1
- 字段: `t:'ready', v:1, seq:u32, memory:bool`（host 当前 `visitMemoryEnabled`，等价于一条初始 `consent{scope:'session'}`，省一个来回）
- 上限: 每场 1 条（重连不重发，outbox 重传除外）；host `VISIT_ACCEPT_TIMEOUT_S=60` 内亲人未点 → host 发 `leave{reason:'declined'}`；guest 侧 hello 核验通过后 65 s 未收到 `ready` → `finalize('declined')`；**guest 收到 `ready` 才 `publish(track)`**（4.3 `media{publish:true}`），host 发出 `ready` 后才 `media{subscribe:true}`（LiveKit `autoSubscribe:false` → `setSubscribed(true)`；TRTC `REMOTE_VIDEO_AVAILABLE` → `startRemoteVideo({view:null})` + `getVideoTrack`），所以 OD-26「双方点头」在媒体层也严格成立

#### ack
- 方向: 双方
- 面: 数据通道 cmd 1
- 字段: `t:'ack', v:1, seq:u32`（**累计**：`seq` = 接收侧**连续**落地的最大序号，表示 `≤ seq` 的全部必达消息都已收到；有缺口时只推进到缺口之前；**接收侧对必达消息严格按 `seq` 顺序处理**：缺口之后先到的消息只缓存、不处理（重排缓存上限 `VISIT_REORDER_BUFFER_MAX=64` 条，超出 → `finalize('peer_protocol_violation')`），缺口补齐后按 `seq` 依次处理并一次推进 ack——所以 `consent` 永远在它之前的 `text` 之后生效；可丢消息（`line_delta / line_abort / typing / stats / hb / state / ack`）不进重排、到即处理；`leave` 是终止消息，同样**不进重排、到即生效**：先把重排缓存里缺口之前已连续的消息按序处理完，缺口及其后的缓存直接放弃，然后 finalize——发送端只重发一次 `leave` 就退房，缺口可能永远补不齐）
- 上限: 收到任一必达消息后 ≤`VISIT_ACK_COALESCE_MS=200` 内回一条（合并窗口内只回当前连续落地的最大 seq，**不是**收到的最大 seq——否则 `seq=2` 丢、`seq=3` 先到时会误删 2）；≤80 B；不进 outbox；发送侧 outbox 收到 `ack{seq}` 即删除 `≤ seq` 的全部项；重传时间表 `VISIT_OUTBOX_RETRY_S=(1,2,4,8,8)`，排完后按最后一档（8 s）继续重传，直到收到 ack；任一必达项自首发起超过 `VISIT_DELIVERY_TIMEOUT_S=30` 仍未确认 → `finalize('delivery_failed')`（发 `leave{reason:'delivery_failed'}`）；**这 30 s 只计传输已连接的时间**：自身 SDK 重连（`VISIT_SELF_RECONNECT_S=25` 窗口）与页面重载宽限（`VISIT_LOCAL_PAGE_GRACE_S=20`）期间暂停，恢复后 outbox 重发未 ack 项并继续计时——不能交给心跳时钟，因为心跳照常到达时对端不会判死，必达文本会永久缺失；接收侧幂等：`text` 按 `ln`、其余必达按 `seq`，各一个 LRU(`VISIT_DEDUP_LRU=512`)，重复只回 ack 不重复处理；按序处理：必达消息进重排缓存（≤`VISIT_REORDER_BUFFER_MAX=64`），只有 `seq == 已处理最大 seq + 1` 的才交给上层

#### hb
- 方向: 双方
- 面: 数据通道 cmd 1
- 字段: `t:'hb', v:1, lp_seen:int`（本侧已观察到的最大 Lamport 值，供对端 `observe_lp`）
- 上限: 每 `VISIT_HEARTBEAT_S=5` s 一条；≤60 B；不进 outbox；接收侧**收到对端任何消息**（不限 hb）都刷新 `peer_last_seen`；`now - peer_last_seen > VISIT_PEER_LOST_S=30` → `finalize('peer_lost')`（OD-11 v2：唯一的对端判死时钟；host 侧对端 `hello` 核验通过后才启动，此前见 hello 条的 `VISIT_INVITE_WAIT_S`，guest 侧自入房起即以 30 s 计；vendor 的超时类离开事件 TRTC `REMOTE_USER_EXIT{reason:1}` / LiveKit `ParticipantDisconnected` 无显式 bye 不单独处理）

#### state
- 方向: 双方（主要是 guest → host）
- 面: 数据通道 cmd 1
- 字段: `t:'state', v:1, hidden:bool`（本侧父页 **1 s 内没有成功 `onFrame`** 推导得出、经 4.4 `hidden{on}` 告知 iframe，不读 `document.hidden`——Pet 窗 `backgroundThrottling:false` 下 visibility 恒 visible，见 §3.11）, `crop:'upper'|'full', tier:'sd600', enc:'h264'|'vp8'|'vp9'|null`（实际编码器，来自 `getStats().codecId`，只作诊断）
- 上限: 变化时发，最多 1 Hz；≤120 B；不进 outbox（下一条覆盖）；host 收到 `hidden:true` → 最后一帧 `opacity:.6` + 徽标「离开了一下」（`visit_state_change{peer_hidden}`），`hidden:false` 或任一新帧到达 → 恢复（`peer_visible`）；hidden 期间不计入 `VISIT_IDLE_TIMEOUT_S`

#### consent
- 方向: 双方
- 面: 数据通道 cmd 1
- 字段: `t:'consent', v:1, seq:u32, memory:bool, scope:'session'|'all'`
- 上限: 初次在 hello 核验通过后立即发一条（guest 侧；host 侧由 `ready.memory` 兼任），此后只在 `visitMemoryEnabled` 变化或用户点「让对方忘掉我」时发；每场 ≤5 条；接收侧把最近一次的 `memory` 盖到之后每句 spool 行的 `peer_consent_at_receipt`（OD-09 v2）；`memory:false, scope:'session'` → 本场对端句全部标不可 digest；`memory:false, scope:'all'` → 另对这一对的三个 subject 各调一次 `POST /internal/memory/{name}/scoped_forget`（`app/memory_server/routes.py:2796`）+ 删 `visit_peers.json` 该人项 + 抹掉 `state.json` 里的 `peer_uid / pair_id`（OD-17 v2 对偶）；黑名单不受影响

#### wrap_up
- 方向: 双方（`propose` 只 guest → host；`begin / done` 只 host → guest；`ack` guest → host 可省）
- 面: 数据通道 cmd 1
- 字段: `t:'wrap_up', v:1, seq:u32, lp:int, ph:'propose'|'begin'|'ack'|'done', reason:'quiet'|'budget'|'recall'|'time_up'`（分别对应 6 句无人插话 / 本侧满 40 句 / 「叫她回来」/ `max_duration - 60 s`）, `initiated_by:'host'|'guest'`
- 上限: 每场 ≤4 条；`propose` 5 s（`VISIT_WRAP_UP_PROPOSE_TIMEOUT_S`）无 `begin` → guest 直接开始说告别；**告别行本身就是状态载体**：任一侧收到 `wu:true` 的 `line_delta` 或 `text` 即进 WRAP_UP，所以 `begin` 丢了也不卡死，`ack` 因此可省（`wu:true` 首片到达等价于 ack）；`recall` 的 `propose` host 不判条件立即 `begin`；超时：从 `begin` 到对方告别行**第一片**到达 ≤`VISIT_WRAP_UP_STEP_S=15`（告别行本身按正常播放走完，≤400 tok 天然有界；告别提示词要求 ≤40 字、最多两个分句），`begin` 起 `VISIT_WRAP_UP_MAX_S=45` 硬顶无条件 finalize；`done` 丢了由 45 s 硬顶与对端 `leave` 兜底（§3.6.3）

#### leave
- 方向: 双方
- 面: 数据通道 cmd 1
- 字段: `t:'leave', v:1, seq:u32, last_seq:u32（leave 之前最后一条必达消息的 seq）, consent:{memory:bool, scope:'session'|'all'}（发送方此刻的同意状态快照）, reason:'home'|'ended'|'declined'|'goodbye'|'character_changed'|'shutdown'|'error'|'wrapup'|'proto_mismatch'|'peer_identity_rejected'|'peer_protocol_violation'|'visit_disabled'|'delivery_failed'`
- 上限: 发送侧在正常 finalize 时**先排空 outbox**：最多等 `VISIT_LEAVE_DRAIN_S=2` 秒让此前未确认的必达消息（尤其 `consent` 撤销与最后一行 `text{final}`）拿到 ack，再发 `leave`（断线 / 关机类 finalize 不等）；发出后 1 s 内未 ack 重传一次，然后不等 ack 直接 finalize（进 outbox 只为这一次重传）；接收侧收到立刻生效（不进重排，见 `ack`）：**先按 `leave.consent` 快照执行同意状态**（`memory:false, scope:'all'` 即使原 `consent` 消息丢失也照样执行撤销清理），`seq ≤ last_seq` 仍缺的必达消息记入本场 `anomalies`（发送方自己的转录与云端上传里仍有这些行），然后 `finalize(映射 reason)`（`home / wrapup / ended` → `peer_left`；`declined` → `declined`；`delivery_failed` → `finalize('delivery_failed')`；其余原名），不必等 30 s；**本侧 finalize reason → `leave.reason` 映射**：`route_end / recall / max_duration / time_up` → `'ended'`，`wrap_up` → `'wrapup'`，`character_switch` → `'character_changed'`，`peer_blocked` → `'peer_identity_rejected'`（对端只见离开），其余同名（`home / goodbye / shutdown / error / visit_disabled / delivery_failed`）；**不发 `leave` 的 finalize reason**：`invite_expired`（host 等待期没有已核验对端）与 `unsupported`（能力门失败，SDK 未入房、没有数据通道）；两侧同时 leave → 第二条只回 ack；幂等（LRU 命中只回 ack）；`shutdown` 在 Electron 分发态**发不出去**（壳先销毁窗口再请求后端关机，`lanlan_frd/src/main/backend-runtime.js:2483-2490`），对端 30 s 后 `peer_lost`——OD-11 v2 与产品文案明写；`visit_disabled` = 用户中途关闭 `visitEnabled`（固定句告别，不走收尾流程）

#### line_delta
- 方向: 双方
- 面: 数据通道 cmd 2（**可丢**：不进 outbox；接收侧只上屏，不入史、不入 spool、不计数）
- 字段: `t:'line_delta', v:1, ln:str(≤12, 'h:123'|'g:123'), i:int(0 起, 实际发出的片序：发送时连续分配，250 ms 合并后的片占一个 i、后续顺延，不留洞), lp:int, txt:str(≤800 B)`；**`i==0` 额外带** `sp:'c'|'h'`（speaker kind：cat / human）, `ad:'hc'|'hh'|'gc'|'gh'`（addressee = side 首字母 + kind 首字母）, `rt:str(≤12, reply_to 的 ln，开场为 '')`, `wu:bool`（告别行）
- 上限: 整条 payload ≤900 B（payload >900 B 的 `line_delta` → 丢弃该消息 + 异常计数，B.3；与 4.1「单片 >1000 B」同规）、`txt` ≤800 B（`utils/visit_wire.py::ClauseSplitter`（增量版 `split_clauses`）在字符边界硬拆，优先标点 / 空格之后，绝不切 codepoint；单测「随机 1000 组中文 / emoji / 俄文编码后 ≤900 B」）；恒 n=1 片；同行最小间隔 250 ms（合并规则见 4.1）；接收侧 `Map<ln, {clauses[], bubble, lastAt}>` 按 `i` 落位，乱序 / 缺片留 `…` 占位**不补洞**（无 `line_req`），等 `text` 全文覆盖；`i==0` 建气泡（自家猫娘 role `assistant`，对端 role `tool`，OD-19）；`VISIT_LINE_STALL_S=20` 无新片且无 `text` → 本地按 stall 截断并标 `truncated`；`redact_outbound` 先作用在本行累积缓冲上再切片（受保护词不跨片，§3.6.4），每片放出前再过 `strip_emotion_tags → sanitize_relay_text`；发送时机：语音开 = 按已播音频对齐，`min(自开播经过时间, played_ms) ≥ Σ_{j<i} estimate_speech_ms(clause_j)` 时放出第 i 片（`played_ms` 来自 4.5 `visit_speech_progress`），`ended` 时剩余已生成分片一次放出；语音关 = 文本估时定时器（OD-15 v3）；`VISIT_STREAM_DELTAS=False`（紧急开关）时不发 delta，只发 `text`（字幕退回整句模式，TTS 仍流式）

#### text
- 方向: 双方
- 面: 数据通道 cmd 2（**必达**：进 outbox）
- 字段: `t:'text', v:1, ln:str, lp:int, seq:u32, sp:'c'|'h', ad:'hc'|'hh'|'gc'|'gh', rt:str, wu:bool, final:true, txt:str(≤4096 B)`（全文，或被打断时 = **已放出分片的拼接**）, `truncated:bool, i_done:int`（实际发出的 `line_delta` 片数，250 ms 合并后按合并后的片计，与 `line_delta.i` 同一编号；未打断时 = 发出片总数）, `trunc_reason?:'human_interrupt'|'wrap_up'|'tts_error'|'llm_error'|'stall'|'wire_size'`, `lang?:str(≤16)`, `tail_ms?:int`（末分句 `estimate_speech_ms` 估时，单位 ms；接收侧作回复延迟基数 `not_before = now + tail_ms/1000 + U(1.0, 2.5)`，§3.6.3 ⑦ / §3.2.4 第 13 条；缺省按 0）
- 上限: 猫娘行（OD-21 v3）：`txt` = 已放出分片拼接，已过 OD-23 清洗链（`redact_outbound` 作用在累积缓冲上再切片，`strip_emotion_tags / sanitize_relay_text` 逐片，§3.6.4），出站前**不再**做整行 `truncate_to_tokens`（整行长度由 `max_response_length=VISIT_RESPONSE_MAX_TOKENS` 约束），不变量「已放出分片拼接 == `txt`」；人类行（不流式）整行过同一清洗链 + `truncate_to_tokens(VISIT_HUMAN_LINE_MAX_TOKENS=600)`（`utils/tokenize.py:143`）；两者最后都 `clamp_text_utf8(4096)`（第一道上限）；**第二道上限以编码后字节为准**：按最终信封形式编码后 >`VISIT_PIECES_MAX=8` 片 → 在字符边界截短 `txt`、置 `truncated:true, trunc_reason:'wire_size'` 后重编码，直到 ≤8 片（4.1 信封）；此时 `txt` 是已放出分片拼接的前缀，「已放出分片拼接 == `txt`」不变量只对这一种 `trunc_reason` 放宽为前缀关系；普通正文 ≤5 片；**一行永远以一条 `text` 收口**（正常说完、被打断、stall、LLM/TTS 出错都发）；接收侧以 `text` 为准：覆盖气泡全文、`append(HumanMessage)` 入隔离会话历史（`ad` 是我 → 触发回复；否则纯入史）、`VisitSpool.append`（带两枚接收章）、`VisitRoom` 计数与 `is_stale`、`VisitMemoryBuffer` 排序键 `(lp, side_rank)`；`truncated:true` 的行入史前缀 + `VISIT_MARK_INTERRUPTED`（8 语「（说到这里被打断了）」）；发送侧被打断时用 `session_pool.pop_trailing_ai_message(session, expected=整行)` 弹出整行再 append 已放出前缀（d4 §2.6「已说出的入史、未说出的不入史」；OD-21 v3 以已放出分片为准）；`ln` LRU 幂等；每侧 ≤1 行/s（自然节拍，非硬限）

#### line_abort
- 方向: 双方
- 面: 数据通道 cmd 2（**可丢**）
- 字段: `t:'line_abort', v:1, ln:str, lp:int, i_done:int, reason:'human_interrupt'|'wrap_up'|'tts_error'|'llm_error'`
- 上限: ≤120 B；只是让对端 UI **立刻**截到 `i_done` 并加「（被打断）」的提示，随后 ≤1 s 内必有同 `ln` 的 `text{truncated:true}`；接收侧收到 `line_abort` 若被回的行正是自己 pending 回复的 `rt` → 丢弃该回复计划（§3.6.3 ③）；丢了无后果（`text` 兜底）

#### typing
- 方向: 双方
- 面: 数据通道 cmd 3（可丢）
- 字段: `t:'typing', v:1, lp:int, sp:'c'|'h'`（只发 on；该行第一片 `line_delta` 到达即隐含 off）
- 上限: 每行 ≤1 条、每侧 ≤1 条/s；≤60 B；`VISIT_STREAM_DELTAS=False` 时退回 v1 语义加 `on:bool`（on/off 各一条）；接收侧 8 s 未见首片自动清掉 typing 指示

#### stats
- 方向: 双方（主要 host → guest，驱动 guest 的拥塞阶梯 OD-06 v2）
- 面: 数据通道 cmd 3（可丢）
- 字段: `t:'stats', v:1, rx_fps:float`（`requestVideoFrameCallback` 的 `presentedFrames` 5 s 差分）, `rx_kbps:int, rtt_ms:int, loss_pct:float, rx_w:int, rx_h:int`（`video.videoWidth/Height`——libwebrtc 可能自行降分辩率，接收端只能这样观测，D.7）, `qlr?:'none'|'bandwidth'|'cpu'|'other'`（发送侧 `outbound-rtp.qualityLimitationReason`，guest 自报给自己的后端时才有）
- 上限: 每 5 s 一条；≤160 B；`rx_fps < 24` 或本侧 `uplinkLoss > 15%` 连续 10 s → 降一级裁剪（上半身 320×448/560 kbps → 256×352/400 → 192×272/300；全身 256×560/560 → 208×448/400 → 160×352/300；最低不低于标清带下限 300 kbps）；30 s 干净升一级

### 4.3 iframe ↔ 本机后端：`WS /api/visit/transport/ws`

新端点 `main_routers/visit_router/transport_ws.py`：`@router.websocket("/transport/ws")`（子路由写相对路径，由 `visit_router` 的 `APIRouter(prefix='/api/visit')` 补前缀；对外 URL 即 `/api/visit/transport/ws`，写成全路径会注册成 `/api/visit/api/visit/transport/ws`），query `visit_id, side`，与 `WS /api/vmc/ws`（`main_routers/vmc_router.py:11`）同一套本机 Origin / CSRF 校验；无末尾斜杠（`websocket_router.py:24-28` 约定）。JSON 文本帧 ≤16 KB，无二进制帧。**凭证只在这条 socket 下发**，不经父页、不经 preload、不进 display socket、不进日志（OD-29）。

#### caps
- 方向: iframe → 后端（分两段、各 1 条：`stage:'preflight'` 是连接建立后第一条，跑能力门 ①②，与 transport 无关；`stage:'sdk'` 在收到 `credentials`、按 `transport` 懒加载 SDK 之后跑能力门 ③，D.4 / §3.3.4）
- 面: transport WS
- 字段: 预检段 `type:'caps', stage:'preflight', visit_id, side, preflight_ok:bool, reason?:'insecure_context'|'foreign_websocket'|'no_webrtc', is_secure_context:bool, ua:str(≤200)`（① `isSecureContext`；② 原生 `WebSocket`，并确认 `RTCPeerConnection`、画布 2D / WebGL、`captureStream` 在场）；SDK 段 `type:'caps', stage:'sdk', visit_id, side, transport_ok:bool, video_ok:bool, reason?:'sdk_unsupported'|'sdk_load_failed'|'no_encoder', codecs:str[]`（`TRTC.isSupported()` 结果 / `RTCRtpSender.getCapabilities('video')` 的 mimeType 列表）
- 上限: 每连接每段 1 条；`preflight_ok:false` → 后端**不去 Servers 领凭证**、不占 takeover、不消耗配额，直接 `visit_state_change{ended, reason:'unsupported'}` + `status{VISIT_UNSUPPORTED_ON_THIS_MACHINE}`——**不耗配额的承诺只覆盖预检段**；设置页「串门」分组打开时可用 1×1 探测 iframe 预跑一次预检段并缓存（有效期同本次 Pet 页生命周期；**不常驻探测 iframe**，D.4 / OD-27）；建房 / 入房时若无缓存则先建 iframe 跑预检再领凭证，`POST /api/visit/rooms` **仅缓存命中 false 时**同步 409（F-08）；SDK 段 `transport_ok:false` → `finalize('unsupported')` + `release_takeover` + `status{VISIT_UNSUPPORTED_ON_THIS_MACHINE}`，**此时 Servers 已计一次签发**（每日签发分钟数已扣，C.7）；`video_ok:false` 而 `transport_ok:true` → 串门照常（文本 + 字幕），`hello.caps.video=false` 让对端显示头像占位

#### credentials
- 方向: 后端 → iframe（`caps{stage:'preflight'}.preflight_ok` 且 Servers 4.7 成功之后）
- 面: transport WS
- 字段: `type:'credentials', visit_id, side, transport:'trtc'|'livekit', vendor:{trtc?:{sdk_app_id:int, user_id:str(≤32), user_sig:str, str_room_id:str(≤64), expire:int(guest 2400 / host 3000)} , livekit?:{url:str(wss), token:str, ttl_s:int(guest 2400 / host 3000)}}`（只含所选 vendor）, `own_vid:str(26), peer_vid?:str(26)`（**guest 侧必填**，由 Servers 4.7 给出；**host 侧领凭证时 guest 尚不存在、为 null**，靠票据 `vid` 核验，对端 `hello` 核验通过后由后端经 `media{peer_vid}` 补齐——此前 host 的 LiveKit 发送省略 `destinationIdentities`，房间 `max_participants=2` 兜底）, `allowed_hosts:str[]`（`VISIT_LIVEKIT_HOSTS`；iframe 校 `url` 主机名精确命中，否则 `state{error, error_code:'host_not_allowed'}`）, `tier:'sd600', crop:'upper'|'full', publish:{codec:'vp9'|'vp8'|'h264', bitrate_kbps:560, fps:30, scalability_mode:'L1T1', simulcast:false, degradation:'maintain-framerate'}`, `expires_at:float`
- 上限: ≤8 KB；每连接 ≤1 条（页面重载后新 iframe 重连 → 同一份凭证再发一次；`expires_at` 已过 → 后端直接 finalize 不再发）；iframe 收到即懒加载对应 UMD（`static/libs/trtc.js` 或 `static/libs/livekit-client.umd.js`，OD-28）→ 能力门 ③ → `caps{stage:'sdk'}`（`transport_ok:false` 则不入房，等后端 `stop`）→ `enterRoom({sdkAppId, userId, userSig, strRoomId, scene:SCENE_RTC, role:ROLE_ANCHOR, autoReceiveVideo:false})` / `new Room({adaptiveStream:false, dynacast:false, publishDefaults:{…publish}}).connect(url, token, {autoSubscribe:false})`

#### media
- 方向: 后端 → iframe
- 面: transport WS
- 字段: `type:'media', publish:bool`（guest：收到 `ready` 后 true；拥塞阶梯换裁剪时重发 `crop/ladder`）, `subscribe:bool`（host：发出 `ready` 后 true）, `crop?:'upper'|'full', ladder?:int(0..2)`（阶梯级别，按当前 `crop` 取 `VISIT_CONGESTION_LADDER` 对应那条）, `peer_crop?:'upper'|'full'`（host 侧：对端数据通道 `state.crop` 首次到达与变化时下发，解包侧据此核对裁剪几何）, `peer_vid?:str(26)`（host 侧：对端 `hello` 核验通过后即下发，可与 `subscribe:true` 同条，补齐 `credentials` 里为 null 的 `peer_vid`；iframe 此后 LiveKit 定向发送以它作 `destinationIdentities`）；`credentials` 里**不带** `publish_video`：发布 / 订阅 / 阶梯时机只由本消息驱动（OD-29、§3.5.7、PR-07 的 transport WS 下行消息列表以本条为准：`credentials / media / send / stop`）
- 上限: 每场 ≤10 条；`publish:true` → `startLocalVideo({publish:true, option:{videoTrack, profile:{width, height, frameRate:30, bitrate}}})` / `publishTrack(track, publish)`；`publish:false` → `stopLocalVideo()` / `unpublishTrack`；`profile.width/height` 取当前构图的打包尺寸（上半身 320×896、全身 256×1120，§3.4.2），`crop` 变化 → 先按新尺寸重建 `scratch / pack` 画布再 `updateLocalVideo`；阶梯变化用 `updateLocalVideo` / `setPublishingQuality`（LiveKit 无逐参 API 时重发布）

#### send
- 方向: 后端 → iframe
- 面: transport WS
- 字段: `type:'send', cmd:1|2|3, payload:object`（4.2 的任意一条；iframe 负责序列化、分片、信封、限速）
- 上限: `payload` 按最终信封形式编码后 ≤`VISIT_PIECES_MAX=8` 片（后端发出前已按 4.1 信封条保证，超出的 `text` 已截短为 `trunc_reason:'wire_size'`）；iframe 出站队列上限 200 条，超出回 `tx_backpressure{dropped:true}` 并丢弃**可丢类**（cmd 3 与 `line_delta`），永不丢 cmd 1 与 `text`（排队不丢只对必达消息与 cmd 1；可丢类在积压 >10 s 或队列 >200 条时作废，正文由 `text` 兜底——与 4.1 限速条同一规则）

#### stop
- 方向: 后端 → iframe
- 面: transport WS
- 字段: `type:'stop', reason:str`
- 上限: 每连接 1 条；iframe 顺序执行 `stopLocalVideo → exitRoom()` / `unpublish → disconnect(true)` → `state{left}` → 关闭 transport WS → 父页收到 `visit_state_change{ended}` 后 `iframe.remove()`

#### state
- 方向: iframe → 后端
- 面: transport WS
- 字段: `type:'state', state:'joining'|'joined'|'reconnecting'|'connected'|'left'|'kicked'|'error', peer_present:bool, remote_video:bool, error_code?:str, vendor_reason?:str`（TRTC `KICKED_OUT.reason` `kick|banned|room_disband` / `REMOTE_USER_EXIT.reason` 0..3 / LiveKit `DisconnectReason` 原文）, `hidden?:bool`
- 上限: 事件驱动，无速率上限（vendor 事件本身稀疏）；后端映射：`reconnecting` → 起 `VISIT_SELF_RECONNECT_S=25` 计时 + `visit_state_change{reconnecting}` + `outbox.pause()`（`VISIT_DELIVERY_TIMEOUT_S` 暂停计时，4.2 ack），`connected` 在 25 s 内 → `outbox.resume()`、重发未 ack 项、清计时；超时 → `stop` + `finalize('relay_lost')`；`kicked{banned|room_disband}` → 立即 `finalize('kicked')` 不重连；`peer_present:false` 且 `vendor_reason` 为显式离开（TRTC reason 0 / LiveKit 主动 disconnect）→ 立即 `finalize('peer_left')`，超时类不处理（交心跳时钟）

#### recv
- 方向: iframe → 后端
- 面: transport WS
- 字段: `type:'recv', from_vid:str(26), cmd:1|2|3, payload:object`（已重组、已 `JSON.parse`、已校 `r`）
- 上限: 转发不限速（上游 vendor 已限）；后端做 4.1「发送者绑定与丢弃」与 4.2 各条校验

#### stats
- 方向: iframe → 后端
- 面: transport WS
- 字段: `type:'stats', tx_fps:float, enc_fps:float`（`outbound-rtp.framesEncoded` 5 s 差分，D.3 软编回退判据）, `tx_kbps:int, tx_w:int, tx_h:int, enc:str, qlr:str, rx_fps:float, rx_kbps:int, rtt_ms:int, loss_pct:float, rx_w:int, rx_h:int, dc_queue:int`（出站队列长度）, `softenc_overloaded?:bool`（VP9 软编 `enc_fps < 27` 持续 10 s 置 true，后端记 `codec_pref='vp8'` 供下次串门，`VISIT_VP9_CPU_FALLBACK`）
- 上限: 每 5 s 一条；后端据此发数据通道 `stats`（4.2）、更新 `GET /api/visit/state`、写 T6/T8 实测记录；§3.5.7 与 PR-07 / PR-10 的 `stats` 与 `credentials` 字段列表以本条与 4.3 `credentials` 为准

#### tx_backpressure
- 方向: iframe → 后端
- 面: transport WS
- 字段: `type:'tx_backpressure', on:bool, queue:int, dropped:bool`
- 上限: 变化时发；`on:true` → 后端暂停 `typing` 与新 delta 入队（只保 cmd 1 与 `text`），`on:false` 恢复

#### 连接生命周期（不是消息，是这条 socket 的规则）
- 方向: 双向
- 面: transport WS
- 字段: 建立 = 父页 `visit_state_change{pending}` 后懒建 iframe → iframe 连 `ws(s)://{location.host}/api/visit/transport/ws?visit_id=&side=`（只连 `location.host`，静态门检查）；断开 = iframe 消失 / Pet 页刷新 / 崩溃
- 上限: 断开起 `VISIT_LOCAL_PAGE_GRACE_S=20` s 宽限（比对端 30 s 判死短 10 s）：新页面 `GET /api/visit/state` 得知在飞 → 重建 iframe → 重连本端点 → 后端重发同一份 `credentials` → 同 `vid` 再入房 → 后端重发 `hello`（同 jti）→ outbox 重发；超 20 s → `finalize('local_page_lost')`；同一 `visit_id, side` 第二条连接 → 顶掉旧连接（旧的收 close 4409）

### 4.4 父页 ↔ iframe（postMessage + 一个同步入口）

来源校验：父页只处理 `event.source === iframe.contentWindow && event.origin === location.origin`；iframe 只处理 `event.source === window.parent && event.origin === location.origin`。全部 JSON，低频。帧触发**不走 postMessage**（异步任务里后备缓冲已被合成清空），走同步跨 realm 调用。

#### __nekoVisitFrameSink.onFrame（同步入口）
- 方向: 父页 → iframe（同一 JS 任务内的直接函数调用）
- 面: 跨 realm 同步调用（同源）
- 字段: iframe 在 `window.__nekoVisitFrameSink = {onFrame(sourceCanvas:HTMLCanvasElement, rectPx:{x,y,w,h}, tsMs:number): void, suspend(on:bool)}` 暴露；父页在 `live2dManager.pixi_app.renderer.on('postrender', fn)` 内调用（VRM `vrm-manager.js:877` / MMD `mmd-core.js:1291` 各 +1 行；PNGTuber 用 `nekoFramePacing.requestPacedFrame` 30 Hz 采样）；父页调用前守卫：① iframe 已 `ready` 且未销毁；② `renderer.lastObjectRendered === pixi_app.stage`（跳过 avatar-portrait 的临时舞台与 `generateTexture`，D.6）；③ `!renderer.renderTexture.current`；④ **分数累加器** `acc += 30 / renderFps; if (acc >= 1) { onFrame(...); acc -= 1 }`（`renderFps` = 最近 1 s 实测 postrender 频率，D.5——不再用「距上次 ≥33 ms」门）
- 上限: 30 次/s；iframe 内每次 ≤1.5 ms（估算：裁剪 `drawImage` 0.3~1 ms + 两次打包合成 <0.5 ms）；`sourceCanvas` 只能是模型画布（`#live2d-canvas` 等四个容器之一的 canvas），父页不传其它元素（§3.8「视频取自屏幕」闸门）；`suspend(true)` 期间 iframe 丢帧不 `requestFrame()`（`avatarPortrait.capture` 前后由父页调用）

#### ready
- 方向: iframe → 父页
- 面: postMessage
- 字段: `{t:'ready', v:1, side, visit_id}`
- 上限: iframe `load` 后 1 条；父页收到才缓存 `iframe.contentWindow.__nekoVisitFrameSink` 并开始 `crop / place`

#### crop
- 方向: 父页 → iframe
- 面: postMessage
- 字段: `{t:'crop', rectPx:{x,y,w,h}`（后备缓冲像素坐标，`canvas.width / getBoundingClientRect().width` 换算）, `srcW:int, srcH:int, mode:'upper'|'full'}`
- 上限: 每 300 ms~1 s 一条（`getHeadDetectionGeometryInfo()` 无缓存，`live2d-core.js:5030`）；滞回：中心偏移 <4% 且尺寸变化 <8% 不发；iframe 收到后 300 ms 线性过渡到新框；`getModelScreenBounds()` 返回 null（edge-peek hidden / 模型管理器覆盖）→ 父页停止调 `onFrame`，1 s 后父页发 `hidden{on:true}`（见下 hidden 条）

#### place
- 方向: 父页 → iframe（host 侧）
- 面: postMessage
- 字段: `{t:'place', left:int, top:int, width:int, height:int}`（视口 CSS px；父页据 `getModelScreenBounds()` 摆到本家猫娘左右空位较大一侧，高 `clamp(L.height×0.9, 200, 900)`、宽 = 高 × cropW/cropH（上半身 320/448、全身 256/560，取 4.5 `visit_state_change.peer_crop`）
- 上限: 每 300 ms 一条；实际由父页直接改 iframe 的 `style`，本消息只让 iframe 知道自己的 CSS 尺寸以设置解包画布 `width/height`（DPR 取整到偶数）；guest 侧 iframe 1×1 置于视口外，不发 place

#### hidden
- 方向: 父页 → iframe
- 面: postMessage
- 字段: `{t:'hidden', on:bool, cause:'frame_starvation'|'forced'}`（`forced` = 父页主动挂起，如 `suspend(true)` 期间）
- 上限: 变化时发，≤1 Hz；**父页做帧饥饿检测**（与 §3.2.9 / OD-02 v2 / PR-11 同一检测者）：父页 >`VISIT_FRAME_STARVATION_S=1.0` s 无成功 `onFrame` 调用（无 postrender、守卫 ②③ 不过或 `getModelScreenBounds()` 为 null）→ 发 `hidden{on:true}`；iframe 收到后经数据通道发 `state{hidden:true}`；父页下一次成功 `onFrame` → 发 `hidden{on:false}`，iframe 经数据通道发 `state{hidden:false}`；**不依赖 `visibilitychange`**（verify_platform M2；hide-all 正常模式下帧是否还来是唯一判据）

#### visit_state
- 方向: 父页 → iframe
- 面: postMessage
- 字段: `{t:'visit_state', phase:'pending'|'joining'|'awaiting_accept'|'active'|'wrap_up'|'ending'|'ended'}`（父页从 display socket `visit_state_change` 转述）
- 上限: 变化时发；iframe 只用它决定 `ended` 时自清理（保险，正常由 4.3 `stop` 驱动）

#### stats
- 方向: iframe → 父页
- 面: postMessage
- 字段: `{t:'stats', fps:float, kbps:int, rtt_ms:int, hidden:bool}`
- 上限: 每 5 s 一条；父页只用于开发者面板与徽标（不再转发后端，后端已从 4.3 拿到）

#### first_frame
- 方向: iframe → 父页（host 侧）
- 面: postMessage
- 字段: `{t:'first_frame', dataURL:str(image/png 96×96)}`（解包画布首个 rVFC 后 `toBlob` 缩略）
- 上限: 每场 1 条；≤16 KB；父页作访客 tool 气泡头像（OD-19）

#### log
- 方向: iframe → 父页
- 面: postMessage
- 字段: `{t:'log', level:'info'|'warn'|'error', msg:str(≤500), code?:str}`
- 上限: ≤5 条/s；父页 `console.*` 转发（iframe 无 preload，console 不被镜像到 Chat 窗）；永不含凭证、票据、正文

### 4.5 本机 display socket（父页 ↔ 后端，`/ws/{lanlan_name}`，全部 JSON）

沿 v1 §3.3.3 的位置：`main_routers/websocket_router.py:503` 的既有 socket；**不承载任何串门二进制、凭证或视频**（v1 的 `visit_capture / visit_view / NKVF 下行 / Blob sniff` 全部删除，`:789-800` 二进制分支与 `app-websocket.js:3058-3066` Blob 分支 diff 为空）。多窗口归属照 v1：`__NEKO_MULTI_WINDOW__ === true` 且路径匹配 `/chat` 或 `/chat_full`（`app-websocket.js:1092-1094`）的 chat 窗只渲染 `visit_line/*` 与 debrief 芯片，不建 iframe。字段名用全称，与数据通道短名对应关系在各条注明。

#### visit_line
- 方向: 后端 → 两页（index.html 与 chat.html）
- 面: 本机 display socket
- 字段: `type:'visit_line', visit_id, line_id:str`（= 数据通道 `ln`）, `lp:int, speaker:{side:'host'|'guest', kind:'cat'|'human', name:str(≤64, 取票据 display_name 经 OD-23 清洗), self:bool}, addressee:{side, kind}, reply_to:str, goodbye:bool`（= `wu`）, `text:str, final:true, truncated:bool, i_done:int, trunc_reason?:str, ts:float`
- 上限: `text` 猫娘行长度由 `max_response_length=VISIT_RESPONSE_MAX_TOKENS` 约束（出站不再整行 `truncate_to_tokens`，OD-21 v3）/ 600 tok（human），两者 ≤4096 B；**自家猫娘的气泡也只由 `visit_line_delta / visit_line` 驱动**（流式 mirror 入口 `open_mirror_speech_stream` 以 `mirror_text=False` 推 TTS，不发 `gemini_response`），两侧字幕节拍因此同源；前端收到 `final:true` 覆盖该 `line_id` 气泡全文并按 `truncated` 追加「（被打断）」

#### visit_line_delta
- 方向: 后端 → 两页
- 面: 本机 display socket
- 字段: `type:'visit_line_delta', visit_id, line_id, i:int, lp:int, text:str`（该分句）, `speaker:{side, kind, name, self}`（每片都带，前端不必等 i==0）, `addressee:{side, kind}, goodbye:bool, ts:float`
- 上限: `VISIT_STREAM_DELTAS=True` 默认开；自家猫娘的 delta 按已播音频对齐放出（语音开，依据 `visit_speech_progress`，规则同 4.2 `line_delta`）或按估时定时器发（语音关）；`static/visit/text-mouth-driver.js` 只在 `visitVoiceEnabled=false` 且 `S.lipSyncActive===false` 时消费 `self:true` 的 delta 驱动 Live2D 嘴型（OD-15 v3）

#### visit_line_abort
- 方向: 后端 → 两页
- 面: 本机 display socket
- 字段: `type:'visit_line_abort', visit_id, line_id, i_done:int, reason:'human_interrupt'|'wrap_up'|'tts_error'|'llm_error'|'stall', ts:float`
- 上限: 随后必有同 `line_id` 的 `visit_line{truncated:true}`；前端立即截断到 `i_done`

#### visit_typing
- 方向: 后端 → 两页
- 面: 本机 display socket
- 字段: `type:'visit_typing', visit_id, speaker:{side, kind, name}, on:bool`
- 上限: 前端 8 s 未见首片自动清掉

#### visit_speech_progress
- 方向: 页 → 后端（`static/visit/visit-pacer.js`，只在 `visitVoiceEnabled=true`）
- 面: 本机 display socket（普通 `action` 帧，走 `websocket_router.py:1334` 旁新增 `elif action == "visit_speech_progress"` → OD-03 注册表 `route_external_page_signal`；game 不注册即忽略）
- 字段: `action:'visit_speech_progress', visit_id, speech_id:str`（本行 `MirrorSpeechStream` 的 `speech_id`，一行一个；finalize 后的仪式句与 debrief 简述同样是带 speech_id 的 mirror 语音，pacer 对它们也回报，其 `ended` 用于 `VisitInbox` 交还时机；这两个 speech_id 登记在与路由状态无关的 `_pending_inbox_handoff`，路由状态已 pop 也照转，§3.2.6 第 22 条）, `played_ms:int`（该 speech_id 已播音频时长）, `ended:bool`（播完或被清掉）
- 上限: 播放期间约 4 Hz（同 speech_id 间隔 ≥250 ms），`ended:true` 恰一条收尾；pacer 监听 `neko-speech-playback-state`（`app-audio-playback.js:531`）中该 speech_id 的 `reason==='chunk_scheduled'`（带 `scheduledEndAudioTime / audioContextTime`）→ 复刻 `:1595-1597` 钳位换算真开播时刻与已播时长；后端 `VisitRuntime.on_speech_progress` 按 `min(自开播经过时间, played_ms) ≥ Σ_{j<i} estimate_speech_ms(clause_j)` 放出第 i 片 → 数据通道 `line_delta` + 本机 `visit_line_delta{self:true}`，`ended` 时剩余已生成分片一次放出（OD-15 v3）；首段推入后 `VISIT_TTS_START_TIMEOUT_S=4` 未收到首条 → 本行切文本估时 + `status{VISIT_TTS_FALLBACK}`（每场一次），本场剩余各行不再重试 TTS；开播后 `VISIT_SPEECH_PROGRESS_STALL_S=3` 无新条且未 `ended` → 剩余分片按估时从最后一次 `played_ms` 续放，收到该行 `__audio_done__` 后再加 `estimate_speech_ms(剩余)` 为硬上限，到点强制放完并发 `text{final}`（§3.6.4「兜底」）；未知 / 旧 `speech_id` 忽略；Pet 桥会像其它帧一样把它镜像到 Chat 窗（无害）

#### visit_state_change
- 方向: 后端 → 两页
- 面: 本机 display socket
- 字段: `type:'visit_state_change', action:'pending'|'invite_ready'|'joining'|'awaiting_accept'|'started'|'departed'|'wrap_up'|'peer_hidden'|'peer_visible'|'peer_crop'|'reconnecting'|'video_reconnecting'|'ending_soon'|'ending'|'ended'`, `side:'host'|'guest', visit_id, transport?:'trtc'|'livekit', tier?:'sd600', peer_crop?:'upper'|'full'`（host 侧：随 `started` 携带对端当前构图（已知时），对端 `state.crop` 变化时以 `action:'peer_crop'` 补发；host 父页据此按 cropW/cropH 摆访客 iframe，4.4 place）`, peer_name?:str, peer_short_id?:str(6), peer_human_label?:str, invite_code?:str(10)`（仅 `invite_ready`，host 侧）, `invite_expires_at?:float, ends_at?:float`（`ending_soon`）, `reason?:str, initiated_by?:'host'|'guest'`（`wrap_up`）, `memory_pending?:bool, cross_region?:bool, ts:float`
- 上限: 状态机单调（`ended` 后不再有本 `visit_id` 的任何消息，除 `visit_debrief`）；v1 的 `quiet` 删除（改为 `wrap_up`）、`relay_reconnecting` 改名 `reconnecting`；前端映射：`departed` → A 侧 `#live2d-container` 加 `.visiting-away` + 徽标（不用 `.minimized`，OD-14 v2）；`wrap_up` → 徽标「道别中」+ composer 禁用；`peer_hidden` → 访客层 `opacity:.6` + 「离开了一下」；`reconnecting` → 徽标 + composer 禁用；`ended` → 去徽标、父页 `iframe.remove()`

#### visit_invite
- 方向: 后端 → host 页（对端 `hello` 核验通过之后才发，OD-01 v2）
- 面: 本机 display socket
- 字段: `type:'visit_invite', visit_id, peer_name:str`（票据 `display_name` 经清洗）, `peer_short_id:str(6)`（= `visit_uid[:6]` 大写）, `peer_human_label?:str, cross_region:bool, expires_at:float`
- 上限: 等 `POST /api/visit/rooms/{visit_id}/accept`；`VISIT_ACCEPT_TIMEOUT_S=60` 内未调用 → 自动 `leave{declined}`；黑名单命中的对端**永不**产生本消息（hello 阶段已拒）

#### visit_debrief
- 方向: 后端 → 两页（finalize 之后，OD-16 v3）
- 面: 本机 display socket
- 字段: `type:'visit_debrief', visit_id, phase:'summary'|'asked'|'chosen'|'expired'|'interrupted', request_id:str('visit-debrief:'+visit_id), choice?:'diary'|'forget', ts:float`
- 上限: 芯片本体**不是**本消息：用既有 `chat_blocks` 帧（`main_logic/core/turn.py:2001 render_chat_blocks`，`app-websocket.js:3079` → `appendReactChatBlocks`）发一条 `role:'system'` 消息含 `text` 块 + `buttons` 块（两个按钮「记成日记 / 不记」，`action:'visit_debrief_choice', payload:{visit_id, choice}`，`variant:'danger'` 给 forget）；本消息只是**状态**：`static/app/app-react-chat-window/visit-chat.js`（index.html 与 chat.html 都加载）监听 `react-chat-window:action`（`message-bundle-actions-and-prompts.js:322-337`，今天零监听者）→ `POST /api/visit/debrief/choice` → 收到 `chosen` 后经 `react-chat-window:update-message`（`resize-drag-and-api.js:442`）把两个按钮置 `disabled` 并追加 status 块；`expired` = `VISIT_DEBRIEF_TIMEOUT_S=600` 到期或用户先开新会话，**默认 `VISIT_DEBRIEF_DEFAULT='ask_later'`：芯片保留可点、spool 保留 7 天、不写私聊记忆**；`interrupted` = 启动补录发现上次崩溃 → 重新出同一组芯片 + `status{VISIT_INTERRUPTED_LAST_TIME}`；监听器必须在 index.html 宽 / 窄与 chat.html 三条路径都加载（G.2）；`visitMemoryEnabled=false` 时只有 `summary`，不出芯片

#### status（串门码）
- 方向: 后端 → 页
- 面: 本机 display socket（既有 `{"type":"status","message":json}` 帧）
- 字段: `type:'status', message:json{code:str, details:{visit_id?, reason?, retry_after_s?, peer_short_id?, request_id?（`VISIT_INPUT_REFUSED_*` 回带发起提交的客户端 id）}}`；`code ∈ {VISIT_VOICE_UNAVAILABLE, VISIT_INPUT_REFUSED_AWAY, VISIT_INPUT_REFUSED_WRAPUP, VISIT_INPUT_REFUSED_NOT_READY, VISIT_E_BUSY, VISIT_RECALL_ALREADY, VISIT_TTS_FALLBACK, VISIT_UNSUPPORTED_ON_THIS_MACHINE, VISIT_LOGIN_REQUIRED, VISIT_BANNED, VISIT_QUOTA_EXCEEDED, VISIT_CROSS_REGION_UNSUPPORTED, VISIT_INVITE_INVALID, VISIT_SERVERS_UNREACHABLE, VISIT_PROTO_MISMATCH, VISIT_PEER_IDENTITY_REJECTED, VISIT_PEER_LOST, VISIT_RELAY_LOST, VISIT_KICKED, VISIT_INTERRUPTED_LAST_TIME, VISIT_MEMORY_RETRY_LATER}`
- 上限: 每码映射一条 8 语 toast（`static/locales/*.json` 的 `visit.status.<code>`，`scripts/check_i18n_sync.py` 锁步）；`details` 永不含正文、票据、完整 `visit_uid`

#### stream_data（`source` 扩展）
- 方向: host 页 → host 后端
- 面: 本机 display socket（既有 action）
- 字段: `action:'stream_data', input_type:'text', data:str, request_id?:str, source:'neko_visit:guest_cat'|'neko_visit:own_cat'`（收件人：对方猫娘 / 自家猫娘）
- 上限: `websocket_router.py:1041-1047` 的 `_stamp_user_input_ingress / _record_stream_engagement_ingress` **保持原位**（亲人确实在电脑前）；`:1048-1054` 的 `is_game_route_active → route_external_stream_message` 改查 OD-03 注册表：串门 kind 的 `route_stream_message` 对 text → `mgr.mirror_user_input(send_to_frontend=False)` → `VisitRoom.on_local_human_line` → outbox `text{sp:'h', ad}`（`VisitBackpressure` → `status{VISIT_E_BUSY}`）→ 返回 True（router `continue`）；`phase ∈ {wrap_up, ending}` → `status{VISIT_INPUT_REFUSED_WRAPUP}`，文本留在 composer；guest 侧一律 `status{VISIT_INPUT_REFUSED_AWAY}`（OD-22）；`audio` → `status{VISIT_VOICE_UNAVAILABLE}`；`screen / camera / 图片` 吞掉；`start_session{text}` 由 `on_start_session` ack-only（`send_session_started('text')`，不建普通文本会话，同 `:949-956`），`start_session{audio}` 拒

### 4.6 本机 HTTP（`/api/visit`，无末尾斜杠；变更端点**与读端点**（state / transcript / details / memory/peers / 邀请预览）一律过 `_validate_local_mutation_request` 同款本机来源校验，`main_routers/system_router/_shared.py`）

`NEKO_VISIT_ENABLED` 关着时 `/api/visit/*` 全部 404、设置页不显示分组（OD-09 v2 第 9 条）。建房 / 入房是**异步**流程：HTTP 只做同步可判的检查并立即返回 202，其余进度经 4.5 `visit_state_change` 推送（F-08）。

#### POST /api/visit/rooms
- 方向: host 前端 → host 后端
- 面: 本机 HTTP
- 字段: req `{catgirl:str, crop?:'upper'|'full', _csrf_token?}` → **202** `{visit_id:str(22), phase:'pending'}`（`invite_code / transport` **不在本响应里**：领到凭证后 `invite_code` 只经 display socket 的 `visit_state_change{invite_ready}` 推给本机前端，**不进** `GET /api/visit/state`；`transport` 可由 `GET /api/visit/state` 读回）| 409 `{code:'VISIT_LOGIN_REQUIRED'}`（`_desktop_session_snapshot()` 无 `local_user_id`，`main_routers/card_drop_router.py:585-600`）| 409 `{code:'VISIT_UNSUPPORTED_ON_THIS_MACHINE', reason}`（**仅**设置页预跑的 `caps` 缓存命中 false 时同步 409；无缓存则 202 后由 `caps` 结果经 `visit_state_change{ended, reason:'unsupported'}` + `status{VISIT_UNSUPPORTED_ON_THIS_MACHINE}` 推送）| 409 `{reason:'voice_session_active'|'route_owned'|'goodbye_silent'|'busy'|'visit_disabled'|'already_visiting'}` | 403 `{code:'VISIT_BANNED'}`（上次 Servers 403 的 60 s 缓存）
- 上限: 顺序固定（D.4）：`activate_visit_route(phase='pending')` 占位 → 前置检查 → 202 返回 → `visit_state_change{pending}` → 父页建 iframe → `caps{stage:'preflight'}` → 通过才 `POST Servers /api/visit/credentials{role:'host'}` → 成功后 `acquire_takeover('neko_visit')` → `credentials` 下发 iframe → `caps{stage:'sdk'}`（能力门 ③，失败 → `finalize('unsupported')` + `release_takeover`，此时已计一次签发）→ `visit_state_change{invite_ready, invite_code}` → `joining / awaiting_accept / started`；任何失败分支 `release_takeover` + `finalize_visit_route_state` + `visit_state_change{ended, reason}` + 对应 `status`；Servers 不可达 → `status{VISIT_SERVERS_UNREACHABLE}`；`visit_id = secrets.token_urlsafe(16)`（22 字符，≤ TRTC strRoomId 64 B）

#### GET /api/visit/invites/{invite_code}/preview
- 方向: guest 前端 → guest 后端（后端代转 Servers 4.7 同名端点，bearer 不出前端）；A 前端在弹「让她出门去 X 家？」确认框**之前**调用
- 面: 本机 HTTP（只读）
- 字段: path `invite_code:str(10)`（`^[A-Z2-7]{10}$`）→ 200 `{visit_id:str(22), host_display_name:str(≤64)`（经 OD-23 清洗）`, host_short_code:str(6), cross_region:bool, expires_at:float}` | 400 `invite_code_format` | 404 `{code:'invite_invalid'}` | 410 `{code:'invite_expired'}` | 403 `{code:'VISIT_BANNED'}`（Servers 403 `visit_banned` 映射，同 rooms）| 409 `VISIT_LOGIN_REQUIRED` | 429 `{code:'rate_limited', retry_after_s}`（Servers 每账号 30 次/分钟限速原样透传）| 503 `servers_unreachable`
- 上限: **本机来源校验**：与变更端点相同，过 `_validate_local_mutation_request` 同款 Origin / Host 白名单 + CSRF token（`main_routers/system_router/_shared.py`），Docker / 局域网访问不放行，无 token → 403；只读、**不消耗邀请码**、不占位、不建 iframe、不占 takeover、不落盘；404 / 410 / 403 时前端不弹确认框，直接给对应 8 语文案；确认框只显示 `host_display_name` + `host_short_code` + `cross_region` 一行（不显示技术数字，OD-26 v3）；`NEKO_VISIT_ENABLED` 关着 404

#### POST /api/visit/rooms/{visit_id}/join
- 方向: guest 前端 → guest 后端
- 面: 本机 HTTP
- 字段: req `{catgirl:str, invite_code:str(10), confirm:true, _csrf_token?}` → **202** `{ok:true, visit_id, phase:'pending'}`（`transport / cross_region` 不在本响应里，经 `visit_state_change{joining}` 与 `GET /api/visit/state` 给出）| 400 `confirm_required` | 400 `invite_code_format`（`^[A-Z2-7]{10}$`，base32 无易混字符）| 409 `VISIT_LOGIN_REQUIRED` | 409 `{reason}`（同上）| 409 `VISIT_UNSUPPORTED_ON_THIS_MACHINE`（同 rooms：仅缓存命中时同步）
- 上限: `confirm` 来自 A 前端「让她出门去 X 家？」对话框（显示 `GET /api/visit/invites/{invite_code}/preview` 返回的对端 `host_display_name` + `host_short_code` 与跨区提示；**不显示 token / TTS 次数等技术数字**，OD-26 v3；实际用量结束后经「查看详情」`GET /api/visit/details/{visit_id}` 可查）；邀请里**不携带任何 URL**；后续 Servers `403 cross_region_unsupported / invite_invalid / invite_expired / room_full` 经 `visit_state_change{ended, reason}` + `status{VISIT_CROSS_REGION_UNSUPPORTED | VISIT_INVITE_INVALID}` 推送；guest 领到凭证后 `visit_state_change{joining}` → 入房 → hello → 等 `ready`（65 s）→ `started` + `departed`

#### POST /api/visit/rooms/{visit_id}/accept
- 方向: host 前端 → host 后端
- 面: 本机 HTTP
- 字段: req `{catgirl:str, accept:bool, _csrf_token?}` → 200 `{ok:true}` | 404 `no_pending_invite` | 409 `already_decided`
- 上限: `accept:true` → 数据通道 `ready` + `media{subscribe:true}` + 建隔离 `OmniOfflineClient` + `_park_proactive_for_goodbye()`（`main_logic/core/proactive.py:82`）→ `visit_state_change{started}`；`false` → `leave{declined}` + finalize；60 s 内未调用视为 `false`

#### POST /api/visit/route/end
- 方向: 任一侧前端 → 本机后端
- 面: 本机 HTTP
- 字段: req `{lanlan_name:str, visit_id:str, reason:'recall'|'route_end', _csrf_token?}` → 200 `{ok:true, mode:'wrap_up'|'finalize', exit_task_started:bool}` | 409 `VISIT_RECALL_ALREADY`（已在收尾）| 404
- 上限: **`recall`（「叫她回来」）不立即结束**：guest 侧 `VisitRoom.on_local_recall` → `wrap_up{ph:'propose', reason:'recall'}`，host 收到不判条件即 `begin`，走自然收尾（OD-08 v2）；host 侧 `recall` 语义 = 「送客」，直接 `begin`；`route_end` = 硬结束（设置里关掉 `visitEnabled`、切换角色、用户在收尾卡死时的第二次点击）→ 锁内翻状态 → 锁外 `leave{reason}` → 立即 `release_takeover` → 固定句 `VISIT_FIXED_LINE`（跳过 LLM）→ `ended`；返回时只保证状态已翻转，长尾在独立 `_exit_task`

#### GET /api/visit/state
- 方向: 前端 → 后端
- 面: 本机 HTTP（只读）
- 字段: `?catgirl=` → `{active:bool, role:'host'|'guest'|null, side, visit_id, phase:'pending'|'joining'|'awaiting_accept'|'active'|'wrap_up'|'ending'|'ended'|null, transport, tier, crop, peer:{cat_name, short_id, human_label, lang, hidden:bool}|null, connected:bool, reconnecting:bool, rtt_ms, rx_fps, tx_fps, reconnects:int, anomalies:int, invite_expires_at?:float, credentials_expires_at?:float, cross_region:bool, memory_pending:bool, debrief:{pending:bool, request_id?}, room:VisitRoom.snapshot()`（`cat_turns_since_human, own_lines_total, phase, wrap_up{…}`）, `transcript:VisitLine[]`（≤50，与 `visit_line` 同形）`}`
- 上限: **本机来源校验**：与变更端点相同，过 `_validate_local_mutation_request` 同款 Origin / Host 白名单 + CSRF token（`main_routers/system_router/_shared.py`），Docker / 局域网访问不放行，无 token → 403；只读；页面重载后前端据 `active && phase not in {ended}` 决定是否重建 iframe（4.3 生命周期）；**响应不含 `invite_code`**（只经 display socket 的 `visit_state_change{invite_ready}` 推给本机前端；host 页面重载后 display socket 重连时，后端对仍在 `invite_ready` 且未过期的房间重推一次该消息）

#### GET /api/visit/transcript
- 方向: 前端 → 后端
- 面: 本机 HTTP（只读）
- 字段: `?catgirl=&visit_id=` → `{visit_id, peer_short_id, peer_uid:str(24), started_at, ended_at?, transport, lines:[{line_id, lp, side, speaker_kind, addressee, ts, text, truncated, i_done}], anomalies:int, source:'spool'|'memory'}`
- 上限: **本机来源校验**：与变更端点相同，过 `_validate_local_mutation_request` 同款 Origin / Host 白名单 + CSRF token（`main_routers/system_router/_shared.py`），Docker / 局域网访问不放行，无 token → 403；`visitMemoryEnabled=true` 时读 `config_dir/visit_spool/<visit_id>.jsonl`（页面重载也能导，7 天内）；否则读内存，本场及结束后 `VISIT_TRANSCRIPT_MEMORY_TTL_S=600` 内可取；不含对端 `visit_uid` 以外的任何身份字段；供用户导出与 `POST /api/visit/report` 附件（OD-26）；离线兜底，完整记录以 Servers 转录为准（OD-26 v3，4.7）

#### GET /api/visit/details/{visit_id}
- 方向: 前端 → 后端（后端代转 Servers 4.7 `GET /api/visit/details/{visit_id}`，bearer 不出前端）；入口 = 结束后该场系统消息折叠区与记忆浏览器串门面板里藏得较深的「查看详情」（OD-26 v3）
- 面: 本机 HTTP（只读）
- 字段: `?catgirl=` → 200（原样透传 Servers 响应）`{visit_id, transport, started_at, ended_at, duration_s:int, free_minutes_deducted:int, usage:{llm_input_tokens:int, llm_output_tokens:int, tts_requests:int, tts_chars:int}, transcript:[{lp, side, from, ts, text, truncated}], uploaded:{host:bool, guest:bool}}` | 404 `unknown_visit` | 403 `not_participant` | 409 `VISIT_LOGIN_REQUIRED` | 503 `servers_unreachable`
- 上限: **本机来源校验**：与变更端点相同，过 `_validate_local_mutation_request` 同款 Origin / Host 白名单 + CSRF token（`main_routers/system_router/_shared.py`），Docker / 局域网访问不放行，无 token → 403；只读、本机不缓存不落盘；可读性由 Servers 判定（只有该场双方账号与管理员）；本侧转录尚未上传成功时 `uploaded.<role>=false` 且 `transcript` 只含已到的那份；token / TTS 等技术数字**只**在这里出现，确认框与主界面都不显示；`NEKO_VISIT_ENABLED` 关着 404

#### POST /api/visit/debrief/choice
- 方向: 前端 → 后端
- 面: 本机 HTTP
- 字段: req `{visit_id:str, choice:'diary'|'forget', _csrf_token?}` → 200 `{ok:true, applied:choice}` | 409 `already_chosen` | 404 `unknown_or_expired`（spool 已过 7 天）| 503 `{retry:true}`（memory_server 不可用；芯片保持可点）
- 上限: 幂等且**按 `visit_id` 串行**：选择判定与提交在同一把 per-visit 锁里做，第一个请求把 `state.json.debrief_choice` 原子写成 `'committing:diary'` 或 `'forget'` 后才开始写入，之后的请求在锁内判定：选择不同（如已占 `committing:diary` 再点「不记」）或已完成 → 409 `{error:'already_chosen', choice, completed:bool}`（前端据 `completed` 决定是否保留「记成日记」的重试入口），选择一旦占住不可改；**同一选择的重试**（状态仍是 `committing:diary`、锁空闲）→ 继续提交，只补 `debrief_writes` 里未完成的那一步——某一步明确失败时响应 503 `{retry:true}`、前端只保留「记成日记」可点，所以重试必须能进来；`debrief_pending`（暂存的日记与事实正文）在两步都完成、选「不记」、对端撤销 `scope:'all'`、7 天到期这四种情况下立即清除，不随 `state.json` 留存；`diary` → 两次写入按可恢复的两步提交执行：生成结果（日记段 + 事实）先原子写进 `state.json.debrief_pending`，重试时直接复用、不重新生成；先写 `visit_facts`（服务端精确哈希去重，重试幂等）并记 `debrief_writes.facts=true`，再写 `/cache` 并记 `debrief_writes.cache=true`；每步的「成功」判据看响应体：`/cache` 只有 **HTTP 200 且 `status=='cached'`** 才算成功——`app/memory_server/routes.py:985-987` 异常时也返回 HTTP 200 `{"status":"error"}`，这种与 HTTP 4xx/5xx、连接被拒一样算**明确失败**（可重试、不记 `debrief_writes.cache`）；`visit_facts` 同理只有 HTTP 200 且响应体 `ok==true` 才算成功；只有明确失败才重试该步，超时等结果不确定时按「已写」处理，所以日记最多进一次近期记忆、不会重复；两步都完成才把 `debrief_choice` 定为 `diary`，否则启动补录按 `debrief_writes` 只补未完成的那一步（7 天内）；一次 LLM 调用同时产出两样（都过 `redact_outbound` + `assert_no_peer_ngram(n=8)`）：(a) 日记段 ≤`VISIT_DIARY_MAX_TOKENS=300` → `POST /cache/{lanlan}`（`app/memory_server/routes.py:912`，只含一条 `type:'ai'`）进近期记忆——事实抽取（`app/memory_server/signal_extraction.py:494`）跳过无用户消息的窗口，它不会变成长期事实；(b) ≤`VISIT_DIARY_FACTS_MAX=3` 条串门事实（每条 ≤60 字）→ `POST /internal/memory/{lanlan}/visit_facts`（下文，新增）进 fact 层，不进 reflection；`forget` → 不写私聊、立即删 `.jsonl`；**串门记忆区（`group_chat` 等）的 digest 与本选择无关**：只受 `visitMemoryEnabled` 与对端 consent 控制，在 finalize（或补录）时做一次（G.2）；成功后发 `visit_debrief{chosen}`

#### GET /api/visit/memory/peers
- 方向: 前端 → 后端
- 面: 本机 HTTP（只读）
- 字段: `?catgirl=` → `{peers:[{peer_uid:str(24)`（**完整** `visit_uid`；本机 API，数据本来就在本机磁盘的名册里，响应保留它是为了让「清除这个人 / 拉黑」按钮调 `POST /api/visit/memory/forget{peer_uid}` 与 `POST /api/visit/contacts/block{peer_uid}`）`, short_id:str(6), display_name, first_seen, last_seen, visits:int, blocked:bool, fact_count:int, reflection_count:int, chars:[{peer_char_id:str(26), display_name, pair_id:str(24), last_visit_at, fact_count}]}]}`
- 上限: **本机来源校验**：与变更端点相同，过 `_validate_local_mutation_request` 同款 Origin / Host 白名单 + CSRF token（`main_routers/system_router/_shared.py`），Docker / 局域网访问不放行，无 token → 403；按 `visit_uid` 聚合（人级 `participant('neko_visit', peer_uid)` 一行，展开到 pair / 角色）；数据源 = `config_dir/visit_peers.json` 名册的 `by_char[catgirl]`（名册按本机角色分开：`{peers: {<visit_uid>: {display_name, short_code, first_seen, last_seen, by_char: {<本机角色名>: {pairs, chars}}}}}`，只列与 `catgirl` 串过门的人）× memory_server 只读端点 `scoped_subjects`；**界面上**只显示 `display_name` + `short_id`，永不显示完整 `peer_uid`（它只作按钮的调用参数，不渲染进任何可见文本）

#### POST /api/visit/memory/forget | forget_all | contacts/block
- 方向: 前端 → 后端
- 面: 本机 HTTP
- 字段: forget `{catgirl, peer_uid}`；forget_all `{catgirl}`；block `{peer_uid, blocked:bool}` → 200 `{ok:true, forgotten:int}` | 409 `visit_active`（串门中先 end）| 503 `{retry:true}`（信赖池未加载时 fail closed）
- 上限: forget **只作用于 `catgirl` 这个本机角色**：对该角色下该人的 `participant` subject + 名册 `by_char[catgirl]` 里所有 pair 的 `group_chat` + `group_participant` 逐个 `scoped_forget` + 删 `by_char[catgirl]`（`by_char` 为空时才删整条 peer）+ 该人在该角色下的残留 spool / `state.json` 删除；黑名单项**不**随 forget 删除（拉黑不是记忆）；block 写 `config_dir/visit_blocklist.json`（`atomic_write_json`，`utils/file_utils.py:785`），主键 `visit_uid`，在飞串门中拉黑对端 → 立即 `route_end`；UI 文案注明「对方机器上的副本无法远程清除；回家自述那句不在清除范围」

#### POST /api/visit/report
- 方向: 前端 → 后端（后端代转 Servers 4.7 `POST /api/visit/reports`，bearer 不出前端）
- 面: 本机 HTTP
- 字段: req `{visit_id, reason:'harassment'|'sexual'|'privacy'|'spam'|'other', note?:str(≤500), include_transcript:bool, _csrf_token?}` → 200 `{ok:true, report_id}` | 404 | 409 `VISIT_LOGIN_REQUIRED` | 503
- 上限: 转录附件取 `GET /api/visit/transcript` 同源（≤80 行、≤64 KB）；每 `visit_id` 只能举报一次

#### GET /visit/transport
- 方向: 父页 iframe `src` → 后端 `pages_router`
- 面: 本机 HTTP（HTML 模板，无末尾斜杠）
- 字段: `?v={static_asset_version}&side=host|guest&visit_id=` → `templates/visit_transport.html`（只引 `/static/visit/transport/*.js?v=…`，不静态引用任何 vendor UMD，不引用任何非 `/static/` 资源）
- 上限: 同源；`pages_router.py` 与 `/`（`:265`）、`/chat`（`:453`）并列的新路由；`NEKO_VISIT_ENABLED` 关着 404

#### GET /internal/memory/{name}/scoped_subjects
- 方向: 主进程 → memory_server
- 面: 记忆 HTTP（只读，OD-18；memory_server 新增的唯一只读端点，另一个新增写端点是下条 `visit_facts`）
- 字段: `?platform=neko_visit` → `{subjects:[{subject_kind:'group_chat'|'participant'|'group_participant', subject_id, scope, display_name, facts:int, reflections:int, persona:bool, last_write_at, archived:bool}]}`
- 上限: 不进围栏；按 `(subject_kind, platform)` 过滤而不是裸前缀（`participant` 的 `neko_visit:<uid>` 与 `group_chat` 的 `neko_visit:<pair>` 前缀相同）

#### POST /internal/memory/{lanlan}/visit_facts（新增，OD-16 v3）
- 方向: 主进程（`main_logic/visit/debrief_writers.py`）→ memory_server
- 面: 记忆 HTTP（写，进围栏写 op 登记）
- 字段: req `{visit_id:str(22), facts:[{text:str(≤60 字)}](1..VISIT_DIARY_FACTS_MAX=3)}` → 200 `{ok:true, written:int, deduped:int}` | 409 limited_mode（与既有写端点一致）| 422
- 上限: 服务端统一盖字段，调用方不可覆盖：`source='ai_disclosure'`（她自己的经历）、`importance=4`、`absorbed=True`、`origin='neko_visit'`、`visit_id`；写入走 `FactStore._apersist_new_facts` 的语义去重；`importance=4`（低于 reflection 门槛 5）与 `absorbed=True` 双保险——`aget_unabsorbed_facts`（`memory/facts.py:5483`，`min_importance=5`）永远取不到它们，reflection 不合成；召回照常可取；`main_logic/card_forge_facts.py` 抽样前过滤 `origin=='neko_visit'`，不进社区分享卡片

#### POST /internal/memory/{name}/scoped_history | scoped_context | scoped_mentions | scoped_forget | scoped_facts（既有）
- 方向: 主进程 → memory_server（经自建的 `memory/scoped_client.py::ScopedMemoryClient`，OD-31 v3）
- 面: 记忆 HTTP
- 字段: 同今天的契约（`app/memory_server/routes.py:1949 / :2700 / :3276 / :2796 / :1875`）；`subject_kind: Literal["group_chat","participant","group_participant"]`（`:1221`）；segments 批 `speaker_tier="none"`、`speaker_id` 猫娘 `neko_visit:<peer_char_id>` / 人 `neko_visit:<peer_uid>`、`display_name` 经 `_sanitized_display_name`（`:1831`）
- 上限: `scoped_context` subjects 1..8（顺序即预算优先级，`include_legacy_private=False`）；`scoped_history` 一批 1..200 条（`config/memory_settings.py:210`）；`scoped_facts` 1..32 条、每条 ≤2000 字；`scope=all` 撤销时 `scoped_forget` 对群 + 对方猫娘 + 对方亲人三者

### 4.7 Servers（闭源，独立排期；本节是给 Servers 的唯一契约）

基址 `https://community.project-neko.cn`（`main_routers/card_drop_router.py:41`、`utils/social_base.py:12`）；客户端用 `get_external_http_client()`（`utils/http/external_client.py:65`）；bearer = 社区 OAuth `access_token`（`_desktop_session_snapshot()`，`card_drop_router.py:585-600`），平台 token 只发给 Servers 自己（既有收紧姿态 `:999-1006`）。**Servers 核验社区账号发生在任何 vendor 连接之前**：没有 OAuth 就拿不到 UserSig / JWT。串门进行中不依赖 Servers（凭证与票据都在手里，guest 40 / host 50 min TTL 覆盖 30 min 硬顶 + 重连）。

#### POST /api/visit/credentials
- 方向: 本机后端 → Servers
- 面: 云端 HTTP
- 字段: headers `{Authorization: Bearer <access_token>, X-Client-Id: <client_id>}`；req `{role:'host'|'guest', visit_id:str(22), char_tag:str(32hex), display_name?:str(≤64), region_hint:'cn'|'global'|'unknown'`（`ConfigManager._region_cache` 只读，`aensure_region_resolved(timeout=1.5)`，`utils/config_manager/core_config.py:529`；None → `'unknown'`）, `tier:'sd600', app_version:str, invite_code?:str(10)`（**guest 必填**）`}` → 200 `{transport:'trtc'|'livekit', expires_at:float, vendor:{trtc?:{sdk_app_id, user_id, user_sig, str_room_id, expire:2400}, livekit?:{url, token, ttl_s:2400}}, identity_ticket:str, visit_uid:str(24)`（自己的）, `vid:str(26)`（自己的）, `peer_vid?:str(26)`（guest 侧：host 的）, `invite_code?:str(10), invite_expires_at?:float`（host 侧，10 min）, `cross_region:bool=false, entitlement:{tier:'sd600', free_minutes_left_today:int, concurrent_rooms_left:int}}` | 401 `unauthenticated` | 403 `{code:'banned'}` | 403 `{code:'tier_not_entitled'}` | 403 `{code:'cross_region_unsupported'}` | 403 `{code:'invite_invalid'|'invite_expired'|'room_full'}` | 409 `{code:'role_taken'}` | 429 `{code:'quota_exceeded', retry_after_s:int}` | 5xx
- 上限: **房间绑定**（C.5）：host 领凭证时 Servers 把 `visit_id` 登记到 host 的 `visit_uid` 下并返回一次性 `invite_code`（10 min）；guest 必须带 `invite_code`，校验后绑到该房；每房 host + guest 各一，第三者领不到（`room_full`）；**transport 由 host 区域决定**（Servers 以来源 IP 复核 `region_hint`，不一致以 Servers 为准），guest 拿同一 transport，guest 的 `region_hint` 只用于判 `cross_region`；**跨区默认 fail-closed** → `403 cross_region_unsupported`（Servers 侧开关可改为「允许 + 警告」，T9 后由 owner 定）；**配额**（C.7）：每账号「每日签发分钟数」= 签发次数 × 30 min，免费档 `VISIT_FREE_MINUTES_PER_DAY`（占位 120，**由 owner 定价时拍板**），每账号并发房 ≤2（**名额释放以 vendor 房间结束事件为准**：TRTC 房间解散回调 / LiveKit `room_finished` webhook，或 **Servers 自行向 vendor 查询确认该参与者 / 房间已不在**之后才释放；客户端 finalize 后的 `POST /api/visit/transcripts` **只触发一次这样的查询、不直接释放**（否则改过的客户端可以边在房边上传转录来多开）；都没来时凭证 `expires_at` 到期兜底），重连（同 `visit_id` + 同 `visit_uid` 且未过 `expires_at`）**不消耗**配额、返回同一份凭证；付费档只改 `entitlement`；vendor TTL **按 `role` 签不同值**：guest `VISIT_CREDENTIAL_TTL_S=2400`（40 min），host `VISIT_HOST_CREDENTIAL_TTL_S=3000`（50 min = 等待对端 600 + 硬顶 1800 + 余量 600），身份票 `exp` 与 vendor 凭证同值、`expires_at` 返回实际值；TRTC UserSig `expire=2400|3000`（`HMAC-SHA256(SDKSecretKey; SDKAppID, UserID, expire)`，服务端生成，`userId ≤32 B [a-zA-Z0-9_-]`、`strRoomId ≤64 B`，https://web.sdk.qcloud.com/trtc/webrtc/v5/doc/en/TRTC.html ），LiveKit JWT HS256 `ttl=40m|50m`、grants 按侧位收紧——host `{roomJoin:true, room:visit_id, canPublish:false, canSubscribe:true, canPublishData:true}`（host 只收视频、只发数据），guest `{roomJoin:true, room:visit_id, canPublish:true, canPublishSources:['camera'], canSubscribe:true, canPublishData:true}` + 服务端 `room.max_participants=2`；**画质档位服务端约束**（开源客户端可改 SDK 参数，客户端的 `profile` / `maxBitrate` 不可信）：LiveKit 侧 Servers 订阅 webhook `track_published`（带轨道宽高），超出凭证 `tier` 的尺寸 → `RoomService.RemoveParticipant` 踢人 + 记违规；TRTC 侧 host 能否以观众角色（`ROLE_AUDIENCE`）进房仍 `sendCustomMessage` 未确认（§3.12 T13，确认前 host 仍用 `ROLE_ANCHOR`），Servers 定时拉 TRTC 用量统计 / 事件回调，按账号比对实际分辨率档与码率，超档 → 走封禁流程（`POST /admin/visit/bans`）；检测到并处置之前，单账号最坏按凭证允许的最高档计费（§3.5.8）；IP：TRTC / LiveKit 都是 SFU，对端拿不到你的 IP，Servers 因区域复核知道（C.10）

#### 身份票 identity_ticket（claims 全表）
- 方向: Servers → 本机后端 → （`hello.ticket`）→ 对端后端
- 面: 鉴权（Ed25519）
- 字段: 编码 `base64url(claims_json) + '.' + base64url(sig_64B)`（两段，不是 JWT 三段；`cryptography.hazmat.primitives.asymmetric.ed25519`，`pyproject.toml:62` 已依赖）；claims = `{v:1, iss:'neko-servers', aud:'neko-visit', kid, sub:<visit_uid>, vid, visit_id, role, transport, char_tag, display_name?, iat, exp, jti}`，每个 claim 的类型、含义与接收侧核验见下表
- 上限: 序列化 ≈560 B（估算）；不含 email、client_id、access token、community_uuid 原文；`pair_id = sha256(min(uid_a, uid_b) + '|' + max(uid_a, uid_b))[:24]`（两端各算一遍结果相同，G.1）；`short_id = visit_uid[:6].upper()`（UI 唯一可见形式）；Servers 换盐 = 所有对端变新人（运维文档明写）；变异单测五种必红：篡改 `sub` / 过期 / `role` 对调 / `vid` 不符 / 未知 `kid`

| claim | 类型 | 含义 | 接收侧核验 |
|---|---|---|---|
| `v` | int=1 | 票版本 | 必须 1 |
| `iss` | `'neko-servers'` | 签发者 | 相等 |
| `aud` | `'neko-visit'` | 受众 | 相等 |
| `kid` | str(≤16) | 公钥 id | 查 `VISIT_SERVERS_PUBKEYS`，miss → `GET /api/visit/pubkeys` 一次 → 仍 miss fail closed |
| `sub` | str(24 hex) | `visit_uid` = `HMAC-SHA256(server_secret, community_uuid)` 十六进制前 24 字符（稳定、不透明、Servers 可反查；**不是**裸 uuid，C.1） | `∉ visit_blocklist.json`；记忆 / 名册 / 举报主键 |
| `vid` | str(26) | vendor userId / identity = `role[0] + '_' + sha256(visit_uid + '|' + visit_id).hexdigest()[:24]`（C.3；落 TRTC userId 字符集） | **== vendor 盖的 `from_vid`** |
| `visit_id` | str(22) | 房间 | == 本房 |
| `role` | `'host'|'guest'` | 侧位 | == 对侧 |
| `transport` | `'trtc'|'livekit'` | 一房一 transport | == 本侧 credentials.transport |
| `char_tag` | str(32hex) | 对端自报角色标签（只在 `sub` 命名空间下有意义） | 派生 `peer_char_id = 'c_' + sha256(peer_uid + '|' + char_tag)[:24]` |
| `display_name` | str(≤64)? | 猫娘显示名 | 经 OD-23 清洗后才进 UI / 名册 |
| `iat` | int | 签发时刻 | `iat - 300 ≤ now` |
| `exp` | int | `= iat + 2400`（guest 40 min）或 `iat + 3000`（host 50 min，`VISIT_HOST_CREDENTIAL_TTL_S`） | `now ≤ exp + 300`（`VISIT_TICKET_CLOCK_TOLERANCE_S=300`） |
| `jti` | str(22) | 票 id | 同房同 `vid` 重连允许重放同一 jti；跨房 / 跨 vid 重放无效（`visit_id`、`vid` 已绑） |

#### GET /api/visit/pubkeys
- 方向: 本机后端 → Servers
- 面: 云端 HTTP（公开，无鉴权）
- 字段: → `{keys:[{kid:str, alg:'Ed25519', pub:str(base64url 32 B), not_before?:int, not_after?:int}], ttl_s:86400}`
- 上限: 客户端缓存 `VISIT_PUBKEYS_CACHE_S=86400`；内置表 `config/visit_settings.py::VISIT_SERVERS_PUBKEYS = {kid: base64url}` 随发版更新（`kid` 轮换靠发版，本端点是轮换期第二来源）；`kid` 不命中且拉不到 → fail closed（C.8）；开发环回：`NEKO_VISIT_DEV_KEYFILE=<path>` 指向本地 Ed25519 私钥，`scripts/visit_dev_mint.py` 用它签票并把公钥追加进开发键位，**核验代码路径与生产完全相同**（没有「跳过验签」分支，C.9）；本地起 LiveKit 用 `NEKO_VISIT_DEV_LIVEKIT_URL / _API_KEY / _SECRET`

#### GET /api/visit/invites/{invite_code}/preview
- 方向: 本机后端（代转 4.6 同名端点）→ Servers（bearer）
- 面: 云端 HTTP（只读）
- 字段: headers `{Authorization: Bearer <access_token>, X-Client-Id: <client_id>}`；path `invite_code:str(10)` → 200 `{visit_id:str(22), host_display_name:str(≤64), host_short_code:str(6)`（= host `visit_uid[:6]` 大写）`, cross_region:bool`（host 区域 vs 请求者区域，按 `POST /api/visit/credentials` 同一规则由 Servers 以来源 IP 判定）`, expires_at:float}`（邀请码到期时刻）| 401 `unauthenticated` | 404 `{code:'invite_invalid'}` | 410 `{code:'invite_expired'}` | 403 `{code:'visit_banned'}` | 429 `{code:'rate_limited', retry_after_s:int}`
- 上限: **每账号 30 次/分钟**，超限 429；邀请码是 10 位 base32（50 bit）、10 min 有效，按此限速枚举不可行；**只读、不消耗邀请码**（一次性 `invite_code` 只在 guest 的 `POST /api/visit/credentials` 成功时被消耗）；不写签发记录、不扣配额；只返回确认框需要的这五个字段，不返回 host 的完整 `visit_uid`、`vid` 或任何 vendor 信息

#### POST /api/visit/reports
- 方向: 本机后端 → Servers（bearer）
- 面: 云端 HTTP
- 字段: req `{visit_id, peer_uid:str(24), reason:'harassment'|'sexual'|'privacy'|'spam'|'other', note?:str(≤500), transcript?:[{line_id, lp, side, speaker_kind, ts, text}]`（≤80 行、≤64 KB）, `anomalies:int, app_version}` → 201 `{report_id}` | 400 `{code:'peer_mismatch'}` | 401 | 404 `unknown_visit`（Servers 只接受自己签发过的 `visit_id` 且举报人是该房成员）| 429
- 上限: **被举报方由 Servers 推导**：以 `visit_id` + 举报人 bearer 账号查签发记录，取该房另一侧的 `visit_uid` 作被举报方；请求里的 `peer_uid` 只作校验，与推导结果不一致 → 400 `peer_mismatch`（客户端无法借举报指认一个不在场的人）；每 `visit_id` 每账号 1 次；无第三方盖章（双侧 spool / outbox JSONL + 双方各自上传的转录（`POST /api/visit/transcripts`）是全部证据链，§3.8 如实写）

#### POST /api/visit/transcripts
- 方向: 本机后端 → Servers（bearer；每场 finalize 后由后台任务上传一次，双方各传自己那份，OD-26 v3）
- 面: 云端 HTTP
- 字段: headers `{Authorization: Bearer <access_token>, X-Client-Id: <client_id>}`；req `{visit_id:str(22), role:'host'|'guest', started_at:float, ended_at:float, finalized_reason:str, usage:{duration_s:int, llm_input_tokens:int, llm_output_tokens:int, tts_requests:int, tts_chars:int}, lines:[{lp:int, side:'host'|'guest', from:'own_cat'|'peer_cat'|'own_human'|'peer_human', ts:float, text:str(≤4096 B), truncated:bool}], anomalies:int, app_version:str}` → 201 `{ok:true}` | 200 `{ok:true, duplicate:true}`（同 `visit_id + role` 已存）| 401 `unauthenticated` | 403 `{code:'not_participant'}`（签发记录里该账号不是本房该 `role`）| 404 `unknown_visit`（Servers 未签发过该 `visit_id`）| 413 `too_large` | 429 `{code:'rate_limited', retry_after_s:int}` | 5xx
- 上限: **按 `visit_id + role` 幂等**（第二次上传返回 200 `duplicate:true`，不覆盖）；待传内容与 spool 解耦、**一律临时存盘，与 `visitMemoryEnabled` 无关**：finalize 时写只含上传字段的 `config_dir/visit_spool/<visit_id>.upload.json`（原子写，`0o600`，上传成功即删；关机 `stop_all('shutdown')` 也在 ≤3 s 预算内从内存转录 + 用量同步写出它，下次启动补传），所以 debrief 选「不记」立即删 `.jsonl`、spool 7 天清理、记忆开关关着都不影响待传转录；失败下次启动重试；自结束起 7 天仍未上传成功则放弃并记一条本地诊断事件；`role` 由 Servers 以 bearer 账号对照签发记录核验；`lines` 按 `(lp, side_rank)` 排序，文本是本侧收口的 `text{final}`（对端行 = 对端已过出站清洗的文本，本侧猫娘行 = 已放出分片拼接，本侧亲人行是其原文）；与 `visitMemoryEnabled` 无关（记忆开关管「她记不记」，上云管账单与举报）；失败重试只看 `.upload.json`（进程内退避 + 下次启动重试），不看记忆开关；Servers **长期保留**（与账单记录同期）；对端撤销 `consent{scope:'all'}` **不删**云端转录；披露只写进隐私政策；用量计数另经现有遥测 counter / histogram 上报（低基数维度、不带 `visit_id`），本端点是唯一带 `visit_id` 的用量记录

#### GET /api/visit/details/{visit_id}
- 方向: 本机后端（代转 4.6 同名端点）→ Servers（bearer）；管理员后台用同一数据
- 面: 云端 HTTP（只读）
- 字段: headers `{Authorization: Bearer <access_token>}` → 200 `{visit_id, transport, started_at, ended_at, duration_s:int, free_minutes_deducted:int, usage:{llm_input_tokens, llm_output_tokens, tts_requests, tts_chars}, transcript:[{lp, side, from, ts, text, truncated}], uploaded:{host:bool, guest:bool}}` | 401 | 403 `{code:'not_participant'}` | 404 `unknown_visit`
- 上限: **只有该场双方账号（签发记录里的两个 `visit_uid`）与管理员可读**；`duration_s` 与 `free_minutes_deducted`（本场计入该账号「每日签发分钟数」的分钟数，C.7）来自签发记录；`usage` 取请求者本侧上传的那份；`transcript` = 双方两份按 `(lp, side_rank)` 合并，同一行两份不一致时以说话方那一侧上传的为准（本侧猫娘 / 本侧亲人行信本侧）；`usage` 只返回请求者本侧那份（对方的消耗不给看）；某侧未上传时 `uploaded.<role>=false`

#### POST /admin/visit/bans
- 方向: 管理员 → Servers
- 面: 云端 HTTP（admin）
- 字段: req `{visit_uid:str(24), reason:str, until?:int}` → 204；对偶 `DELETE /admin/visit/bans/{visit_uid}` → 204；`GET /admin/visit/bans?visit_uid=` → 列表
- 上限: 效果 ① `POST /api/visit/credentials` 对该 `sub` 回 403 `banned`（**下一场**立即发不出去）；② 客户端黑名单立即生效（hello 阶段拒，与服务端封禁独立）；③ **在飞场踢人 = Servers 侧 follow-up（不阻塞 v1）**：接口名 `POST /admin/visit/kick {visit_id, visit_uid}` → Servers 按签发记录找到 `vid` 与 transport → TRTC 服务端 `RemoveUserByStrRoomId`（`RoomId` 字符串 + `UserIds.N` ≤10，https://cloud.tencent.com/document/product/647/50426 ）/ LiveKit `RoomService.RemoveParticipant(room, identity)`（https://docs.livekit.io/home/server/managing-participants/ ）→ 客户端收 `KICKED_OUT{reason:'banned'}` / `Disconnected` 当终态（4.3 `state{kicked}`），无需改客户端（C.6）

#### Servers 侧需要实现的清单（供排期）
- 方向: —
- 面: 云端
- 字段: ① `POST /api/visit/credentials`（账号核验 → 封禁表 → 房间绑定 / invite_code → 配额扣减 → host 区域 → transport → 铸 UserSig 或 JWT → 签 Ed25519 票）；② `GET /api/visit/pubkeys`；③ `POST /api/visit/reports`；④ `POST|DELETE|GET /admin/visit/bans`；⑤ 密钥托管：腾讯云 SDKAppID / SDKSecretKey、LiveKit API key / secret（Cloud 或自建）、Ed25519 私钥 + kid 轮换；⑥ 签发记录表 `{visit_id, role, visit_uid, vid, transport, issued_at, expires_at}`（配额、房间绑定、踢人、举报核对、转录上传与详情鉴权都靠它）；⑦ `POST /api/visit/transcripts` + 转录 / 用量存储（按 `visit_id + role`，长期保留、与账单记录同期，对端撤销不删）；⑧ `GET /api/visit/details/{visit_id}`（该场双方与管理员可读）；⑪ `GET /api/visit/invites/{invite_code}/preview`（guest 确认框用的只读预览，不消耗邀请码，每账号 30 次/分钟限速）；⑫ 画质档位服务端约束：LiveKit token 按侧位收紧（host `canPublish:false`、guest `canPublishSources:['camera']`）+ 订阅 `track_published` webhook 超档踢人记违规；TRTC 定时拉用量统计 / 事件回调按账号比对分辨率档与码率，超档走封禁；⑬ 并发名额释放：以 vendor 房间结束事件为准（TRTC 房间解散回调 / LiveKit `room_finished` webhook），或 Servers 自行向 vendor 查询确认参与者 / 房间已不在之后释放；`POST /api/visit/transcripts` 只触发一次该查询、不直接释放；凭证到期兜底；（follow-up）⑨ `POST /admin/visit/kick`；⑩ Servers 侧「跨区允许 + 警告」开关
- 上限: 复用既有 `/api/users/me`、`/api/auth/session/bootstrap`、`/api/clients/register`；Servers 宕机只影响新场次，不影响在飞

### 4.8 常量表（`config/visit_settings.py`，每条赋值后紧跟英文 docstring，仿 `config/focus_settings.py`；env 用 `config/network._read_bool_env/_read_str_env`）

| 常量 | 默认值 | 来源章节 | 说明 |
|---|---|---|---|
| **发布与开发** | | | |
| `VISIT_ENABLED` | env `NEKO_VISIT_ENABLED`，默认 False | OD-09 v2 | 总闸；关着 `/api/visit/*` 404、设置页无分组 |
| `NEKO_VISIT_DEV_KEYFILE` | env，默认 '' | OD-01 v2 / 4.7 | 开发环回 Ed25519 私钥路径；非空时其公钥进开发 kid |
| `NEKO_VISIT_DEV_LIVEKIT_URL / _API_KEY / _SECRET` | env，默认 '' | OD-07 v2 | 本地 LiveKit 开发环回 |
| **视觉通道** | | | |
| `VISIT_TIERS` | `{sd600: enabled, hd1200: disabled, fhd2400: disabled}` | OD-06 v2 / §3.4 | 每档 `crop_upper(W,H) / crop_full / pack(W,2H) / area / fps=30 / video_kbps / data_kbps_max / trtc_tier` |
| `VISIT_VIDEO_TIER_DEFAULT` | `'sd600'` | OD-06 v2 | 唯一发布档 |
| `VISIT_VIDEO_KBPS` | 560 | OD-06 v2 | 视频码率上限 |
| `VISIT_DATA_KBPS_MAX` | 40 | OD-06 v2 / 4.1 | 数据通道预算（= 5 KB/s） |
| `VISIT_CAPTURE_FPS` | 30 | §3.4 / D.5 | 分数累加器目标；不低于 30 是硬要求 |
| `VISIT_CROP_DEFAULT` | `'upper'` | OD-06 v2 | 上半身；`'full'` 256×560 |
| `VISIT_CROP_REFRESH_MS` | (300, 1000) | 4.4 crop | 裁剪框刷新区间 |
| `VISIT_CROP_HYSTERESIS` | 中心 4% / 尺寸 8% / 过渡 300 ms | 4.4 crop | 滞回 |
| `VISIT_CONGESTION_LADDER` | `{upper: [(320,448,560),(256,352,400),(192,272,300)], full: [(256,560,560),(208,448,400),(160,352,300)]}` | OD-06 v2 / D.7 | 只降分辩率不降 fps；最低 300 kbps（标清带下限） |
| `VISIT_CONGESTION_TRIGGER_S` / `_RECOVER_S` | 10 / 30 | OD-06 v2 | `rx_fps<24` 或 `uplinkLoss>15%` 连续 10 s 降；30 s 干净升 |
| `VISIT_LIVEKIT_PUBLISH` | `{videoCodec:'vp9', simulcast:False, scalabilityMode:'L1T1', maxBitrate:560000, maxFramerate:30, degradationPreference:'maintain-framerate'}` | D.3 / §3.5 | 不显式设 `scalabilityMode` 则 SDK 对 vp9 默认 `L3T3_KEY` |
| `VISIT_VP9_CPU_FALLBACK` | (27 fps, 10 s) | D.3 / T8 | 编码 fps <27 持续 10 s → 下次串门 vp8 |
| `VISIT_LIVEKIT_HOSTS` | Cloud + 自建域列表 | OD-12 v2 / 4.3 | iframe 校 `url` 主机名精确命中 |
| `VISIT_FRAME_STARVATION_S` | 1.0 | 4.4 hidden / §3.11 | 父页 1 s 无成功 `onFrame` → `hidden{on:true}` → iframe 发 `state{hidden:true}`（不用 `document.hidden`） |
| **数据通道与可靠层** | | | |
| `VISIT_WIRE_PROTO` | 1 | 4.1 版本偏斜 | `hello.caps.proto`；主版本不同 → `proto_mismatch` |
| `VISIT_PIECE_MAX_BYTES` | 1000 | 4.1 信封 | 每片含信封 ≤1000 B |
| `VISIT_PIECES_MAX` | 8 | 4.1 信封 | `n` 上限；按编码后字节计，超出则截短 `text.txt` 并置 `trunc_reason:'wire_size'` |
| `VISIT_DELTA_TEXT_MAX_BYTES` | 800 | 4.2 line_delta | `txt` 上限；整条 ≤900 B 恒一片 |
| `VISIT_TEXT_MAX_BYTES` | 4096 | 4.2 text | `clamp_text_utf8` |
| `VISIT_LINE_MAX_TOKENS` / `VISIT_HUMAN_LINE_MAX_TOKENS` | 400 / 600 | OD-23 / 4.2 text | `truncate_to_tokens`（猫娘行出站不再整行截断，OD-21 v3；人类行仍用 600） |
| `VISIT_REASSEMBLY_TIMEOUT_S` | 2 | 4.1 重组 | 首片起 2 s 未齐丢整条 |
| `VISIT_DATA_BUCKET_BPS` / `_BURST_BYTES` | 5120 / 3072 | 4.1 限速 | 字节桶 |
| `VISIT_MSG_BUCKET_PER_S` / `_BURST` | 20 / 10 | 4.1 限速 | 条数桶 |
| `VISIT_DELTA_MIN_INTERVAL_MS` | 250 | 4.1 限速 | 同行 delta 合并 |
| `VISIT_DELTA_BACKLOG_MERGE_S` / `_DROP_S` | 3 / 10 | 4.1 限速 | 积压合并 / 作废 |
| `VISIT_OUTBOX_RETRY_S` | (1, 2, 4, 8, 8) | 4.2 ack | 排完后按 8 s 继续重传 |
| `VISIT_LEAVE_DRAIN_S` | 2 | 4.2 leave | 正常离开前最多等 2 s 排空 outbox |
| `VISIT_DELIVERY_TIMEOUT_S` | 30 | 4.2 ack | 必达项首发起 30 s 未确认 → `finalize('delivery_failed')`（与判死同一个数）；只计传输已连接的时间，自身重连与页面重载宽限期间暂停 |
| `VISIT_ACK_COALESCE_MS` | 200 | 4.2 ack | ack 合并窗 |
| `VISIT_DEDUP_LRU` | 512 | 4.2 ack | `ln` / `seq` 幂等 |
| `VISIT_REORDER_BUFFER_MAX` | 64 | 4.2 ack | 必达消息严格按 `seq` 处理，缺口后先到的最多缓存 64 条，超出 → `finalize('peer_protocol_violation')` |
| `VISIT_ANOMALY_FINALIZE_COUNT` | 20 | 4.1 版本偏斜 / B.3 | 连续异常才 finalize |
| `VISIT_STREAM_DELTAS` | **True** | OD-21 v3 | 紧急开关；False 字幕退回整句，TTS 仍流式 |
| `VISIT_CLAUSE_SOFT_MAX_CHARS` | 24 CJK / 12 拉丁词 | OD-21 v3 / d4 §2.1 | 逗顿号只在累积 ≥24 字时切；只影响字幕切片粒度 |
| `VISIT_LINE_STALL_S` | 20 | 4.2 line_delta | 无新片且无 `text` → 本地截断 |
| `VISIT_OUTBOX_FILE` | `config_dir/visit_spool/<visit_id>.outbox.jsonl` | OD-30 / OD-17 v2 | 与 spool 同目录同 helper（新 helper：O_APPEND 单次 write，fsync 是新增；先例 `utils/event_logger.py:263-264` 不 fsync） |
| **身份与凭证** | | | |
| `VISIT_SERVERS_PUBKEYS` | `{kid: base64url}` | OD-01 v2 / 4.7 | 内置公钥表 |
| `VISIT_PUBKEYS_CACHE_S` | 86400 | 4.7 pubkeys | 24 h |
| `VISIT_TICKET_CLOCK_TOLERANCE_S` | 300 | 4.7 claims | 与 telemetry 同容差（`local_server/telemetry_server/security.py:38`） |
| `VISIT_CREDENTIAL_TTL_S` | 2400 | OD-01 v2 / C.2 | guest 的票与 vendor 凭证同 40 min |
| `VISIT_HOST_CREDENTIAL_TTL_S` | 3000 | OD-01 v2 / 4.7 | host 的票与 vendor 凭证 50 min = `VISIT_INVITE_WAIT_S`(600) + `VISIT_MAX_DURATION_S`(1800) + 余量 600 |
| `VISIT_INVITE_CODE_TTL_S` | 600 | C.5 / 4.7 | 10 min |
| `VISIT_SHORT_ID_LEN` | 6 | OD-05 v2 | UI 只显短码 |
| `VISIT_FREE_MINUTES_PER_DAY` | **120（占位，owner 定价拍板）** | C.7 / 4.7 | Servers 侧强制；本地只用于文案 |
| `VISIT_MAX_CONCURRENT_ROOMS_PER_ACCOUNT` | 2 | C.7 | Servers 侧 |
| `VISIT_PEERS_FILE` / `VISIT_BLOCKLIST_FILE` | `config_dir/visit_peers.json` / `visit_blocklist.json` | OD-05 v2 | 主键 `visit_uid`，`atomic_write_json` |
| **生命周期（OD-11 v2 / E）** | | | |
| `VISIT_HEARTBEAT_S` | 5 | 4.2 hb | |
| `VISIT_PEER_LOST_S` | 30 | 4.2 hb | 对端判死唯一时钟；host 侧对端 `hello` 核验通过后才启动；guest 侧自入房起算（核验前也只等 30 s） |
| `VISIT_INVITE_WAIT_S` | 600 | 4.2 hello / OD-11 v2 | **只用于 host**：对端 `hello` 核验前（`invite_ready` 阶段）的唯一等待上限，与邀请码 10 min 一致；超时 → `finalize('invite_expired')` |
| `VISIT_SELF_RECONNECT_S` | 25 | 4.3 state | 比 30 短 5 s；是上限，实际截止 = `min(断线时刻 + 25 s, 最后一次成功发出心跳 / 必达消息的时刻 + 30 s − VISIT_RECONNECT_MARGIN_S(3))` |
| `VISIT_RECONNECT_MARGIN_S` | 3 | OD-11 v2 / §3.2.7 第 25 条 | 重连截止相对「最后一次成功发出 + 30 s」的余量 |
| `VISIT_LOCAL_PAGE_GRACE_S` | 20 | 4.3 生命周期 | transport WS 断开起算 |
| `VISIT_SHUTDOWN_BUDGET_S` | 3 | E / §3.11 | `on_shutdown` 最前 `stop_all` |
| `VISIT_ACCEPT_TIMEOUT_S` | 60 | 4.2 ready / 4.6 accept | host 接待确认 |
| `VISIT_MAX_DURATION_S` | 1800 | v1 保留 | 硬顶 |
| `VISIT_TIME_UP_WRAP_UP_S` | 60 | OD-08 v2 / d4 §4.2 | `max_duration - 60 s` 起收尾（`reason:'time_up'`） |
| `VISIT_ENDING_SOON_S` | 120 | v1 保留 | 徽标提示 |
| `VISIT_IDLE_TIMEOUT_S` | 300 | v1 保留 | 双方都无任何行 300 s（hidden 期间不计） |
| `VISIT_SWEEP_INTERVAL_S` | 2 | OD-11 v2 | `visit_sweep_loop` |
| `VISIT_INBOX_HANDOFF_MAX_S` | 20 | §3.2.6 第 22 条 / §5 总则 2b | finalize 后 `VisitInbox` 交还的硬顶：仪式句与 debrief 简述两个 speech_id 的 `visit_speech_progress{ended}` 到齐即交还；兜底 20 s **从两段都入 TTS 队列之后**起算（生成期间不计），到点时任一段仍在播放则继续等 |
| `VISIT_INBOX_HANDOFF_ABS_MAX_S` | 120 | 3.2.6 finalize | `VisitInbox` 绝对期限的封顶；期限按两段语音估时 + 10 s 计算，到点一律重投、从不丢弃 |
| **对话（OD-08 v2 / F）** | | | |
| `VISIT_MAX_CAT_TURNS_WITHOUT_HUMAN` | 6 | §3.6.3 | 6 句无人插话 → 收尾 |
| `VISIT_OWN_LINES_PER_VISIT` | 40 | §3.6.3 | 本侧满 40 句 → 收尾 |
| `VISIT_OWN_LINES_PER_MINUTE` | 6 | §3.6.3 | 只顺延不收尾 |
| `VISIT_REPLY_GAP_S` | (1.0, 2.5) | §3.6.3 | 均匀随机 |
| `VISIT_WRAP_UP_STEP_S` | **15** | F.1 / 4.2 wrap_up | begin → 对方告别行第一片 |
| `VISIT_WRAP_UP_MAX_S` | **45** | F.1 | 硬顶 |
| `VISIT_WRAP_UP_PROPOSE_TIMEOUT_S` | 5 | 4.2 wrap_up | propose 无 begin → guest 直接告别 |
| `VISIT_SPEAKING_ABORT_AFTER_S` | 10 | §3.6.3 | 收尾期在飞行 10 s 未完 → abort；只对收尾 `begin` 时在飞的旧行；告别行不受此限 |
| `VISIT_GOODBYE_LLM_TIMEOUT_S` | 8 | d4 §4.6 | 超时用固定句 |
| `VISIT_GOODBYE_MAX_CHARS` | 40 | F.1 | 告别提示词 ≤40 字、≤2 分句 |
| `VISIT_MAX_LINES` | 80 | v1 → 违约守卫 | 对端猫娘超 40+8 句仍说 → 异常计数 |
| `VISIT_RESPONSE_MAX_TOKENS` | 160 | v1 保留 | 隔离会话 `max_response_length` |
| `VISIT_HISTORY_MAX_MESSAGES` | 40 | v1 保留 | 隔离会话历史裁剪 |
| `VISIT_CONTEXT_MAX_TOKENS` | 2000 | v1 保留 | bootstrap 记忆块 |
| `VISIT_LLM_TIMEOUT_S` / `VISIT_CEREMONY_TIMEOUT_S` | 20 / 8 | v1 保留 | |
| `VISIT_PEER_LABEL_MAX_TOKENS` | 16 | v1 保留 | |
| `VISIT_SHARE_HUMAN_LABEL` / `VISIT_RECALL_TOOL_ENABLED` | False / False | v1 保留 | |
| **口型与 TTS（OD-15 v3）** | | | |
| `VISIT_VOICE_DEFAULT` | True | OD-15 v3 | `visitVoiceEnabled` 默认；不是同意开关，只进 `ALLOWED_CONVERSATION_SETTINGS`（`utils/conversation_settings_constants.py:17`）+ 8 locale |
| `VISIT_TTS_START_TIMEOUT_S` | 4 | 4.5 visit_speech_progress | 首段推入后无首个 progress → 本行文本估时兜底 |
| `VISIT_SPEECH_PROGRESS_STALL_S` | 3 | 4.5 visit_speech_progress / §3.6.4 | 开播后 3 s 无新 progress 且未 `ended` → 剩余分片按估时续放；`__audio_done__` + `estimate_speech_ms(剩余)` 为硬上限，到点强制放完发 `text{final}` |
| `VISIT_CJK_MS_PER_CHAR` / `VISIT_LATIN_MS_PER_WORD` / `VISIT_PUNCT_END_MS` / `VISIT_PUNCT_COMMA_MS` | 180 / 250 / 250 / 120 | d4 §3.4 | `estimate_speech_ms`（owner 指定 180/250；标点为设计值） |
| `VISIT_CLAUSE_MIN_MS` / `VISIT_CLAUSE_MAX_MS` | 400 / 12000 | d4 §3.4 | 估时钳位 |
| **记忆 / spool / debrief（OD-16 v3 / OD-17 v2 / G）** | | | |
| `VISIT_SPOOL_DIR` | `config_dir/visit_spool/` | OD-17 v2 | `<visit_id>.jsonl` + `.state.json` + `.outbox.jsonl`；不进 Steam 云存档（只同步 `MANAGED_MEMORY_FILENAMES`） |
| `VISIT_SPOOL_FSYNC_S` | 30 | OD-17 v2 | + finalize 时一次 |
| `VISIT_SPOOL_RETENTION_DAYS` | 7 | OD-17 v2 | `state.json` 与未答 spool 留 7 天 |
| `VISIT_SPOOL_DIR_CAP_BYTES` | 20 MB | OD-17 v2 | 与 event_logger 同规 |
| `VISIT_DIGEST_INTERVAL_S` | 0（关） | OD-17 v2 | 周期 digest 留作开关 |
| `VISIT_DEBRIEF_DEFAULT` | **`'ask_later'`** | G.2 / 4.5 visit_debrief | 超时与崩溃都不默认写私聊记忆 |
| `VISIT_DEBRIEF_TIMEOUT_S` | 600 | 4.5 visit_debrief | 到期 → `expired`（芯片保留） |
| `VISIT_DEBRIEF_MAX_TOKENS` / `VISIT_DIARY_MAX_TOKENS` | 200 / 300 | OD-16 v3 | |
| `VISIT_DIARY_FACTS_MAX` | 3 | OD-16 v3 / 4.6 visit_facts | 「记成日记」同一次 LLM 最多抽 3 条串门事实（每条 ≤60 字）进 fact 层；`importance=4`、`absorbed=True`、`origin='neko_visit'`，不进 reflection、不进铸卡 |
| `VISIT_TRANSCRIPT_MEMORY_TTL_S` | 600 | 4.6 transcript | memory 关时内存驻留 |
| `VISIT_PEER_NGRAM_N` | 8 | OD-23 | `assert_no_peer_ngram` |

提示词键（`config/prompts/prompts_visit.py`，8 语含 zh-TW，不是 settings 常量）：`VISIT_SCENE_BLOCK_GUEST / _HOST`、`VISIT_SYSTEM_NOTICE_ARRIVED / _PEER_ARRIVED`、`VISIT_SYSTEM_NOTICE_WRAP_UP_GUEST / _HOST`、`VISIT_WRAP_UP_REASON_HINT{quiet, budget, recall, time_up}`、`VISIT_GOODBYE_FALLBACK_GUEST / _HOST`、`VISIT_MARK_INTERRUPTED`、`VISIT_FIXED_LINE`、`VISIT_DEBRIEF_INSTRUCTION / VISIT_DIARY_INSTRUCTION / VISIT_DEBRIEF_FALLBACK`、`VISIT_SPEAKER_HEADER_CAT / _HUMAN`、`FAMILY_NEUTRAL_TERM`（8 语「家里人」语义，禁用物化称呼（见用户级偏好））。

**自 v1 删除的常量**（不再有宿主）：`VISIT_RELAY_ENDPOINTS`、`VISIT_RELAY_URL`、`VISIT_RELAY_PSK`、`VISIT_RELAY_GRACE_S=90`、`VISIT_LOCAL_SOCKET_GRACE_S=10`、`VISIT_PEER_HUMAN_RESETS_MAX=5`、`VISIT_MIN_CAT_REPLY_GAP_S`、`VISIT_READ_DELAY_PER_CHAR_S`、`VISIT_READ_DELAY_MAX_S`、`VISIT_FRAMES_IN_FLIGHT`、`VISIT_FRAME_MAGIC`、`VISIT_FRAME_HEADER`、`VISIT_PEER_ID_SALT`、`VISIT_PEER_RATE_MAX_PER_10S`、`VISIT_MEMORY_SHUTDOWN_FLUSH_S`、`VISIT_RETURN_LINE_MAX_CHARS`、`VISIT_SYSTEM_NOTICE_GO_HOME`（自述并入 debrief）、`VISIT_RETURN_REPORT_SUMMARY`（不再走 `submit_proactive_callback`）；`VISIT_STREAM_DELTAS` 默认由 False 改 True；`VISIT_TIERS` 由 lite/standard/hd 三档改为 sd600 单档发布 + 两个付费表项。

## 5. 实施计划（16 个 PR，按合并顺序）

PR-01 是纯重构、不依赖任何拍板；PR-02 起每个 PR 标注它依赖的拍板项（编号见 §2.2）。骨架取 synth §7.2，按裁决文件改动：v1 的 PR-05「自建中继服务器」整条删除，其位置改为 OD-31 v3 自建的 `memory/scoped_client.py`（直连 memory_server 五个 `/internal/memory/*` 端点）；PR-06 去掉 buffer / backpressure / frames，换成 identity / outbox / room（Lamport + wrap_up 状态机）/ liveness / spool；PR-07 从中继客户端改为 Servers 凭证客户端 + iframe 传输 WS；PR-09b 不再改 `websocket_router.py` 的二进制分支；PR-10 / PR-11 从「NKVF 取帧 + 双 img 承载」改为「同源 iframe 传输页 + 父页 parent-bridge」；PR-14 新增 debrief；PR-16 从中继部署改为 `deploy/livekit/` + Servers 契约文档 + 实测记录。另列「N.E.K.O. Servers（闭源，独立排期）」与「lanlan_frd（可选 follow-up）」。

### 0. 总则

- **合并顺序 = 下文编号**。每个 PR 可独立评审与合并；后序 PR 只依赖前序已合并的符号。PR-09a 合并后运行时零影响（新文件未 include），PR-09b 才把它接进 `web_app.py`。
- **不依赖任何拍板项的 PR 排最前**：PR-01（external route 注册表，纯重构、game 路径逐字节等价）与 PR-02 的 API 半段（`TakeoverMixin`，同 owner 配对时等价）。其余全部依赖至少一个 OD。
- **回归报告**：凡触碰 `app/ main_logic/ main_routers/ memory/` 的 `*.py`（`scripts/check_pr_report.py:58 WATCHED_PREFIXES`），PR 描述必须有非空「回归报告」，计入文件 >20（`:60 FILE_COUNT_LIMIT`）需「不拆分理由」；测试目录、locale、静态资源不计数。每段四段式：现状 / 改成什么 / 回归风险 / 收益。
- **本地门命令**（仓库根、`uv run`）：`ruff check .`；`python scripts/check_async_blocking.py`；`check_no_loguru.py`；`check_no_tkinter.py`；`check_no_temperature.py`；`check_startup_import_lazy.py`；`check_prompt_hygiene.py`；`check_llm_budget.py`；`check_api_trailing_slash.py`；`check_frontend_api_trailing_slash.py`；`check_module_layering.py`；`check_core_contracts.py`；diff 型（**commit 后**、`--base origin/main`）：`check_i18n_sync.py`、`check_docstring_no_cjk.py`、`check_prompt_zh_tw.py`、`check_no_nonascii_asset_names.py`。单测：`uv run pytest tests/unit/<file>.py -q`（在主仓库 checkout 跑，cwd=worktree）。Node 侧静态/行为测试统一经 `tests/node_harness.py:499 run_node_script`。
- **静态门（本设计新增，synth §7.3）**：① `static/visit/parent-bridge.js` 与 `static/visit/*.js`（非 transport 目录）不含 `requestAnimationFrame(` 与 `new WebSocket(`；② `static/visit/transport/*.js` 里 `new WebSocket(` 只出现在 `backend-ws.js` 且 URL 由 `location.host` 拼出；③ `templates/visit_transport.html` 不引用任何非 `/static/` 资源、不静态引用 vendor SDK；④ `main_routers/websocket_router.py` 二进制分支（`:59` magic、`:89-114` 解码、`:789-800` 分派）与 `static/app/app-websocket.js:3058-3066` Blob 分支 **diff 为空**（v2 不再有 NKVF）；⑤ 任何 `static/visit/**` 不含 `visibilitychange`（可见性只由「1 s 无 postrender」推导，§3.11）。这五条各由一个 `test_*_static.py` 钉住。
- **实测闸（§3.12 T1~T13）**：T1~T5 在 PR-10 合并前完成，任一失败退设计 1（附录 B），后端 PR-01~09 与 PR-12~16 不受影响；T6~T8、T10~T12 的结果表随 PR-10 / PR-11 附在 PR 描述；T9 决定 Servers 侧 `cross_region` 开关是否从 403 翻成「允许 + 警告」（OD-12 v2），不阻塞客户端 PR；d4 §3.5 的两项 Pet 窗实测随 PR-13。
- **核对到的与设计稿不同的落点**：
  1. `scripts/check_core_contracts.py` `CORE_MANAGER_SHAPE` 规定 `manager.py` 类体只有常量 + `__init__` → `acquire_takeover/release_takeover` 必须放**新 mixin** `main_logic/core/takeover.py`，并在 `MIXIN_SUPPORT_CLASSES`（`:148`）登记支持类，`main_logic/core/__init__.py` 文档串与 `manager.py:50-61` base 列表同步。
  2. 既有 10 个测试文件的假 manager 直接写或伪造 takeover 属性（`test_game_router.py / test_core_game_route_memory_contract.py / test_galgame_router.py / test_proactive_sid_guard.py / test_proactive_sm_integration.py / test_realtime_sid_rotation_atomicity.py / test_startup_greeting_delivery.py`，以及一起看 / 你画我猜新增的 `test_watch_together_live.py / test_watch_together_speech_priority.py / test_drawing_guess_router.py`——后三个用 SimpleNamespace 假对象并直接调用 `_start_watch_speech_takeover` 或未绑定的 `TurnMixin.interrupt_ordinary_speech_for_takeover`）→ PR-02 二选一：acquire / release 写成直接操作同三个属性的薄 helper（假对象无需改），或把这些 double 迁移为提供 `acquire_takeover/release_takeover`。
  2a. **main 上 takeover 已是三个属性、三个写入点**（一起看 #3106 / #3121 / #3141 带来）：`manager.py:277-283` 初始化 `_takeover_active / _takeover_input_dispatcher / _takeover_callback_sink`（`:274` 注释仍写「只认这两个 flag」，PR-02 顺手改）；写入点 = `game_router/runtime.py:2075-2084` `game_route_start` 置位（watch-together / drawing_guess 另建 `LiveInbox` 并挂 `_takeover_callback_sink = inbox.accept`）、`runtime.py:1877-1895` `_start_watch_speech_takeover` 失败回滚（在路由锁与 supersede 锁内同步清三个属性，`:1893` 关 inbox，不派生 postgame）、`postgame.py:1277-1280` 释放后再 `_close_takeover_callback_inbox`（`route_lifecycle.py:68-90`：把 inbox 里的 cue 经 `submit_proactive_callback` 重投或 nack；「先释放、再交还 inbox」的顺序被 `test_watch_together_live.py:137-138` 钉住）。新方法 `turn.py:2078-2096` `interrupt_ordinary_speech_for_takeover()` 只在 `_takeover_active` 为真时生效，负责接管时切掉普通语音。
  2b. **takeover 期间主动搭话与插件回调不是「静音」而是「扣住」**：`proactive.py:393 / :398` `feed_tts_chunk` 与 `:2994` `_can_release_proactive` 在整个 takeover 期间拒绝；`submit_proactive_callback`（`:2158-2176`）在有可调用 sink 时把 respond 回调交给 sink。串门取与一起看相同的策略：acquire 时挂一个 `VisitInbox.accept` 作 sink，串门期间插件回调全部停在 inbox；finalize 时**先** `release_takeover`（位置不变，亲人立刻可以说话）**再**按 `_close_takeover_callback_inbox` 的同一方式重投（她回家后再说），重投不了的 nack；但**交还延后**到仪式句与 debrief 简述都已入 TTS 队列并播完之后（以前端 pacer 对这两个 speech_id 回报的 `visit_speech_progress{ended}` 为准——runtime 在 pop 路由状态前把它们登记进与路由状态无关的 `_pending_inbox_handoff`，`on_page_signal` 对它们在路由 pop 后照转；语音关时没有 TTS，按两段文本的 `estimate_speech_ms` 之和交还；或**自仪式句与简述两段都入 TTS 队列之后**起 `VISIT_INBOX_HANDOFF_MAX_S=20` 秒硬顶（两段 LLM 生成期间不计时；硬顶到点时若任一段仍在播放——仍在收到它的 `visit_speech_progress` 且未 `ended`——就继续等，不交还）），否则仪式句最长 8 s 的 LLM 期间，释放后立即重投的插件回调会抢在或盖过回家那句（§3.2.6 第 22 条）。不挂 sink 会让回调在 `proactive_manager` 里一直排到释放，行为不可控。
  3. `websocket_router.py`：game 直连三处在 `:765 / :949-968 / :1048-1052`；goodbye 分支 `:885-900`；`voice_play_start/end` 在 `:1334-1348`；display socket 断开的路由清理先例 `finalize_icebreaker_route` 在 `:1446-1448`（`is_current` 判据 `:1399`）——**v2 不在这里挂串门宽限**（唯一宽限源是 transport WS 断开，OD-11 v2）。
  4. `app/main_server/web_app.py`：router import 段 `:356-386`（`game_router` 在 `:386`），`include_router` 段 `:721-764`（`game_router` `:746`、`pages_router` 兜底 `:764` 必须最后）。
  5. `main_routers/vmc_router.py`（docstring `:11` 声明 `WS /api/vmc/ws`，实现 `:424 @router.websocket("/ws")`）是非 `/ws/{name}` 的 CSRF 校验 WS 先例，`transport_ws.py` 照抄。
  6. `_USER_OWNED_FIELDS` 有**两份**：`main_routers/proactive_router.py:58` 与 `plugin/plugins/proactive_controller/__init__.py:43`（客户端镜像，注释 `:40-42` 写明），必须同 PR 改。
  7. `main_logic/mirror_meta.py:84 is_mirror_event_memory_disabled`，默认分支 `:108 return not has_user_input`。
  8. `static/i18n-i18next.js:33 LOCALE_VERSION`；`static/app/app-react-chat-window/resize-drag-and-api.js:442` 已有 `react-chat-window:update-message` 宿主事件（debrief 芯片置灰用它，零 React 改动）。
  9. `_validate_local_mutation_request` 在 `main_routers/system_router/_shared.py:158`。
  10. `templates/index.html:456` 加载 `app-websocket.js`，串门父页脚本紧随其后；`templates/chat.html:686` 同位置，但 chat 窗只加载渲染台词的脚本，不加载 parent-bridge。
  11. `utils/event_logger.py:263-264` 的 `open(path,"ab").write(payload)` 只提供 O_APPEND 单次 write，**不 fsync**；spool / outbox 的 fsync 是本设计新增，helper 新写。
  12. `utils/config_manager/storage_roots.py:160-161`：`config_dir` 与 `memory_dir` 是兄弟目录；spool / outbox / 名册 / 黑名单统一落 `config_dir`（与 `visit_peers.json` 同根）。

---

### PR-01 external route 注册表（纯重构；不依赖拍板）

**目标**：把 game route 的三处 router 劫持点、proactive 门、角色切换 finalize 泛化为 `utils/external_route_registry.py`；game 导入期注册，行为逐字节等价。这是 OD-03 / OD-24 的承载机制，但本 PR 自身不改任何语义。v1 原样。

**文件与签名**
- 【新】`utils/external_route_registry.py`
  ```python
  @dataclass(frozen=True)
  class ExternalRouteKind:
      kind: str
      is_active: Callable[[str], bool]
      route_stream_message: Callable[[str, dict], Awaitable[bool]]
      on_start_session: Callable[[str, dict], Awaitable[bool]] | None
      finalize_for_character: Callable[[str], Awaitable[int]]
      route_voice_transcript: Callable[[str, dict], Awaitable[bool]] | None = None   # 独立 ASR 转写（见下）
      on_page_signal: Callable[[str, dict], Awaitable[bool]] | None = None           # 页面信号（串门的 visit_speech_progress），game 不注册
  def register_external_route_kind(spec: ExternalRouteKind) -> None
  def get_active_external_route(lanlan_name: str) -> ExternalRouteKind | None
  def is_external_route_active(lanlan_name: str) -> bool
  async def route_external_stream_message(lanlan_name: str, message: dict) -> bool
  async def route_external_start_session(lanlan_name: str, message: dict) -> bool   # 无活动路由或 on_start_session=None → False
  async def finalize_external_routes_for_character(lanlan_name: str) -> int
  async def route_external_voice_transcript(lanlan_name: str, message: dict) -> bool   # 无路由或字段为 None → False
  async def route_external_page_signal(lanlan_name: str, message: dict) -> bool       # 有活动路由 → 交给它；无活动路由时依次问各已注册 kind 的 on_page_signal（visit 只认登记在 _pending_inbox_handoff 里的 speech_id，其余返回 False；game 无该字段）→ 都不认返回 False
  def _reset_for_tests() -> None
  ```
- 【改】`main_routers/game_router/__init__.py`：末尾 `register_external_route_kind(ExternalRouteKind(kind='game', is_active=is_game_route_active, route_stream_message=route_external_stream_message, on_start_session=None, finalize_for_character=finalize_game_routes_for_character, route_voice_transcript=route_external_voice_transcript))`（handler 用原函数对象，保证 `gr_patch_all` 仍能 patch；`route_voice_transcript` 必须传 `main_routers/game_router` 里现有的 `route_external_voice_transcript`——即 `main_logic/voice_input/consumers/game.py` 今天调用的那个——漏传则注册表返回 False，游戏语音转写报 `GAME_VOICE_TRANSCRIPT_NOT_ROUTED`）。
- 【改】`main_routers/websocket_router.py`：`:51` import 改注册表；`:765 / :949 / :1048` 三处 `is_game_route_active(lanlan_name)` → `get_active_external_route(lanlan_name)` 并调其 `route_stream_message`（`:949` 分支 `on_start_session is None` 时保留原 ack-only 代码路径）。
- 【改】`main_routers/system_router/proactive_chat_flow.py:124-128` `_game_route_active_for` → `is_external_route_active`。
- 【改】`main_routers/characters_router/crud.py:1123-1124` → `finalize_external_routes_for_character(old_catgirl)`。
- 【改】`main_logic/core/streaming.py:284` 前一行：`if mode == 'audio' and await route_external_start_session(self.lanlan_name, {'input_type': 'audio'}): return`（game 未注册 on_start_session → False → 原样）。
- 【改】`main_logic/voice_input/consumers/game.py:32 / :61`：独立 ASR 的语音转写不经 websocket_router，直接 `is_game_route_active` → `route_external_voice_transcript` 送进游戏——v2 漏列的第四个劫持点（`b0b283e34` 与 main 都有）。改为查注册表：`ExternalRouteKind` 增加可选 `route_voice_transcript: Callable[[str, dict], Awaitable[bool]] | None`，game 注册原函数；visit 注册一个「吞掉并回 `VISIT_VOICE_UNAVAILABLE`」的实现，否则串门期间的语音会从这条路漏进别处。
- 【改】`main_logic/activity/tracker.py:1012`：`is_game_route_active` 抑制情境提示推送 → `is_external_route_active`（串门期间同样不推）。
- 【改】`main_logic/core/turn.py:1180` `_maybe_handle_mini_game_magic_command`（#3118 斜杠指令，在 `streaming.py:246` 先于会话就绪检查运行）：开头查 `get_active_external_route()`，非 game 的外部路由活跃时回一句提示、不推 `open_game`——否则小游戏窗口会先打开，再被 PR-02 的 `/route/start` 归属检查拒掉。
- 【改】`tests/unit/conftest.py`：autouse 夹具追加 `external_route_registry._reset_for_tests()`（对偶 `_reset_game_sessions`）。

**测试**
- 【新】`tests/unit/test_external_route_registry.py`：注册/查询/未注册 kind 返回 None；`route_external_start_session` 无路由 False；`finalize_external_routes_for_character` 汇总各 kind 计数；变异：注册表为空时 websocket_router 三处不劫持；**game 活动时独立 ASR 转写仍进游戏**：game 注册项 `route_voice_transcript is route_external_voice_transcript`，`consumers/game.py` 经 `route_external_voice_transcript`（注册表版）送达游戏且不出现 `GAME_VOICE_TRANSCRIPT_NOT_ROUTED`（变异：game 注册漏传 `route_voice_transcript` 即红）。
- 【改】`tests/unit/test_websocket_binary_audio.py:164 _install_protocol_endpoint`：monkeypatch 目标由 `websocket_router.is_game_route_active/route_external_stream_message` 改为注册表函数（保持既有断言）。
- 跑：`test_game_router.py`、`test_game_router_concurrency.py`、`test_core_game_route_memory_contract.py`、`test_websocket_home_tutorial_guard.py`、`test_icebreaker_router.py`、`test_websocket_goodbye_state_static.py`。

**门**：layering（utils 不 import main_routers —— 注册表只存 callable）、core_contracts（streaming.py 新 import 经 `_core_facade` 晚绑定）、async_blocking、pr_report、ruff。

**回归报告要点**：websocket_router 三处 / proactive_chat_flow / crud:1123 / streaming.py:284 / voice_input consumers/game.py / activity tracker / 斜杠指令入口各一段：现状 = 直接 import game；改成 = 查注册表；风险 = game 唯一注册者时逐字节等价，注册表为空时 `streaming.py` 门返回 False；收益 = 第二种接管者不再复制劫持代码。

**依赖拍板**：无。

---

### PR-02 takeover 归属令牌（OD-24）

**目标**：接管旗有归属；game/icebreaker `/route/start` 查注册表拒绝抢占。owner 已同意（2026-09-26）。

**文件与签名**
- 【新】`main_logic/core/takeover.py`（唯一类 `TakeoverMixin`，只含方法；支持类 `TakeoverToken`(dataclass: owner, issued_at) 与 `TakeoverOwned(RuntimeError)`）：
  ```python
  def acquire_takeover(self, owner: str, dispatcher: Callable[..., Awaitable[bool]] | None, *, callback_sink: Callable[[dict], bool] | None = None) -> TakeoverToken
  def set_takeover_callback_sink(self, token: TakeoverToken, sink: Callable[[dict], bool] | None) -> None   # game 先 acquire、后建 inbox 的顺序用
  def release_takeover(self, token: TakeoverToken | None) -> bool   # 原子清三个属性：_takeover_active / _takeover_input_dispatcher / _takeover_callback_sink
  def takeover_owner(self) -> str | None
  ```
  `interrupt_ordinary_speech_for_takeover`（`turn.py:2078`）保留原位，文档串写明「acquire 之后调用」；串门 acquire 后与一起看一样调用它，失败时照搬 `_start_watch_speech_takeover` 的同步回滚（锁内 `release_takeover(token)` + 关 inbox，不派生收尾任务）。
- 【改】`main_logic/core/manager.py:50-61` base 列表加 `TakeoverMixin`；`__init__` `:277-283` 旁加 `self._takeover_token: TakeoverToken | None = None`；`:274` 注释改为三个属性。
- 【改】`main_logic/core/__init__.py` 文档串 mixin 列表加 `takeover`；`scripts/check_core_contracts.py:148 MIXIN_SUPPORT_CLASSES` 加 `"takeover": {"TakeoverToken", "TakeoverOwned"}`。
- 【改】`main_routers/game_router/runtime.py:1899 game_route_start`：自有 `_character_route_owned_by_another_game` 旁加 `if (r := get_active_external_route(lanlan)) and r.kind != 'game': return {ok: False, reason: 'route_owned_by_external'}`；`:2075-2084` 改 `state['_takeover_token'] = mgr.acquire_takeover('game', _takeover_dispatcher)`（`TakeoverOwned` → 同上拒绝），watch-together / drawing_guess 建 inbox 后 `mgr.set_takeover_callback_sink(token, inbox.accept)`；token 必须在 await `_start_watch_speech_takeover` **之前**写进 `state`。
- 【改】`main_routers/game_router/runtime.py:1890-1892`（`_start_watch_speech_takeover` 失败回滚）→ `mgr.release_takeover(state.pop('_takeover_token', None))`；这里在锁内且刻意不派生 postgame，token 不匹配必须当错误处理（记 error 并强制清三个属性），不能静默 no-op，否则启动失败会把 takeover 留住。
- 【改】`main_routers/game_router/postgame.py:1277-1279` → `mgr.release_takeover(state.pop('_takeover_token', None))`，其后 `:1280` `_close_takeover_callback_inbox` 顺序不变。
- 【改】`main_routers/icebreaker_router.py:263 /route/start` 加同一归属检查。

**测试**
- 【改】`tests/unit/test_external_route_registry.py`：token 不匹配 release no-op 且旗不变（变异必红）；已持有再 acquire 抛 `TakeoverOwned`。
- 【改】10 个含 takeover 属性的测试 double（总则第 2 条清单）按所选方案处理，断言 `/route/end` 后三个属性都已清空不变；`test_watch_together_live.py:137-138`「先释放再交还 inbox」与 `test_watch_together_speech_priority.py:73-83` 回滚断言保持通过。
- 【新】回滚用例：`_start_watch_speech_takeover` 中 `interrupt_ordinary_speech_for_takeover` 抛错 → 三个属性清空、`takeover_owner() is None`（变异：回滚改成不 release 必红）。
- 【改】`test_game_router.py`：注册一个假 kind 活动 → `/route/start` 返回 `route_owned_by_external`；`test_icebreaker_router.py` 同。

**门**：core_contracts（CORE_MIXIN_SHAPE / DISJOINT / BASES / MANAGER_SHAPE）、pr_report、ruff。

**回归报告要点**：manager（新 mixin，三个 takeover 属性只由它写）/ game runtime:1899+2075+1890 回滚 / postgame:1277 / icebreaker:263 各一段；行为变化 = 串门在飞时打开小游戏被拒；同 owner 配对逐字节等价。

**依赖拍板**：OD-24（已拍板）。

---

### PR-03 L0/L1 基础常量与数据通道 schema（OD-06 v2 / OD-09 v2 / OD-11 v2 / OD-30 / OD-08 v2 常量）

**文件与签名**
- 【新】`config/visit_settings.py`（每条赋值后紧跟英文 docstring，仿 `config/focus_settings.py`；env 用 `config/network._read_bool_env/_read_str_env`）。全部常量即 §3.9 的清单，按轴分组：
  - 生命周期（OD-11 v2）：`VISIT_HEARTBEAT_S=5`、`VISIT_PEER_LOST_S=30`、`VISIT_SELF_RECONNECT_S=25`（上限）、`VISIT_RECONNECT_MARGIN_S=3`（重连截止 = min(断线 + 25 s, 最后成功发出 + 30 s − 3 s)）、`VISIT_LOCAL_PAGE_GRACE_S=20`、`VISIT_SHUTDOWN_BUDGET_S=3`、`VISIT_INVITE_WAIT_S=600`（只用于 host：对端 hello 核验前的等待上限，与邀请码 10 min 一致；guest 核验前只等 `VISIT_PEER_LOST_S`）、`VISIT_INBOX_HANDOFF_MAX_S=20`、`VISIT_MAX_DURATION_S=1800`。
  - 可靠层与限速（OD-30）：`VISIT_OUTBOX_RETRY_S=(1, 2, 4, 8, 8)`（排完后按 8 s 继续）、`VISIT_DELIVERY_TIMEOUT_S=30`、`VISIT_DATA_BUCKET_BPS=5120`（5 KB/s ≈ 40 kbps）、`VISIT_MSG_BUCKET_PER_S=20`、`VISIT_MSG_BUCKET_BURST=10`、`VISIT_DELTA_MIN_INTERVAL_MS=250`、`VISIT_DELTA_TEXT_MAX_BYTES=800`、`VISIT_TEXT_MAX_BYTES=4096`、`VISIT_PIECE_MAX_BYTES=1000`（按字节，不是 1024）、`VISIT_PIECES_MAX=8`（按编码后字节计）、`VISIT_DEDUP_LRU=512`、`VISIT_REORDER_BUFFER_MAX=64`（必达消息按 `seq` 重排缓存上限）、`VISIT_PEER_TEXT_PER_10S=20`、`VISIT_PEER_CTL_PER_S=4`、`VISIT_PEER_LOSSY_PER_S=2`、`VISIT_ANOMALY_FINALIZE_COUNT=20`（连续 20 条异常才 finalize）、`VISIT_WIRE_PROTO=1`（常量名一律以 §4.8 表为准）。
  - 对话（OD-08 v2 / OD-15 v3 / OD-21 v3）：`VISIT_MAX_CAT_TURNS_WITHOUT_HUMAN=6`、`VISIT_OWN_LINES_PER_VISIT=40`、`VISIT_OWN_LINES_PER_MINUTE=6`、`VISIT_REPLY_GAP_S=(1.0, 2.5)`、`VISIT_WRAP_UP_STEP_S=15`（从 begin 到对方告别行**第一片**到达）、`VISIT_WRAP_UP_MAX_S=45`、`VISIT_WRAP_UP_PROPOSE_TIMEOUT_S=5`、`VISIT_SPEAKING_ABORT_AFTER_S=10`、`VISIT_GOODBYE_LLM_TIMEOUT_S=8`、`VISIT_GOODBYE_MAX_CHARS=40`、`VISIT_LINE_MAX_TOKENS=400`、`VISIT_MAX_LINES=80`（只作违约守卫）、`VISIT_STREAM_DELTAS=True`（紧急开关）、`VISIT_CLAUSE_SOFT_MAX_CHARS=24`、`VISIT_TTS_START_TIMEOUT_S=4`、`VISIT_SPEECH_PROGRESS_STALL_S=3`、`VISIT_LINE_STALL_S=20`、`VISIT_CONTEXT_MAX_TOKENS`、`VISIT_RESPONSE_MAX_TOKENS`、`VISIT_HISTORY_MAX_MESSAGES=40`。
  - 身份（OD-01 v2）：`VISIT_SERVERS_PUBKEYS: dict[str, str]`（kid → base64 Ed25519 公钥，含开发键位）、`VISIT_TICKET_CLOCK_TOLERANCE_S=300`、`VISIT_CREDENTIAL_TTL_S=2400`（guest）、`VISIT_HOST_CREDENTIAL_TTL_S=3000`（host：等待 600 + 硬顶 1800 + 余量 600）、`VISIT_PUBKEYS_CACHE_S=86400`、`VISIT_LIVEKIT_HOSTS: frozenset[str]`。
  - 视频（OD-06 v2）：`VISIT_TIERS`（`sd600` `enabled=True`：裁剪 320×448 / 256×560，打包 320×896 / 256×1120，30 fps，视频 560 kbps，数据 ≤40；`hd1200` / `fhd2400` 表项 `enabled=False`）、`VISIT_CONGESTION_LADDER={'upper': ((320,448,560),(256,352,400),(192,272,300)), 'full': ((256,560,560),(208,448,400),(160,352,300))}`（按构图分两条；最低档 300 kbps，不低于标清带下限）、`VISIT_VP9_SOFTENC_MIN_FPS=27`。
  - 记忆（OD-16 v3 / OD-17 v2）：`VISIT_SPOOL_FSYNC_S=30`、`VISIT_SPOOL_RETENTION_DAYS=7`、`VISIT_SPOOL_DIR_CAP_BYTES=20*1024*1024`、`VISIT_DIGEST_INTERVAL_S=0`（周期 digest 默认关）、`VISIT_DEBRIEF_MAX_TOKENS=200`、`VISIT_DIARY_MAX_TOKENS=300`、`VISIT_DEBRIEF_DEFAULT='ask_later'`。
  - 总闸与开发环回：`NEKO_VISIT_ENABLED`（默认关；关着时 `/api/visit/*` 全部 404、设置页不显示分组）、`NEKO_VISIT_DEV_KEYFILE`（本地 Ed25519 私钥路径，仅开发）。
  - **删除**（v1 有、v2 无）：`VISIT_RELAY_ENDPOINTS / VISIT_RELAY_URL / NEKO_VISIT_RELAY_PSK / VISIT_RELAY_GRACE_S / VISIT_LOCAL_SOCKET_GRACE_S / VISIT_FRAMES_IN_FLIGHT / VISIT_PEER_HUMAN_RESETS_MAX / VISIT_MEMORY_SHUTDOWN_FLUSH_S / VISIT_MIN_CAT_REPLY_GAP_S / VISIT_READ_DELAY_*`。
- 【改】`config/__init__.py` 旁 `from .visit_settings import (...)  # noqa: F401` + `__all__`。
- 【新】`utils/visit_wire.py`（纯函数，**无 NKVF / NKVC**）：
  ```python
  # 分片信封（TRTC 每片 ≤1000 B；LiveKit 恒 i=0,n=1）
  def fragment(payload_json: str, *, visit_id: str, msg_id: int, max_bytes: int = 1000) -> list[bytes]
  def fit_text_to_wire(payload: dict, *, visit_id: str, max_pieces: int = 8) -> dict
      # 以编码后字节为准：按最终信封形式（payload JSON + 信封字符串 p 两次转义）编码数片数，
      # 超过 max_pieces 就在字符边界截短 txt、置 truncated=True, trunc_reason='wire_size' 后重编码，直到 <= max_pieces；
      # clamp_text_utf8(4096) 仍是调用方的第一道上限
  class Reassembler:  # 按 (from_vid, m) 重组，2 s 未齐丢整条并计数
      def feed(self, from_vid: str, frag: bytes, now: float) -> dict | None
  # 消息 schema（pydantic；cmd 1 ctl = hello, ready, ack, hb, state, consent, wrap_up, leave；cmd 2 text = line_delta, text, line_abort；cmd 3 lossy = typing, stats）
  def encode_msg(msg: dict) -> str
  def decode_msg(text: str) -> dict            # 未知 t → {'t': '_unknown', 'raw_t': ...}；未知字段忽略
  def cmd_of(t: str) -> int                    # 1 / 2 / 3
  def is_reliable(t: str) -> bool              # hello / ready / consent / wrap_up / leave / text
  def proto_compatible(local_major: int, peer_caps: dict) -> bool
  # 对话轴纯函数（d4 §2.1 / §3.4）
  def split_clauses(line: str) -> list[str]    # "".join(clauses) == line 不变量
  class ClauseSplitter:                        # 增量版 split_clauses（OD-21 v3）：LLM 增量流 → 分片，只辅助字幕对齐
      def __init__(self, *, redact: Callable[[str], str] = lambda s: s, holdback_chars: int = 0): ...
      # 先脱敏再切片：每切出一片前对本行累积缓冲整体调 redact（调用方注入 redact_outbound，utils 不 import main_logic）；
      # 800 B 硬切时末尾保留 holdback_chars = max(len(受保护词)) - 1 个字符不切出，等更多文本或 flush 再判
      def feed(self, delta: str) -> list[str]  # 返回本次新切出的完整分片
      def flush(self) -> list[str]             # 行尾残片
  def estimate_speech_ms(text: str) -> int     # 180×CJK + 250×拉丁词 + 250×句末 + 120×逗顿，钳 [400, 12000]
  def max_worst_case_rates() -> tuple[float, float]   # 返回 (条/s, KB/s) 纸面上界，供单测与文档同源
  ```
- 【新】`utils/visit_route_state.py`（仿 `utils/game_route_state.py`）：`_visit_route_states: dict[str, dict]`；`activate_visit_route(lanlan, *, phase='pending') -> dict`；`get_visit_route_state(lanlan)`；`is_visit_route_active(lanlan) -> bool`；`_get_visit_route_lock(lanlan) -> asyncio.Lock`；`finalize_visit_route_state(lanlan)`。
- 【改】`utils/conversation_settings_constants.py:17 ALLOWED_CONVERSATION_SETTINGS` 加 `visitEnabled`（默认关）/ `visitMemoryEnabled`（默认关）/ `visitVoiceEnabled`（默认开）；`main_routers/proactive_router.py:58 _USER_OWNED_FIELDS` **与** `plugin/plugins/proactive_controller/__init__.py:43` 镜像同 hunk 加 `visitEnabled` / `visitMemoryEnabled`（`visitVoiceEnabled` 不是同意开关，不进；d5 OD-09 v2）。

**测试**
- 【新】`tests/unit/test_visit_wire.py`：分片/重组往返（随机 1000 组中文 / emoji / 俄文 payload，**每片 ≤1000 B**、重组字节相等、乱序与缺片 2 s 丢弃计数）；**最长合法 `text`（4096 B 正文 + 全字段）分片后每片 ≤1000 B**；**全是反斜杠 / 引号的 4096 B 正文 → `fit_text_to_wire` 后 ≤8 片且 `truncated:true, trunc_reason:'wire_size'`、`txt` 在字符边界截断且是原文前缀**（变异：去掉截短循环、只靠 `clamp_text_utf8(4096)` 即红）；普通 CJK / emoji 4096 B 正文不被 `fit_text_to_wire` 截短；`line_delta` 内层 JSON ≤900 B（`txt` ≤800 B 留转义膨胀余量）；`decode_msg` 未知 t → `_unknown` 且未知字段被忽略；`proto_compatible` 主版本不同 → False；`split_clauses` 拼接不变量、句末标点切、≥24 字才逗号切、<2 字并入、800 B 硬拆不切 codepoint（含 emoji / 合字）；`ClauseSplitter` 任意切分的增量喂入与整行 `split_clauses` 结果一致、`feed` 全部输出 + `flush` 拼接 == 输入（`redact` 为恒等时）；**亲人名恰好跨 800 B 硬切边界**（前置填充使 800 B 硬切点正好落在名字中间，名字逐字分段喂入）→ 注入假 `redact` 后两片拼接不含原名、含替换词（变异：改回逐片脱敏必红）；`holdback_chars` 生效时被扣下的尾巴在 `flush` 后原样放出；`estimate_speech_ms` 纯 CJK / 纯拉丁 / 混排 / 只有标点 / 钳位；`max_worst_case_rates()` 断言 ≤30 条/s 且 ≤8 KB/s，并把算式写进测试文档串（delta ≤4/s × 900 B + text ≤1/s × 1000 B × 5 片 + ack 1/s + hb 0.2/s + wrap_up/consent 忽略 → ≈10.2 条/s、≈8.7 KB 峰但被 5 KB/s 桶摊平 → 出站 ≤5 KB/s；含 5 片 text 重传的最坏条数 ≈15.2/s）。
- 【新】`tests/unit/test_visit_route_state.py`（pending 占位即 active；锁 WeakValueDictionary 语义）。
- 【新】`tests/unit/test_visit_settings_constants.py`：三键在 `ALLOWED_CONVERSATION_SETTINGS`；`_USER_OWNED_FIELDS` 两份集合相等且含 `visitEnabled/visitMemoryEnabled` 不含 `visitVoiceEnabled`；`VISIT_OUTBOX_RETRY_S` 之和 < `VISIT_PEER_LOST_S`；`VISIT_PEER_LOST_S - VISIT_SELF_RECONNECT_S >= VISIT_HEARTBEAT_S`（30 − 25 = 5，裁决 E 要求恰好短 5 s）；`VISIT_SELF_RECONNECT_S - VISIT_LOCAL_PAGE_GRACE_S >= VISIT_HEARTBEAT_S`（对偶性检查表「各差 ≥5 s」）；`VISIT_INVITE_WAIT_S == VISIT_INVITE_CODE_TTL_S`（等待上限与邀请码有效期一致）；`VISIT_CONGESTION_LADDER` 两种构图（`upper` / `full`）每档尺寸为 16 的倍数、打包面积 <307,200、码率 ≥300、首档等于 `VISIT_TIERS.sd600` 对应构图尺寸；`VISIT_TIERS` 只有 `sd600.enabled`。

**门**：layering（utils 只 import config）、docstring_no_cjk（config/ 与 utils/ 在范围）、pr_report（`proactive_router.py` 一段）、ruff。

**回归报告要点**：`proactive_router._USER_OWNED_FIELDS` 只增两键，`proactive_controller` 插件写路径会多拒两键（预期行为）。

**依赖拍板**：OD-06 v2、OD-09 v2、OD-11 v2、OD-30；常量段引用 OD-08 v2、OD-15 v3、OD-21 v3、OD-01 v2、OD-16 v3、OD-17 v2 的数字。

---

### PR-04 提示词与记忆标题表（OD-04 / OD-08 v2 / OD-10 / OD-16 v3 / OD-23）

**文件**
- 【新】`config/prompts/prompts_visit.py`（8 语含 zh-TW，键 `zh, zh-TW, en, ja, ko, ru, es, pt`，`_loc` 走 `prompts_sys._loc` + `normalize_prompt_locale`；分隔符成对 `======以下为串门场景======/======以上为串门场景======`；称呼一律「亲人 / 家里人」，物化称呼 denylist 集中放 `VISIT_FORBIDDEN_TERMS`（8 语，与 `FAMILY_NEUTRAL_TERM` 相邻）供测试与 `check_prompt_hygiene` 共用）：
  - 场景与固定句（v1 保留）：`VISIT_SCENE_BLOCK_GUEST / VISIT_SCENE_BLOCK_HOST`（加一句「对方说话时不要抢话；被打断就停在当前这句」）、`VISIT_SYSTEM_NOTICE_ARRIVED / _PEER_ARRIVED`、`VISIT_SPEAKER_HEADER_{CAT,HUMAN}`、`VISIT_FIXED_LINE`（断线 / 切换 / 关机 / goodbye 固定句）、`VISIT_INVITE_*` UI 文案、`FAMILY_NEUTRAL_TERM`（8 语「家里人」）。
  - 收尾（OD-08 v2，d4 §8）：`VISIT_SYSTEM_NOTICE_WRAP_UP_GUEST`（该回家了，向 `{peer_cat}` 说告别；**≤40 字、最多两个分句**；不复述对方原话；不提亲人姓名 / 住址 / 日程）、`VISIT_SYSTEM_NOTICE_WRAP_UP_HOST`（`{peer_cat}` 刚说了「`{goodbye}`」，送客一句；同上限制）、`VISIT_WRAP_UP_REASON_HINT{quiet, budget, recall, time_up}`、`VISIT_GOODBYE_FALLBACK_GUEST / _HOST`、`VISIT_MARK_INTERRUPTED`（「（说到这里被打断了）」）。
  - debrief（OD-16 v3，d5）：`VISIT_DEBRIEF_INSTRUCTION`（到家了，两三句讲讲去了谁家聊了什么；不复述原话；`{peer_consent}` 条件段「不要提对方亲人」）、`VISIT_DIARY_INSTRUCTION`（一次输出第一人称日记段 ≤300 tok + ≤3 条串门事实 ≤60 字，OD-16 v3）、`VISIT_DEBRIEF_FALLBACK`。**删除** v1 的 `VISIT_SYSTEM_NOTICE_GO_HOME` 里「顺带 ≤80 字自述」要求、`VISIT_RETURN_LINE_FALLBACK`、`VISIT_RETURN_REPORT_SUMMARY`（自述改由 debrief 单独一次 LLM 调用）。
  - `build_visit_instructions(name, side, lang, *, raw_card: str, memory_block: str, peer_display: str) -> str`（`{LANLAN_NAME}`→角色名、`{MASTER_NAME}`→`FAMILY_NEUTRAL_TERM`；memory_block 已按 `VISIT_CONTEXT_MAX_TOKENS` 截断由调用方保证）。
- 【改】`config/prompts/prompts_memory.py`：`SCOPED_PERSONA_SECTION_HEADER` 与 `_NAMED` 两张表各加 `"group_chat@neko_visit"` 与 `"participant@neko_visit"` 两键（8 语；`_NAMED` 含 `{display_name}` 与 `{subject_id}`；`group_participant` 沿用通用成员标题）；`get_scoped_persona_section_header`（`:3884`）改为按 **`(subject_kind, platform)`** 选表：`platform = subject_id.split(':', 1)[0]`，`key = f"{subject_kind}@{platform}"` 命中才用专表，否则回落 `subject_kind`——不按裸前缀判，因为 `participant` 的 `neko_visit:<uid>` 与 `group_chat` 的 `neko_visit:<pair>` 前缀相同（G.1）。

**测试**：【新】`tests/unit/test_visit_prompts_i18n.py`（每张表 8 locale；分隔符成对；全文对 `VISIT_FORBIDDEN_TERMS` 的 8 语物化称呼零命中——变异：往任一 locale 塞一个 denylist 词即红；`build_visit_instructions` 输出不含传入的 master_name 字面；两条 WRAP_UP 提示 8 语都含「40」或等价字数限制标记；debrief 三键（`VISIT_DEBRIEF_INSTRUCTION / VISIT_DIARY_INSTRUCTION / VISIT_DEBRIEF_FALLBACK`）存在）；【改】`tests/unit/test_participant_memory_and_display_name.py:461-480` 断言纳入新键（两表 key 集合仍相等）；【新】`test_scoped_header_kind_platform.py`：`group_chat` + `neko_visit:<pair>` 选专表、`participant` + `neko_visit:<uid>` 选专表、`group_participant` + `neko_visit:...` 回落通用、`qq:*` 逐字节不变（变异：改回裸前缀判定即红）；`test_memory_zh_tw.py` 若有表清单则加。

**门**：check_prompt_zh_tw（新表必含 zh-TW）、check_prompt_hygiene、docstring_no_cjk、ruff。无回归报告（config/ 不在 WATCHED_PREFIXES）。

**依赖拍板**：OD-04（键值按 OD-05 v2）、OD-08 v2、OD-10、OD-16 v3、OD-23。

---

### PR-05 `memory/scoped_client.py` 自建共享记忆客户端（OD-31 v3；只新增）

**目标**：QQ 自动回复插件已于 2026-09-28 移出仓库（#2996，`3618e75fe`），main 上除 memory_server 自身外没有任何 scoped 记忆端点的客户端。本 PR 自建主仓可 import 的共享客户端，直接对 memory_server 五个 `/internal/memory/*` 端点（`scoped_context / scoped_mentions / scoped_forget / scoped_history`（单 subject 与 segments 批两形态），`app/memory_server/routes.py`）；wire 形状以 memory_server 路由的请求模型为准，以 `b0b283e34` 版 QQ `plugin/plugins/qq_auto_reply/memory_bridge.py`（现已移出仓库；`:109 fetch_scoped_bootstrap_memory`、`:137 post_scoped_mentions`、`:155 post_scoped_forget`、`:303 post_scoped_memory_history`、`:498 post_scoped_memory_history_batch`）作对照。**不等外部商量**：是否经插件 SDK 把它开放成「bot 公共记忆组件」、接口长什么样，由 owner 与 QQ 插件作者商量后另开 PR（届时本客户端可作底座，也可被替换）。

**文件与签名**
- 【新】`memory/scoped_client.py`：
  ```python
  class ScopedMemoryClient:
      def __init__(self, *, base_url: str, http=None): ...   # http 默认 get_internal_http_client()
      async def fetch_bootstrap(self, lanlan, *, subjects: list[dict], lang, include_legacy_private=False, max_tokens) -> str
      async def post_mentions(self, lanlan, *, subject: dict, mentions: list[dict]) -> bool
      async def post_forget(self, lanlan, *, subject: dict) -> bool
      async def post_history(self, lanlan, *, subject: dict, messages: list[dict]) -> bool
      async def post_history_batch(self, lanlan, *, segments: list[dict]) -> bool
      async def list_scoped_subjects(self, lanlan, *, platform: str) -> list[dict]     # 新，对接 PR-08 只读端点
  ```
  请求体形状与端点路径以 memory_server 路由的请求模型为准，退避（502 → 5/15/45 s ≤3 次）沿用 `b0b283e34` 版 QQ 实现；只 import `utils/`（L1）与标准库。

**测试**：【新】`tests/unit/test_scoped_client_wire.py`：httpx `MockTransport` 捕获请求；wire 请求体快照 fixture（`tests/unit/fixtures/scoped_client_wire/*.json`，按 memory_server 请求模型写定，并用 `b0b283e34` 版 QQ `memory_bridge` 同输入录制一次作对照）与 `ScopedMemoryClient` 产出的 URL + body 字节相等（五方法各 ≥2 向量）；fixture body 能被 memory_server 对应 pydantic 请求模型解析；502 退避次数；`include_legacy_private` 默认 False。

**门**：layering（memory L2 只 import utils L1；`scripts/check_module_layering.py:25-31`）、pr_report（memory/ 一段）、async_blocking、ruff。

**回归报告要点**：memory/ 新增一个文件、零既有调用方。若日后商量出的 bot 公共记忆组件接口形状不同，串门侧只需改 `main_logic/visit/memory_bridge.py` 一处调用面。

**依赖拍板**：OD-31 v3。

---

### PR-06 `main_logic/visit/` 纯逻辑层（OD-01 v2 / OD-05 v2 / OD-08 v2 / OD-09 v2 / OD-11 v2 / OD-17 v2 / OD-23 / OD-30）

全部可单测、不做网络 I/O、不持事件循环（spool 的文件写在 `asyncio.to_thread` 里，接口仍是纯数据）。

**文件与签名**
- `identity.py`（OD-01 v2）：`verify_identity_ticket(ticket: str, *, expect_visit_id, expect_role, expect_vid, now, pubkeys: dict[str, bytes], blocklist: Blocklist, jti_window: JtiWindow) -> TicketClaims`。核验顺序固定：验签（`kid` 查表；不命中且缓存无 → `UnknownKid` fail closed）→ `aud=='neko-visit'` / `visit_id` / `role` 互补 / `iat±300 s ≤ now < exp` → `vid == expect_vid`（vendor 盖章的发送者 id）→ `sub ∉ blocklist`。`JtiWindow`：同房同 `vid` 重放同一 `jti` 允许，异 `vid` 拒。用 `cryptography.hazmat.primitives.asymmetric.ed25519.Ed25519PublicKey.verify`（`pyproject.toml:62` 已依赖）。
- `outbox.py`（OD-30）：`class VisitOutbox(visit_id, side, *, clock, spool_dir)`：`send(msg) -> int`（必达消息分配单调 `seq` 并落 `<config_dir>/visit_spool/<visit_id>.outbox.jsonl`；可丢消息不落盘）、`on_ack(seq)`（累计 ack）、`due(now) -> list[bytes]`（重传 1→2→4→8→8 s，**只在桶有余量时发**；桶满排队不丢）、`pause(now)` / `resume(now)`（自身 SDK 重连与页面重载宽限期间暂停 `VISIT_DELIVERY_TIMEOUT_S` 计时，恢复后重发未 ack 项并从暂停处继续计时）、`buckets`：总量令牌桶 5 KB/s + 条数桶 20 条/s（桶 10）；`line_delta` 同行最小间隔 250 ms 合并（末片不合并），`i` **在发送时**按实际发出的片连续分配（合并后的片占一个 `i`，后续顺延，不留洞），本行 `text{final}.i_done` 同步按实际发出片数计；`class InboxSequencer(lru=512, reorder_max=VISIT_REORDER_BUFFER_MAX)`：必达消息严格按 `seq` 顺序交给上层，缺口之后先到的先缓存（超过 64 条 → `peer_protocol_violation`），缺口补齐后按序放出；按 `ln` / `seq` 幂等，重复只回 ack；可丢消息不经它；`replay_after_reload()`（Pet 页刷新重入房后重发未 ack 项）。
- `room.py`（OD-08 v2，接口按 d4 §6 原样：`LineRef / IncomingLineStart / IncomingLineDone / ReplyPlan / WrapUpDecision / RoomEffects / WrapUpState / VisitRoom`）。差异只有数字：`wrap_up_step_s=15`、`wrap_up_max_s=45`，且 step 计时以「对方告别行**第一片** `line_delta{wu:true}` 到达」为止，告别行本身按正常播放走完。`observe_lp` 对未知 `t` 只计数不判违约；`violation_streak` 连续 20 条异常才返回 `finalize_reason='peer_protocol_violation'`；`lp` 回退 >1000 或同发送方 `lp` 不单调计一次异常——单调检查**只作用于新开的行 / 新控制事件**（`observe_lp(lp, *, ln=None, is_retransmit=False)`：`ln` 已见过或是 outbox 重传时跳过单调检查、保留原 `lp`）。`RoomEffects` 执行顺序由 `VisitRuntime` 固定：violation → finalize_reason → abort_speaking → cancel_pending_reply → wrap_up 出网 → ui_state → say_goodbye → reply。
- `liveness.py`（OD-11 v2，一句一个意思的纯计时器）：`class VisitLiveness(side, now, *, invite_wait_s=VISIT_INVITE_WAIT_S, peer_lost_s=VISIT_PEER_LOST_S)`（构造即进入「等对端」态；host 在 `invite_ready` 时构造，等待上限 = `invite_wait_s`；guest 在自己入房时构造，host 早已在房，等待上限 = `peer_lost_s`）：`on_peer_verified(now)`（对端 `hello` 核验通过 → 退出等待态、**此刻起** 按 `peer_last_seen` 计时）；`on_peer_message(now)` 刷新 `peer_last_seen`（等待态下只记录、不改变等待上限）；`on_self_disconnected(now)` / `on_self_connected(now)`；`on_page_lost(now)` / `on_page_back(now)`；`on_peer_explicit_leave()`；`tick(now) -> LivenessVerdict|None`，判据：host 等待态超 `invite_wait_s`（600 s）→ `invite_expired`；guest 等待态超 `peer_lost_s`（30 s）→ `peer_lost`；核验通过后 `peer_last_seen` 超 30 s → `peer_lost`；自己断线超截止 → `relay_lost`，截止 = `min(断线时刻 + 25 s, 最后一次成功发出心跳 / 必达消息的时刻 + 30 s − VISIT_RECONNECT_MARGIN_S(3))`（`on_message_sent(now)` 记录最后一次成功发出心跳 / 必达消息的时刻）；页面（transport WS）断超 20 s → `local_page_lost`；显式 `leave` / TRTC `REMOTE_USER_EXIT reason 0` / LiveKit 主动 disconnect → 立即 `peer_left`；vendor 超时类事件（TRTC reason 1、`ParticipantDisconnected` 无 bye）**不单独处理**。`heartbeat_due(now)` 每 5 s。
- `spool.py`（OD-17 v2）：`class VisitSpool(config_dir, visit_id)`：`open(header: dict)`（首行 `{v:1, visit_id, role, own_char, pair_id, peer_uid, peer_char_id, peer_char_tag, started_at, lang}`）；`append(line: dict)`（每句一行 `{lp, side, ts, from:'own_cat'|'peer_cat'|'peer_human'|'own_human', text(≤4096 B 已清洗), local_memory_at_receipt, peer_consent_at_receipt}`，单行 <4 KB，单次 `write`，单写线程队列保序）；`fsync_due(now)` 每 30 s 与 finalize 一次；`state`（`atomic_write_json`：`{digested_through_lp, digest_runs, finalized, debrief_choice:null|'ask_later'|'committing:diary'|'diary'|'forget', debrief_pending:{diary, facts}|null, debrief_writes:{facts:bool, cache:bool}, peer_revoked_scope, peer_uid, pair_id}`——这是 canonical schema，PR-08 补录与 PR-14 两步提交都只读写这些字段）；`delete_transcript()`（`forget` / 中途关 `visitMemoryEnabled` → 删 `.jsonl`）；`delete_peer_fields()`（对端 `consent{scope:'all'}` → `state.json` 的 `peer_uid/pair_id` 一并抹掉，对偶「删名册项」）；`sweep(now)`：>7 天或目录 >20 MB 删（20 MB 上限清理跳过 `.upload.json`，它只受「自结束起 7 天」约束）。目录与 outbox 同为 `config_dir/visit_spool/`；写一句：Steam 云存档只同步 `MANAGED_MEMORY_FILENAMES`（`utils/cloudsave_runtime/snapshots.py:76/:196`），spool 不会被同步。
- `subjects.py`（OD-05 v2）：`derive_pair_id(a, b) = sha256(min|max)[:24]`；`derive_peer_char_id(peer_uid, char_tag) = 'c_' + sha256(peer_uid|char_tag)[:24]`；`derive_vid(role, visit_uid, visit_id) = role[0] + '_' + sha256(visit_uid|visit_id)[:24]`（26 字符，落 TRTC userId 字符集）；`resolve_visit_recall_subjects(state) -> list[dict]`：顺序即预算优先级 `[group_chat('neko_visit', pair_id), group_participant('neko_visit', pair_id, peer_char_id), participant('neko_visit', peer_uid)]`（缺参返回 `[]`）；`class PeerRoster(config_dir)`（`visit_peers.json`，主键 `visit_uid`，按本机角色分开：`{display_name, short_code, first_seen, last_seen, by_char: {<本机角色名>: {pairs:[pair_id], chars:{peer_char_id:{char_tag, display_name, last_seen}}}}}`；`upsert(peer_uid, own_char, ...)` / `remove_char(peer_uid, own_char)`——删完 `by_char` 为空才删整条 peer）。
- `consent.py`（OD-09 v2）：`class VisitConsentGuard(local_enabled, peer_consent_seen)`：`stamps_at_receipt(line) -> (local_memory_at_receipt, peer_consent_at_receipt)`；`apply_peer_consent(enabled, scope) -> ConsentAction`：`session` → 本场对端句全部标不可 digest；`all` → 另加「三个 subject 各 forget + 名册删项 + spool 删 peer 字段」；`on_local_memory_off_midway() -> ConsentAction`（这场按不记：删 `.jsonl`、结束不出芯片）。
- `limits.py`：`PeerRateLimiter`（每发送者 text ≤20/10 s、ctl ≤4/s、lossy ≤2/s；超限丢弃并计数，计入 `violation_streak`）；`Blocklist`（`visit_blocklist.json`，主键 `visit_uid`：`{blocked:[{visit_uid, display_name_at_block, blocked_at, reason?}]}`，`atomic_write_json_async / read_json_async`（`utils/file_utils.py:866/:886`）。
- `sanitize.py`（OD-23，v1 原样）：`sanitize_relay_text`（去控制字符、nonce 信封转义、`truncate_to_tokens(VISIT_LINE_MAX_TOKENS)`、`clamp_text_utf8(4096)`）；`redact_outbound(text, *, family_names)`（casefold + NFC 整词）；`neutralize_display_name`；`assert_no_peer_ngram(text, peer_lines, n=8)`；`clamp_peer_line`。
- `__init__.py` 只 re-export。**不再有** v1 的 `buffer.py`（`VisitMemoryBuffer` 由 spool 取代）、`backpressure.py`、`frames.py`。

**测试**
- 【新】`test_visit_identity.py`：五种变异必红（篡改 `sub` / 过期 / role 对调 / `vid` 不符 / 未知 kid）；`iat` 提前 299 s 通过、301 s 拒；同房同 vid 重放同 jti 通过、异 vid 拒；黑名单命中 → `PeerBlocked`；核验顺序（验签失败时不读黑名单，用 spy 断言）。
- 【新】`test_visit_outbox.py`：`leave` 前排空 outbox（2 s 内拿到 ack 再发）；`leave.consent` 快照在原 `consent` 丢失时仍执行 `scope:'all'` 撤销（变异：接收侧忽略快照必红）；`leave` 不进重排：seq=N 丢、N+1 为 `leave` → 立即 finalize，缺口之前已连续的消息先处理（变异：leave 进重排缓存 → 等满 30 s 才结束必红）；累计 ack 清掉 ≤seq 全部项；重传时序恰为 1/2/4/8/8 s 之后每 8 s（虚拟时钟），必达项 30 s 未确认 → `delivery_failed`（心跳照常到达时也触发，变异：改回「重传 5 次后停」必红）；**重连期间暂停计时**：一条 `text` 发出后连接态已计 15 s、随后断开 19 s 再恢复（`pause` / `resume`，墙钟共 34 s > 30 s）→ 不 `delivery_failed`、恢复后立即重发，此后连接态再过 15 s 未 ack 才 `delivery_failed`（变异：去掉暂停即红）；累计 ack 只推进到连续落地的最大 seq（`seq=2` 丢、`3` 先到 → 回 `ack{1}`，2 仍在 outbox，变异：改成回最大收到 seq 必红）；**按序生效**：`seq=N`（`text`）丢、`N+1`（`consent{memory:false}`）先到 → `consent` 被缓存不生效，N 补到后先处理 N（spool 行按**旧** consent 盖章）再处理 N+1（变异：缺口后先到的照常处理即红）；重排缓存超 `VISIT_REORDER_BUFFER_MAX=64` 条 → `peer_protocol_violation`；可丢消息（`line_delta`）不经重排直接交付；桶满时 `due()` 不吐重传、余量恢复后按序吐（排队不丢）；条数桶 20/s 与字节桶 5 KB/s 各自封顶；同行 delta 250 ms 内合并、末片不合并，发出的 `i` 恰为 0,1,2,… 连续无洞且 `text{final}.i_done` = 实际发出片数（变异：合并后沿用「`i` 取前者、后者作废」即红）；`InboxSequencer` 重复 `ln` / `seq` 只回 ack；JSONL 落盘只含必达消息；`replay_after_reload` 只重发未 ack；变异：删掉「只在桶有余量时重传」即红。
- 【新】`test_visit_room.py`：d4 §7 第 1~17 条原样（数字换 15 / 45），并加：**重传不被 `lp` 单调检查拒掉**——对端 `ln='g:5'` 的 `text{final, lp=10}` 丢失、下一行 `lp=11` 先到（其 `line_delta` 不经 `seq` 重排、已 `observe_lp(11)`；其 `text` 在重排缓存里等 `g:5`）、`g:5` 的 final 以 `lp=10` 重传 → 被接受、入史 / 入 spool 并回 ack，不计异常（变异：对重传也做单调检查即红，该行永远收不到 ack → `delivery_failed`）；**新开的行** `lp` 倒退仍计一次异常；F-13 用例「LLM 7.9 s + 三分句告别」——第一片 14.9 s 到达即停 step 计时，host 不抢送客；未知 `t` 连续 19 条不 finalize、第 20 条 finalize；`lp` 回退 >1000 计一次异常而非立即 finalize。
- 【新】`test_visit_liveness.py`：**host 建房 31 s 无人入房不判死、599 s 仍等待、600 s → `invite_expired`**（变异：host 构造即启动 `peer_lost` 计时即红）；**guest 入房后 29 s 未收到对端 hello 仍等、31 s → `peer_lost`**（变异：guest 也用 600 s 等待即红）；`on_peer_verified` 之后 29 s 复联存活 / 31 s 判死；对端**任何**消息刷新 `peer_last_seen`（含 lossy）；自身 24 s 恢复继续、26 s → `relay_lost`（断线前刚发过消息时）；**最后一次成功发出在断线前 10 s → 截止 = 断线后 17 s**（30 − 3 − 10）：16 s 恢复继续、18 s → `relay_lost`（变异：只按断线 + 25 s 计即红）；页面 19 s 恢复、21 s → `local_page_lost`；显式 leave 立即；TRTC reason 1 不改变判死时刻（变异：把 reason 1 当立即结束即红）；hb 每 5 s 恰一次。
- 【新】`test_visit_spool.py`：写一半模拟 kill -9（截断最后一行）后重放行数 = 完整行数；`state.json` 字段集合恰为 canonical schema（含 `debrief_pending / debrief_writes`，`debrief_choice` 只允许 `null / 'ask_later' / 'committing:diary' / 'diary' / 'forget'`，写入枚举外值抛错；变异：删掉 `debrief_writes` 字段即红）；两枚章落在每行；`forget` 即删 `.jsonl` 且 `state.json` 留 7 天但 `peer_uid/pair_id` 被抹；`consent all` 同上；`sweep` 删 >7 天与超 20 MB，且超 20 MB 时 `.upload.json` 不被删（目录塞满到 25 MB、其中有一份 3 天前的 `.upload.json` → sweep 后它仍在，变异：sweep 不跳过即红）；`fsync_due` 30 s 节拍；目录在 `config_dir` 而非 `memory_dir`。
- 【新】`test_visit_subjects.py`：`pair_id` 对称；`peer_char_id` 定长 26；`derive_vid` 26 字符且只含 `[a-zA-Z0-9_-]`；三 subject 顺序与形态（第三个是 `participant` 而不是 `group_participant`，变异必红）；缺 `char_tag` → `[]`；名册 upsert / 删项；**按本机角色分开**：同一 `peer_uid` 与角色 A、B 各串过一次 → `by_char` 有 A、B 两项，`remove_char(peer_uid, 'A')` 后整条 peer 仍在、B 项不变，再删 B 才删整条（变异：删项不看角色即红）。
- 【新】`test_visit_consent.py`、`test_visit_limits.py`（超限丢弃计数、黑名单读写 async 对偶）、`test_visit_sanitize.py`（v1 原样：亲人名整词替换、n-gram 命中、显示名冒名归一、token 截断而非字符）。

**门**：layering（main_logic L2 可 import memory L2 同层、utils L1；不 import main_routers）、llm_budget（`truncate_to_tokens` 出现在含动态 prompt 的函数）、async_blocking（文件写走 `to_thread` / async 对偶）、pr_report、ruff。

**回归报告要点**：全新目录，一段说明「无既有调用方」。

**依赖拍板**：OD-01 v2、OD-05 v2、OD-08 v2、OD-09 v2、OD-11 v2、OD-17 v2、OD-23、OD-30。

---

### PR-07 Servers 凭证客户端（invite_code、guest 40 / host 50 min）+ iframe 传输 WS（OD-01 v2 / OD-07 v2 / OD-12 v2 / OD-29）

**文件与签名**
- 【新】`main_routers/visit_router/credentials.py`：
  ```python
  async def fetch_visit_credentials(*, role, visit_id, char_tag, tier='sd600', display_name=None, invite_code=None) -> VisitCredentials
  ```
  流程：`resolve_saved_oauth_status()`（`community_oauth.py:447`）→ `asyncio.to_thread(_desktop_session_snapshot)`（`card_drop_router.py:585-600`）→ 无 `local_user_id` 抛 `VisitLoginRequired` → `region_hint`：读 `ConfigManager._region_cache`，None 时 `await aensure_region_resolved(timeout=1.5)`（`core_config.py:529`）仍 None → `'unknown'`；**绝不**调 `_check_non_mainland()` → `get_external_http_client().post({social_base}/api/visit/credentials, headers={Authorization: Bearer, X-Client-Id}, json={role, visit_id, char_tag, tier, region_hint, display_name?, invite_code?})`（`social_base` 取 `card_drop_router.py:41` / `utils/social_base.py:12` 的既有常量）。返回 `{transport, expires_at(guest =iat+2400，host =iat+3000), invite_code?(host 才有，10 min), vendor{trtc{sdk_app_id, user_id, user_sig, str_room_id} | livekit{url, token}}, identity_ticket, peer_vid?, cross_region}`；guest 端本地先校 `invite_code` 非空再出网。错误映射：401 → `VisitLoginRequired`（HTTP 409 `VISIT_LOGIN_REQUIRED`）；403 `blocked` → `VISIT_BANNED`（本机 HTTP 403，§4.6）；403 `tier_not_entitled`；403 `cross_region_unsupported`（fail-closed，D.2）；403 `room_full`；429 → `VISIT_QUOTA_EXCEEDED`；网络 / 5xx → 503 `servers_unreachable`。LiveKit `url` 主机名必须命中 `VISIT_LIVEKIT_HOSTS` 否则拒。
  `async fetch_pubkeys() -> dict[str, bytes]`：`GET {social_base}/api/visit/pubkeys`，缓存 24 h，合并 `VISIT_SERVERS_PUBKEYS`；拉不到且 `kid` 不命中 → fail closed（由 PR-06 `identity.py` 判）。
  `async fetch_invite_preview(invite_code: str) -> InvitePreview`：本地先校 `^[A-Z2-7]{10}$` 再出网；同一套 OAuth 快照 + `get_external_http_client().get({social_base}/api/visit/invites/{invite_code}/preview, headers={Authorization: Bearer, X-Client-Id})`（§4.7）；返回 `{visit_id, host_display_name（经 OD-23 清洗）, host_short_code, cross_region, expires_at}`；错误映射：404 `invite_invalid` / 410 `invite_expired` 原样、403 `visit_banned` → `VISIT_BANNED`、429 `rate_limited`（Servers 每账号 30 次/分钟）原样带 `retry_after_s`、401 → `VisitLoginRequired`、网络 / 5xx → `servers_unreachable`；只读、不缓存、不落盘，**不消耗邀请码**（Servers 侧保证）。
- 【新】`main_routers/visit_router/transport_ws.py`：`@router.websocket("/transport/ws")`（子路由写相对路径，由 PR-09a `visit_router` 的 `prefix='/api/visit'` 补前缀，对外 URL `/api/visit/transport/ws`；query `visit_id, side`；本机 Origin / CSRF 校验照 `vmc_router.py:424`）。上行：`caps` 分两段（§4.3）——`caps{stage:'preflight', preflight_ok, reason?:'insecure_context'|'foreign_websocket'|'no_webrtc'}`（能力门 ①②，领凭证前）与 `caps{stage:'sdk', transport_ok, video_ok, reason?:'sdk_unsupported'|'sdk_load_failed'|'no_encoder', codecs[]}`（能力门 ③，收到 `credentials` 后）、`state{state:'joining'|'joined'|'reconnecting'|'connected'|'left'|'kicked'|'error', peer_present, remote_video, error_code?}`、`recv{from_vid, cmd, payload}`（iframe 已重组）、`stats{rx_fps, rtt_ms, loss_pct, rx_w, rx_h}`（全表见 §4.3）、`tx_backpressure{}`；下行：`credentials{visit_id, side, transport, vendor, own_vid, peer_vid?, tier, crop, codec_pref}`（`peer_vid` guest 侧必填、host 侧 null；无 `publish_video`）、`media{publish, subscribe, crop?, ladder?, peer_crop?, peer_vid?}`（独立下行消息，驱动发布 / 订阅 / 拥塞阶梯 / 给 host 侧补 `peer_vid`）、`send{cmd, payload}`（iframe 负责分片 / 信封）、`stop{reason}`。JSON 文本帧 ≤16 KB，超限关闭。**凭证只在这条 socket 下发**（不进父页、不进 display socket、不进日志）。socket 断 → `liveness.on_page_lost()`（20 s 宽限起点）+ `outbox.pause()`（交付超时计时暂停，新页面重入房后 `resume`）。`caps{stage:'preflight'}` 结果写入 `visit_route_state`（供 PR-09a 的能力门缓存）；`caps{stage:'sdk'}` 转 runtime 回调（`transport_ok:false` → PR-09a `finalize('unsupported')`）。

**测试**
- 【新】`tests/unit/test_visit_credentials.py`（httpx `MockTransport`）：无 OAuth 会话 → `VisitLoginRequired`；guest 无 `invite_code` 本地即拒不出网；403 四种码与 429 各自映射；5xx → `servers_unreachable`；`region_hint` 从 `_region_cache` 读且 `_check_non_mainland` 未被调用（spy 断言）；LiveKit url 主机名不在白名单 → 拒；pubkeys 缓存 24 h、拉取失败仍返回内置表；`expires_at - iat` 按 role：guest == 2400、host == 3000（假 Servers 按 role 签，客户端断言收到的值与 role 对应且不本地改写；并断言 `VISIT_HOST_CREDENTIAL_TTL_S == VISIT_INVITE_WAIT_S + VISIT_MAX_DURATION_S + 600`，变异：host 也用 2400 即红）；`fetch_invite_preview`：格式不符本地即拒不出网、200 五字段、404 / 410 / 403 / 429 / 401 / 5xx 各自映射、请求是 GET 且不带 `role` / `visit_id` 请求体（只读）。
- 【新】`tests/unit/test_visit_transport_ws.py`（TestClient websocket）：恶意 Origin 403；挂进 `APIRouter(prefix='/api/visit')` 后路由表恰有 `/api/visit/transport/ws`、不存在 `/api/visit/api/visit/` 前缀（变异：装饰器写回全路径即红）；`caps{stage:'preflight', preflight_ok:false}` 写入 route state 且不下发 credentials；credentials 只在预检段通过后下发一次；`caps{stage:'sdk', transport_ok:false}` 触发假 runtime 的 `unsupported` 回调；`recv` 转 runtime 回调（假 runtime）；>16 KB 帧关闭；断开触发 `on_page_lost`（假 liveness spy）；vendor 凭证字面量不出现在任何 logger 记录（caplog 断言）。

**门**：startup_import_lazy、async_blocking（`_desktop_session_snapshot` 走 `to_thread`）、api_trailing_slash（`/api/visit/transport/ws` 无末尾斜杠）、pr_report、ruff。

**回归报告要点**：全新文件；`main_routers/visit_router/` 尚未 include，运行时零影响。

**依赖拍板**：OD-01 v2、OD-07 v2、OD-12 v2、OD-29。

---

### PR-08 记忆桥 + spool 提交 + 启动补录（只弹芯片不自动写）+ `mirror_meta` 显式键 + memory_server 只读端点（OD-04 / OD-09 v2 / OD-16 v3 / OD-17 v2 / OD-18 / OD-31 v3）

**文件**
- 【新】`main_logic/visit/memory_bridge.py`（用 PR-05 `ScopedMemoryClient`）：`fetch_visit_context(name, subjects, lang) -> str`（`include_legacy_private=False`，`truncate_to_tokens(VISIT_CONTEXT_MAX_TOKENS)`）；`post_visit_digest(name, pair_id, lines)`（spool 全场可 digest 句 → `/scoped_history` 单 subject `group_chat`）；`post_visit_segments(name, pair_id, lines)`（对端猫娘 / 对端亲人两位，`speaker_id` 分别 `neko_visit:<peer_char_id>` / `neko_visit:<peer_uid>`，`speaker_tier="none"`，`display_name` 过 `_sanitized_display_name`）；`post_visit_forget(name, peer_uid, pair_ids)`（每个 pair 的 `group_chat` + `group_participant`，再 `participant('neko_visit', peer_uid)` 一次）；`list_visit_subjects(name)`；`shutdown_mode` 单次 ≤3 s。
- 【新】`main_logic/visit/memory_commit.py`：`commit_visit_region(spool, *, local_enabled, peer_consent) -> CommitResult`——串门记忆区（`group_chat` digest + 两位 segments）**只受 `visitMemoryEnabled` 与对端 consent 控制，与 debrief 选择无关**，在 finalize 时（或补录时）做一次；只吃两枚章都为真的句子；成功后推进 `state.digested_through_lp`，并在 debrief 已落地时删 `.jsonl`。
- 【新】`main_logic/visit/recovery.py`：`async visit_spool_recovery(render_chips, upload_transcript=None)`（由 PR-09b 在启动后 `create_task`，**不在启动链路上**；`upload_transcript` 回调由 PR-09b 注入 PR-09a 的 `upload_visit_transcript`，对目录里每个残留的 `<visit_id>.upload.json` 重试一次——与 `visitMemoryEnabled` 无关；自结束起 7 天仍失败则删文件并记本地诊断事件，OD-26 v3）：扫 `config_dir/visit_spool/`（启动清理**只删**残留 `.outbox.jsonl`，`.jsonl` 转录、`.state.json`、`.upload.json` 一律保留给补录与重传）；`finalized` 非空且 `digested_through_lp` 落后 → 补 `commit_visit_region`；`finalized` 为空（崩溃）→ 标 `finalized='crash'`、补 `commit_visit_region`（串门记忆区只受 `visitMemoryEnabled` 与对端 consent 控制，与崩溃无关）、**不写任何私聊记忆**，只重新弹 debrief 芯片（PR-14 的同一组按钮）+ status「上次串门意外中断」；`debrief_choice=='ask_later'` 且芯片未答 → 再弹；**`debrief_choice=='committing:diary'`（两步提交做到一半崩了）→ 用 `debrief_pending` 按 `debrief_writes` 只补未完成那步（`facts=false` 先补 `visit_facts`，`cache=false` 再补 `/cache`），不重新生成，两步都成则 `debrief_choice='diary'` 并清 `debrief_pending`**（7 天内）；memory_server 不可用 → 下次启动再试；`sweep()` 7 天 / 20 MB（20 MB 上限跳过 `.upload.json`）。
- 【改】`main_logic/mirror_meta.py:84-108 is_mirror_event_memory_disabled`：开头加 `if 'memory_enabled' in event: return not bool(event['memory_enabled'])`（显式键优先）；无键时后续分支逐字节不变。串门所有 mirror event 传 `{'memory_enabled': False}`。
- 【改】`app/memory_server/routes.py`：`@app.get("/internal/memory/{lanlan_name}/scoped_subjects")`（query `platform`；只读；limited_mode 409 与既有一致）。
- 【新】`main_routers/visit_router/memory_routes.py`（装饰器写相对路径，如 `@router.get('/memory/peers')`，由 `prefix='/api/visit'` 补前缀）：`GET /api/visit/memory/peers?catgirl=`（按 `visit_uid` 聚合名册 + subjects + 黑名单；响应带 `display_name`、6 位短码 `short_id = visit_uid[:6].upper()` 与**完整 `peer_uid`**——本机 API、数据本来就在本机名册里，保留它是为了让「清除 / 拉黑」按钮能调下面两个只收 `peer_uid` 的端点；界面上不显示，§4.6）、`POST /api/visit/memory/forget{catgirl, peer_uid}`（只作用于 `catgirl` 这个本机角色：该人在 `by_char[catgirl]` 下所有 pair 三 subject + `participant`，再删 `by_char[catgirl]`，`by_char` 为空才删整条 peer；信赖池未加载 fail closed → 409 稍后重试）、`POST /api/visit/memory/forget_all{catgirl}`、`POST /api/visit/contacts/block{peer_uid, blocked}`；变更端点与读端点 `GET /memory/peers` 一律过 `_validate_local_mutation_request`（`system_router/_shared.py:158`）同款本机来源校验（Origin / Host 白名单 + CSRF token，Docker / 局域网访问不放行）。

**测试**：【新】`test_visit_memory_bridge.py`（httpx MockTransport：`participant` 主体的 speaker_id 形态；退避次数；shutdown 单次；bootstrap token 截断；forget 顺序含 `participant`）；【新】`test_visit_memory_commit.py`（只吃双章句；与 debrief choice 无关——`forget` 选择下串门区仍 digest，变异必红；`local_enabled=False` 零请求）；【新】`test_visit_spool_recovery.py`（启动清理后 `.outbox.jsonl` 被删、同场 `.jsonl` / `.state.json` / `.upload.json` 都还在（变异：改回「outbox 与其 spool 一并清理」即红）；残留 `.upload.json`（含 `visitMemoryEnabled=false` 的场次）被 `upload_transcript` 回调重试一次；目录超 20 MB 时补录前的 `sweep()` 不删 `.upload.json`（只按「自结束起 7 天」删，变异：20 MB 清理不跳过即红）；**两步提交中途崩溃恢复**：`state.json{debrief_choice:'committing:diary', debrief_writes:{facts:true, cache:false}, debrief_pending:{…}}` → 补录只发 `/cache` 一次（请求体取自 `debrief_pending`）、不再发 `visit_facts`、不调 LLM，成功后 `debrief_choice=='diary'` 且 `debrief_pending` 清空（变异：补录重新生成或两步都重发即红）；崩溃 spool（未选过）→ 不发 `/cache`、不发 `/scoped_facts`、不发 `visit_facts`，只调 `render_chips` 一次 + status；finalized 且 digest 落后 → 补 digest；memory_server 502 → 文件保留下次再试；不在启动链路：函数从未被 `startup` 同步 await——静态断言 `app/main_server/__init__.py` 只以 `create_task` 引用它）；【新】`test_mirror_meta_memory_enabled.py`（显式 `False` → disabled True；显式 `True` → False；无键 → 原行为逐字节，用既有用例集回放；变异：删显式分支即红）；【新】`test_memory_server_scoped_subjects.py`（TestClient；platform 过滤；limited_mode 409；不写盘）；【新】`test_visit_memory_routes.py`（合法 / 缺 token / 错 token / 恶意 Origin / 允许 Origin 五类（含只读的 `GET /memory/peers`：无 CSRF token → 403）；forget 三 subject + participant 顺序；forget 只清 `catgirl` 这个角色下的 subjects 与 `by_char[catgirl]`，另一角色下同一人的 subjects 零请求、名册项保留（变异：forget 不看角色即红）；`peers?catgirl=A` 不列只与角色 B 串过门的人；失败不产生副作用；`peers` 每行 `peer_uid` 为完整 24 位且能直接回填 `forget` / `block` 请求体完成操作（往返用例））。

**门**：api_trailing_slash、async_blocking、layering（`main_logic/visit` → `memory/scoped_client` 同层允许）、pr_report（`app/memory_server` 与 `main_logic/mirror_meta.py` 各一段）、ruff。

**回归报告要点**：memory_server 新只读端点不登记 `_CHARACTER_SCOPED_WRITE_OPS`（读 op，不进排空围栏）；`mirror_meta` 只增一个显式键分支，无键路径逐字节不变（用回放用例证明）；既有路由零改动。

**依赖拍板**：OD-04、OD-09 v2、OD-16 v3、OD-17 v2、OD-18、OD-31 v3。

---

### PR-09a visit_router 运行时（含流式 TTS 与字幕对齐、debrief 端点、转录上传与查看详情）（OD-03 / OD-08 v2 / OD-10 / OD-15 v3 / OD-16 v3 / OD-21 v3 / OD-22 / OD-26 v3）

本 PR 只加新文件；`web_app.py` 的 include 放 PR-09b，因此合并后运行时零影响。

**文件**
- 【新】`main_routers/visit_router/__init__.py`：`router = APIRouter(prefix='/api/visit')`；include `memory_routes`、`transport_ws`、`http`、`debrief`（各子模块 `APIRouter()` 不带前缀，装饰器**一律写相对路径**：`@router.websocket('/transport/ws')`、`@router.post('/rooms')`、`@router.get('/invites/{invite_code}/preview')`、`@router.post('/debrief/choice')`、`@router.get('/memory/peers')` 等；本 PR 与 PR-07 / PR-08 下文写的 `/api/visit/...` 全路径只表示对外 URL，写进装饰器会注册成 `/api/visit/api/visit/...`）；导入期 `register_external_route_kind(ExternalRouteKind(kind='neko_visit', is_active=is_visit_route_active, route_stream_message=runtime.route_stream_message, on_start_session=runtime.on_start_session, finalize_for_character=runtime.finalize_for_character, route_voice_transcript=runtime.route_voice_transcript, on_page_signal=runtime.on_page_signal))`（两个可选字段 PR-01 已在注册表里定义；漏传 `on_page_signal` 会让 `visit_speech_progress` 无人处理、每行 4 s 后都退回文本估时，`test_visit_websocket_integration.py` 钉住）；`NEKO_VISIT_ENABLED` 关着时所有端点 404。
- 【新】`session_pool.py`：`async create_visit_session(name, side, *, instructions, lang) -> OmniOfflineClient`（`tool_definitions=[]`、`max_response_length=VISIT_RESPONSE_MAX_TOKENS`、`master_name=FAMILY_NEUTRAL_TERM`；模型经 `config_manager.get_model_api_config(...)`）；`sort_visit_history(session)`（每轮 LLM 前在 `_llm_turn_lock` 内按每条消息登记的 `(lp, side_rank)` 稳定排序，同 `lp` host 在前；或 `append` 时按 `sort_key` 插入）；`trim_visit_history(session, max_messages=40)`（`len≤1` 早退）；`pop_trailing_ai_message(session, expected: str) -> bool`（被打断行弹出整行、再 append 已放出前缀 + `VISIT_MARK_INTERRUPTED`）；`async close_visit_session(session)`。
- 【新】`runtime.py`：`class VisitRuntime`（成员：`room: VisitRoom`、`outbox`、`liveness`、`spool`、`consent`、`limiter`、`line_by_speech_id`（一行一个 speech_id）、`usage`（本场 LLM / TTS 用量累计）、`phase`）。
  - `async activate_visit(name, side, *, visit_id, invite_code?, peer_profile) -> dict`，顺序（D.4 / F-08）：占位 `activate_visit_route(phase='pending')` → HTTP 立即 202（§4.6，F-08）→ 前端 `visit_state_change{pending}` 建 iframe → 等 `caps{stage:'preflight'}`（route state 缓存或 ≤5 s）→ `preflight_ok=false` → 释放占位 + `visit_state_change{ended, reason:'unsupported'}` + `status{VISIT_UNSUPPORTED_ON_THIS_MACHINE}`（只有设置页预跑缓存命中 false 时才在 HTTP 层同步 409；**不消耗配额、不占 takeover**，这条承诺只覆盖预检段）→ `handle_interruption` ≤3 s → `fetch_visit_credentials`（失败 → 释放占位，不占 takeover，经 `visit_state_change{ended}` + `status` 推送）→ `acquire_takeover('neko_visit', _visit_voice_dispatcher)` → 下发 `credentials` 到 transport WS → 等 `caps{stage:'sdk'}`（能力门 ③；`transport_ok=false` → `finalize('unsupported')` + `release_takeover`，**此时已计一次签发**）→ host 侧 `visit_state_change{invite_ready, invite_code, invite_expires_at}`（`invite_code` 只经此 display socket 推送，**不进** `GET /api/visit/state` 响应）→ `state{joined}` → 建隔离会话 → `_park_proactive_for_goodbye()`（`proactive.py:82`）→ `visit_state_change{started}`（§3.2.1 第 8 条 / §4.5 action 枚举，无 `probing` / `active`）。
  - `hello` 阶段：对端入房 → 经 outbox 发 `hello{ticket, caps{video, tier, proto:1, app_version}, lang}`；收对端 `hello` → `identity.verify_identity_ticket` → 失败 `leave{peer_identity_rejected}` + finalize；通过 → `liveness.on_peer_verified(now)`（此刻起按 `peer_last_seen` 30 s 判死；此前等待态：host 只受 `VISIT_INVITE_WAIT_S=600` 约束 → `finalize('invite_expired')`，guest 只等 `VISIT_PEER_LOST_S=30` → `finalize('peer_lost')`）；`proto` 主版本不同 → `leave{proto_mismatch}` + status；通过前不订阅视频、不接受 `text`、host 不弹接待确认；host 接待确认（60 s）→ `ready`（host→guest，每场 1 条）→ active；guest **收到 host `ready` 后**才让 iframe `publish(track)`，也才开第一轮 LLM、发第一条 `text`；`awaiting_accept` 期间（核验通过到 `ready`）接收侧只放行 `hello / ready / consent / leave / hb / ack`，其余台词类消息丢弃计数，`text` 只回 ack 不处理不落盘（§4.2「接待前闸门」）。
  - `async on_start_session(name, message) -> bool`（text ack-only / audio 拒 `VISIT_VOICE_UNAVAILABLE`）；`async route_stream_message(name, message) -> bool`（guest 拒 `VISIT_INPUT_REFUSED_AWAY`；`phase in {wrap_up, ending}` → `VISIT_INPUT_REFUSED_WRAPUP` 且 composer 不清空；`awaiting_accept` 期间 host 亲人打字 → `VISIT_INPUT_REFUSED_NOT_READY`、不进 outbox（测试：接待确认期间打字 → outbox 无 `text`，变异：放行即红）；host text → `mirror_user_input` + `room.on_local_human_line` + `_speak_human_line`；screen / camera 吞；audio 拒）；`async on_page_signal(name, message)`（`visit_speech_progress{speech_id, played_ms, ended}` → `on_speech_progress`）。
  - **流式 TTS 与字幕对齐**（OD-15 v3 / OD-21 v3）：`_speak_line(...)`：一行一个 speech_id；语音开时 `stream = mgr.open_mirror_speech_stream(metadata=build_mirror_meta(source='neko_visit', kind='visit_line', session_id=visit_id, event={'memory_enabled': False}), request_id=ln)`（PR-09b 新增）；隔离会话 `stream_text` 的 `on_text_delta` 每段增量 → `stream.push(delta)`（本地 TTS 输入不过出站清洗，情绪标签按主聊天同一处理剥离）+ `ClauseSplitter(redact=redact_outbound, holdback_chars=max(len(受保护词)) - 1).feed(delta)`（先对累积缓冲脱敏再切片）切出的每个分片再过 `strip_emotion_tags → sanitize_relay_text` 后排队待放；行尾 `stream.finish()` + `ClauseSplitter.flush()`。`on_speech_progress(speech_id, played_ms, ended)`：第 i 片在 `min(自开播经过时间, played_ms) ≥ Σ_{j<i} estimate_speech_ms(clause_j)` 时放出 → `outbox.send(line_delta{ln, i, lp, txt(+sp/ad/rt/wu 于 i==0)})` + 本机 `visit_line_delta{self:true}`；`ended` → 剩余已生成分片一次放出；全部放完 → `outbox.send(fit_text_to_wire(text{final:true, txt=已放出分片拼接, clamp_text_utf8(4096)}))`（PR-03；编码后超 8 片才截短为 `trunc_reason:'wire_size'`，人类行同样经过它）+ **同一步里** `spool.append(from='own_cat', …)`（盖两枚章）+ `room.on_local_line_done(ref, truncated)`（正常收口、人类打断截断、收尾掐断——所有发出 `text{final}` 的路径统一经 `_commit_local_line(ref, txt, truncated)` 做 spool 追加、房间计数与上传转录，截断行按已放出前缀同样记账） + 记进上传用转录（被截断行按已放出前缀同样记录）+ `visit_line`（不再整行 `truncate_to_tokens(400)`）；首段推入后 `VISIT_TTS_START_TIMEOUT_S=4` 无首个 progress 或 TTS 未就绪 → 本行按 `estimate_speech_ms` 定时放出 + `status{VISIT_TTS_FALLBACK}`（每场一次），本场剩余各行不再重试 TTS；**开播后进度看门狗**（§3.6.4「兜底」）：已收到首个 progress 后 `VISIT_SPEECH_PROGRESS_STALL_S=3` 无新 progress 且未 `ended` → 剩余已生成分片改按估时从最后一次 `played_ms` 续放；收到该行 TTS 的 `__audio_done__`（送达完成）后起一个 `estimate_speech_ms(剩余未放出分片)` 的硬上限定时器，到点强制放完并发 `text{final}`；`visitVoiceEnabled=False` → 不开流，定时器按估时发片；被打断 → **立即停**：`stream.abort()`（内部 `mgr.interrupt_mirror_speech()`，PR-09b 提取）+ `text{final:true, truncated:true, txt=已放出分片拼接, i_done}` + `pop_trailing_ai_message`；每行累计 `usage`（LLM input / output token、TTS 请求数 / 字数）。
  - 收侧：`line_delta` 只上屏；`text{final}` 为准覆盖气泡、入史、`spool.append`、计数；`line_abort` 只截 UI。
  - `async finalize_visit_route(state, *, reason, notify_peer=True)`（锁内翻状态 + `_exit_task` 幂等；步骤与 §3.2.6 第 22 条同序：`leave{reason}` 尽力 → 锁外 `release_takeover`（位置不变）→ 仪式句 → `visit_state_change{ended}`（父页移除 iframe）→ `spool.finalize()`（fsync + `state.json{finalized:reason}`）→ `commit_visit_region`（PR-08）→ 派生转录上传任务（下）→ debrief（下）→ **交还 `VisitInbox`**：等仪式句与 debrief 简述都已入 TTS 队列并播完（两者都是带 speech_id 的 mirror 语音，`on_page_signal` 收到这两个 speech_id 的 `visit_speech_progress{ended:true}` 都到齐为准；因为下面 `_visit_route_states.pop` 会先于 `ended` 发生，runtime 在 pop 前把两个 speech_id 登记进与路由状态无关的模块级小表 `_pending_inbox_handoff{visit_id: {speech_ids, deadline}}`，注册表 `route_external_page_signal` 对登记在表里的 speech_id **不看路由是否仍活动**照转给它；`visitVoiceEnabled=false` 时没有 TTS，交还时机 = 两段文本发出后再等 `estimate_speech_ms(仪式句) + estimate_speech_ms(简述)`），或**自仪式句与简述两段都入 TTS 队列之后**起 `VISIT_INBOX_HANDOFF_MAX_S=20` 秒硬顶（两段 LLM 生成期间不计时；硬顶到点时若任一段仍在播放——仍在收到它的 `visit_speech_progress` 且未 `ended`——就继续等，不交还）（先到者），再按 `_close_takeover_callback_inbox` 同一方式重投、重投不了的 nack——交还始终在 `release_takeover` 之后，但不紧跟它）；`finalize_for_character(name) -> int`；`async stop_all(reason)`（关机：只做 spool fsync + `state.json{finalized:'shutdown'}` + **同步写出 `<visit_id>.upload.json`**（从内存转录 + `build_visit_usage` 构造，与 `visitMemoryEnabled` 无关，不在关机时上传）+ 释放 takeover，**不尝试发 `leave`**——页面已被 Electron 销毁，`backend-runtime.js:2483-2490`）；`async visit_sweep_loop()`（2 s：`liveness.tick` / `room.on_tick` / `outbox.due` / hb / max_duration−60 s 起收尾 / manager 被替换）。
- 【新】`http.py`（建房 / 入房是异步流程，以 §4.6 为准）：`POST /api/visit/rooms{catgirl, crop?}` → **202** `{visit_id, phase:'pending'}`（只做同步可判的检查后立即返回；`invite_code / transport / expires_at` **不在响应里**，凭证、失败与 `invite_code` 一律经 `visit_state_change` 推送——成功 `{invite_ready, invite_code, invite_expires_at}`、失败 `{ended, reason}` + `status`——`invite_code` 不进 `GET /api/visit/state`，页面重载后由后端经 display socket 重推 `invite_ready`）| 409 `VISIT_LOGIN_REQUIRED` | 409 `VISIT_UNSUPPORTED_ON_THIS_MACHINE`（仅预跑缓存命中 false）| 409 `{reason}`（前置检查）| 403 `{code:'VISIT_BANNED'}`（上次 Servers 403 的 60 s 缓存；本机被封响应统一 403，§4.6）；Servers 不可达发生在 202 之后 → `status{VISIT_SERVERS_UNREACHABLE}` + `ended`；`GET /invites/{invite_code}/preview`（guest 侧出门确认框的数据源：代转 Servers `GET {social_base}/api/visit/invites/{invite_code}/preview`，经 PR-07 `fetch_invite_preview`，bearer 不出前端；只读、不消耗邀请码、不占位、不建 iframe、不占 takeover；200 `{visit_id, host_display_name, host_short_code, cross_region, expires_at}` | 400 `invite_code_format` | 404 `invite_invalid` | 410 `invite_expired` | 403 `VISIT_BANNED` | 409 `VISIT_LOGIN_REQUIRED` | 429 `rate_limited` | 503 `servers_unreachable`，§4.6）；`POST /rooms/{visit_id}/join{catgirl, invite_code, confirm:true}`（`confirm` 必需；邀请里不携带任何 URL）→ **202** `{ok:true, visit_id, phase:'pending'}`（`transport / cross_region` 经 `visit_state_change{joining}` 与 `GET /api/visit/state` 给出，Servers 侧失败经 `visit_state_change{ended, reason}` + `status` 推送）；`POST /rooms/{visit_id}/accept`；`POST /route/end{catgirl, visit_id, reason:'recall'|'route_end'}`（`recall` = 「叫她回来」→ `room.on_local_recall` 走自然收尾，已在收尾 → 409 `VISIT_RECALL_ALREADY`；`route_end` = 硬结束；§4.6，**不另设** `/api/visit/recall`）；`GET /state`（字段全表见 §4.6）；`GET /transcript`（`visitMemoryEnabled` 时读 spool，否则内存 10 min）；`GET /details/{visit_id}?catgirl=`（OD-26 v3「查看详情」：代转 Servers `GET {social_base}/api/visit/details/{visit_id}`，bearer 不出前端，不缓存不落盘，§4.6）；`POST /api/visit/report{visit_id, peer_uid, reason}`（本机端点单数，§4.6；转录取 spool，代转 Servers `POST /api/visit/reports`，bearer 不出前端）。变更端点**与读端点**（`GET /state`、`GET /transcript`、`GET /details/{visit_id}`、`GET /invites/{invite_code}/preview`）一律过 `_validate_local_mutation_request` 同款本机来源校验（Origin / Host 白名单 + CSRF token），Docker / 局域网访问不放行；`GET /state` 响应不含 `invite_code`（只经 display socket 推送）。
- 【新】`debrief.py`：`async run_debrief(state)`（finalize 之后）：读 spool 全场（只取可 digest 句 + 我方句）→ 隔离会话 `stream_text(VISIT_DEBRIEF_INSTRUCTION)` ≤200 tok、`_llm_turn_lock` 内、`wait_for` 8 s → `strip_emotion_tags → redact_outbound → assert_no_peer_ngram(n=8)`（命中 → `VISIT_DEBRIEF_FALLBACK`）→ `mirror_assistant_speech(简述, metadata=build_mirror_meta(..., kind='visit_debrief', event={'memory_enabled': False}))` → 仅 `visitMemoryEnabled=true` 且 spool 有可 digest 句时 `mgr.render_chat_blocks([{type:'text'}, {type:'buttons', buttons:[diary, forget]}], request_id=f'visit-debrief:{visit_id}', source='system', source_name=<猫娘名>)`（`turn.py:2001`）；`state.debrief_choice='ask_later'`。`POST /api/visit/debrief/choice{visit_id, choice:'diary'|'forget'}`：幂等（第二次 409 `already_chosen`）；调用 PR-14 的两条写入路径；**超时不默认写**：`VISIT_DEBRIEF_DEFAULT='ask_later'`，芯片保留可点、spool 保留 7 天，7 天后 `sweep` 删。
- 【新】`transcript_upload.py`（OD-26 v3）：`build_visit_usage(runtime) -> dict`（`{duration_s, llm_input_tokens, llm_output_tokens, tts_requests, tts_chars}`；计数同时经现有遥测 `utils/instrument.py` counter / histogram 上报，低基数维度、不带 `visit_id`）；`async upload_visit_transcript(state)`（finalize 后后台任务，不阻塞 finalize）：本侧转录（按 `(lp, side_rank)` 排序的 `lp / side / from / ts / text / truncated`）+ 用量 → `get_external_http_client().post({social_base}/api/visit/transcripts, Bearer)`（§4.7，按 `visit_id + role` 幂等，200 `duplicate` 视同成功）；与 `visitMemoryEnabled` 无关；**上传前先原子写 `config_dir/visit_spool/<visit_id>.upload.json`**（只含上传字段，`0o600`，不论记忆开关开关），成功即删；失败重试：进程内退避，并由下次启动的补录任务按残留 `.upload.json` 重试（PR-09b 启动时把 `upload_visit_transcript` 以回调注入 PR-08 的 `visit_spool_recovery`，同 `render_chips` 的注入方式，保持 main_logic 不 import main_routers）；自结束起 7 天仍失败 → 放弃、删文件、记一条本地诊断事件；日志不含正文与票据。

**测试**（`tests/unit/test_visit_router.py`，仿 `test_activity_signal_router.py` + 假 manager / 假 transport WS / 假 Servers）：text start_session 不建 `mgr.session`；audio 发 `VISIT_VOICE_UNAVAILABLE`；guest 打字 `VISIT_INPUT_REFUSED_AWAY`；wrap_up 中 host 打字 `VISIT_INPUT_REFUSED_WRAPUP` 且返回 True（d4 #26）；`POST /rooms` 恰返回 202 `{visit_id, phase:'pending'}`、响应体不含 `invite_code / transport / expires_at`，`invite_code` 只经 `visit_state_change{invite_ready}` 推送（变异：改回同步 200 带 `invite_code` 即红）；`join` 恰返回 202 `{ok, visit_id, phase:'pending'}`；预检段 `preflight_ok=false`：缓存命中 → 同步 409，无缓存 → 202 后推 `ended{unsupported}`，两种都 Servers 假端点零调用、takeover 未占（变异必红）；SDK 段 `transport_ok=false`（凭证已领）→ `finalize('unsupported')` 且 `release_takeover` 被调、Servers 签发计数 = 1（如实断言已耗一次签发）；Servers 503 → 占位释放、takeover 未占、推 `status{VISIT_SERVERS_UNREACHABLE}`；`GET /invites/{code}/preview` 代转假 Servers、404 / 410 / 403 原样映射、零副作用（不占位、不建 iframe、不占 takeover、之后同一 `invite_code` 的 `join` 仍能领到凭证）；host 建房 31 s 无对端 hello 不 finalize、600 s → `finalize('invite_expired')` 且不发 `leave`；guest 入房 31 s 无对端 hello → `finalize('peer_lost')`；hello 五种拒绝各 → `leave{peer_identity_rejected}`；`proto` 主版本不同 → `leave{proto_mismatch}`；核验通过前收到 `text` 被丢弃计数；**awaiting_accept 期间对端 `text` 不上屏不进 spool 不触发回复**：hello 核验通过、host 尚未 accept 时对端发来 `text` / `line_delta` / `wrap_up` → 无 `visit_line*` 推送、spool 行数不变、`_reply_task` 未创建，`text` 仍被 ack 且对端不会 `delivery_failed`；accept 之后同样内容正常处理（变异：闸门只挡核验前即红）；guest 在收到 `ready` 前不发 `text`（变异必红）；guest 在 `ready` 前不下发 `publish`；一行只开一条 `MirrorSpeechStream`（一个 speech_id），`on_text_delta` 每段增量都 `push`、行尾恰一次 `finish`，不出现逐分句 `mirror_assistant_speech`（变异：改回逐分句即红）；本地 TTS 推入的是未清洗增量、出站分片逐片清洗（亲人名只出现在 TTS 输入、不出现在任何 `line_delta / text`）；`visit_speech_progress` 驱动放出：`played_ms` 未达 `Σ est` 时不放、达到即放第 i 片且恰一次、`ended` 时剩余一次放完、未知 / 旧 speech_id 忽略；4 s 无首个 progress 转估时且 toast 一次、本场后续各行不再开流；**progress 中途停止**（首个 progress 之后既不再回报也不发 `ended`）→ 3 s 后剩余分片按估时从最后一次 `played_ms` 续放、`__audio_done__` + 剩余估时到点强制放完，`text{final}` 必发且 `txt` = 全部分片拼接（虚拟时钟；变异：去掉看门狗必红——该行永远不收口）；`visitVoiceEnabled=False` 不开流（#24）；打断立即 `abort`（不等分句边界）、整行 AIMessage 弹出、已放出前缀 + 标记入史、`text{truncated:true}` 的 `txt` = 已放出分片拼接（#25）；**本侧台词出现在 spool 与上传转录里**：每发一条 `text{final}`，spool 恰多一行 `from:'own_cat'`（两枚章齐）、上传转录恰多一行，被截断行记的是已放出前缀（变异：去掉 `spool.append` 必红）；**本侧满 40 句触发收尾**：本侧连发 40 行 → 第 40 行 `text{final}` 同一步里 `on_local_line_done` 让 `VisitRoom` 进 WRAP_UP（host 发 `wrap_up{begin, reason:'budget'}`、guest 发 `propose`，变异：去掉 `on_local_line_done` 调用必红；人类打断 / 收尾掐断产生的 `text{truncated:true}` 行同样出现在 spool、计数与上传转录里（变异：截断路径绕过 `_commit_local_line` 必红））；「已放出分片拼接 == `text{final}.txt`」；**delta 合并后的编号**：5 个分句在 250 ms 内放出、被合并成 3 片发出 → 线上 `line_delta.i` 恰为 0 / 1 / 2（无洞）、`text{final}.i_done == 3`，被打断时 `line_abort.i_done` / `text{truncated}.i_done` 同样按已发出片数（变异：`i` 沿用分句序号即红）；两侧同时开场各一句；**两侧同 `lp` 开场 → 双方历史顺序一致**：host 与 guest 各以 `lp=1` 开场、两侧收到对方开场句的时刻不同 → 两侧下一轮 LLM 前的隔离会话历史都是「host 句、guest 句」同一顺序（按 `(lp, side_rank)`），乱序到达的后续行也按 `lp` 落位（变异：按到达顺序 append 即红）；未知 `t` 连续 20 条才 finalize；finalize 在 `_reply_task` 内触发仍发 leave；finalize 锁持有 <50 ms；**`VisitInbox` 交还时机**：串门期间扣住一条插件回调 → finalize 后 `release_takeover` 先于任何重投、仪式句与简述两个 speech_id 的 `ended` 到齐之前零重投（只到一个也不重投）、到齐后恰重投一次；**路由已 pop 后 `ended` 仍触发交还**（`_visit_route_states` 已无该角色，`visit_speech_progress{ended}` 经 `on_page_signal` 仍命中 `_pending_inbox_handoff` 并重投，变异：`on_page_signal` 只看活动路由即红）；**语音关按估时交还**（`visitVoiceEnabled=False`：两段文本发出后恰在 `estimate_speech_ms(仪式句) + estimate_speech_ms(简述)` 时重投，不等 20 s，变异：语音关也等 20 s 即红）；**两段 LLM 各 8 s 生成 → 交还不早于两段播完**（仪式句 8 s 生成 + 播 6 s、简述 8 s 生成 + 播 9 s，`ended` 延迟到达：交还时刻 ≥ 两段都播完，且 20 s 兜底从第二段入队起算而不是从 finalize 起算，变异：兜底改回自 finalize 起算即红）；兜底到点时仍在收到某段进度且未 `ended` → 不交还；另有一个不依赖播放事件的绝对期限，到点**一律重投、从不丢弃**（`resolve_callback_delivery_ack` 只对挂了确认 future 的回调有意义，nack 不带 future 的回调等于丢失）：期限 = `max(finalize + 30 s, 两段都入 TTS 队列的时刻 + estimate_speech_ms(仪式句) + estimate_speech_ms(简述) + 10 s)`，封顶 finalize + `VISIT_INBOX_HANDOFF_ABS_MAX_S=120` 秒——按两段语音的估时留足播放时间，只有播放严重超出估时的病态情况才可能重叠（测试：进度一直上报不 ended → 到估时期限时重投、回调不丢；200 token 简述在期限内播完前不重投；变异：去掉绝对期限、或到点 nack 丢弃必红）（变异必红）；`ended` 始终不来且已无进度时，自两段都入队起 20 s 硬顶后重投（虚拟时钟；变异：改回释放后立即交还即红）；join 未 confirm 400；accept 超时 60 s `leave{declined}`；transcript 读 spool；`stop_all` 不调用 `outbox.send('leave')`（变异必红）；**关机中断 → 下次启动补传且内容完整**：在飞串门（`visitMemoryEnabled=False` 与 `True` 各一遍）直接 `stop_all('shutdown')` → `.upload.json` 已落盘、`lines` 与内存转录逐条相等、`usage` 非空；随后跑补录（假 Servers）恰上传一次且请求体与该文件相同（变异：`stop_all` 不写 `.upload.json` 即红）；六个端点五类 CSRF 用例；**读端点同样鉴权**：`GET /state`、`GET /transcript`、`GET /details/{visit_id}`、`GET /invites/{invite_code}/preview` 无 CSRF token → 403、非本机 Origin / Host（模拟 Docker / 局域网访问）→ 403（变异：读端点跳过校验即红）；**`GET /state` 响应不含 `invite_code`**（host 在 `invite_ready` 阶段取 state，递归检查响应体无该键；邀请码只出现在 display socket 的 `visit_state_change{invite_ready}`，display socket 重连时重推一次）；**路由表**：`include_router(visit_router)` 后每条 visit 路由都恰以一个 `/api/visit/` 开头（含 WS `/api/visit/transport/ws`），任何路径不含 `/api/visit/api/visit/`（变异：任一子模块装饰器写回全路径即红）。`test_visit_session_pool.py`：`len≤1` 早退；pop 内容不等不弹。`test_visit_runtime_effects.py`：`RoomEffects` 执行顺序固定（spy 序列）。`test_visit_debrief_endpoint.py`：memoryOff 不出芯片；有芯片时 `debrief_choice=='ask_later'`；choice 幂等 409；n-gram 命中退固定句（变异必红）；consent=false 时记录块不含对端句；超时（虚拟时钟 10 min）零写入、芯片仍可点；简述 mirror event 带 `memory_enabled: False`；芯片只有 `diary / forget` 两个、枚举外的 `choice` → 422。【新】`test_visit_transcript_upload.py`（httpx MockTransport 假 Servers）：finalize 后恰上传一次、请求体按 `(lp, side_rank)` 排序且只含本侧那份；`visitMemoryEnabled=False` 也上传（变异：加 memory 门即红）；`visitMemoryEnabled` 开 / 关两种情况 finalize 后都恰写一份 `.upload.json`（权限 `0o600`、字段集合恰为 §4.7 transcripts 请求体）；5xx 时 `.upload.json` 保留、下次启动（假 recovery）重试一次，成功即删（变异：memory 关时不落盘即红）；自结束起第 8 天仍失败 → 文件被删且写一条诊断事件；200 `duplicate` 视同成功不再重试；遥测 counter 标签不含 `visit_id`；日志不含正文。`GET /details/{visit_id}` 代转 Servers、403 / 404 原样映射、不写盘。

**门**：全部；pr_report 只需一段「全新目录、未 include、运行时零影响」。

**回归报告要点**：无既有文件改动；注册表新增 kind 对 game 路径零影响（`get_active_external_route` 同一角色只会有一个活动 kind）。

**依赖拍板**：OD-03、OD-08 v2、OD-10、OD-15 v3、OD-16 v3、OD-21 v3、OD-22、OD-26 v3（及已合并 OD-24）。

---

### PR-09b 接线：websocket_router / turn.py / crud / main_server / web_app（OD-03 / OD-11 v2 / OD-13 / OD-15 v3 / OD-25）

**文件**
- （注册表的 `on_page_signal` 字段与 `route_external_page_signal` 已在 PR-01 定义，串门注册项在 PR-09a 已传入 `runtime.on_page_signal`；本 PR 只接 websocket_router 一侧。）
- 【改】`main_routers/websocket_router.py`：`:885-900` goodbye 分支旁 `if active and is_visit_route_active(lanlan_name): _fire_task(finalize_visit_route(state, reason='goodbye'))`（OD-25：全局告别不走收尾流程）；`:1334` 旁 `elif action == "visit_speech_progress": await route_external_page_signal(lanlan_name, message)`；**`:59 / :89-114 / :789-800` 二进制分支不动，`:1399 / :1446-1448` 断线处不加串门宽限**（唯一宽限源是 transport WS，PR-07）。
- 【改】`main_logic/core/turn.py`：从 `mirror_assistant_speech` 的 `interrupt_audio` 前奏（`:2130-2155`：`_clear_tts_pipeline` + `release_speech_playback_gain` + `send_user_activity(current_speech_id)`）提取公共方法 `async def interrupt_mirror_speech(self) -> None`，原处改为调用它；行为不变的提取。新增公共流式 mirror 入口（OD-15 v3）`def open_mirror_speech_stream(self, *, metadata, request_id) -> MirrorSpeechStream`：`push(delta)` / `finish()` / `abort()`；内部复用主聊天推 LLM 增量进 TTS 的 `_enqueue_tts_text_chunk`（行尾 `_request_tts_done_locked`）与 mirror 元数据，`mirror_text=False`、不入私聊历史；`abort()` 即 `interrupt_mirror_speech()`；一条流一个 speech_id；必要时连带 `tts_runtime.py`。
- 【改】`main_routers/characters_router/crud.py:748-750` 语音守卫后 `if is_external_route_active(old_name): return JSONResponse({'success': False, 'error_code': 'EXTERNAL_ROUTE_ACTIVE'}, 400)`（在 `:802 release_memory_server_character` 之前；OD-13）。
- 【改】`app/main_server/__init__.py`：`:928-933` 预加载 / game cleanup 任务旁 `create_task(visit_router.runtime.visit_sweep_loop())` 与 `create_task(visit_recovery.visit_spool_recovery(..., upload_transcript=visit_router.transcript_upload.upload_visit_transcript))`（都在 startup 之后、不阻塞启动链路）；`on_shutdown`（`:1234`）最前、`close_voice_identity_runtime`（`:1239-1241`）之前：`try: await asyncio.wait_for(visit_router.runtime.stop_all('shutdown'), VISIT_SHUTDOWN_BUDGET_S) except Exception: log`。
- 【改】`app/main_server/web_app.py`：`:386` 旁 `from main_routers.visit_router import router as visit_router`；`:746` 旁 `app.include_router(visit_router)`（`pages_router` `:764` 之前）；`main_routers/__init__.py` 列表 + `__all__`。
- 【改】`main_logic/core/streaming.py:284` 门已在 PR-01 落地，本 PR 只补 visit 的 `on_start_session` 返回 True 用例。

**测试**：【改】`test_websocket_goodbye_state_static.py`：goodbye 块含 `finalize_visit_route`；【新】`tests/unit/test_visit_websocket_integration.py`：`_EventWebSocket` 序列「visit 活动 + stream_data{source}」→ `route_stream_message` 被调且不进 `mgr.stream_data`；`visit_speech_progress` → `runtime.on_speech_progress`；game 注册无 `on_page_signal` → 忽略不报错；**display socket 断开不 finalize 串门**（变异：加回 10 s 宽限即红）；【改】`test_websocket_binary_audio.py`：既有用例全绿 + 静态断言 `websocket_router.py` 源码不含 `NKVF` / `visit_frame`（二进制分支 diff 为空）；【新】`test_interrupt_mirror_speech.py`：`mirror_assistant_speech(interrupt_audio=True)` 的调用序列（`_clear_tts_pipeline → release_speech_playback_gain → send_user_activity`）提取前后一致（spy 序列快照）；【新】`test_mirror_speech_stream.py`（OD-15 v3）：推流中途 `abort` 立即清管线且不再入队；`finish` 后 `audio_done` 对账正确；与主聊天 speech_id 不串（主聊天 turn 与串门流交替时各自 speech_id 不混）；一条流只一个 speech_id、只在行尾发一次 `_request_tts_done_locked`；【改】`test_game_router.py` / crud 测试：game 在飞 rename 400（新行为）；串门在飞 rename 400；【新】`tests/unit/test_main_server_visit_shutdown.py`：`stop_all` 超时 3 s 不阻塞后续钩子；`stop_all` 内 `.upload.json` 的同步写出计入 3 s 预算（写盘慢到超时也不阻塞后续钩子）；无活动会话零调用；`stop_all` 内不出现 `leave` 发送；【新】`test_external_route_registry.py` 追加 `on_page_signal` 默认 None 与路由用例；【新】`test_visit_startup_tasks_static.py`：`visit_sweep_loop` / `visit_spool_recovery` 只以 `create_task` 出现在 `app/main_server/__init__.py`，不被 `await`。

**门**：全部；pr_report（websocket_router / turn.py / crud / app/main_server / web_app / utils 各一段）。

**回归报告要点**：websocket_router 两处 if（goodbye 只在 visit 活动时多一个 task；新 action 只在注册表有 `on_page_signal` 时生效；二进制路径逐字节不变）；`turn.py` 行为不变的方法提取（spy 序列快照证明）+ 新增公共流式 mirror 入口（核心热路径，新代码路径只被串门调用，三类用例证明）；crud rename 从「只拒语音」变「也拒外部路由在飞」（含 game，行为变化）；main_server 启动多两个后台 task（不在链路上）、关机多 ≤3 s 且 try 包裹；web_app 只增 include。

**依赖拍板**：OD-03、OD-11 v2、OD-13（已拍板）、OD-15 v3、OD-25（已拍板）。

---

### PR-10 iframe 传输页 + vendor SDK vendoring（OD-27 / OD-28 / OD-29 / OD-02 v2 / OD-06 v2 / OD-07 v2 / OD-14 v2）

**文件**
- 【新】`templates/visit_transport.html`：`html,body{background:transparent;margin:0;overflow:hidden}`；只含隐藏 `<video muted playsinline>`（`position:absolute; width:2px; height:2px; opacity:0.01`，不能 `display:none`）与透明 WebGL 画布；只引 `/static/visit/transport/*.js?v={{ static_asset_version }}`；**不**静态引用任何 vendor SDK；无 CSP 变化。
- 【改】`main_routers/pages_router.py`：`GET /visit/transport`（无末尾斜杠，注入 `static_asset_version`，在 `:453 /chat` 之后、兜底之前）。
- 【新】`static/visit/transport/loader.js`：能力门拆两段（§3.3.4）。**预检段**（连上 transport WS 即跑，与 transport 无关）：① `isSecureContext`；② `window.WebSocket.name === 'WebSocket'` 且原型原生（防未来壳给子 frame 加 preload），并确认 `RTCPeerConnection`、画布 2D / WebGL、`HTMLCanvasElement.prototype.captureStream` 在场 → `caps{stage:'preflight', preflight_ok, reason?}`；失败 → 不加载 SDK，后端不领凭证。**SDK 段**（收到 `credentials` 后）：按 `credentials.transport` 动态插一份 `<script>` → ③ `onload` 后 `await TRTC.isSupported()` 或 `RTCRtpSender.getCapabilities('video')` 含 VP9 / VP8 → `caps{stage:'sdk', transport_ok, video_ok, reason?, codecs[]}`；加载失败 / 不受支持 → `transport_ok:false`（后端 `finalize('unsupported')` + `release_takeover`，此时已计一次签发），不入房；③ 只视频失败 → `video_ok:false`，照常入房。
- 【新】`backend-ws.js`：整个 iframe 里唯一一处 `new WebSocket(`，URL 由 `location.protocol`（http → ws、https → wss）与 `location.host` 拼成 `/api/visit/transport/ws?visit_id=…&side=…`，不接受任何外来 URL；协议见 PR-07。
- 【新】`transport.js`（`VisitTransport` 接口：`join / leave / publish / onRemoteTrack / sendData / onData / onPeer / onState / stats`）+ 分片 / 重组（信封 `{v, r, m, i, n, p}`，每片 ≤1000 B **按字节**；按 `(from_vid, m)` 重组 2 s 未齐丢）+ 分流（cmd 1 / 2 reliable、cmd 3 lossy；LiveKit topic `visit.ctl / visit.text / visit.lossy`）+ 丢非当前 `visit_id` + 盖 `from_vid`；未知 `t` 原样上交后端（后端忽略计数）。
- 【新】`trtc-transport.js`：`TRTC.create()` → `enterRoom({sdkAppId, userId, userSig, strRoomId: visit_id, scene: SCENE_RTC, role: ROLE_ANCHOR, autoReceiveVideo: false})`；`stopPlugin('SmallStreamAutoSwitcher')`；`startLocalVideo({publish:true, option:{videoTrack, profile:{width: packW, height: packH, frameRate:30, bitrate:560}}})`（`packW×packH` 取自当前构图，见 `pack.js`：上半身 320×896、全身 256×1120；`profile` 对自定义轨是否生效 → T6）；切构图 → `updateLocalVideo({option:{profile:{width, height}}})`；`REMOTE_VIDEO_AVAILABLE{userId===peer_vid}` → `startRemoteVideo({userId, streamType: STREAM_TYPE_MAIN, view:null})` + `getVideoTrack`；`sendCustomMessage({cmdId, data})` / `CUSTOM_MESSAGE`；事件映射：`REMOTE_USER_EXIT reason 0` → `state{peer_present:false, explicit:true}`（立即 peer_left），`reason 1` → 不上报（交心跳）；`KICKED_OUT{banned|room_disband}` → `kicked`；`CONNECTION_STATE_CHANGED` → `reconnecting / connected`；`NETWORK_QUALITY.uplinkLoss` 进 stats。
- 【新】`livekit-transport.js`：`new Room({adaptiveStream:false, dynacast:false, publishDefaults:{simulcast:false, videoCodec: codec_pref('vp9'|'vp8'), scalabilityMode:'L1T1', videoEncoding:{maxBitrate:560_000, maxFramerate:30}, degradationPreference:'maintain-framerate'}})`（**`scalabilityMode` 必须显式**，否则 SDK 对 vp9 默认 `L3T3_KEY` 三层 SVC）；`connect(url, token, {autoSubscribe:false})`，收到 host `ready` 后 `setSubscribed(true)`；`publishData(bytes, {reliable, destinationIdentities:[peer_vid], topic})`；`Disconnected`（主动）→ explicit；`ParticipantDisconnected` 无 bye → 不上报；VP9 软编 `enc_fps < 27` 持续 10 s → 上报 `stats{softenc_overloaded:true}`，后端记 `codec_pref='vp8'` 供下次串门。
- 【新】`pack.js`：画布尺寸**从当前构图几何推导**，不写死——`CROP_GEOMETRY = {upper:{w:320, h:448}, full:{w:256, h:560}}`（与 Python `VISIT_TIERS.sd600` 的 `crop_upper / crop_full` 对偶），`scratch` = `w×h`（透明）、`pack` = `w×2h`（`getContext('2d',{alpha:false})`；上半身 320×896、全身 256×1120）；`window.__nekoVisitFrameSink = {onFrame(parentCanvas, rectPx, tsMs)}` 同步入口：`pack` 填黑 → 上半 `drawImage(parentCanvas, 源矩形 → 0,0,w,h)` → `scratch` 填白 + `destination-in` 画源矩形 → `pack` 下半 `drawImage(scratch → 0,h)` → `packTrack.requestFrame()`；`credentials.crop` 定初始构图，`media{crop}` 切构图时按新尺寸重建 `scratch / pack` 并调 transport 的 `updateLocalVideo`（`profile.width/height` = 新 `pack` 尺寸；LiveKit 按 §4.3 `media` 规则）；`pack.captureStream(0)` 的轨 `contentHint='motion'`；只在收到 host `ready` 后 `publish`；拥塞阶梯 `VISIT_CONGESTION_LADDER`（按当前构图取一条：上半身 320×448/560 → 256×352/400 → 192×272/300，全身 256×560/560 → 208×448/400 → 160×352/300；每级同样重建画布并 `updateLocalVideo`；只缩裁剪不动 fps；`rx_fps<24` 或 `uplinkLoss>15%` 连续 10 s 降一级，30 s 干净升一级；接收端以 `videoWidth/Height` 观测 libwebrtc 自行降分辨率）。
- 【新】`unpack.js`：`<video>.srcObject` → `requestVideoFrameCallback` 每帧一次 `texImage2D` → 解包 shader（上半 rgb、下半 r 作 a；分界与显示比例从构图推导——裁剪高 = `videoHeight/2`、宽高比 = `videoWidth/(videoHeight/2)`，对端 `state.crop` 切换或 libwebrtc 自行降分辨率时随之重算，不写死 320/448；`blendFunc(ONE, ONE_MINUS_SRC_ALPHA)`，画布 `premultipliedAlpha:true, alpha:true`）；rVFC 1 s 不触发 → `setTimeout` 30 Hz 回落；首帧 96×96 `toBlob` → `postMessage({t:'first_frame'})`；`stats` 每 5 s（`rx_fps` 由 rVFC `presentedFrames` 差分、`rx_w/rx_h`、`quality_limitation_reason`；字段全表见 §4.3）。
- 【新】`frame-sink.js`：`postMessage` 收 `crop{rectPx, srcW, srcH}`、`place{...}`（host 侧由父页摆）、`hidden{on}`（父页帧饥饿检测推导；本文档**不用** `visibilitychange`）、`visit_state{phase}`；来源校验 `event.source === parent && event.origin === location.origin`；`hidden{on}` → 1 Hz `state{hidden:true}`（cmd 1）。
- 【新】`static/libs/trtc.js`（trtc-sdk-v5 5.20.1，npm `license: ISC`，实施时以包内 LICENSE 文件复核）+ `static/libs/livekit-client.umd.js`（2.22.3，Apache-2.0）+ `static/libs/licenses/{TRTC,LIVEKIT}.LICENSE`；【改】`static/libs/THIRD_PARTY_NOTICES.md` 各加一节（版本钉死、来源 URL、本地改动 = 无）；【改】`scripts/check_nuitka_dist.py:53 _REQUIRED_ASSETS` 追加 4 行。

**测试**：【新】`tests/unit/test_visit_transport_static.py`：`transport/*.js` 中 `new WebSocket(` 只在 `backend-ws.js` 且拼 `location.host`；模板不引用非 `/static/` 资源、不含 `trtc.js` / `livekit-client` 字面量；`livekit-transport.js` 含 `scalabilityMode: 'L1T1'`、`simulcast: false`、`autoSubscribe: false`、`maintain-framerate`；`trtc-transport.js` 含 `autoReceiveVideo: false`、`view: null`、`SmallStreamAutoSwitcher`；`static/visit/**` 不含 `visibilitychange`；`pack.js` 含 `alpha: false` 与 `requestFrame()`；`trtc-transport.js` / `unpack.js` 不含字面量 `896` 与 `448`，`pack.js` 里尺寸数字只出现在 `CROP_GEOMETRY` 与阶梯表的定义处（尺寸只从构图几何推导，变异：`profile` 写回 `width:320, height:896` 即红）；`loader.js` 预检段不引用 `TRTC` / `LivekitClient`（③ 只在收到 `credentials` 后跑）；`_REQUIRED_ASSETS` 含 4 行；`THIRD_PARTY_NOTICES.md` 含两节。【新】`tests/unit/test_visit_frag_node.py`（`run_node_script`）：JS 分片 / 重组与 Python `utils/visit_wire.fragment/Reassembler` 对偶——随机 1000 组 payload 两侧分片字节相等、每片 ≤1000 B、重组一致；未知 `t` 透传；非当前 `visit_id` 丢弃计数。【新】`test_visit_crop_geometry_pack_node.py`（`run_node_script`，两种构图都覆盖）：`upper` → `scratch` 320×448、`pack` 320×896、`profile` 320×896、分界 y=448；`full` → 256×560 / 256×1120 / 256×1120 / y=560；`upper → full → upper` 切换各重建一次画布并各调一次 `updateLocalVideo`（参数 = 新尺寸）；解包侧对 320×896 与 256×1120 两种输入算出的裁剪高与宽高比正确；`CROP_GEOMETRY` 与 Python `VISIT_TIERS.sd600` 两种构图尺寸相等。【新】`test_visit_congestion_ladder_node.py`：`rx_fps<24` 连续 10 s 降一级、30 s 干净升一级、最低档 300 kbps 不再降，`upper` 与 `full` 两条阶梯各跑一遍（全身 256×560 → 208×448 → 160×352）；档位尺寸与 Python `VISIT_CONGESTION_LADDER` 两条都相等。【新】`test_pages_router_visit_transport.py`：路由存在、无末尾斜杠、注入 `static_asset_version`。

**门**：frontend_api_trailing_slash、check_no_nonascii_asset_names、pr_report（`pages_router` 一段）、ruff；**合并前完成 T1~T8、T10~T13 并把结果表附进 PR 描述**（T13 决定 `trtc-transport.js` 的 host 用 `ROLE_AUDIENCE` 还是 `ROLE_ANCHOR`）（T1~T5 任一失败 → 退设计 1，见附录 B）。

**回归报告要点**：`pages_router` 只增一条模板路由；首屏零变化（SDK 只在串门时、只在 iframe 内加载）；包体 +2~3 MB（估算）。

**依赖拍板**：OD-27、OD-28、OD-29、OD-02 v2、OD-06 v2、OD-07 v2、OD-14 v2。

---

### PR-11 父页 parent-bridge（OD-27 / OD-02 v2 / OD-06 v2 / OD-14 v2）

**文件**
- 【新】`static/visit/parent-bridge.js`（无每帧循环、无 rAF、无 WebSocket）：
  - 懒建 / 移除 iframe：`visit_state_change{pending}` → `<iframe id="visit-frame" class="transparent-overlay" src="/visit/transport?v=…&side=…&visit_id=…">`（host 侧 `position:fixed; z-index:9; border:0; background:transparent; pointer-events:none`；guest 侧 1×1 置于视口外）；`ended` → `iframe.remove()`；同一时刻最多一个；`load` 后缓存 `iframe.contentWindow.__nekoVisitFrameSink`。
  - `postrender` 钩子：`live2dManager.pixi_app.renderer.on('postrender', fn)`；`fn` 两道过滑：`renderer.lastObjectRendered === live2dManager.pixi_app.stage`（avatar-portrait 的临时舞台 `renderer.render(tempStage)` 与 `generateTexture` 也触发 postrender）且 `!renderer.renderTexture.current`；**分数累加器采样**：`renderFps` = 最近 1 s 实测 postrender 频率，`acc += 30 / renderFps; if (acc >= 1) { sink.onFrame(canvas, rectPx, now); acc -= 1; }`（替代「距上次 ≥33 ms」门——144 / 75 Hz 下那个门只有 28~29 fps）；`avatarPortrait.capture` 前后 `suspendCapture()`。
  - 保 30 fps：只在 `0 < window.targetFrameRate < 30` 时 `savedFps = window.targetFrameRate; live2dManager.setTargetFPS(30)`（`live2d-core.js:752-758` 会改写 `window.targetFrameRate`，结束时恢复）；不 toggle `ticker.stop/start`。
  - 裁剪框：`getModelScreenBounds()` + `getHeadDetectionGeometryInfo().headRect/bodyRect` 每 300 ms~1 s 刷新 + 滞回（中心偏移 <4% 且尺寸变化 <8% 不动框，动框 300 ms 线性过渡）；上半身框比例取自 `static/visit/crop-geometry.js`（把 `avatar-portrait.js:509-521 makeUpperBodyRect` 的比例搬成共享纯函数，`avatar-portrait.js` 不动）；bounds 为 null / 容器 `visibility:hidden` → 视同 hidden。
  - 可见性 = **帧饥饿检测**：串门 active 且 >1 s 无成功 `onFrame` → `postMessage({t:'hidden', on:true})` 1 Hz，首帧恢复即 `on:false`；不监听 `visibilitychange`（Pet 窗 `backgroundThrottling:false`，Electron 官方文档：此时 visibility 保持 `visible`，`win.hide()` 后 `document.hidden` **不一定**为 true；取帧是否停止以「postrender 是否还来」为唯一判据，有帧就发，没帧 B 显示最后一帧半透明）。
  - host 侧摆位：每 300 ms 按 `getModelScreenBounds()` 把 iframe 摆到本家猫娘左右空位较大一侧，高 `clamp(L.height×0.9, 200, 900)`、宽 = 高 × cropW/cropH（上半身 320/448、全身 256/560，取 `visit_state_change.peer_crop`（§4.5，随 `started` 与 `action:'peer_crop'` 更新），不写死）；`first_frame` 缩略图交聊天面作访客头像。
  - A 侧 `.visiting-away` 徽标（不用 `.minimized`，否则断水印坐标链）。
- 【新】`static/visit/crop-geometry.js`：`upperBodyRect(subjectRect, aspect, biasY=0.32)`、`fullBodyRect(bounds)`、`cssRectToPixelRect`。
- 【改】`static/live2d/live2d-core.js:1029-1044 _hasRenderActivity()` 加一行 `if (this._visitCaptureActive) return true;`（对偶 `appState.lipSyncActive`；这是 live2d-core.js 唯一改动）。
- 【改】`static/vrm/vrm-manager.js:877` `renderer.render` 后 +1 行 `window.nekoVisitParentBridge?.onExternalRender?.()`；`static/mmd/mmd-core.js:1289-1291` 两个 render 分支后、`:1294 _flushRenderWaiters()` 前 +1 行；PNGTuber `<img>` 由 parent-bridge 用 `nekoFramePacing.requestPacedFrame`（`static/frame-pacing.js:147`）30 Hz 采样，`pngtuber-core.js` 不改。
- 【改】`static/app/app-websocket.js`：`visit_state_change` 分支转 `nekoVisitParentBridge`（`__NEKO_MULTI_WINDOW__ === true && /^\/chat(?:_full)?(?:\/|$)/`（`:1092-1094`）时忽略建 iframe，只渲染台词）。
- 【改】`templates/index.html:456` 之后加载 `crop-geometry.js` 与 `parent-bridge.js`；`templates/chat.html` **不**加载；`static/css/index.css` 加 `#visit-frame` 与 `.visiting-away` 规则。

**测试**：【新】`tests/unit/test_visit_parent_bridge_static.py`：`parent-bridge.js` / `crop-geometry.js` 不含 `requestAnimationFrame(`、`new WebSocket(`、`visibilitychange`；含 `lastObjectRendered` 与 `renderTexture.current` 守卫字面量；含 `targetFrameRate` 保存 / 恢复；`live2d-core.js` 恰一处 `_visitCaptureActive`；`vrm-manager.js` / `mmd-core.js` 各恰一次 `onExternalRender`；`index.html` 加载序在 `app-websocket.js` 之后；`chat.html` 不含 `parent-bridge.js`；`avatar-portrait.js` diff 为空。【新】`tests/unit/test_visit_sampler_node.py`（`run_node_script`，把累加器抽成纯函数）：60 / 75 / 144 / 165 Hz 与定时器 17 ms 序列各跑 10 s，输出帧率 30 ± 0.5；对照「≥33 ms 门」用例在 144 Hz 下 <29.5（变异对照）；`renderFps` 突变时 1 s 内收敛。【新】`test_crop_geometry_node.py`：比例与 `makeUpperBodyRect` 数学一致（w = max(w×1.04, h×0.58×aspect)，h = max(h×0.64, w/aspect)）、滞回阈值。

**门**：frontend_api_trailing_slash、ruff（无 Python 改动无回归报告）；合并前附 T2 / T3 / T4 / T10 实测记录（T2 用 `RTCRtpSender.getStats().framesPerSecond` 验收，不靠推理）。

**依赖拍板**：OD-27、OD-02 v2、OD-06 v2、OD-14 v2。

---

### PR-12 聊天面与知情同意（OD-19 / OD-22 / OD-26 v3 / OD-08 v2 UI + 8 locale）

**文件**
- 【新】`static/app/app-react-chat-window/visit-chat.js`（index.html 与 chat.html 都加载）：`I.isVisitChatActive()`；composer 收件人开关（`source:'neko_visit:guest_cat'|'neko_visit:own_cat'`）；guest 侧只留「叫她回来」（→ `POST /api/visit/route/end {reason:'recall'}`，§4.6；已在收尾 → 409 `VISIT_RECALL_ALREADY` → toast `visit.wrapUp.recallAlready`）；出门确认框（**先**调 `GET /api/visit/invites/{invite_code}/preview`（§4.6，只读、不消耗邀请码）拿 `{visit_id, host_display_name, host_short_code, cross_region, expires_at}`，再弹框；框内显示对端 `host_display_name` + `host_short_code`、`cross_region` 一行；预览 404 `invite_invalid` / 410 `invite_expired` / 403 `VISIT_BANNED` → 不弹框、给对应 8 语文案；点确认才 `POST /api/visit/rooms/{visit_id}/join{confirm:true}`；**不显示 token / TTS 次数等技术数字**，OD-26 v3）；`visit_invite` 接待确认（60 s）→ `POST /api/visit/rooms/{id}/accept`；导出转录（`GET /api/visit/transcript`）；「查看详情」入口（OD-26 v3，藏得较深：放在结束后该场系统消息的折叠区里，不上主界面；记忆浏览器串门面板里的同一入口随 PR-15；点开 → `GET /api/visit/details/{visit_id}` → 展示本场时长、扣减的免费分钟、token / TTS 消耗、双方合并转录；403 / 404 / 503 各一条文案）；举报按钮（本机 `POST /api/visit/report`，单数；后端代转 Servers `/api/visit/reports`）；分句拼接 `Map<line_id, {clauses[], bubble}>`：`line_delta` 按 `i` 落位、缺片留 `…` 不补洞、`text{final}` 全文覆盖并标 final、`line_abort` 立即截断 + `visit.stream.truncated`、20 s 无新片且无 final → 本地截断；wrap_up：徽标「道别中」+ composer 禁用；`peer_hidden` 徽标「离开了一下」。；提交前按串门阶段先拦（`awaiting_accept` / `wrap_up` / `ending` / guest 侧不调 `sendTextPayload`、不清空输入框），并按 `request_id` 处理后端拒绝：id 相符且输入框自提交后未编辑才恢复文字，对应本地气泡标「未送达」（node 行为测试：接待确认期间回车 → 输入框文字仍在、未发 WS；后端拒绝兜底 → 文字恢复且气泡标未送达；提交后又改了草稿、旧拒绝才到 → 新草稿不被覆盖；变异：去掉前端拦截、去掉 id 比对、或不标气泡即红）；8 语新增 `visit.input.notDelivered`
- 【改】`static/app/app-websocket.js`：`visit_line / visit_line_delta / visit_line_abort / visit_typing / visit_state_change{pending|started|wrap_up|ended|peer_hidden|peer_visible|peer_crop|video_reconnecting} / visit_invite` 分支（action 枚举以 §4.5 为准）+ `status{code}` toast 映射（`VISIT_INPUT_REFUSED_AWAY / VISIT_INPUT_REFUSED_WRAPUP / VISIT_INPUT_REFUSED_NOT_READY / VISIT_RECALL_ALREADY / VISIT_TTS_FALLBACK / VISIT_UNSUPPORTED_ON_THIS_MACHINE / VISIT_LOGIN_REQUIRED / VISIT_BANNED / VISIT_QUOTA_EXCEEDED / cross_region_unsupported / servers_unreachable / proto_mismatch / peer_identity_rejected`）；**`:3058-3066` Blob 分支不动**。
- 【改】`static/app/app-chat-adapter.js`：`visit_line` 直挂 role `'tool'`（author 按 speaker / side；访客头像用 `first_frame`）；`message-bundle-actions-and-prompts.js:375-383` 收件人标签；`static/app/app-buttons.js` `sendTextPayload(text, {source})` 透传。
- 【改】`static/css/index.css` 新增 `.message-bubble-tool / .avatar-tool` 规则；`.visiting-away` 徽标样式；`.visit-wrap-up` 徽标。
- 【改】`static/locales/{zh-CN,zh-TW,en,ja,ko,ru,es,pt}.json` 同 hunk：`visit.*` 键（确认框、邀请预览失败三种文案（无效 / 过期 / 被封）、接待、查看详情（入口 / 时长 / 免费分钟 / 用量 / 转录 / 错误）、状态徽标、`visit.wrapUp.badge / toastRefused / recallAlready / composerPlaceholder`、`visit.stream.truncated / ttsFallback`、错误码文案含 `proto_mismatch`「对方版本不兼容，请双方更新」与 `insecure_context`「自定义后端地址需 https 或 localhost」、导出、举报）；`static/i18n-i18next.js:33 LOCALE_VERSION` 更新。
- 【改】`templates/index.html` / `templates/chat.html`（`:686` 旁）加载 `visit-chat.js`。

**测试**：【新】`tests/unit/test_visit_chat_static.py`：8 locale 键集合相等；`app-websocket.js` 含全部 `visit_*` 分支且 Blob 分支正则钉住原文（`pendingAudioChunkMetaQueue` 前置条件不变）；chat 窗忽略 `visit_state_change` 建 iframe 但渲染 `visit_line`；adapter 对 `visit_line` 只用 role `'tool'`；`index.css` 含两条 tool 规则；index / chat 双模板加载 `visit-chat.js`；`visit-chat.js` 无 rAF / 无 WebSocket；确认框相关 locale 值与 `visit-chat.js` 确认框渲染不含 token / TTS 次数字段（OD-26 v3，变异：塞回用量行即红）；「查看详情」只调 `/api/visit/details/` 字面量（无末尾斜杠）；出门确认框的数据只取自 `/api/visit/invites/` 预览字面量（无末尾斜杠），且预览调用在 `join` 请求之前、预览失败分支不发 `join`（静态顺序断言）。【新】`test_visit_chat_assembly_node.py`（`run_node_script`）：乱序落位、缺片留占位不补洞、`text{final}` 覆盖、abort 截断、20 s stall（d4 #27 去掉 `line_req`）。React 包零改动（`npm run typecheck && npm test` 仍跑）。

**门**：check_i18n_sync（commit 后 `--base origin/main`）、frontend_api_trailing_slash；合并前按 `chat_three_contexts` 在 index.html 宽 / 窄 + chat.html 三路径手测并记录。

**依赖拍板**：OD-19、OD-22、OD-26 v3、OD-08 v2（UI 部分）。

---

### PR-13 口型 / 流式 / `visitVoiceEnabled` 设置 + 8 locale（OD-15 v3 / OD-21 v3 / OD-09 v2 / OD-06 v2）

**文件**
- 【新】`static/visit/visit-pacer.js`：监听 `neko-speech-playback-state`（`app-audio-playback.js:531`）中本行 speech_id 的 `reason==='chunk_scheduled'`（带 `scheduledEndAudioTime / audioContextTime`）→ 换算真开播时刻（`max(prevScheduledEnd, audioContextTime)`，复刻 `:1630-1632` 钳位；若有 `chunkStartAudioTime` 直接用）与已播音频时长 → 播放期间约 4 Hz `S.socket.send({action:'visit_speech_progress', speech_id, visit_id, played_ms, ended})`，播完 / 被清掉时恰一条 `ended:true`；一行一个 speech_id，同一 speech_id 的后续块只累加时长、不重置开播时刻；finalize 后后端登记的仪式句与 debrief 简述两个 speech_id 同样回报（后端以它们的 `ended` 决定 `VisitInbox` 交还时机，PR-09a）。
- 【新】`static/visit/text-mouth-driver.js`：只在 `visitVoiceEnabled=false` 且 `S.lipSyncActive!==true` 时工作；消费本机 `visit_line_delta{self:true}`，按 `estimate_speech_ms` 同一公式（JS 副本，单测钉住与 Python 一致）排一段 8~10 Hz 开合（幅度 0.35~0.8 随机、句末 250 ms 衰减），`LanLan1.setMouth`，排帧只用 `nekoFramePacing.requestPacedFrame`；VRM / MMD / PNGTuber 语音关时不驱动（follow-up）。**不再有**「本地静音但保留 RMS」开关与 `speakerGainNode.gain=0` 方案（白烧配额）。
- 【改】`static/app/app-audio-playback.js:1761-1771` `chunk_scheduled` patch 加 `chunkStartAudioTime: scheduledStartTime, chunkDurationSec: nextBuffer.duration` 两字段（纯加法，无行为变化）。
- 【改】`static/app/app-settings.js` / `app-state.js`：设置页「串门」分组（`NEKO_VISIT_ENABLED` 关着不显示）：`visitEnabled`（默认关）、`visitMemoryEnabled`（默认关，tooltip `visit.debrief.memoryOffHint`）、`visitVoiceEnabled`（默认开；说明「她在邻居家说话，你在自家听见，像开着免提；`.visiting-away` 徽标表示她不在家」）、构图「上半身 / 全身」、档位显示（只读 `sd600`，`GET /api/visit/state.default_tier`）。
- 8 locale 同 hunk（`visit.settings.*`、`visit.stream.ttsFallback`、构图）+ `LOCALE_VERSION`；`templates/index.html` 加载两个新脚本（chat.html 不加载 pacer / mouth-driver）。

**测试**：【新】`test_visit_pacer_static.py`（无 rAF；监听事件名字面量；`text-mouth-driver.js` 含 `lipSyncActive` 互斥判据与 `requestPacedFrame`；不含 `speakerGainNode`）；【新】`test_visit_pacer_node.py`（`run_node_script`：开播时刻换算含 `prevScheduledEnd` 落后于 `audioContextTime` 的钳位；`chunk_scheduled` 领先真开播最多 5 s（lookahead）时 `played_ms` 不超前；同 speech_id 多块累加时长、不重置开播时刻；progress 频率 ≈4 Hz、`ended:true` 恰一条）；【新】`test_visit_estimate_ms_parity.py`（JS `estimate_speech_ms` 与 Python 对 50 组向量结果相等）；【新】`test_visit_settings_static.py`（三键与 `ALLOWED_CONVERSATION_SETTINGS` 一致；8 locale；无「静音」键；`NEKO_VISIT_ENABLED` 门）；【改】`test_app_audio_playback_static.py`（`chunk_scheduled` patch 字段集合 = 原集合 ∪ 两个新字段，其它调用点不变）。

**门**：check_i18n_sync、frontend_api_trailing_slash；`app-audio-playback.js` 不在 WATCHED_PREFIXES，但 PR 描述仍写一段「纯加字段」。

**实施期必测（OD-15 v3）**：(a) 同一 speech_id 流式推入时口型是否连续；(b) `chunk_scheduled` 领先真开播最多 5 s（lookahead）时 `played_ms` 换算是否正确；(c) 8 个 provider（http_sentence / ws_bistream / gptsovits 等）上流式推入与 `audio_done` 对账；(d) 官方免费 TTS 一场 ≈40 次请求（一行一次）的限流行为——触顶后该行走 4 s 兜底转估时、本场剩余各行不再重试。

**依赖拍板**：OD-15 v3、OD-21 v3、OD-09 v2、OD-06 v2。

---

### PR-14 debrief 芯片与两条写入路径（OD-16 v3 + 8 locale；含 memory_server `visit_facts` 新端点与 `card_forge_facts` 过滤）

**文件**
- 【新】`main_logic/visit/debrief_writers.py`：`async write_diary(mgr, session, spool) -> bool`（再一次 LLM，`VISIT_DIARY_INSTRUCTION` **一次调用同时产出两样**，逐项清洗 + `assert_no_peer_ngram(n=8)`：(a) 第一人称日记段 ≤`VISIT_DIARY_MAX_TOKENS=300` → `POST /cache/{lanlan}` `input_history=[{"type":"ai","content":日记段}]` 进近期记忆（`/cache` 的事实抽取 `app/memory_server/signal_extraction.py:494` 跳过无用户消息的窗口，所以它不会变成长期事实）；(b) ≤`VISIT_DIARY_FACTS_MAX=3` 条串门事实（每条 ≤60 字，n-gram 命中的单条丢弃）→ `POST /internal/memory/{lanlan}/visit_facts{visit_id, facts}` 进 fact 层；都经 `get_internal_http_client()`（`utils/http/internal_client.py:69`））；`async write_forget(spool)`（不写私聊、删 `.jsonl`；名册 `last_seen` 仍更新）。两条路径都不影响串门记忆区（PR-08 `commit_visit_region` 已在 finalize 做过）。
- 【改】`main_routers/visit_router/debrief.py`：`POST /api/visit/debrief/choice` 接两条写入路径（`choice:'diary'|'forget'`）；成功 → `render_chat_blocks` 追加 status 块（`visit.debrief.savedDiary / forgot`）并把 `state.debrief_choice` 落盘；`ask_later` 保持芯片可点。
- 【改】`main_logic/visit/recovery.py`：崩溃 / 未答的 spool 在启动后重新弹同一组芯片（复用 `debrief.render_chips`）。
- 【改】`app/memory_server/routes.py`：新增 `POST /internal/memory/{lanlan_name}/visit_facts`（§4.6）——服务端统一盖 `source='ai_disclosure'`、`importance=4`、`absorbed=True`、`origin='neko_visit'`、`visit_id`（调用方不可覆盖），走 `FactStore._apersist_new_facts` 的语义去重；登记进 `_CHARACTER_SCOPED_WRITE_OPS`（写 op，进排空围栏）；limited_mode 409 与既有写端点一致。
- 【改】`memory/facts.py`：`_apersist_new_facts` 目前按白名单逐字段构造新事实，`origin` / `absorbed` 不会从入参透传；按 `_external_import` 同一种方式新增一个受控透传：入参带 `_visit_origin{visit_id}` 时，新建事实盖 `origin='neko_visit'`、`visit_id`、`absorbed=True`（只作用于新建，不改已存在事实；精确去重命中时不升级这些字段）。回归报告一段：其他调用方不带该键时行为逐字节不变（快照测试）。
- 【改】`main_logic/card_forge_facts.py`：`build_forge_facts_payload` 抽样（`:205 _weighted_pick`）前过滤 `origin=='neko_visit'` 的事实（邻居家的内容不进社区分享卡片）；无 `origin` 字段的存量事实结果逐字节不变。
- 【改】`config/prompts/prompts_visit.py`：`VISIT_DIARY_INSTRUCTION` 改为一次输出日记段 + ≤3 条事实的结构化结果（8 语，含 zh-TW）。
- 【改】`static/app/app-react-chat-window/visit-chat.js`（PR-12 已让 index.html **与** chat.html 都加载；debrief 监听放进这个文件，**不另建** `visit-debrief.js`，与 OD-16 v3 (5) / §3.7.4 步骤 4 / §3.9 一致）：监听 `react-chat-window:action`（`message-bundle-actions-and-prompts.js:322-337` 派发），`action==='visit_debrief_choice'` → `POST /api/visit/debrief/choice{visit_id, choice}` → 200 后经 `react-chat-window:update-message`（`resize-drag-and-api.js:442`）把该消息两个按钮置 `disabled` 并追加 status 块；409 `already_chosen` 按响应里的 `{choice, completed}` 处理：`completed:true` → 两个按钮都置灰；`completed:false`（提交中，当前只可能是 `committing:diary`）→ 只灰「不记」、「记成日记」保持可点并显示「正在记…」，以便记忆服务恢复后重试；503 `{retry:true}` → 选择已被锁定为「记成日记」，同样只灰「不记」、只保留「记成日记」的重试入口（与 `completed:false` 的 409 同一种显示）。
- 8 locale 7 键：`visit.debrief.question / choiceDiary / choiceForget / savedDiary / forgot / askLaterHint`（「芯片会保留 7 天，随时可以点」）/ `memoryOffHint`；`LOCALE_VERSION`。

**测试**：【新】`tests/unit/test_visit_debrief.py`：并发——两个请求同时进 `diary` / `diary+forget` → 只有一个执行、另一个 409，`/cache` 恰一次（变异：去掉 per-visit 锁必红）；`visit_facts` 返回 503 → 响应 503 `{retry:true}`，再点「记成日记」只补未完成步骤并成功、再点「不记」仍 409 且 `completed:false`，前端只灰「不记」、「记成日记」仍可点（node 静态 / 行为测试；变异：409 一律两钮置灰必红）（变异：committing 状态一律 409 必红）；提交完成后 `state.json` 不含 `debrief_pending`（变异：不清除必红）；两步提交——`visit_facts` 成功、`/cache` 返回 503 → `debrief_choice` 仍未定、补录只补 `/cache` 且不重新生成；`/cache` 超时 → 按已写处理、不再重发（变异：超时也重试 → 日记写两次必红）；**`/cache` 返回 HTTP 200 `{status:'error'}` → 不记 `debrief_writes.cache`、`debrief_choice` 仍未定、可重试**（重试或补录再写一次且只写一次，变异：只看 HTTP 200 即当成功必红）；`visit_facts` 返回 200 但 `ok:false` → 同样视为明确失败、不记 `debrief_writes.facts`（变异必红）；`diary` → `/cache` 请求体形状恰为一条 `type:'ai'`、不发 `/scoped_facts`；`forget` → 零写入且 `.jsonl` 被删、名册 `last_seen` 更新；日记 n-gram 命中退固定句（变异必红）；choice 幂等 409；`ask_later` 超时 10 min / 6 天零写入且芯片可点、第 8 天 `sweep` 删 spool；崩溃补录只弹芯片不写；consent=false 的日记 prompt 记录块不含对端句；两条写入都不触碰 `/scoped_history`（串门区已由 finalize 提交，变异：在 writer 里再 digest 一次即红）；**diary → `/cache` 恰一条 `type:'ai'` + `visit_facts` ≤3 条且每条 `importance=4`、`absorbed=True`、`origin='neko_visit'`**（LLM 假返回 5 条 → 只写 3 条；变异：改成 `importance=5` 或 `absorbed=False` 即红）；`forget` 不发 `visit_facts`。【新】`tests/unit/test_memory_server_visit_facts.py`（TestClient）：调用方传 `importance=9 / absorbed=False` 被服务端覆盖回 4 / True；同文事实二次写入被语义去重；**reflection 合成不取 `origin=neko_visit` 事实**——写入后跑 `aget_unabsorbed_facts(min_importance=5)` 与 reflection 合成一轮，结果不含这些事实（变异：端点改盖 `importance=5, absorbed=False` 即红）。【改】`tests/unit/` 既有 card_forge 测试文件追加：**铸卡抽样不含 `origin=neko_visit`**（事实池混入 3 条串门事实、重复抽样 200 次零命中，变异：删过滤即红）；无 `origin` 字段的存量事实抽样结果与改前逐字节相同（回放既有用例）。【新】`test_visit_debrief_static.py`：`visit-chat.js` 含 `react-chat-window:action` 与 `update-message` 字面量、`static/visit/` 下不存在 `visit-debrief.js`；index / chat 双模板加载 `visit-chat.js`；8 locale 7 键；React 包零改动。

**门**：check_i18n_sync、api_trailing_slash、pr_report（`main_logic/visit` 新文件、`app/memory_server/routes.py`、`main_logic/card_forge_facts.py` 各一段）、check_prompt_zh_tw、ruff；合并前按 `chat_three_contexts` 三路径手测芯片置灰。

**回归报告要点**：`/cache` 收到只含 AI 消息的批次时 `_has_human_messages` 为假、跳过 review-clean（`routes.py:968`），`recent.json` 出现一条她的独白是预期，事实抽取也不会把它变成长期事实（`signal_extraction.py:494`）；**memory_server 一段**：新增 `visit_facts` 写端点（新路由、写 op 登记围栏，复用既有去重），私聊事实池每场最多多 3 条 `importance=4 / absorbed=True` 的串门事实，召回里会出现、reflection 永不合成；**`card_forge_facts.py` 一段**：抽样前加 `origin=='neko_visit'` 过滤——现状 = 全池加权抽样；改成 = 先排除串门事实；风险 = 无 `origin` 字段的存量事实不受影响（回放用例证明）；收益 = 邻居家的内容不进社区分享卡片。估算 ≈4 人日（d5 3 人日 + chat.html 三上下文与 i18n 各 0.5）。

**依赖拍板**：OD-16 v3。

---

### PR-15 记忆浏览器串门面板 + 黑名单（OD-18 / OD-05 v2 / OD-09 v2 / OD-26 v3 + 8 locale）

**文件**：【改】`templates/memory_browser.html` + `static/js/memory_browser.js`（`:421` 已加载）：「串门记忆」面板——按 `visit_uid` 聚合（数据 = `GET /api/visit/memory/peers`，其响应每行带完整 `peer_uid`（本机 API，§4.6）；每人一行「`display_name` · 短码 · N 只猫娘 · 最近」，展开到 pair / 角色；**界面上永不显示完整 id**，`peer_uid` 只留作该行按钮的调用参数）、「清除这个人」（按钮用该行 `peer_uid` 调 `POST /api/visit/memory/forget{catgirl, peer_uid}`：只清当前角色下该人所有 pair 三 subject + `participant` + `by_char[当前角色]`（该人与其它角色的串门记忆不动，`by_char` 为空才删整条）；信赖池未加载 → 提示稍后重试）、「全部清除」、黑名单折叠区（拉黑 / 解除，按钮用该行 `peer_uid` 调 `POST /api/visit/contacts/block{peer_uid, blocked}`；拉黑不是记忆，撤销 consent 不影响它）、「让对方忘掉我」（只在同一场在飞时可达，发 `consent{memory:false, scope:'all'}`；离线后说明「对方机器上的副本无法远程清除」）；文案如实：只清本机、串门记忆区与私聊记忆分开、`scope:'all'` 会连带清掉本家对这一对的串门史、跨对可关联是设计、spool 不进 Steam 云存档；每场记录旁一个不显眼的「查看详情」（复用 PR-12 的详情视图，`GET /api/visit/details/{visit_id}`，OD-26 v3）；8 locale 同 hunk；`LOCALE_VERSION`。

**测试**：【新】`test_memory_browser_visit_static.py`（端点字面量无末尾斜杠；8 locale 键；渲染函数只用 `short_id` / `shortCode(visit_uid)` 不直接把 `peer_uid` 插值进可见文本——静态断言；「清除这个人」与拉黑按钮的请求体取自该行 `peer_uid`（静态断言 forget / block 调用处引用 `peer_uid`）；forget 请求体带当前面板的 `catgirl`（名册按本机角色分开，只清当前角色，静态断言）；「清除这个人」确认文案写明「只清她（当前角色）对这个人的串门记忆」（8 locale））。

**门**：check_i18n_sync、frontend_api_trailing_slash。

**依赖拍板**：OD-18、OD-05 v2、OD-09 v2、OD-26 v3（查看详情入口）。

---

### PR-16 `deploy/livekit/` + Servers 契约文档 + 开发环回 + 实测记录（OD-07 v2 / OD-12 v2 / OD-01 v2 / OD-10）

**文件**
- 【新】`deploy/livekit/{docker-compose.yml, Caddyfile, livekit.yaml, README.md}`：GCP 东京 e2-standard-4 起步（$125.51/月，来源 research_livekit-gcp.md），Caddy 443 复用 HTTPS + TURN/TLS（需真域名 + CA 证书），`room.max_participants: 2`、`room.departure_timeout: 20`、`limit.data_channel_max_buffered_amount: 1 MB`；README 写：上线期用 LiveKit Cloud Ship（$50/月、1,000 并发），月房·小时持续 >≈2,500 两个月再切自建；切换只换 Servers 下发的 `{url, token}` 与签发密钥，客户端零改动；`livekit-cli load-test` 500 房 1,000 轨压测是上线前置（默认 400 轨/CPU，e2-standard-4 名义 1,600 刚够）；流量按 MB/GB 十进制算（0.27 GB/房·小时），引用 GCP 报价时注明 GiB 换算。
- 【新】`docs/design/visit-servers-contract.md`（**Servers 契约唯一权威**，其它章节只引用）：`POST /api/visit/credentials` 请求 / 响应 / 错误码；claims 表 `{v:1, iss:'neko-servers', aud:'neko-visit', kid, sub:<visit_uid>, vid, visit_id, role, transport, char_tag, display_name?, iat, exp(guest =iat+2400、host =iat+3000), jti}`，Ed25519；`visit_uid = HMAC(server_secret, community_uuid)[:24]`；`vid = role[0]+'_'+sha256(visit_uid|visit_id)[:24]`；TTL 按 role：guest 40 min、host 50 min（TRTC `expire=2400|3000`、LiveKit `ttl=40m|50m`）；时钟容差 ±300 s；`invite_code`（host 领凭证返回，10 min，一次性；guest 必带；每房 host + guest 各一，第三者 403 `room_full`）；transport 按 host 区域，guest `region_hint` 只判 `cross_region`，跨区默认 403 `cross_region_unsupported`（Servers 侧开关，T9 后 owner 决定）；`GET /api/visit/invites/{invite_code}/preview`（guest 确认框的只读预览，返回 `{visit_id, host_display_name, host_short_code, cross_region, expires_at}`，不消耗邀请码，每账号 30 次/分钟限速超限 429，404 `invite_invalid` / 410 `invite_expired` / 403 `visit_banned`）；`GET /api/visit/pubkeys`（公开，缓存 24 h）；`POST /admin/visit/bans{visit_uid, until?}`；`POST /api/visit/reports{visit_id, peer_uid, transcript, reason}`；`POST /api/visit/transcripts`（每场双方各传本侧转录 + 用量，按 `visit_id + role` 幂等，长期保留、与账单同期，对端撤销不删，OD-26 v3）；`GET /api/visit/details/{visit_id}`（时长 / 扣减免费分钟 / token·TTS 消耗 / 双方合并转录，只有该场双方与管理员可读）；免费额度「每日签发分钟数 = 签发次数 × 30 min」，`VISIT_FREE_MINUTES_PER_DAY` 占位 120（**owner 定价时拍板**），每账号并发房 ≤2，付费档只改 entitlement；follow-up：在飞踢人（TRTC `RemoveUserByStrRoomId`、LiveKit `RoomService.RemoveParticipant`，均已核实存在）。
- 【新】`docs/design/visit-sensitive-memory-issue.md`（OD-10 issue 草稿，不实现）：`memory/sensitivity.py` 接口草案；全局「完全隔离亲人记忆」开关 **默认 False**（opt-in 逃生阀）；筛除接口不改变现网铸卡结果（`card_forge_facts.py:205 _weighted_pick` 默认继续读 `facts.json`，筛除是可选前置过滤）；老数据回填走 memory_server 后台低优先任务 + `classifier_version` 增量，不进启动链路；「小模型」拆成可选异步增强，纯规则部分才叫纯函数。
- 【新】`docs/design/visit-field-tests.md`：T1~T13 结果表模板（每项：日期、机器、结论、`chrome://webrtc-internals` 关键字段 `framesPerSecond / frameWidth / frameHeight / qualityLimitationReason / scalabilityMode / encodings.length / encoderImplementation`）。
- 【改】`docs/design/visit-infrastructure.md`：即本稿定稿（含 §3.11 失败表「关机时对方 30 秒后才知道你离开」「浏览器多窗口态不支持画面」）。
- 【新】`scripts/visit_dev_mint.py`（开发环回：用 `NEKO_VISIT_DEV_KEYFILE` 私钥签票据并输出开发公钥的 `kid`；核验路径与生产同一条，没有「跳过验签」分支）；【改】`docs/contributing/developer-notes.md` 一段（如何本地起 LiveKit + 两个后端互串）。

**测试**：【新】`tests/unit/test_visit_dev_mint.py`：脚本签出的票据经 PR-06 `verify_identity_ticket` 通过；篡改一字节即拒；`kid` 落在 `VISIT_SERVERS_PUBKEYS` 开发键位。

**门**：`check_docs_no_relative_paths.py`；docs 站 `npm run build`（死链即失败）；docstring_no_cjk（`scripts/` 在范围）；ruff。

**依赖拍板**：OD-07 v2、OD-12 v2、OD-01 v2、OD-10（issue 草稿）、OD-06 v2（免费额度占位值待 owner 定价）。

---

### N.E.K.O. Servers（闭源，独立排期；卡 PR-07 联调与 PR-16 契约文档）

按 `docs/design/visit-servers-contract.md`（PR-16）实现，客户端侧一律以该文档为准：
1. `POST /api/visit/credentials`：校验社区 OAuth bearer → 查封禁表 → host：登记 `visit_id → host visit_uid`、按 **host 来源 IP 区域**定 `transport`、签 vendor 凭证 + 身份票、返回一次性 `invite_code`（10 min）；guest：必带 `invite_code`，绑到该房，拿同一 `transport`，两侧区域不同 → 403 `cross_region_unsupported`（开关可改「允许 + 警告」）；每房 host + guest 各一；每账号并发房 ≤2（名额以 vendor 房间结束事件释放：TRTC 房间解散回调 / LiveKit `room_finished` webhook，或 Servers 自行向 vendor 查询确认该参与者 / 房间已不在之后释放；客户端 finalize 后的 `POST /api/visit/transcripts` 只触发一次这样的查询、**不直接释放**；凭证到期兜底；Servers 侧测试：上传转录时 vendor 查询显示仍在房 → 名额不释放，变异：上传即释放必红）；每日签发分钟数 = 次数 × 30 min，免费档 `VISIT_FREE_MINUTES_PER_DAY`（占位 120，owner 拍板）；`tier != sd600` → 403 `tier_not_entitled`。配套只读端点 `GET /api/visit/invites/{invite_code}/preview`（bearer）：guest 出门确认框的数据源，返回 `{visit_id, host_display_name, host_short_code, cross_region, expires_at}`；**不消耗邀请码**（一次性 `invite_code` 只在 guest 领凭证成功时消耗）、不写签发记录、不扣配额；每账号 30 次/分钟限速，超限 429（邀请码 10 位 base32，枚举不可行）；404 `invite_invalid` / 410 `invite_expired` / 403 `visit_banned`。字段见 §4.7。
2. `GET /api/visit/pubkeys`（公开，24 h 缓存）；`kid` 轮换靠发版 + 此端点。
3. `POST /admin/visit/bans{visit_uid, until?}` → 拒发新凭证；客户端黑名单在 hello 阶段拒（立即生效）；**follow-up（不阻塞 v1）**：封禁时对该 `visit_uid` 的在飞房调 vendor 服务端踢人（TRTC `RemoveUserByStrRoomId`、LiveKit `RoomService.RemoveParticipant`）。
4. `POST /api/visit/reports{visit_id, peer_uid, transcript, reason}`：被举报方由 Servers 以 `visit_id` + 举报人账号从签发记录推导，请求里的 `peer_uid` 只作校验，不一致 → 400 `peer_mismatch`；管理员按 `visit_uid` 反查社区账号。
5. **转录与用量（OD-26 v3）**：`POST /api/visit/transcripts`（bearer；按 `visit_id + role` 幂等；`role` 对照签发记录核验；双方各传本侧那份；请求体含本场 `usage{duration_s, llm_input_tokens, llm_output_tokens, tts_requests, tts_chars}` 与按 `(lp, side)` 排序的转录）+ 存储（**长期保留，与账单记录同期**；对端 `consent{scope:'all'}` 不删）；`GET /api/visit/details/{visit_id}`（时长、扣减免费分钟、token / TTS 消耗、双方两份按 `(lp, side)` 合并的转录；**只有该场双方账号与管理员可读**）；隐私政策补一段披露（对话全文上云、长期保留、用于账单与举报；确认框不提，owner 选择）。字段与错误码见 §4.7。
6. 密钥托管：腾讯云 SDKAppID / SecretKey（UserSig tls-sig-api-v2）、LiveKit API key / secret（HS256）、Ed25519 签票私钥、`server_secret`（`visit_uid` HMAC 盐；换盐 = 所有对端变新人，写进运维文档）。
7. IP：Servers 以来源 IP 复核区域，所以 Servers 知道用户 IP；对端经 SFU 拿不到（OD-01 v2 风险段已写）。
8. **画质档位服务端约束**（开源客户端可改 SDK 参数）：LiveKit token 按侧位收紧（host `canPublish:false`、只 `canPublishData`；guest `canPublishSources:['camera']`），订阅 LiveKit webhook `track_published`（带宽高），超出凭证 `tier` 即 `RoomService.RemoveParticipant` 踢人 + 记违规；TRTC 定时拉用量统计 / 事件回调，按账号比对实际分辨率档与码率，超档 → 封禁流程；host 能否以 TRTC 观众角色进房待 T13。字段见 §4.7。

### lanlan_frd（闭源壳）：零必需改动；两条可选 follow-up

**核对项**（PR-10 / PR-11 合并前在 `C:/Users/wehos/Project/lanlan_release/lanlan_frd` 只读确认）：
1. `src/preload/bridges/pet-websocket-bridge.js:333-346` 的 `PetWebSocket` 只在主 frame 生效；全仓无 `nodeIntegrationInSubFrames`（`src/window-manager.js:1001-1011` webPreferences）→ 同源 iframe 拿到原生 `WebSocket` / `RTCPeerConnection`，`_activeWs` 不受影响（T1 验收）。
2. `pet-input-region-bridge.js:2211` 把 `.transparent-overlay` 当背景、`:2722 / :3108 / :5205-5206` 用 `elementFromPoint` → iframe `pointer-events:none` + `transparent-overlay` 两道保险（T3 验收）。
3. `PET_ONLY_CAPTURE_BRIDGE_REQUEST_TYPES` 不需新增：串门没有任何二进制或凭证走 display socket；`visit_*` JSON 被 Pet 桥镜像到 Chat 窗无害。

**可选 follow-up（不影响 v2 结论）**：
- **销毁窗口前先发 `leave`**：`src/main/backend-runtime.js:2483-2490` 是 `destroyAllWindows()` 先于 `beginOwnedBackendShutdown()`，`destroy()` 不触发 unload，所以关机时 `leave` 发不出去，对端 30 s 后才 `peer_lost`（§3.11 明写）。若要更快，PC 侧在 destroy 前给 Pet 页 ≤500 ms 的 `leave` 窗口，或先 `POST /api/visit/stop`。
- **hide-all IPC**：Windows 兼容模式 `shapeHideNow` 只 `setShape` 1×1（`src/main/hotkey-manager.js:809-820`），正常模式 `fadeOutAndHide → win.hide()`（`:894`），两者对渲染的影响不同；v2 用帧饥饿检测统一处理（有帧就发、没帧就报 hidden），行为自洽；若产品要求「hide-all 一律停止画面」才需新 IPC。

---

### 对偶性检查表（每个 PR 自检）

| 维度 | 检查项 | 落点 |
|---|---|---|
| 读 / 写 | `fetch_visit_context`（读）↔ `post_visit_digest / post_visit_segments / post_visit_forget`（写）同 PR-08；`Blocklist` / `PeerRoster` 读写皆 async 对偶 | PR-06 / 08 |
| A / B 两侧 | `VisitRuntime(side)` 同一类；`VISIT_SCENE_BLOCK_GUEST/HOST`、`VISIT_SYSTEM_NOTICE_WRAP_UP_GUEST/HOST`、`VISIT_GOODBYE_FALLBACK_GUEST/HOST` 成对；guest `publish` ↔ host `ready`；guest 出门确认 ↔ host 接待确认；guest 拒打字 ↔ host 收件人开关；guest `propose` ↔ host `begin` | PR-04 / 09a / 10 / 12 |
| 必达 / 可丢 | cmd 1 ctl 与 `text` 进 outbox 必达 ↔ `line_delta / line_abort / typing / stats` 可丢；一行永远以一条 `text{final}` 收口（正常 `truncated:false` ↔ 打断 `truncated:true`） | PR-03 / 06 / 09a |
| 文本 / 语音 | `on_start_session` text ack-only ↔ audio `VISIT_VOICE_UNAVAILABLE`；`route_stream_message` text 劫持 ↔ audio 拒 ↔ screen / camera 吞；`visitVoiceEnabled=true` 流式 TTS + 按已播音频进度（`visit_speech_progress`）放出 ↔ `false` 文本估时驱动放出与嘴型；`streaming.py:284` 门只对 audio | PR-09a / 09b / 13 |
| 三个计时器 | 对端 30 s ↔ 自己 25 s ↔ 页面 20 s，严格递减且各差 ≥5 s（常量单测钉住）；显式离开 ↔ 超时类事件（前者立即、后者交心跳） | PR-03 / 06 |
| 记忆区 / 私聊 | 串门区 digest 只受 `visitMemoryEnabled` + consent 控制，在 finalize 做 ↔ 私聊写入只由 debrief 选择决定；`forget` 删 `.jsonl` ↔ `scope:'all'` 删名册项 + `state.json` peer 字段 | PR-08 / 14 |
| index / chat 双模板 | `visit-chat.js`（含 debrief 监听）两处加载 ↔ `parent-bridge.js` / `visit-pacer.js` / `text-mouth-driver.js` 只 index；`__NEKO_MULTI_WINDOW__ && /^\/chat/` 忽略建 iframe 但渲染 line | PR-11 / 12 / 13 / 14 |
| 8 locale | 后端表 `prompts_visit.py` / `prompts_memory.py` 两键 8 语含 zh-TW；前端 `static/locales` 8 json 同 hunk + `LOCALE_VERSION` | PR-04 / 12 / 13 / 14 / 15 |
| 分隔符 | `======以下为X======/======以上为X======` 成对 | PR-04 |
| 两份 `_USER_OWNED_FIELDS` | `proactive_router.py:58` ↔ `proactive_controller/__init__.py:43` 集合相等 | PR-03 |
| Python / JS 对偶 | `fragment / Reassembler` ↔ `transport.js` 分片；`estimate_speech_ms` ↔ `text-mouth-driver.js`；`VISIT_CONGESTION_LADDER` ↔ `pack.js` 阶梯；累加器纯函数 ↔ Python 参考实现 | PR-03 / 10 / 11 / 13 |
| 状态翻转 / 收尾 | `activate_visit` 每条失败分支都释放占位；takeover 只在凭证成功后占、失败即 release；`finalize` 幂等 `_exit_task`；`stop_all` 不发 `leave` | PR-09a |
| 三类接管者 | game / icebreaker / visit 都经 `acquire_takeover` 与注册表归属检查 | PR-02 / 09a |
| 热路径 diff 为空 | `websocket_router.py` 二进制分支 ↔ `app-websocket.js` Blob 分支 | PR-09b / 12 |

### PR 列表（合并顺序）
- PR-01 external route 注册表（纯重构，game 路径逐字节等价） | 依赖拍板: [] | 文件 7 | 测试 8 | 门 5
- PR-02 takeover 归属令牌：TakeoverMixin + game/icebreaker /route/start 归属检查 | 依赖拍板: ['OD-24'] | 文件 7 | 测试 4 | 门 3
- PR-03 L0/L1 基础：visit_settings 全部常量、visit_wire（信封 / schema / split_clauses / ClauseSplitter / estimate_speech_ms，无 NKVF）、visit_route_state、三开关 + 两份 _USER_OWNED_FIELDS | 依赖拍板: ['OD-06 v2', 'OD-09 v2', 'OD-11 v2', 'OD-30'] | 文件 7 | 测试 3 | 门 4
- PR-04 提示词表 prompts_visit.py（场景 / 收尾 / debrief）+ prompts_memory 两表新键（按 kind+platform 选表） | 依赖拍板: ['OD-04', 'OD-08 v2', 'OD-10', 'OD-16 v3', 'OD-23'] | 文件 2 | 测试 4 | 门 4
- PR-05 memory/scoped_client.py 自建共享记忆客户端（直连 memory_server 五个端点，只新增） | 依赖拍板: ['OD-31 v3'] | 文件 1 | 测试 1 | 门 4
- PR-06 main_logic/visit 纯逻辑层：identity / outbox / room（Lamport + wrap_up）/ liveness（5/30/25/20 s）/ spool / subjects / consent / limits / sanitize | 依赖拍板: ['OD-01 v2', 'OD-05 v2', 'OD-08 v2', 'OD-09 v2', 'OD-11 v2', 'OD-17 v2', 'OD-23', 'OD-30'] | 文件 10 | 测试 9 | 门 5
- PR-07 Servers 凭证客户端（invite_code、邀请只读预览、guest 40 / host 50 min、region_hint 只读、pubkeys fail closed）+ iframe 传输 WS /api/visit/transport/ws | 依赖拍板: ['OD-01 v2', 'OD-07 v2', 'OD-12 v2', 'OD-29'] | 文件 2 | 测试 2 | 门 5
- PR-08 记忆桥 + 串门区提交 + 启动补录（只弹芯片）+ mirror_meta 显式 memory_enabled + memory_server 只读 scoped_subjects + /api/visit/memory 路由 | 依赖拍板: ['OD-04', 'OD-09 v2', 'OD-16 v3', 'OD-17 v2', 'OD-18', 'OD-31 v3'] | 文件 6 | 测试 6 | 门 5
- PR-09a visit_router 运行时：activate（能力门 ①② 先于领凭证、③ 在凭证后）/ hello 互验 / 流式 TTS 与字幕对齐 / finalize / stop_all / HTTP 面（含查看详情代理）/ 转录上传任务 / debrief 端点（ask_later） | 依赖拍板: ['OD-03', 'OD-08 v2', 'OD-10', 'OD-15 v3', 'OD-16 v3', 'OD-21 v3', 'OD-22', 'OD-26 v3'] | 文件 6 | 测试 5 | 门 5
- PR-09b 接线：websocket_router goodbye + visit_speech_progress、turn.py 提取 interrupt_mirror_speech + 新增 open_mirror_speech_stream、crud rename 守卫、main_server sweep / recovery / 关机钩子、web_app include；二进制分支 diff 为空 | 依赖拍板: ['OD-03', 'OD-11 v2', 'OD-13', 'OD-15 v3', 'OD-25'] | 文件 7 | 测试 9 | 门 5
- PR-10 iframe 传输页：模板路由、loader 能力门、transport 两实现（LiveKit L1T1）、pack / unpack / frame-sink / backend-ws、vendor UMD vendoring + 通知 + 打包表 | 依赖拍板: ['OD-27', 'OD-28', 'OD-29', 'OD-02 v2', 'OD-06 v2', 'OD-07 v2', 'OD-14 v2'] | 文件 15 | 测试 4 | 门 4
- PR-11 父页 parent-bridge：postrender 分数累加器 + lastObjectRendered 过滑 + setTargetFPS 保存恢复 + 帧饥饿检测 + iframe 懒建摆位；live2d-core 一行、vrm / mmd 各一行 | 依赖拍板: ['OD-27', 'OD-02 v2', 'OD-06 v2', 'OD-14 v2'] | 文件 8 | 测试 3 | 门 2
- PR-12 聊天面与知情同意：visit-chat.js（拼接 / 收件人 / 出门与接待确认（无技术数字）/ 查看详情入口 / 举报）、tool role 气泡、wrap_up 徽标、status 码映射、8 locale | 依赖拍板: ['OD-19', 'OD-22', 'OD-26 v3', 'OD-08 v2'] | 文件 10 | 测试 2 | 门 2
- PR-13 口型 / 流式：visit-pacer（visit_speech_progress）、text-mouth-driver、app-audio-playback 两字段、设置页三开关 + 构图、8 locale | 依赖拍板: ['OD-15 v3', 'OD-21 v3', 'OD-09 v2', 'OD-06 v2'] | 文件 8 | 测试 5 | 门 2
- PR-14 debrief 芯片与两条写入路径（diary = 日记段进 /cache + ≤3 条事实经新端点 visit_facts 进 fact 层、不进 reflection；forget）+ card_forge 排除串门事实 + 启动补录重弹 + 8 locale | 依赖拍板: ['OD-16 v3'] | 文件 9 | 测试 4 | 门 5
- PR-15 记忆浏览器「串门记忆」面板（按 visit_uid 聚合、短码）+ 黑名单 + 查看详情入口 + 8 locale | 依赖拍板: ['OD-18', 'OD-05 v2', 'OD-09 v2', 'OD-26 v3'] | 文件 4 | 测试 1 | 门 2
- PR-16 deploy/livekit + Servers 契约文档 + 敏感记忆 issue 草稿 + 开发环回脚本 + 实测记录 | 依赖拍板: ['OD-07 v2', 'OD-12 v2', 'OD-01 v2', 'OD-10', 'OD-06 v2'] | 文件 10 | 测试 1 | 门 4

## 附录 A · 修订记录

> A.1 是 v1（2026-09-11）稿的对抗核验修订记录原文，逐条保留作判据与 file:line 证据链存档。其中涉及自建中继、WebP-alpha 图片帧、member_token、PSK 产品路径、成对假名 peer_id、「≤5 次清零」、speakerGainNode 静音、90 s 宽限等机制，在 v2 已被整条替换（见 A.2 与 §2.2 各「OD-xx v2」条目）；保留不代表 v2 仍采用这些机制。A.2 是 v2 的修订记录：owner 两轮反馈的落点、被证伪的断言、三份核验报告的逐条处置、主会话裁决摘要。

### A.1 v1（2026-09-11）对抗核验修订

- [fixed] role 'tool' 无既有 CSS 规则，「微调配色」不能进 react styles.css
  → 核实 grep static/css 与 frontend/react-neko-chat/src 均无 .message-bubble-tool/.avatar-tool 规则、MessageBubble.tsx:26/35/42 只给类名。§3.5.5/OD-19/§3.8 改为「在 static/css/index.css 新增规则」，并明写此前无任何 tool 样式；导出面板 :158/171 同组列 follow-up。
- [fixed] VISIT_RESPONSE_MAX_CHARS 单位应是 token
  → 核实 _streaming.py:109-111 docstring 与 _client.py:209-220（budget+20 token）。常量改名 VISIT_RESPONSE_MAX_TOKENS=160，§3.5.10 输出估算改为 ≤180 output token。
- [fixed] submit_proactive_callback 的 priority/coalesce_key 是关键字参数；detail 经 prompt_ephemeral 抄送插件总线
  → 核实 proactive.py:2118-2124 签名、_lifecycle.py:547-568 总线抄送、_shared.py:57 'proactive.callback'=1000。§3.2.1(e) 步骤 8 改关键字传参；detail 改为猫娘自述句（见 blocker 条目）；§3.7 加「回家自述进私聊记忆与插件总线」一行。
- [fixed] OmniOfflineClient 没有 send_text，步骤 9/14 混淆中继会话与 LLM 会话
  → 核实 omni_offline_client 公共 async 方法集无 send_text。步骤 10/15 拆成 relay_session.send_text（出站入队）+ llm_session._conversation_history.append（纯入史）两步，全文改用 relay_session/llm_session 前缀。
- [fixed] mirror request_id 不做去重
  → 核实 turn.py:1860-1905 只透传 request_id 进 payload、cross_server.py:925 只作 turn 分组。§3.7 该行改为「去重在 relay_client line_id LRU 与 VisitRoom order 单调；request_id 仅供 monitor/turn 关联」。
- [fixed] stream_data 在 :1003-1009 先记 engagement 再到 :1010 劫持点
  → 核实 websocket_router.py:1041-1047 顺序。设计明写此副作用并**保留原位**（亲人确实在电脑前，记账语义正确；主动搭话已被 takeover 压制），§3.5.8 表加一行；不移动判定点以免改变 game 行为。
- [fixed] OD-05「reflection 才攒得够 5 条」语义错
  → 核实 memory/reflection/_shared.py:41 MIN_FACTS_FOR_REFLECTION=5。§3.6.1/OD-04 改为「同一 pair 累积 ≥5 条未吸收事实才会生成 reflection」。
- [fixed] 复读守卫会把隔离会话历史清成只剩 SystemMessage
  → 核实 _streaming.py:1830 _check_repetition 与 :317 `[history[0]]`。session_pool.trim/pop 对 len≤1 早退；§3.5.1 明写 VisitMemoryBuffer 独立于 _conversation_history；加单测。
- [fixed] 重连只能带过期票/已用 jti，Servers 成为续连硬依赖（blocker）
  → 设计自证（§3.3.5 jti 一次性 + exp 600 s + Upgrade 层验票）成立。改为 welcome 下发 member_token 作 Upgrade 层续连凭证（`Bearer member.<room>.<token>`，TTL=房间寿命+90 s，绑 sub/role），票据只用于首连/重建；4409 改为「无 member_token 的重复席位」，带合法 member_token 的 resume 顶掉半开旧连接。OD-01/OD-11/§3.2.2/协议目录同步。
- [fixed] 4404 同时是正常终点，重建会消耗票并建空房
  → §3.2.2 加判据：已收 welcome 且未收 room_closed 且 /health.boot_id 变化才重建；否则 finalize('relay_lost')；guest join 4404 ≤3 次×2 s；重建房 unpaired_timeout 30 s；/health 与 welcome 加 boot_id。
- [fixed] handle_interruption 只翻标志，半句仍会入史，stream_text 无串行锁
  → 核实 _lifecycle.py:836-838、_streaming.py:1228/1805/890/1975、_client.py:146-147。打断改 task 级（_reply_task.cancel + gather）；VisitRuntime 加 _llm_turn_lock 串行 stream_text/append/trim；pop_trailing_ai_message 只作兜底并校验内容。
- [fixed] finalize 在 _reply_task 内调用会自取消；reader 回调持锁调 finalize 会自锁
  → 核实 game 用 _exit_task（postgame.py:1152-1184、runtime.py:4998）。finalize 锁内翻状态 + 派生 _exit_task 幂等；_reply_task 只在 is not current_task 时 cancel；reader 回调只 create_task；加「on_text 内触发 violation 5 s 内发 leave」单测。
- [fixed] finalize 长尾在锁内会卡 sweep/读循环/切换请求
  → 核实 crud.py:1121-1124 同步 await。锁只包状态翻转；仪式/TTS/flush/leave 在锁外 _exit_task；character_switch/manager_replaced/shutdown 时 flush 单次 ≤3 s 跳过仪式；finalize_external_routes_for_character 只等状态翻转。
- [fixed] game /route/start 不查注册表且无条件覆盖/解除 takeover
  → 核实 game_router/runtime.py:1898 game_route_start、:658-692 只查 _game_route_states、:2075-2076 无条件写、postgame.py:1277-1278 无条件置 False；icebreaker_router.py:263 /route/start 不置 takeover。新增 OD-24：manager acquire_takeover/release_takeover 令牌 API，game/icebreaker /route/start 查注册表；三段回归报告。
- [fixed] activate 未检查 _is_responding 且 mint/连中继与检查之间有 TOCTOU
  → 核实 turn.py:475 takeover 下丢弃 completion、cross_server.py:906-925 current_turn 悬挂。§3.2.1 步骤 1 改为先占位（phase='pending'，is_active 立即 True）→ 前置检查 → 若 _is_responding 则 handle_interruption 并等 turn end ≤3 s → acquire_takeover → mint/连中继；失败分支 leave('error') + release。
- [fixed] 关机 1 s 上限与 3×20 s flush 矛盾且钩子位置太靠后
  → 核实 on_shutdown（__init__.py:1233-1275）顺序。stop_all 挪到最前（close_voice_identity_runtime 之前），并发 leave ≤1 s + 单次 flush ≤3 s，总 ≤4 s；文档明写关机最多丢 40 行。
- [fixed] mirror_assistant_speech 的 completion 是「送达」非「播完」，且单槽并发互杀
  → 核实 tts_runtime.py:2028-2046、:137-144、turn.py:2307-2310、proactive.py:2908/2905。VisitRuntime 加 _speech_lock 串行全部串门 speech；「播完」= 等 lifecycle_bus voice_play_end ≤20 s；speech_id 匹配细节列 §3.10.10。
- [fixed] pump 读 mgr.websocket 为 None 会 AttributeError 死亡；两次 send 之间被 cancel 悬挂头
  → 核实 lifecycle.py:4614-4633 置 None、tts_runtime.py:1886-1905 send_speech 先 pin 再进锁。pump 改照 send_speech 形状；下行改为**单次 send_bytes 二进制原样**（不再有 JSON 头），半帧悬挂问题消失；try/except 保活计数。
- [fixed] 「最新 socket 赢」使浏览器多窗口态取帧/出帧目标错
  → 核实 websocket_router.py:547-559。§3.2.2 明写 v1 限制「浏览器多窗口开发态不支持串门画面」（Electron Pet 是唯一真实 socket），window_kind 标记列 v1.5。
- [fixed] reply_to=0 开场并存与「order 前进即打断」互斥
  → §3.5.3 is_stale(reply_to, incoming_reply_to) 对 0==0 豁免；加两侧同时开场单测。
- [fixed] route_stream_message 在 /ws 读循环内 await，send_text 等 ack 或抛 VisitBackpressure 会卡/炸读循环
  → 核实 :1048-1054 await。VisitSession.send_text 定义为入队即返回本地 seq；handler 捕获 VisitBackpressure 转 status VISIT_E_BUSY 返回 True；accept_visit_frame 只做 mailbox.put。
- [fixed] 回家汇报 detail 带对端原话经 prompt_ephemeral 进私聊记忆与插件总线（blocker）
  → 核实 proactive.py:1831 prompt_ephemeral、_lifecycle.py:752-753 persist_response 入史、:559-568 总线抄送、turn.py:1749 sync 队列。OD-16 重写：detail 只含猫娘 ≤80 字自述（仪式同一轮生成），禁止复述对方原话，8 字 n-gram 断言不含对端行，peer 未同意不提对方亲人；文档如实写明这句进私聊记忆与总线，forget_all 清不到；§3.6.7/§3.7 同步。
- [fixed] finalize 顺序让主 manager 静音 8+60 s
  → 核实 manager.py:277-278 及 turn.py 各消费点。takeover 在 leave 后立即 release（仪式走 mirror 不依赖 takeover）；flush 后台；GET /state 回 memory_pending。
- [fixed] build_visit_instructions 用已替换亲人真名的 lanlan_prompt，且 OmniOfflineClient 存 master_name
  → 核实 character_runtime.py:1904-1907 替换、characters.py:219-225 原始 lanlan_prompt_map、_client.py:296-297。OD-10/§3.5.9 改为从原始 map 构造，{MASTER_NAME}→FAMILY_NEUTRAL_TERM，OmniOfflineClient(master_name=中性词)；单测断言 instructions 与出站不含 master_name。
- [fixed] websockets write_limit 32 KiB + 内核缓冲会把帧堆成秒级延迟，延迟估算无排队项
  → 核实 .venv websockets/asyncio/client.py:74/319、connection.py:1006。OD-20/§3.4.3/§3.4.5：应用层 ≤2 帧在飞窗口（ack{frame_seq}）为主，write_limit=max_frame_bytes 为辅，B 页面丢 >500 ms 旧帧；估算加排队项上界 ≤2 帧时间并给出自钳帧率公式。
- [fixed] guest 对邀请里的 relay_url 零校验，恶意 host 可收走票据
  → §3.2.1 步骤 2/OD-12/§3.7：relay_url 主机名必须精确命中 VISIT_RELAY_ENDPOINTS 或 VISIT_RELAY_URL，否则 400 不发起连接；票据加 relay claim，中继比对自身；单测。
- [fixed] speaker{kind,name} 发送方自报可无限重置 quiet、取消本侧推理、逐句换名
  → §3.3.1/§3.5.3/§3.7：删 text.speaker.name，显示名只取 hello profile；对端 human 每场 ≤VISIT_PEER_HUMAN_RESETS_MAX=5 次清零且不取消本侧 pending；单测「对端全标 human」仍在 6+5×6 句内 quiet。
- [fixed] 黑名单存 peer_char_id，换 char_tag 即绕过
  → OD-05/§3.7/§3.3.4：blocklist 主键改 peer_id（用户级），char 级只显示；/memory/peers 按 peer_id 聚合；中继 /admin/ban 按 sub_hash。
- [fixed] 对端 consent scope=all 只清 participant，群 digest 里的对端事实保留
  → OD-09/§3.6.5：scope=all 对群 subject 也 /scoped_forget，本家串门史一并清空并在 UI 提示（不改 digest 形态）。
- [fixed] 未知 t 与 x.* 无限速，成为洪泛通道
  → §3.3.1：每连接控制帧总量 30/10 s，未知/x.* 5/10 s，超限 throttle{control} 再 4429；客户端 decode_control 对 _unknown 直接丢弃。
- [fixed] B 页直接用 A 自报的 mime/w/h
  → §3.2.1 步骤 7/§3.3.2/§3.4.3：B 后端按魔数 sniff 重写 mime、钳 w/h ≤2×max_h、非位图丢帧计数，页面只信重写后的字段。
- [fixed] PSK 票 sub 自报、无 ts 容差与 nonce
  → §3.3.5/协议目录：PSK 加 ts ±300 s + 一次性 nonce；README 明写只防外人；PSK 模式 UI 标「未验证身份」。
- [fixed] on_start_session 只写了 audio，B 亲人第一次打字会起普通文本会话
  → 核实 app-buttons.js:3104-3109 先发 start_session{text}、websocket_router.py:949-956 game 的 ack-only。visit 的 on_start_session 对 text 走同款 ack-only；加「text start_session 不创建 mgr.session」断言。
- [fixed] 票据 ±60 s 容差对家用机太紧
  → 核实 telemetry security.py:79 ±300 s。改 ±300 s；客户端用 /health.server_time 算偏差 >60 s toast。
- [fixed] lite 空闲 1 fps 眨眼跳帧、256 px 放大糊
  → OD-06/§3.4.1/§3.4.4：lite 空闲提到 2 fps（+7~11 KB/s）；B 页面双 img 150~250 ms crossfade；max_h=320,q=0.5 组合列 §3.10.9 待实测。
- [fixed] 缺：复读守卫清史后串门照常、缓冲不依赖 LLM 历史
  → 同上第 8 条；§3.5.1 明写。
- [fixed] 缺：engagement 记账副作用说明
  → 同上第 6 条；§3.2.1 步骤 15 与 §3.5.8 明写。
- [fixed] 缺：§3.7 威胁模型「对端内容进插件总线」
  → §3.7 加「回家自述进私聊记忆与插件总线」行，闸门 = detail 只含自述句 + n-gram 断言。
- [fixed] 缺：galgame_router.py:298 消费 takeover
  → 核实 :298。§3.5.8 表加「galgame 选项 → 回退固定选项」。
- [fixed] 缺：PIXI postrender 可用性未核实
  → 核实 static/libs/pixi.min.js 为 v7.4.3 且含 "postrender" 事件名。§3.0.1/§3.4.2 写明版本。
- [fixed] 缺：group_chat@neko_visit 两张表与 _NAMED 占位符要求
  → 核实 test_participant_memory_and_display_name.py:461-480 断言。§3.8 L0/OD-04 写明两张表同键、8 语、_NAMED 含 {display_name}{subject_id}。
- [fixed] 缺：四个容器并非同一 CSS 规则
  → 核实 index.css:351/407/430/451 各自独立。§3.4.4/OD-14 改为「各自独立规则，复制 live2d 的三条属性」。
- [fixed] 缺：串门中 goodbye 语义
  → 核实 lifecycle.py:82 set_goodbye_silent 只静音主 manager。新增 OD-25：两侧 finalize('goodbye')，固定句静音；§3.2.3/§3.5.7 加 reason。
- [fixed] 缺：语音会话入口未枚举完（streaming.py:284 自动建会话、被顶替 socket 语音路径、MicLease）
  → 核实 streaming.py:275-295 自动 start_session(audio)、ws:742/748。注册表加 route_external_start_session；streaming.py:284 前对 audio 模式加门（visit 拒、game/无路由原样）；被顶替 socket 语音在 takeover 下由 dispatcher 吃转写（无泄漏，明写）；main_logic/core/streaming.py 列回归报告。
- [fixed] 缺：ProactiveDeliveryManager 在 takeover 期间每 2 s 重试刷屏；结束后积压 cue 与汇报抢序
  → 核实 proactive.py:771 拒回、main_logic/proactive_delivery.py min_gap 2 s、proactive.py:82 _park_proactive_for_goodbye。activate 时调 _park_proactive_for_goodbye 停 pump；汇报 priority=3 先释放，park 的 cue 随 trigger_agent_callbacks 其后释放。
- [fixed] 缺：票据/席位并发与互访=两个房间不可达
  → §3.1 命名/OD-03：v1 每角色同一时刻只一个房间，互访留 v1.5 同房双向；§3.2.4：4409 判据 (sub, role, room) 且无 member_token，同 sub 不同房间合法。
- [fixed] 缺：中继状态机表
  → 新增 §3.2.4 表：created/paired/active/席位 reconnecting/closed，unpaired 300 s（重建 30 s）、host 未 ready 60 s、idle 300 s、max_duration 1860 s、两侧同时 leave、drain 语义、claims 校验落点。
- [fixed] 缺：Pet 窗隐藏期间的生命周期分支
  → 新增 §3.2.5：3 s 无 emit → 1 Hz hidden 空帧保活；B 显示最后一帧 0.6 + 徽标；不计 idle；恢复首帧 KEYFRAME 替换。DWM 实测仍列 §3.10.4。
- [fixed] 缺：串门期间仍会调主 manager LLM 的入口
  → 核实 greeting.py:453/895、proactive.py:140/767 均看 takeover，ws:748-750 的 pending callback 经 :771 被推迟。§3.5.8 表写明已由 takeover 守卫覆盖，avatar_interaction 走 greeting 路径。
- [fixed] 缺：visit_frame JSON 头每秒 6~15 条经 RAW_MESSAGE 扇出并 console.log
  → 核实 pet-websocket-bridge.js:423-437 逐条 console.log 且 PET_ONLY 集合只有两个 capture 类型。下行改为二进制原样（不经字符串转发），visit_frame JSON 头从协议删除；Electron 保持零改动。
- [fixed] 缺：举报与留证
  → 新增 GET /api/visit/transcript（本场及结束后 10 min 内带 order/from/relay_ts 盖章，不落盘）+ 前端导出按钮；Servers 侧封禁→票据 403 已在协议；提交举报的闭源流程列 §3.10.11。
- [fixed] 缺：「一键清空」覆盖范围文案
  → §3.6.6/§3.3.4：文案如实写明只清串门记忆区、回家自述那句已进普通记忆、对方副本无法清除。
- [fixed] 缺：peer_id 可被公开串联；指纹字段最小化
  → OD-05/§3.3.1：peer_id 改成对派生 HMAC(salt, sorted(sub_a,sub_b)|sub_被描述者)，不同对不可关联；hello.client.app_version 只发 major.minor。
- [fixed] 缺：票据 aud/region/role/room 与 create_room/join_room 匹配校验落点
  → §3.2.4 末段与协议目录 ticket 条目：aud、relay、region、role→端点、guest room claim==join_room.room_id 逐项列出。
- [fixed] 缺：B 截图含访客图层与 A .minimized 断水印坐标链的回归项
  → 核实 app-screen.js:3509-3514 对 .minimized 返回 null。A 侧改用新类 .visiting-away 不触发该判断（OD-14）；B 侧在飞截图列 §3.10.5 交互轴；测试段加两条回归项。
- [fixed] 缺：A 的人类对出门的知情与同意粒度
  → 新增 OD-26：join 需 confirm:true（对话框显示对端名+短码）；host 收 peer_joined 后经 visit_invite → POST /accept 才发 ready（60 s）；visitEnabled 只表示允许被邀请。
- [deferred] voice_play_end 是否携带 speech_id 供精确匹配
  → 设计写「按 speech_id 匹配，若前端信号无 id 则退化为本侧无在播音频判据」，列 §3.10.10 实施期核对 app-audio-playback 发出的 meta。
- [deferred] lite max_h=320,q=0.5 是否优于 256,q=0.55
  → 需实测同字节清晰度，列 §3.10.9；v1 表保持 256/0.55 + 空闲 2 fps。

### A.2 v2 修订记录（2026-09-12 第一轮反馈 / 2026-09-26 第二轮拍板）

#### A.2.0 v2 怎么来的

- **输入**：owner 第一轮反馈（2026-09-12，5 条，见 A.2.1）与第二轮拍板（2026-09-26，OD-01/03/05/08/09/10/11/13/15/16/17/21/24/25 + 术语规则）。
- **调研**：7 份（TRTC / ARTC / 声网 / LiveKit+GCP / Chromium 146 WebRTC / Xiao8 取帧路径 / lanlan_frd preload 与窗口），2026-09-12 抓取，2026-09-26 由核验代理逐条重抓复核。
- **设计**：视频 / 传输 / 身份轴 3 份独立设计 → 五维打分 → 合成稿（附录 B.2）；对话轴 d4、身份 / 记忆 / 生命周期轴 d5 各为独立单稿（未做三方比选，见 B.2.7）；OD-03「为什么不复用 game / QQ 路径」由 reuse_paths 单独核代码回答。
- **核验**：3 个对抗核验代理（code：file:line / API / vendor 事实；platform：数学与平台行为；product：产品 / 安全 / 成本 / 运维）默认「稿是错的」去找证据，共提出 blocker 5 处（实为同 2 个问题的三路重复）、major 26 处、minor 22 处。
- **裁决**：主会话按三份核验报告与 owner 两轮反馈，对三稿之间的矛盾逐条拍板（A.2.4），修订代理据此重写各章。

#### A.2.1 owner 两轮反馈逐条落点

| 轮次 | owner 原话（缩） | 解读 | 落到哪里 |
|---|---|---|---|
| 一 / 1 | 视觉通道尽可能压低带宽，目前只允许 600 kbps 这一档，后续做付费；尽可能小的区域、适中分辨率、压缩；国内腾讯或阿里 WebRTC，国外 GCP 中转（你来选型） | 单一免费档 600 kbps，阶梯留付费位；托管 WebRTC 取代自建中继 | OD-02 v2（WebRTC 视频轨 + 堆叠 alpha 320×448 → 320×896 不透明）、OD-06 v2（sd600 = 视频 560 + 数据 ≤40 kbps；hd1200 / fhd2400 只留表项）、OD-07 v2 / OD-12 v2（大陆 TRTC；海外 LiveKit Cloud Ship 起步 → 月 >≈2,500 房·小时切 GCP 自建）、§3.4、§3.5.8 成本 |
| 一 / 2 | 2 fps 不行，要低画质不要低 fps，fps 怎么也得 30，画质可低于 720p；取景贴着角色，默认只录上半身 | 真 30 fps 硬要求；裁剪优先于分辨率 | OD-02 v2 / OD-06 v2；§3.4 保 30 fps（postrender 内分数累加器 `acc += 30 / renderFps`，裁决 D.5；`makeUpperBodyRect` 比例上半身框）；T2 以 `RTCRtpSender.getStats().framesPerSecond` 验收；拥塞阶梯只缩裁剪不动 fps |
| 一 / 3 | 台词流式转发有什么问题？为什么默认关？文本节拍是什么、语音才对吧？不开语音才是文本？ | 流式默认开；口型与转发节拍跟本地 TTS，语音关才用文本估时 | OD-21 v2（默认开，分句 `line_delta` + `text{final}` 收口）、OD-15 v2（`visitVoiceEnabled` 默认 true；关则文本估时驱动嘴型与转发）；§3.6 |
| 一 / 4 | 中继自己部署不一定划算，仔细选型；未来 1000 同接；国内走专门 WebRTC 划算 | 不自建大陆中继；成本按 1000 同接 = 500 房算 | OD-07 v2（大陆零服务器）、OD-27~OD-30（同源 iframe 承载 vendor SDK；文本 / 控制走 vendor 数据通道 + 后端 outbox，取代自建文本中继）、§3.1 拓扑、§3.5.8 成本表 |
| 一 / 5 | 「每场 ≤5 次清零」没看懂 | 仲裁规则必须一句话讲清 | OD-08 v2 删该规则；一句话规则「连续 6 句无人插话或本侧满 40 句 → 收尾；每分钟 6 句只顺延」；§3.6.3 |
| 二 / OD-01 | 允许临时鉴权，但握手前仍需核验社区身份，方便管理员封禁 | 短期 vendor 凭证 + 身份票可以；任何 vendor 连接之前 Servers 必须核验 OAuth 账号；PSK 匿名路径不进产品 | OD-01 v2（Servers 一次签发 vendor 凭证 + Ed25519 身份票 40 min；`hello{ticket}` 互验；`invite_code` 绑房；封禁 = 拒发 + 客户端黑名单 + Servers 侧 vendor 踢人 follow-up）；PSK 产品路径删除，开发环回走 `NEKO_VISIT_DEV_KEYFILE`；§3.8、§4 Servers 契约 |
| 二 / OD-03 | 为什么不复用 game 或 Q 群群聊路径？（问题） | 要用代码事实回答 | OD-03 v2「补充问答」段（取 reuse_paths §6）；§3.10 对照表加「QQ 群聊路径」「game 路径」两行；OD-31 新增：QQ `memory_bridge` 五方法上提为 `memory/scoped_client.py` |
| 二 / OD-05 | 记忆能绑定到社区成员身份上吗？（倾向是） | 记忆、黑名单、名册按社区身份聚合；跨对可关联成为设计 | OD-05 v2：`visit_uid` = Servers 派发的稳定不透明 id（`HMAC(server_secret, community_uuid)[:24]`）作唯一主键；对方亲人 `participant('neko_visit', peer_uid)` 人级跨对；对方猫娘 `group_participant('neko_visit', pair_id, peer_char_id)`；隐私后果明写；§3.7 |
| 二 / OD-13 / OD-24 / OD-25 | 同意 / 同意 / 赞成 | 按推荐拍板 | 三条标「owner 已同意（2026-09-26）」，主体不动 |
| 二 / OD-08 | 接受提案，但把「安静」改成「自然收尾回家」：进入不可打断的收尾流程 | 触发后两只猫娘各说一句告别，guest 回家 finalize；期间人类文字拒绝 | OD-08 v2、§3.6.3：host 发 `wrap_up{begin}`（guest 只 propose）→ guest 告别一句 → host 送客一句 → `done` → `leave('home')`；步超时 15 s（到对方告别行第一片）、硬顶 45 s（裁决 F.1）；§4 `wrap_up` 消息 |
| 二 / OD-09 / OD-11 | 写得太乱没看懂；OD-11 的 90 s 太长，30 s 就可以判死 | 人话重写，一句一个意思；对端 30 s 不回来即 peer_lost | OD-09 v2 / OD-11 v2 按 d5 人话骨架再拆（裁决 G.5）；活性常量 `VISIT_HEARTBEAT_S=5 / VISIT_PEER_LOST_S=30 / VISIT_SELF_RECONNECT_S=25 / VISIT_LOCAL_PAGE_GRACE_S=20 / VISIT_SHUTDOWN_BUDGET_S=3`（裁决 E）；§3.11 |
| 二 / OD-10 | 「串门时不记得家里最近的事」可接受，但留 issue：上线前要有筛除机制隔离潜在敏感记忆，可供记忆卡片 / 卡牌系统共用；最坏情况有个开关完全隔离亲人记忆 | v1 OD-10 保留；一页 issue 草稿，不在本次交付 | OD-10 保留 + issue 草稿（`memory/sensitivity.py` 共享筛除接口；全局隔离开关**默认 False**、铸卡默认结果不变、回填走后台任务，裁决 G.4）；§3.13 |
| 二 / OD-15 / OD-21 | 按要求默认流式、默认对口型（语音可选关闭） | 已定 | OD-15 v2 / OD-21 v2；删「本地静音但保留 RMS」开关（裁决 F.2） |
| 二 / OD-16 | 留 issue；理想是猫娘临时记得串门内容，回家简述给用户，问要不要记、怎么记；实现简单就直接做完 | 设计 debrief；如实估成本 | OD-16 v2：回家简述 → 芯片（记成日记 / 只记要点 / 不记）→ `POST /api/visit/debrief/choice`；估算 ≈4 人日（d5 ≈3 人日 + 核验补的置灰通道 / chat.html 三上下文 / i18n），做进本次交付小 PR；超时与崩溃默认 `ask_later` 不自动写（裁决 G.2）；§5 PR-14 |
| 二 / OD-17 | 不理解；解释当前风险，为什么这么久才落盘一次？ | v1 批量（40 行 / 6000 tok / 10 min / 结束）崩溃即丢；改为每句立刻落盘 | OD-17 v2：每句立即追加 `config_dir/visit_spool/<visit_id>.jsonl`（O_APPEND 单次 write，fsync 30 s + finalize）；digest 在结束时做一次；10 min 周期留作开关默认关；spool 不进 Steam 云存档；§3.7 |
| 二 / OD-21 | 为什么不默认开？ | 默认开 | 同 OD-15 / OD-21 行 |
| 二 / 术语 | 对用户的称呼不用旧的物化称呼（owner 术语规则） | 全文改称 | 全文一律「亲人 / 用户」；`{MASTER_NAME}` → 中性词的 v1 规则保留 |

#### A.2.2 评审证伪 / 纠正的断言

（1）合成稿 §0.4 自核的 10 条（对三份视频 / 传输轴设计的纠正；「后续」列是三路核验对这 10 条本身的再纠正）

| # | 出处 | 被证伪 / 纠正的断言 | 证据 | 后续 |
|---|---|---|---|---|
| 1 | 设计 1 | 「trtc-sdk-v5 5.16.0（2026-03-13）」 | npm `latest` = 5.20.1；官方 changelog 首条 5.19.2 @2026-08-25（调研文件是 3 月快照） | 三路核验均复核成立 |
| 2 | 设计 1 | 「TRTC license 是腾讯商业条款，需 owner 确认可分发」 | npm 元数据 `license: ISC`（设计 3 正确） | 成立；OD-28 仍要求实施时以包内 LICENSE 文件为准复核一次 |
| 3 | 设计 1 | 「声网 2×28/1000×60 = 3.36 元/房·小时」 | 单向视频下 host 收 HD 28 + guest 只发按音频 7 → 2.1 元（设计 2 正确） | 成立 |
| 4 | 设计 1 | 「calculate-native-win-occlusion=false 在 src/main.js:919」 | 实际 `:928` | 成立 |
| 5 | 设计 1 | 「Chat 窗从不开自己的后端 socket（ipc-router.js:68-72）」 | `:68-72` 是无关的频道路由表；支撑事实在 `ipc-router.js:7` 头注释 | 成立 |
| 6 | 设计 1 | 「LiveKit DefaultReconnectPolicy 10 次约 35 s」 | 合成稿改为「延迟数组合计 38.6 s（+ 抖动）」 | **合成稿自己也算错**：数组 `[0,300,1200,2700,4800,7000×5]` 之和 = 44.0 s，第 3 次起每次再加 ≤1 s 抖动 ≈44~52 s（code F8 / platform m1 / product F-12，源码 https://raw.githubusercontent.com/livekit/client-sdk-js/main/src/room/DefaultReconnectPolicy.ts ）；d5 的「53~61 s」同错；统一写「约 44 s + 抖动」（裁决 D.8）。结论「我们 30 s 先切」不变 |
| 7 | 设计 1 | `hotkey-manager.js` / `screen-capture-ipc.js` / `pngtuber-core.js` 漏目录 | 实为 `src/main/hotkey-manager.js`、`src/main/screen-capture-ipc.js`、`static/pngtuber-core.js`；行号本身正确 | 成立；hotkey-manager 内部行号另有漂移，见（2） |
| 8 | 设计 3 | 「lanlan_frd/src/main.js:555-558 Linux X11 强制软编」 | `:555-558` 是 `appendLinuxCompatibilityCommandLineSwitches`；X11 软编开关在 `appendLinuxX11CommandLineSwitches` 与常量 `:490-497` | **合成稿给的新行号也偏了**：`appendLinuxCompatibilityCommandLineSwitches` 在 `:557`，`appendLinuxX11CommandLineSwitches` 在 `:563-566`（code F11） |
| 9 | 设计 3 | 「prompts_memory.py:3884 按前缀 `neko_visit:` 选 group_chat@neko_visit 两张表」 | `:3884 get_scoped_persona_section_header(subject_kind, …)` 今天按 `subject_kind` 选表，仓库零处 `neko_visit`；「按前缀选表」是 v1 OD-04 的待新增规则，不是现状 | 成立；且裁决 G.1 定为按 `(subject_kind, platform)` 选表，不按裸前缀（否则 `participant` 的 `neko_visit:<uid>` 与 `group_chat` 的 `neko_visit:<pair>` 撞前缀） |
| 10 | 设计 2 | 「身份票 `exp: iat+600`、jti 一次性」与 OD-11′「30 s 内回来复验通过 / Pet 页刷新同凭证再入房」自相矛盾 | 10 min 后任何重连复验必失败；合成稿改为票据 2 h 且允许同 `vid` 重放同一 jti | **2 h 又被推翻**：2 h + 每日 50 次 = 每账号每天最多 25 h 免费标清账单，且被封账号继续持有有效凭证（product F-04 / F-05）；裁决 C.2 定 TTL 40 min（`exp = iat + 2400`，vendor 凭证同 40 min；串门硬顶 30 min + 重连 ≤25 s 覆盖足够），同房同 `vid` 重连允许重放同一 jti |

（2）三路核验推翻的行号 / 数字 / 事实（含对合成稿、d4、d5、reuse_paths 的纠正；处置以裁决为准）

| 来源 | 稿中原文 | 核验后 | 处置 / 落点 |
|---|---|---|---|
| code F8 / platform m1 / product F-12 | synth「LiveKit 重连合计 38.6 s」；d5「53~61 s」 | 44.0 s + 第 3 次起每次 ≤1 s 抖动 ≈44~52 s | 「约 44 s + 抖动」（D.8）；OD-11 v2 现状 |
| code F9 | synth「Servers 基址 `community_oauth.py:40`」；d5「`utils/social_base.py:15`」 | `community_oauth.py:40` 是 `_DEFAULT_AUTH_URL`（认证域）；社区基址在 `card_drop_router.py:41` 与 `utils/social_base.py:12` | 改行号；OD-01 v2 现状 |
| code F10 | d4「turn.py `_begin_game_speech_completion_wait :2217-2221`、`_enqueue_tts_text_chunk / _request_tts_done_locked :2226-2234`、`emit_turn_end_after :2245-2250`」 | 实际 `:2239`、`:2254-2257`、`:2261-2262`（缓存命中路径另有 `:2186-2187`）；函数名与语义全对 | 改行号；OD-15 v2 现状、§3.6 |
| code F11 | d4「`config/prompts/_locale.py:76` NEKO_CORE_LOCALES」 | `:68`（`:76` 是 `_TRADITIONAL_MARKERS`） | 改行号 |
| code F11 | d5「`App.tsx:3088 onAction={onMessageAction}`」 | `frontend/react-neko-chat/src/FullChatSurface.tsx:3088`；`App.tsx` 无 `onAction=` | 改文件名；OD-16 v2 |
| code F11 | synth「hotkey-manager.js `:431 applyHideAllUI`、`:802-819 shapeHideNow`、`:887-897 fadeOutAndHide`」 | `:433`、`:809-820`、`:894` | 改行号；§3.11 |
| code F11 | reuse_paths「含 `group_participant` 字面量的文件正好 6 个」 | 7 个（漏 `config/prompts/prompts_memory.py`） | 改数；§3.10 |
| code F11 / platform §3.10 / product F-22 | synth「TRTC changelog 里 5.11.0 / 5.17.0 两处 Electron 22 修复记录」 | 三次抓取三种结果（5.11.1 @2025.06.27 + 5.17.1 @2026.04.23；5.13.1 + 5.17.1；5.13.1 @2025.10.10 + 5.17.0 @2026.04.10） | 标「不确定，只说明官方 changelog 有 Electron 相关修复条目」；OD-07 v2 现状 |
| code F12 | synth「原子追加 + fsync 先例 `utils/event_logger.py:25-38`、`main_logic/facts_sync/sync_worker.py:78-81`」；目录 `<memory_dir>/visit_spool/` | 两个先例都**不** fsync（`event_logger.py:263-264` 是 `open(path,'ab').write(payload)`；`sync_worker.py:78-81` 是文本模式 `path.open("a")`）；先例只提供 O_APPEND 单次 write，fsync 是新增 | 目录统一 `config_dir/visit_spool/`（与 `visit_peers.json` / `visit_blocklist.json` 同根，裁决 G.3）；OD-17 v2 |
| code F13 | 零散字段名：`region_hint 'cn'/'global'/'unknown'` vs `'cn'/'intl'`；未登录 409 `desktop_login_required` vs `VISIT_LOGIN_REQUIRED`；封禁 403 `blocked` vs `visit_banned`；公钥 `/.well-known/neko-visit-keys` vs `/api/visit/pubkeys`；首包 `hello` vs `identity`；心跳 `ping{ts}` / `hb{lp_seen}` / `hb{seq, ts}` | 三稿各写一版 | 收口成一张 Servers 契约表：首包 `hello`、心跳 `hb{lp_seen}`、公钥 `GET /api/visit/pubkeys` + `VISIT_SERVERS_PUBKEYS`（裁决 C.4、C.8、B.2）；§4 |
| platform M1 | synth「抽样门距上次抓帧 ≥33 ms 得 30 fps」 | 75 / 144 / 165 Hz 或定时器 60 fps 模式下算出 25 / 28.8 / 27.5 / 29.4 fps（`live2d-core.js:947` 定时器周期 `Math.round(1000/60)=17 ms`；`frame-pacing.js:56-63`） | 分数累加器 `acc += 30 / renderFps; if acc >= 1 { capture; acc -= 1 }`（D.5）；§3.4、T2 |
| platform M2 | synth「hide-all → `win.hide()` → `document.hidden=true` → 无 postrender」「父页 `visibilitychange` → `state{hidden}`」 | Pet 窗 `backgroundThrottling:false`（`window-manager.js:1009`）时 Electron 官方文档明说 visibility 恒 `visible`（ https://www.electronjs.org/docs/latest/api/browser-window ）；`live2d-core.js:955-956` 注释同义；`screen-capture-ipc.js:1488-1491 / :1705-1708` 记录隐藏后 renderer 被拖到秒级 | 删 `visibilitychange` 分支；取帧是否停止以「postrender 是否还来」为唯一判据，1 s 无 postrender 推导 `state{hidden}`（裁决 §I）；§3.11、T10 |
| platform M3 | synth「LiveKit `simulcast:false, videoCodec:'vp9'` 即单层」 | 缺 `scalabilityMode` 时 SDK 默认 `L3T3_KEY` 三层空间 SVC（ https://raw.githubusercontent.com/livekit/client-sdk-js/main/src/room/participant/LocalParticipant.ts `opts.scalabilityMode ?? 'L3T3_KEY'`） | 显式 `scalabilityMode:'L1T1'`（D.3）；T8 看 `encodings.length === 1` |
| platform M4 / code F5 / product F-14 | synth OD-11 v2 ⑤「关机 `leave{shutdown}` + iframe `exitRoom()` ≤1 s」 | Electron `requestAppQuit` 先 `destroyAllWindows()` 再 `beginOwnedBackendShutdown()`（`backend-runtime.js:2483-2490`），`destroy()` 不触发 unload，后端 `on_shutdown` 跑时 iframe 已不存在 | 关机只做 spool fsync + state.json + 释放 takeover ≤3 s，对端 30 s 后才知（E）；PC「销毁窗口前先发 leave」列可选 follow-up；OD-11 v2 |
| platform M4 | synth「后端重启 outbox 落盘回放不丢不重」 | 凭证 / 票据 / 隔离 LLM 会话 / 仲裁状态都不落盘，回放无从投递 | 后端重启 = 这场结束，下次启动只做 spool 补录（E） |
| platform M4 / product F-12 | synth OD-11 v2 ②「`ping` 每 5 s，连续 10 s 未到再起 30 s deadline」 | 最坏 40 s 才判死，与「30 s 判死」不符；自身重连 30 s == 对端判死 30 s 是竞态 | 单判据 `peer_last_seen > 30 s`；自身重连 25 s（比 30 s 短 5 s）；页面宽限 20 s（E） |
| platform m2 | synth「TRTC 超限时 `sendCustomMessage` reject」；只有 5 KB/s 字节桶 | 官方中英文档均未写超限行为（ https://web.sdk.qcloud.com/trtc/webrtc/v5/doc/en/TRTC.html#sendCustomMessage ）；按 synth 自己的类别上限最坏 26 calls/s + 一次 4 KB text 重传 5 片 = 31 > 30 | 并列条数桶 20 条/s（桶 10）；超限排队不丢；最坏速率表按 B.2 重算（≤30 条/s、≤8 KB/s）；T6 加「1 s 内连发 40 条 100 B」 |
| platform m3 | synth 拥塞阶梯最低档「192×272（bitrate 260）……档位不变」 | 260 kbps 低于 TRTC 标清码率带下限 300（ https://cloud.tencent.com/document/product/647/44248 ） | 最低档改 300 kbps（D.7）；OD-06 v2 |
| platform m4 | synth「拥塞阶梯只缩裁剪不动 fps，由我控制」 | libwebrtc 在 MAINTAIN_FRAMERATE 下 QP 质量缩放器仍会因高 QP 主动降分辨率；560 kbps ÷ (286,720 px × 30) ≈ 0.065 bpp 偏低 | 注明「libwebrtc 也可能自行降分辨率，接收端以 videoWidth/Height 观测」；T6/T8 记 `qualityLimitationReason`（D.7） |
| platform m5 | synth「父页 `renderer.on('postrender')` 每次都取帧」 | avatar-portrait 的 `renderer.render(tempStage)`（`avatar-portrait.js:1226 / :1256`）与 `generateTexture` 也触发 postrender，会抓到错位帧 / 陈旧后备缓冲 | 只在 `renderer.lastObjectRendered === pixi_app.stage` 时取帧（D.6） |
| platform m6 / product F-15 | synth「270 MB = 0.264 GiB × $0.12 = $0.032/房·小时；盈亏点 ≈2,580」 | 270 MB = 0.2515 GiB（0.264 是 270 MiB）；$0.030；盈亏点两报告各算 ≈2,517 / ≈2,700 | 一律用 MB/GB 十进制，GiB 只在引用 GCP 报价时出现并注明换算（D.8）；结论「月 >≈2,500 房·小时切 GCP」不变；§3.5.8 |
| platform m7 / product 跨区 | synth「跨区默认允许 + 警告，T9 后再决定是否 403」 | 大陆文档只有「<300 ms」营销句无海外节点表；国际站账号隔离；LiveKit Cloud 无大陆 / HK；GCP 无大陆区域 | 首发 fail-closed：两侧区域不同 → Servers 403 `cross_region_unsupported`，T9 后由 owner 决定是否翻成「允许 + 警告」（D.2） |
| product F-03 | synth「两跳鉴权都不可假冒」 | `POST /api/visit/credentials` 不校 `visit_id` 归属；任何登录账号可领任意房 guest 凭证进房旁听（TRTC `sendCustomMessage` 全房广播，房容量 300） | host 领凭证时登记 `visit_id` 并返回一次性 `invite_code`（10 min）；guest 必带；每房 host + guest 各一（C.5）；OD-01 v2、§3.8 |
| product F-07 | synth OD-27「版本偏斜为零」 | 只对单机（壳 / 页面 / 后端）成立；A/B 两台 Xiao8 版本偏斜零规则，d4 §2.5 把未知消息判成 `peer_protocol_violation` finalize | 未知 `t` 一律忽略并计数（恢复 v1 规则）；`hello.caps.proto` 主版本不同 → `leave{proto_mismatch}`；连续 20 条异常才 finalize（B.3） |
| product F-13 | d4「host 等 guest 告别 ≤12 s（8 s LLM + 开口）」 | guest 的 `wu:true` 行只在末句开播时发，两分句告别 ≈10.8 s、三分句即 >12 s → host 提前送客掐断告别 | 步超时 15 s 以「对方告别行第一片到达」为止，告别行本身正常播完；硬顶 45 s（F.1）；OD-08 v2 |
| product F-17 | synth 分片信封 `p` 是字符串，内层 JSON 引号逐个转义；d4 `line_delta` 整条 ≤900 B | 900 B delta 套信封后 985~995 B 逼近 TRTC 1 KB（1000 还是 1024 未文档化） | 每片总长 ≤1000 B 按字节明写；`txt` ≤800 B 是为转义膨胀留的余量；单测断言「最长合法 text 分片后每片 ≤1000 B」（B.2） |
| product F-18 | d5 OD-16「≈3 人日、零 React 改动」 | 少算置灰通道（已有 `react-chat-window:update-message`，`resize-drag-and-api.js:442`）、chat.html 三上下文加载、8 locale × 13 条文案 | ≈4 人日（估算），仍算小 PR；OD-16 v2、§5 |
| product F-20 | d4 逐分句 TTS 未评估请求配额 | 一场 40 句 × 3 分句 ≈120 次请求，按次限流的 provider 可能触顶 | 写进 OD-15 v2 风险；兜底 = 该分句 4 s 未开播按文本估时转发（F.4） |
| product F-21 | synth §3.3(c)「A 挂 postrender 即 publish」 | 未钉在 host 接待确认之后；LiveKit `autoSubscribe:true` 下 B 已收轨 | guest 收到 host `ready` 后才 publish；LiveKit `autoSubscribe:false`，`ready` 后 `setSubscribed(true)`（裁决 §I minor） |
| product F-06 | synth §5 只列 vendor 成本 | 用户自付：LLM ≈240k input token/侧/场（40 句 × ≈6k），TTS 每句 2~4 次请求 | 成本节增「用户自付部分」（F.5） |
| product F-16 | 威胁模型无「IP」行 | TRTC / LiveKit 都是 SFU，对端拿不到你的 IP；Servers 以来源 IP 复核区域所以 Servers 知道 | 写一句进 OD-01 v2 收益 / 风险（C.10）；§3.8 |
| product F-19 | d5 OD-17「`scope:'all'` 后 `state.json{peer_sub, pair_id}` 留 7 天」 | 与「删名册项」不对偶；另：spool 不进 Steam 云存档（只拷 `MANAGED_MEMORY_FILENAMES`）两稿都没提 | 对端撤销 `scope:'all'` 时 `state.json` 的 `peer_uid/pair_id` 一并删；OD-17 v2 加「不进云存档」一句（G.3） |

#### A.2.3 三份核验报告 blocker / major 逐条处置

处置列的字母编号指 A.2.4 裁决摘要（reconcile_directives §A~§H）；「同」表示与上一条是同一问题的另一镜头。

| 报告 | 编号 | 严重度 | 问题 | 处置（裁决） | 落点章节 |
|---|---|---|---|---|---|
| verify_code | F1 | blocker | 排序原语：synth host 定序 `order` + `ack{seq, order, stale}` vs d4 Lamport `lp`，两稿互相否定 | 采 d4 Lamport：每条消息带 `lp`，`next_lp = max(own, max_seen) + 1`，平局 host < guest，`reply_to` 定陈旧与打断；`order` 字段整体从协议删除（B.1） | §3.5、§3.6.3、OD-30、§4 |
| verify_code | F2 | blocker | 数据通道消息集：synth `text{final}` 全文必达 vs d4 `line{n,h}` + `line_req` 补洞，两套 wire 不兼容 | 采 synth「text{final} 全文必达」模型 + d4 字段名：`line_delta`（可丢，只上屏）/ `text`（必达，一行永远以它收口，被打断 `truncated:true`）/ `line_abort`（提示）；删 `line{n,h}` / `line_req` / CRC；心跳统一 `hb`；cmd 1/2/3 消息集定死（B.2） | §3.5、§4、OD-21 v2、OD-30 |
| verify_code | F3 | major | 身份票 TTL / claims / 主键：synth 2 h + HMAC `visit_uid` + `hello` vs d5 40 min + 裸 uuid + `identity` | claims 统一 `{v, iss, aud, kid, sub=visit_uid, vid, visit_id, role, transport, char_tag, display_name?, iat, exp, jti}`；TTL 40 min；`visit_uid` = HMAC 派生；`vid = role[0]+'_'+sha256(visit_uid|visit_id)[:24]`；首包 `hello`；公钥 `GET /api/visit/pubkeys`（C.1~C.4、C.8） | OD-01 v2、OD-05 v2、§3.8、§4 Servers 契约 |
| verify_code | F4 | major | 对方亲人 subject：synth `participant` 人级跨对 vs d5 `group_participant` 按对 | `participant('neko_visit', peer_uid)` 人级跨对（owner 本意）；标题表按 `(subject_kind, platform)` 选（G.1） | OD-05 v2、§3.7 |
| verify_code | F5 | major | 关机：synth 声称 `on_shutdown` 能经数据通道发 `leave{shutdown}`，但 Electron 先销毁窗口 | 按 d5：关机只 fsync spool + state.json + 释放 takeover ≤3 s；对端 30 s 后知；PC 侧「销毁窗口前先发 leave」列可选 follow-up（E） | OD-11 v2、§3.11、§5 PC follow-up |
| verify_code | F6 | major | 页面重载宽限：synth 30 s（transport WS 断）vs d5 20 s（display socket 断） | 20 s，以 transport WS 断开起算（display socket 可能被 Chat 窗顶替，`websocket_router.py:547-556`）（E） | OD-11 v2、§3.11 |
| verify_code | F7 | major | synth §0.3 / PR-13 引用 d4 已删除的 speakerGainNode 静音方案 | 删；单一 `visitVoiceEnabled`（F.2） | OD-15 v2、§5 PR-13 |
| verify_platform | B1 | blocker | 同 code F1 + F2（线协议互斥六维：排序 / 可靠层 / 消息集 / 限速 / 分片 / is_stale） | 同上（B.1、B.2）；限速取一套：字节桶 5 KB/s + 条数桶 20 条/s（桶 10）+ delta 同行 250 ms 合并 | §3.5、§4 |
| verify_platform | M1 | major | ≥33 ms 抽样门在 75 / 144 / 165 Hz 或定时器 60 fps 模式下只有 25~29.4 fps | 分数累加器（D.5），不强制降本地渲染 | §3.4、T2 |
| verify_platform | M2 | major | hide-all 依赖 `document.hidden=true`，但 `backgroundThrottling:false` 下 visibility 恒 `visible` | 删 `visibilitychange` 分支；「有帧就发，没帧 B 显示最后一帧半透明」；1 Hz `state{hidden}` 由「1 s 无 postrender」推导（§I） | §3.11、T10 |
| verify_platform | M3 | major | LiveKit vp9 缺 `scalabilityMode` → 默认 `L3T3_KEY` 三层 SVC | `scalabilityMode:'L1T1'`（D.3）；VP9 软编 CPU 超阈值（编码 fps <27 持续 10 s）→ 下次串门 vp8 | OD-02 v2、§3.5、T8 |
| verify_platform | M4 | major | 三种断裂恢复路径三稿不一致（页面宽限 30/20 s；判死 40/30 s；自重连 30 == 判死 30；后端重启回放 vs 结束） | 一张活性表：`hb` 5 s；`peer_last_seen > 30 s` → peer_lost；自重连 25 s；页面宽限 20 s；vendor 显式离开立即 finalize、超时类事件交心跳时钟；后端重启 = 结束（E） | OD-11 v2、§3.11 |
| verify_platform | M5 | major | 身份 / 记忆主键两稿不同（同 code F3 / F4） | 同上（C、G.1） | OD-01 v2、OD-05 v2 |
| verify_platform | M6 | major | 关机 `leave{shutdown}` 发不出（同 code F5） | 同上（E） | OD-11 v2 |
| verify_product | F-01 | blocker | 文本通道契约三稿两套（同 code F1 / F2） | 同上（B） | §3.5、§4 |
| verify_product | F-02 | blocker | 身份契约多处矛盾（TTL / 首包 / sub / vid / subject / 容差 / 宽限 / 关机预算 / 判死 / spool 目录 / debrief 写路径 / 公钥端点） | 同上（C）；spool 目录 `config_dir/visit_spool/`；debrief 写路径按 d5（G.2、G.3） | OD-01 v2、OD-05 v2、OD-11 v2、OD-16 v2、OD-17 v2 |
| verify_product | F-03 | major | Servers 凭证端点不绑房：任何登录账号可为任意 `visit_id` 领 guest 凭证进房旁听 | host 领凭证时 Servers 登记 `visit_id` 并返回一次性 `invite_code`（10 min）；guest 必带；每房 host + guest 各一；第三者领不到（C.5） | OD-01 v2、§3.8、§4 |
| verify_product | F-04 | major | 封禁对在飞会话不闭环；2 h TTL 让被封账号继续持有有效凭证；vendor 踢人 API 其实存在 | TTL 40 min；Servers `POST /admin/visit/bans` 拒发新凭证；在飞踢人（TRTC `RemoveUserByStrRoomId` / LiveKit `RoomService.RemoveParticipant`）标 Servers 侧 follow-up 不阻塞 v1；客户端黑名单 hello 阶段拒；举报 `POST /api/visit/reports`（C.6） | OD-01 v2、§3.8、§5 Servers |
| verify_product | F-05 | major | 无服务端可强制的账单上限：2 h × 每日 50 次 = 每账号每天最多 25 h 免费标清 | Servers 按账号记「每日签发分钟数 = 签发次数 × 30 min」；免费档 `VISIT_FREE_MINUTES_PER_DAY` 占位 120，由 owner 定价时拍板；每账号并发房 ≤2；付费档只改 entitlement（C.7） | OD-06 v2、§3.5.8 |
| verify_product | F-06 | major | 用户侧 LLM / TTS 成本不在成本节 | 成本节增「用户自付部分」：LLM ≈240k input token/侧/场（估算），TTS 每句 2~4 次请求（F.5） | §3.5.8 |
| verify_product | F-07 | major | 对端版本偏斜零规则；d4 把未知消息判成违约 finalize | 未知 `t` 忽略并计数；未知字段忽略；`hello.caps.proto` 主版本不同 → `leave{proto_mismatch}` + 提示升级；>900 B / `i` 跳变 / 两行交叠只丢弃计数，连续 20 条异常才 finalize（B.3） | §3.5、§4、§3.11 |
| verify_product | F-08 | major | 能力门与领凭证顺序自相矛盾；`POST /api/visit/rooms` 的同步 409 首次不可能给出；`acquire_takeover` 在 Servers 503 时释放未写 | 建房 / 入房时先建 iframe → 能力门 → 通过后才向 Servers 领凭证；失败 → 409 `VISIT_UNSUPPORTED_ON_THIS_MACHINE`，不消耗配额、不占 takeover（D.4） | §3.2、§3.3 |
| verify_product | F-09 | major | OD-16 超时默认 `key_points`、崩溃补录直接记要点，都是「不问就写记忆」 | `VISIT_DEBRIEF_DEFAULT='ask_later'`：芯片保留可点，spool 保留 7 天，启动补录只重新弹芯片 + status；串门记忆区 digest 与 debrief 无关，只受 `visitMemoryEnabled` 与对端 consent 控制（G.2） | OD-16 v2、OD-17 v2 |
| verify_product | F-10 | major | OD-10 issue 草稿总开关默认 True 会打断现网铸卡（铸卡今天直接读 facts.json） | 全局「完全隔离亲人记忆」开关默认 False（opt-in）；筛除接口不改变现网铸卡结果（可选前置过滤）；老数据回填走后台任务不进启动链路（G.4） | OD-10 issue 草稿、§3.13 |
| verify_product | F-11 | major | 「串门本地静音」开关一稿保留一稿删除；「猫娘不在家却出声」未正面回答 | 删静音开关；产品说明一句「她在邻居家说话，你在自家听见，像开着免提」+ `.visiting-away` 徽标；备选「A 的 TTS 当音频轨随视频发给 B」列 v1.5 评估（TRTC 下零增量计费；代价 ≈32 kbps 与克隆音色出机）（F.2） | OD-15 v2 |
| verify_product | F-12 | major | OD-09 / OD-11 仍一句多意；OD-11 标题「唯一时钟是心跳」与「显式离开立即结束」矛盾；synth ② 实为 40 s | 用 d5 人话骨架重写并再拆到一句一个意思；标题改「30 s 判死；显式离开立即结束」；OD-09「两枚章」改直白句（G.5、E） | OD-09 v2、OD-11 v2 |
| verify_product | F-13 | major | 收尾步超时 12 s 与 TTS 优先节拍不可组合，host 可能掐断 guest 的告别 | `VISIT_WRAP_UP_STEP_S=15`（从 begin 到对方告别行第一片到达），告别行按正常播放走完；`VISIT_WRAP_UP_MAX_S=45`；告别提示词 ≤40 字、最多两个分句（F.1） | OD-08 v2、§3.6.3 |

minor 全部按核验报告给的 fix 采纳（裁决 §I），汇总如下：

| 报告 | 编号 | 问题 → 处置 |
|---|---|---|
| verify_code | F8 | LiveKit 重连总时长两稿都错 → 「约 44 s + 抖动」 |
| verify_code | F9 | 社区基址行号错 → `card_drop_router.py:41` / `utils/social_base.py:12` |
| verify_code | F10 | d4 `turn.py` 行号漂移 → `:2239 / :2254-2257 / :2261-2262` |
| verify_code | F11 | `_locale.py:68`、`FullChatSurface.tsx:3088`、hotkey-manager `:433 / :809-820 / :894`、main.js `:557 / :563-566`、`group_participant` 文件 7 个、TRTC changelog Electron 条目标不确定 |
| verify_code | F12 | 先例不 fsync；fsync 是新增；目录统一 `config_dir/visit_spool/` |
| verify_code | F13 | Servers 契约零散字段名 → 随 C 收口成一张表 |
| verify_platform | m1 | 同 code F8 |
| verify_platform | m2 | TRTC 超限行为未文档化；加条数桶；`text` 全文重复的字节代价按 B.2 重算并接受 |
| verify_platform | m3 | 拥塞阶梯最低档 260 → 300 kbps |
| verify_platform | m4 | libwebrtc QP 缩放器 → 写进 OD-06 v2 风险与 T6；接收端以 videoWidth/Height 观测 |
| verify_platform | m5 | postrender 过滤 `lastObjectRendered === pixi_app.stage` |
| verify_platform | m6 | MB / GiB 混用 → 一律 MB/GB 十进制 |
| verify_platform | m7 | 跨区无证据 → 首发 403 `cross_region_unsupported` |
| verify_product | F-14 | 同 code F5（关机） |
| verify_product | F-15 | 同 platform m6 |
| verify_product | F-16 | 威胁模型加 IP 一句：SFU 中转对端不可见；vendor 与 Servers 可见；不做 P2P |
| verify_product | F-17 | 分片按字节 ≤1000 B；`txt` ≤800 B 余量；单测断言 |
| verify_product | F-18 | OD-16 估算 ≈4 人日；置灰走 `react-chat-window:update-message`；chat.html 三上下文都加载监听 |
| verify_product | F-19 | `scope:'all'` 时 `state.json` 的 `peer_uid/pair_id` 一并删；写「spool 不进 Steam 云存档」 |
| verify_product | F-20 | TTS ≈120 次请求/场 → OD-15 v2 风险 + 4 s 兜底 |
| verify_product | F-21 | guest 收到 host `ready` 后才 publish；LiveKit `autoSubscribe:false` |
| verify_product | F-22 | 同 code F11（changelog 版本号） |

#### A.2.4 主会话裁决摘要（reconcile_directives §A~§H，2026-09-26）

- **A 总体基底**：基底 = 评审赢家设计 2（同源 iframe 承载 vendor SDK 与访客图层，lanlan_frd 零改动）；大陆 TRTC，海外 LiveKit（上线期 Cloud Ship，月 >≈2,500 房·小时后切 GCP 自建，GCP 是稳态目标）。v1 保留 OD-03 主体 / OD-04 三 subject / OD-10 / 13 / 18 / 19 / 22 / 23 / 24 / 25 / 26；OD-13 / 24 / 25 标「已拍板」，OD-15 / 21 默认值、OD-08 收尾、OD-11 30 s 标「owner 已定方向，细节待拍板」。编号沿 v1 26 条，被替换的写「OD-xx v2」；新增 OD-27 同源 iframe、OD-28 vendor SDK 随包分发、OD-29 iframe↔后端独立 WS、OD-30 数据通道 + outbox + Lamport 定序、OD-31 `memory/scoped_client.py` 上提（原 d5 的 OD-27 改号）；debrief 并入 OD-16 v2，敏感记忆筛除 issue 并入 OD-10。
- **B 线协议与排序**：全序与陈旧判定采 d4 Lamport `lp` + `reply_to`（平局 host < guest；开场 `rt==""` 并存豁免；撞车 guest 让一次），删 synth 的 host `order` / `ack{seq, order, stale}`。可靠单元采 synth「`text{final}` 全文必达」，删 d4 `line{n,h}` / `line_req` / CRC；`line_delta` 可丢只上屏；后端 `VisitOutbox` 单调 `seq` + 累计 `ack{seq}` + 1→2→4→8→8 s 重传 + `ln`/`seq` 幂等 LRU(512)，落 `config_dir/visit_spool/<visit_id>.outbox.jsonl`。心跳 `hb`（cmd 1，5 s）。消息集：cmd 1 ctl = hello / ready / ack / hb / state / consent / wrap_up / leave；cmd 2 text = line_delta / text / line_abort；cmd 3 lossy = typing / stats。分片信封 `{v, r, m, i, n, p}` 每片 ≤1000 B（按字节）；限速 5 KB/s 字节桶 + 20 条/s 条数桶 + 250 ms 合并，超限排队不丢。版本偏斜：未知 `t` 忽略计数，`proto` 主版本不同 → `leave{proto_mismatch}`，连续 20 条异常才 finalize。
- **C 身份、凭证、房间安全**：`visit_uid = HMAC(server_secret, community_uuid)[:24]`（不用裸 uuid），记忆 / 黑名单 / 名册 / 举报全部以它为主键，UI 只显 display_name 与 6 位短码。票据 claims 统一、Ed25519、TTL 40 min、时钟容差 ±300 s、同房同 `vid` 允许重放同一 jti；`vid = role[0]+'_'+sha256(visit_uid|visit_id)[:24]`。首包 `hello{ticket, caps{video, tier, proto:1, app_version}, lang}`，核验顺序验签 → aud/visit_id/role/exp → `vid == vendor 盖的发送者 id` → `sub ∉ 黑名单`；通过前不订阅视频、不接受 text、host 不弹接待确认。房间绑定：host 领凭证时 Servers 登记 `visit_id` 并返一次性 `invite_code`（10 min），guest 必带，每房 host + guest 各一。封禁闭环：`POST /admin/visit/bans` 拒发新凭证；在飞踢人（TRTC / LiveKit 服务端 API 均已核实存在）标 Servers 侧 follow-up；客户端黑名单 hello 阶段拒；举报 `POST /api/visit/reports`。免费额度：每日签发分钟数 = 次数 × 30 min，`VISIT_FREE_MINUTES_PER_DAY` 占位 120 由 owner 定价拍板，并发房 ≤2。公钥 `GET /api/visit/pubkeys` + 内置 `VISIT_SERVERS_PUBKEYS`，kid 不命中且拉不到 → fail closed。PSK 产品路径删除，开发环回 `NEKO_VISIT_DEV_KEYFILE` + `scripts/visit_dev_mint.py`。IP：SFU 下对端拿不到你的 IP，Servers 知道。
- **D 传输与区域**：transport 由 Servers 在 host 领凭证时按 host 区域决定，guest 拿同一 transport，`region_hint` 只判 `cross_region`。跨区默认 fail-closed（403 `cross_region_unsupported`），T9 实测后由 owner 决定是否改「允许 + 警告」。LiveKit 发布参数 `vp9 / simulcast:false / scalabilityMode:'L1T1' / maxBitrate 560_000 / maxFramerate 30 / maintain-framerate`，VP9 软编 CPU 超阈值下次 vp8。能力门顺序：先建 iframe → 能力门 → 再领凭证。30 fps 采样用分数累加器；postrender 只在 `lastObjectRendered === pixi_app.stage` 时取帧。TRTC 无 degradationPreference API 与 libwebrtc QP 缩放器写进 OD-06 v2 风险与 T6；阶梯最低档 300 kbps。单位一律 MB/GB；LiveKit 重连「约 44 s + 抖动」。
- **E 生命周期（OD-11 v2）**：`hb` 5 s；对端任何消息刷新 `peer_last_seen`，超 30 s → `finalize('peer_lost')`；自己掉线 SDK 重连 + 25 s 计时，恢复则 outbox 重发，否则 `finalize('relay_lost')`；vendor 显式离开事件立即 `finalize('peer_left')`，超时类事件交心跳时钟；正常结束先发 `leave{reason}`；Pet 页刷新 / iframe 消失后端保留 20 s，新页面 `GET /api/visit/state` → 重建 iframe → 同凭证重入房 → 重发 hello（同 jti）→ outbox 重发；后端重启 = 这场结束，下次只做 spool 补录；关机：Electron 先销毁窗口（`backend-runtime.js:2483-2490`），`leave` 发不出，`on_shutdown` 最前 `await stop_all('shutdown')` ≤3 s（spool fsync + state.json + 释放 takeover），对端 30 s 后才知；PC「销毁窗口前先发 leave」可选 follow-up。常量 `VISIT_HEARTBEAT_S=5 / VISIT_PEER_LOST_S=30 / VISIT_SELF_RECONNECT_S=25 / VISIT_LOCAL_PAGE_GRACE_S=20 / VISIT_SHUTDOWN_BUDGET_S=3`。
- **F 对话轴（d4 为主）**：OD-08 一句话规则沿 d4 §4.1；收尾超时改 `VISIT_WRAP_UP_STEP_S=15`（到对方告别行第一片）、`VISIT_WRAP_UP_MAX_S=45`，告别提示词 ≤40 字两分句。OD-15 单一 `visitVoiceEnabled`（默认 true），删「本地静音但保留 RMS」，产品说明「免提」，备选「音轨随视频」v1.5。OD-21 默认开、`line_delta` + `text{final}`，`VISIT_STREAM_DELTAS=False` 留作紧急开关。TTS ≈120 次/场风险 + 4 s 兜底写进 OD-15 v2。用户侧成本 LLM ≈240k input token/侧/场、TTS 每句 2~4 次写进成本节。
- **G 记忆轴（d5 为主）**：OD-05 v2 对方亲人 `participant('neko_visit', peer_uid)`、对方猫娘 `group_participant('neko_visit', pair_id, peer_char_id)`、这一对 `group_chat('neko_visit', pair_id)`，`pair_id = sha256(min|max)[:24]`，`peer_char_id = 'c_' + sha256(peer_uid|char_tag)[:24]`；标题表按 `(subject_kind, platform)`；名册 / 黑名单主键 `visit_uid`。OD-16 v2 debrief 沿 d5 但超时与崩溃默认 `ask_later`，串门记忆区 digest 与 debrief 选择无关，做进本次交付小 PR（≈4 人日估算），置灰走 `react-chat-window:update-message`。OD-17 v2 沿 d5（每句立即追加、fsync 30 s + finalize、结束 digest 一次、10 min 周期默认关），目录 `config_dir/visit_spool/`，`scope:'all'` 时 `state.json` 的 `peer_uid/pair_id` 一并删，spool 不进 Steam 云存档。OD-10 issue：全局隔离开关默认 False，筛除不改现网铸卡，回填走后台。OD-09 v2 / OD-11 v2 人话骨架再拆一句一意。`mirror_meta.is_mirror_event_memory_disabled` 加显式 `memory_enabled` 键。OD-31 `memory/scoped_client.py` 上提，QQ 本次不切换。
- **H OD-03 问答**：直接采 reuse_paths §6 作 OD-03 v2「补充问答」（≤12 句），§3.10 对照表加两行：QQ 群聊路径只复用记忆层（subject 三形态 / scoped_history 双形态 / 接收边界章 / `name(id)` 标签 / 分批结算），上提为 `memory/scoped_client.py`；game 路径借 takeover / 劫持点 / 隔离会话 / mirror / finalize 骨架，泛化为注册表（新建机制）。

#### A.2.5 核验报告的 fix 与裁决不一致之处（实施时以裁决为准）

核验报告给的 fix 是建议，裁决在以下几处另取了方案；列出是为了避免实施者把核验报告当规范：

| 核验建议 | 裁决 | 为什么 |
|---|---|---|
| platform B1：以 d4 `line{n,h}` + `line_req` 补洞为 wire，outbox 以 `ln` 为键、按行 ack | B.2：以 synth `text{final}` 全文必达为可靠单元，删补洞；outbox 以 `seq` 累计 ack | 接收侧不需要补洞状态机；被打断的行天然 `truncated:true` + 已开口前缀，同时满足 d4 §2.6「已说出的入史、未说出的不入史」；字节 2× 的代价在 8 KB/s 内（B.2 重算 ≤8 KB/s） |
| platform M1 (a)：串门期间无条件 `setTargetFPS(30)` | D.5：分数累加器（M1 的 (b) 方案） | 不降用户本地渲染帧率；任何 ≥30 fps 源平均恰好 30 fps |
| platform m2：24 calls/s 次数桶 | B.2：20 条/s（桶 10） | 与 d4 §2.5 已有的 20 条/s 桶一致，留 10 条/s 余量给重传 |
| product F-05：`expires_at = min(now + 剩余额度, now + 40 min)` 逐场扣减 | C.7：按「签发次数 × 30 min」记每日分钟数，TTL 固定 40 min | 计额与 TTL 解耦，重连不需要重领凭证；付费档只改 entitlement |
| product F-07：`hello` 带 `proto:{min, max}` 取交集 | B.3：`hello.caps.proto:1` 主版本比较 + 未知 `t` / 字段一律忽略 | v1 首发只有一个主版本；未知忽略已覆盖小版本演进 |
| product F-08：Pet 页加载即建 1×1 探测 iframe 缓存 caps | D.4：建房 / 入房时先建 iframe → 能力门 → 再领凭证 | 不常驻探测 iframe；能力门失败 409 同样不耗配额不占 takeover |
| product F-09：`VISIT_DEBRIEF_DEFAULT='forget'`（超时删 spool） | G.2：`'ask_later'`（spool 保留 7 天、芯片可点、启动只重弹芯片） | owner 要的是「询问」；超时删除会让忘了点芯片的用户丢掉本场 |
| code F3：以 d5 的 Servers 契约表为唯一权威 | C：混取——synth 的 HMAC `visit_uid` / `hello` / 26 字符 `vid`，d5 的 40 min / ±300 s / `config_dir` / `/api/visit/pubkeys` | 隐私（对端拿不到裸 uuid）与账单上限（TTL 短）各取更好的一边 |
| product F-03 (c)：LiveKit 侧同时下发 `room.max_participants: 2` | C.5 只写 Servers 侧「每房 host + guest 各一」 | 裁决未点名 vendor 侧参数；可作 Servers 实施细节，列 §3.13 |

### A.3 v3 定稿记录（2026-09-30 逐条拍板）

#### A.3.0 过程

- v2 成稿后 owner 逐条复核 31 项。分组：OD-13 / 24 / 25 在 2026-09-26 已同意且 v2 未改文字，不再重问；OD-08 / 11 / 15 / 21 只拍具体数字；OD-01 / 03 / 05 / 09 / 10 / 16 / 17 确认 v2 是否按第二轮意见改对；视频与传输 10 项、其余 7 项各一轮。每项按「现状 / 改成什么 / 回归风险 / 收益」四段讲给 owner，技术项打包确认。
- 结果：31 项全部拍板；OD-15、16、21、26、31 有实质改动（标题标「v3」），其余按 v2 推荐采纳，部分在「推荐」末尾补了 owner 的附加说明（OD-06 免费额度保持占位、OD-09「记住串门内容」默认关、OD-12 跨区 T9 后再定、OD-19 只要求标明来源）。

#### A.3.1 实质改动

| 编号 | v2 | v3（owner 拍板） | 起因 |
|---|---|---|---|
| OD-15 / OD-21 | LLM 整行生成完 → 切分句 → 每个分句单独一次 `mirror_assistant_speech`（新 speech_id）→ 前端 `visit_clause_playing` 回报后放出该片字幕；打断「说完当前分句再停」 | 一行一个 speech_id，LLM 边生成边经新增 `open_mirror_speech_stream` 推进 TTS（与主聊天同一条推流路径，ws_bistream 流式双工 / http_sentence 在 worker 内切句）；本地 TTS 输入不过出站清洗；发给对方的文字用增量分句器切片、逐片清洗、按 `visit_speech_progress` 的已播音频时长与估时对齐放出；打断立即停，已开口前缀 = 已放出分片 | owner 指出 TTS 一直是流式双工，按分句拆请求既打断语调又多出约 3 倍请求；复核发现 v2 的首字延迟估算（按首 token 算）与它自己的「整行生成后再切」步骤自相矛盾 |
| OD-15 | 「本地静音但保留 RMS」开关删除 | 维持删除 | owner 追问原因后确认：该开关照付 TTS 额度，系统 / 应用静音已能达到同样效果（`speakerGainNode` 在 analyser 之后） |
| OD-16 | 三个芯片「记成日记 / 只记要点 / 不记」 | 两个芯片「记成日记 / 不记」；要点写入路径删除 | 日记与要点的区别（叙事 vs 事实、要点另写串门区）对用户不直观 |
| OD-26 | 出门确认框显示预计 token / TTS 消耗；转录只在本机可导出 | 确认框不出现技术数字；结束后藏得较深的「查看详情」显示时长、token / TTS 消耗与完整转录，数据来自云端；转录每场上传 Servers、长期保留（与账单同期）、只在隐私政策披露；对端撤销不删云端副本 | owner：不给用户看技术细节，但账单与记录要可查、要上云。核对事实：现有遥测只上报 counter / histogram，event 通道从不上传，所以转录上云是新增的内容上传通道 |
| OD-31 | 把 QQ 插件 `memory_bridge` 五个 scoped 方法逐字节上提为 `memory/scoped_client.py`，QQ 本次不切换 | 串门自建 `memory/scoped_client.py`，直接对 memory_server 五个端点；bot 公共记忆组件的形态由 owner 与 QQ 插件作者商量后另定，串门不等 | QQ 自动回复插件已于 2026-09-28 移出仓库（#2996，`3618e75fe`），上提的源头不存在了 |

#### A.3.2 对 v2 事实的更正

- OD-03 补充问答引用的 QQ 插件文件与行号只在 `b0b283e34` 成立（插件已移出仓库）；结论不变，已在该段加注。
- OD-01 的追问「为什么 Servers 需要腾讯 / LiveKit 密钥」已答复并采纳：vendor 入房凭证（TRTC `UserSig`、LiveKit JWT）由账号级密钥签发、按分钟计费到我方账号，密钥进客户端即可被抠出伪造凭证、绕过封禁，所以只能留在 Servers 签发短期凭证。

#### A.3.3 按 main 刷新代码引用与新增接管面

- **行号刷新**：v2 的 946 处代码引用逐条在 `b0b283e34` 与 main 之间比对首尾行文本；439 处平移、18 处内容有变化（例如社区基址常量从 `main_logic/client_registration.py` 搬到 `utils/social_base.py`、`web_app.py` 新注册 watch_together / drawing_guess / plugin_card 三个 router、`LOCALE_VERSION` 换值、`submitCatLocalChatText` 改为返回 bool）、41 处指向已移出仓库的 QQ 插件（保留 `b0b283e34` 引用）、107 处闭源壳 / vendor 引用不在范围。基准现为 main `fd2df860e`。
- **一起看功能带来的新接管面**（v2 成稿后合入的 #3106 / #3118 / #3121 / #3141）：takeover 从两个属性变成三个（新增 `_takeover_callback_sink`），写入点从两处变成三处（新增 `_start_watch_speech_takeover` 失败回滚），新增 `interrupt_ordinary_speech_for_takeover`；takeover 期间主动搭话被整段拒发、插件 respond 回调交给 sink 扣住，v2「respond 回调被静音」的说法不再准确。PR-02 据此改为三属性令牌 + 回滚路径 + 串门挂 `VisitInbox` 作 sink（§5 总则 2a / 2b）。
- **v2 漏列的劫持点**：独立 ASR 语音消费者 `main_logic/voice_input/consumers/game.py` 不经 websocket_router 直接把转写送进游戏（`b0b283e34` 已存在）；另有 `activity/tracker.py` 的情境提示抑制与 #3118 斜杠指令入口。PR-01 注册表一并覆盖。测试里直接写或伪造 takeover 属性的文件从 7 个变成 10 个。

#### A.3.4 PR 评审修订（2026-09-30，Greptile / Codex 两轮）

- 可靠层：累计 ack 只推进到连续落地的最大序号，必达消息严格按 `seq` 顺序处理（缺口后先到的缓存，保证 `consent` 按序生效）；重传排完后每 8 s 继续，必达项在**已连接时间**里 30 s 未确认 → `delivery_failed`（重连 / 页面重载宽限期间暂停计时），`leave.reason` 同步加值；分片数以编码后字节为准，超 8 片截短并标 `wire_size`；合并后的 delta 序号在发送时连续重编。
- 生命周期：`peer_lost` 计时只在对端 `hello` 核验通过后启动（host 等邀请最多 600 s，guest 等 host 30 s）；能力门拆成「预检（领凭证前）+ SDK 检查（领凭证后）」解开互等；开播后 3 s 无进度的看门狗保证 `text{final}` 必发；插件回调的交还延后到回家仪式句与简述播完（20 s 硬顶）。
- 路由：注册表字段统一在 PR-01 定义（game 传 `route_voice_transcript`，visit 传齐 `on_page_signal`）；visit_router 子路由一律相对路径；`POST /api/visit/rooms` 统一为 202 异步。
- 身份与安全：确认框前先调邀请预览端点拿对方昵称 / 短码 / 跨区；举报对象由 Servers 从签发记录推导；LiveKit 按侧位收紧发布权限、Servers 订阅 webhook 与 TRTC 用量核对超档（新增实测 T13）；读取状态 / 转录 / 详情 / 名册 / 预览的本机端点一律过本机来源 + CSRF 校验，`invite_code` 不进 state 响应。
- 记忆与数据：先对累积缓冲脱敏再切片（亲人名不会跨片）；名册按本机角色分开；启动清理只删 outbox，转录与待传文件保留；待传转录与记忆开关无关一律临时存盘（owner 拍板）；并发名额只在 vendor 房间结束事件或 Servers 向 vendor 查询确认已离房后释放（上传转录只触发一次查询，不直接释放）；全身构图的尺寸与拥塞阶梯按构图推导。
- 「记成日记」（owner 拍板）：原稿说只写 `/cache` 就会被后台抽成长期事实，核对代码不成立（`signal_extraction.py:494` 跳过无用户消息的窗口）；改为日记进近期记忆，另抽 ≤3 条串门事实经新端点进 fact 层（`importance=4`、`absorbed=True`，永不进 reflection），铸卡排除这些事实；`memory/facts.py` 因此需要一处受控透传。

## 附录 B · 落选方案与评审要点

> B.1 是 v1（2026-09-11）四轴评审原文，逐字保留作判据存档。其中「视觉通道」「中继与协议」两轴的赢家（WebP-alpha 图片帧走 display socket、自建票据鉴权区域中继）在 v2 被整轴替换，替换后的三方案比选见 B.2；「对话机制」「记忆与安全」两轴的 v1 骨架（串门即 external route、零 schema group_chat + neko_visit）在 v2 沿用，v1 评审驳回的断言仍然有效。注意 B.1 与 B.2 里的「设计 1 / 2 / 3」各自独立编号，不是同一组方案。

### B.1 v1（2026-09-11）四轴落选方案与评审要点

#### B.1.1 视觉通道（v1）

**评审结论**：设计 1（现有 display socket 上走 WebP-alpha 图片帧 + 中继原样转发 + index.html 访客图层），嫁接：编码搬进 Worker（OffscreenCanvas.convertToBlob）、设计 3 的通用渲染后钩子对象与文本节拍口型驱动、设计 2 的 model-ready 重挂与裁剪滞回、设计 3 的 B→A 到达反馈作 v1.5 自动降档。

**三份设计的打分与理由**（正确性/约束契合/复杂度/回归/产品，1~10，复杂度与回归越低越好）：

- 设计 1：现有 display socket 上走 WebP-alpha 图片帧 + 中继原样转发 + index.html 访客图层: 正确8 约束8 复杂3 回归3 产品7 省流6
  几乎所有 file:line 都核实为真（postrender、定时器 tick、NEKO magic、Blob 分支、发送锁、preload 劫持、Chromium 146）。错在编码耗时低估 2× 与 PNG 回落体积，都可在合成稿修掉。零 Electron 改动、零新 HTTP 路由、opt-in、无新 rAF；只在两条热路径各加一个 if。带宽：说话峰值 45~65 KB/s、空闲 8~11 KB/s、均值 ≈20~27 KB/s，延迟 130~190 ms（国内）；不是最省也不是最快，但每帧独立可丢，中继与 B 端都不需要关键帧逻辑。产品：真实像素、表情自然带走、口型依赖 A 本地 TTS（可嫁接文本节拍驱动）。
- 设计 2：预渲染片段包 + 状态流为默认，WebCodecs 堆叠 alpha 实时档升级，HTTP 流式本地管道: 正确5 约束6 复杂9 回归5 产品4 省流9
  省流最狠（0.3 KB/s），但核心本地通路（fetch duplex 上传流到 HTTP/1.1 uvicorn）大概率不成立；MediaRecorder alpha 断言错；postrender 内重入 render、extract.pixels 预乘与否、合成口型写 mouthValue 都未核实。要新增 4 个 HTTP 路由、片段包落盘/保留期/中继寄存、i18n 4 键、机会式采集状态机——一条 PR 装不下。产品上 B 看到的是罐头循环、表情闭集 5 个、缺 talk 时 A 的模型要张嘴 3 秒「彩排」，与「A 的猫娘出现在 B 屏幕上」的保真目标相悖。
- 设计 3：GPU 堆叠 alpha + WebCodecs 硬编 + Worker 直连 443 中继: 正确6 约束6 复杂9 回归4 产品8 省流7
  结构判断对：Worker 作用域 WebSocket 不受 preload 劫持（grep 无 Worker 补丁）、localhost 可信源、Linux 兼容模式必软编。但运行时假设多且都未跑过：硬编可用、Worker 内 new VideoFrame(OffscreenCanvas) 零拷贝、每帧在 postrender 内重入 renderer.render 到 RT、同步 readPixels 1.4 MB 只要 1~2 ms。工程面：两个 Worker + 两套 WebGL2 shader + 编解码协商 + 拥塞控制 + 关键帧对齐丢帧的中继 + 票据体系，且媒体绕过 Python 意味着页面持有中继票据、中继轴接口被本轴改写。延迟最好（60~130 ms）、保真最高，但 v1 无空闲节流，L 档小时流量高于图片帧方案。适合 v2 升级，不适合 v1 骨架。

**评审驳回的断言**：

- [设计 1（minimal）] 单帧 WebP 编码耗时 192×256 ≈ 6 ms、240×320 ≈ 11 ms、360×480 ≈ 18 ms；hd 档主线程 ≈28%
  证据: 本次同一 libwebp（PIL 11.3）稳定复测中位数：192×256 q55 method4 = 13.6 ms、240×320 q65 = 19.5 ms、360×480 q75 = 39.7 ms（method0 分别 3.4/4.4/8.6 ms，但体积 +45%）。设计 1 低估约 2×；若 Chromium toBlob 走主线程且 method≥2，hd 档 15 fps 主线程会到 40~60%。结论：编码必须离主线程（见合成稿 §4），不能把「toBlob 在后台线程」当成未核实假设放过。
- [设计 1（minimal）] toBlob 回落 PNG 时「42 KB < 48 KB 仍在 lite 的 max_bytes 内」
  证据: 实测 192×256 RGBA PNG = 73.9 KB（alpha 覆盖 ~60% 的人形），240×320 = 104 KB；PNG 体积强依赖透明区占比，不能保证低于 48 KB。回落 PNG 必须同时降尺寸（max_h 160）并把 max_bytes 放宽到 3×，否则每帧都被 max_bytes 规则本地丢弃，B 端看到黑屏。
- [设计 1（minimal）与设计 2（frugal）] Chromium 的 MediaRecorder 不编码 alpha（设计 1 §4、§12；设计 2 §9「MediaRecorder…同样无 alpha」）
  证据: 平台事实（仓库内零用法，无法用代码核实，但与设计 3 §2.3 表格「Chromium 录 alpha 属无文档行为」一致）：Chromium 的 VideoTrackRecorder 对 I420A 帧（透明 canvas.captureStream 产出）用 VP8/VP9 编 alpha 平面并写入 WebM BlockAdditions，`<video>`/MSE 可回放。它不适合 v1 的真正原因是 timeslice+MSE 缓冲延迟 300~600 ms、关键帧不可控、无先例，而不是「丢 alpha」。WebCodecs VideoEncoder alpha:'keep' 在 Chromium 确实只剩 discard——这一条三份设计一致，未推翻。
- [设计 1（minimal）] catgirl_switched 会重建模型、可能重建 PIXI（live2d-core.js:293-308）
  证据: live2d-core.js:290-308 的 pixi_app.destroy 分支是「isInitialized 已置位但 pixi_app/stage 不存在」的自愈路径，不是角色切换路径；app-character.js handleCatgirlSwitch 关 socket 重载模型但不销毁 PIXI。stop() 仍应 renderer.off('postrender')，但理由是钩子生命周期，不是 PIXI 重建。
- [设计 2（frugal）] 本地上行用 `fetch(url,{body:ReadableStream,duplex:'half'})` 长连 chunked POST 到 uvicorn，`request.stream()` 逐块吐出
  证据: Chromium 的 fetch 上传流（Chrome 105+）限制为 HTTP/2 或 HTTP/3 连接（web.dev「Streaming requests with the fetch API」限制条款），uvicorn 只提供 HTTP/1.1；仓库唯一先例 voice_identity_router.py:104 读的是有界请求体。设计 2 自己也把它列为未核实并要求 60 分钟烟测。本地通路的核心假设大概率不成立，整条 uplink 需改为 WebSocket 或分块 POST。
- [设计 2（frugal）] 「不能复用 /ws/{name} 发二进制——页面侧 Blob 一律进 enqueueIncomingAudioBlob，后端侧二进制一律 _decode_binary_audio_frame」是阻断性理由
  证据: app-websocket.js:3058-3066 的 Blob 分支只有 9 行，前置一个「待配对 visit 头且尺寸匹配」判断后，音频路径逐字节不变；app-audio-playback.js:1909-1915 对无 header 的 Blob 本来就丢弃不播；websocket_router.py:790-792 只需在 `_decode_binary_audio_frame` 前按 4 字节 magic 分派。两处都是「多一个 if」，不是不能复用。
- [设计 3（latency）] Electron 41 = Chromium 146.0.7680.65；Linux 兼容模式在 main.js:934 关 GPU 合成
  证据: electron.exe 内 UA 为 Chrome/146.0.7680.179（设计 1 正确）；Linux 兼容开关清单在 main.js:486-496，且除 disable-gpu-compositing 外还含 `--disable-accelerated-video-encode/decode`——这反而加强了设计 3「Linux 兼容模式必软编」的结论，但行号与版本号引用不准。
- [设计 3（latency）] L 档 35 KB/s「省流」优于图片帧方案
  证据: 设计 3 v1 无空闲节流，L 档恒定 35 KB/s（≈126 MB/h，空闲也是）；图片帧方案空闲 1 fps ≈ 8~11 KB/s（实测 192×256 单帧 7.5~10.9 KB），30% 说话占比下均值 ≈ 20~27 KB/s。对「大陆省流」目标，v1 图片帧的小时流量反而更低；设计 3 的 idle 缓存要到 v1.5 才追平。

#### B.1.2 中继与协议（v1）

**评审结论**：设计 3：票据鉴权的区域中继（ops-robust）——以它为骨架，嫁接设计 1 的 SOCKS ImportError 回落、`x.*` 扩展前缀、按 codec 区分的 droppable/keyframe 丢帧语义、L1 状态容器+反向注册钩子，嫁接设计 2 的「拒带 Origin 握手」「jti 重放集合」「客户端选 room_id 使中继重启可恢复」「budget/dropped 合并通知 ≤1/s」。

**三份设计的打分与理由**（正确性/约束契合/复杂度/回归/产品，1~10，复杂度与回归越低越好）：

- 设计 1：哑中继（dumb-relay）: 正确8 约束5 复杂5 回归3 产品6 省流6
  代码核实最扎实（proxy/NO_PROXY 链、telemetry 骨架、_WSSlot 改法、埋点门控全部对得上）。致命短板在鉴权：平台 access token 出本机交给中继（与 card_drop_router native-delegate 的收紧姿态相反），未验证 client_id 可入房；跨境无联邦；lite 档 40KB/s=320kbps 上限偏高、3fps 无自适应阶梯；websocket_router 二进制分流碰在飞语音路径（虽只 3 行）。优点：不需要闭源 Servers 任何新端点，本仓库可独立交付；`x.*` 扩展前缀、droppable/keyframe 位按 codec 区分、`visit_route_state` L1 容器+反向钩子都值得嫁接。
- 设计 2：接进现有云端（servers-integrated）: 正确7 约束7 复杂8 回归3 产品7 省流8
  安全模型最完整（Ed25519 票 + jti + cid 绑定 + 中继盖章 + 拒 Origin），全房单调 seq 让两端时间线一致，budget 背压信号清晰，房间按票 lazy 物化让中继重启无状态。代价最重：闭源 Servers 要做 6 个端点 + JWKS + 中继注册表 + 配额（本仓库既做不了也测不了）；一房一人两条 socket 让中继每房 4 条连接、客户端两套 maintainer，而 HOL 阻塞在 lite 档典型帧 4-8KB 下只有 0.2-0.4s，不值这份复杂度；PyJWT 进中继依赖可接受但票据用紧凑自定义格式（设计 3）更省。行号偶有偏移。
- 设计 3：票据鉴权的区域中继（ops-robust）: 正确7 约束8 复杂6 回归2 产品8 省流8
  对闭源 Servers 的要求最小（1 个签票端点 + 公钥），PSK 自建模式让本仓库单测/社区自建不依赖 Servers；Upgrade 前 HTTP 401 拒绝已核实可行（starlette send_denial_response + uvicorn websocket.http.response）；member_token 续接、seq/order 双重幂等、TierLadder 自适应、/admin/accepting 排水、per-room 令牌桶硬顶、Caddy 自动证书，运维面最完整；只改 app/main_server/__init__.py 一处。两处站不住：SOCKS 依赖误判（会 ImportError）、测试 import 方式与平铺 import 自相矛盾。guest 票据的 `room` 字段由谁钉死依赖范围外的邀请流程，需改成可选 claim。

**评审驳回的断言**：

- [设计 3（ops-robust）] 「wss 443 经 HTTP CONNECT/SOCKS5 都被 websockets 15 支持，`httpx[socks]` 已装说明 socks 依赖在」
  证据: httpx 的 SOCKS 依赖是 `socksio`（uv.lock:5221）；websockets 需要的是 `python-socks`，uv.lock 中出现 0 次；websockets/asyncio/client.py:712-719 在缺它时 `raise ImportError("python-socks is required to use a SOCKS proxy")`。系统 SOCKS 代理用户会在 connect 处直接 ImportError，必须像设计 1/2 那样捕获后 `proxy=None` 重试。
- [设计 3（ops-robust）] 测试 `from local_server.visit_relay_server import server` + TestClient.websocket_connect，同时服务端「同样是 `from models import ...` 平铺 import」
  证据: 两句自相矛盾：telemetry server.py:50-52 的平铺 import 只在 sys.path 指向该目录时可解析；仓库既有测试 tests/unit/test_telemetry_canonical.py:16-17 正是 `sys.path.insert(0, <server dir>)` 后 `import storage`。按包路径 import 会 ModuleNotFoundError: models。合成稿改为沿用 sys.path 先例。
- [设计 1（dumb-relay）] 「对既有安全路径零回归」——建房时把 Servers OAuth access token 交给中继反查 /api/users/me
  证据: 仓库对平台 access token 出本机是明确收紧姿态：card_drop_router.py:1489-1494 native-delegate 设计理由是让 Web 标签页拿短时 scoped bearer 而不是平台 token（"so a rogue localhost listener cannot harvest refreshable Web credentials"），:49 还有 `platform_token_native_sync_forbidden` 错误码。让第二个 origin（relay.lanlan.tech）持有平台 access token 是姿态倒退，而且 Servers 是否接受第三方 origin 用用户 token 反查未核实（设计 1 自己也列为未核实）。
- [设计 1（dumb-relay）] 「串门至少一方登录即可；对端可以以 client_id 作『未验证设备』加入」构成安全机制
  证据: sync_worker.py:186-190 注释确认 X-Client-Id 调用今天不带 proof、不带 JWT，中继无法验证 client_id 归属；设计 1 也承认「不验」。未验证对端 = 任何人拿到 guest_token 就能进房，「首连绑定 principal」绑的是自报的 client_id，对假冒零防护，只剩 43 字符 token 这一道门。与需求 7「安全机制」不匹配，只能作为 owner 明确接受的降级。
- [设计 2（servers-integrated）] cross_server「`wait_for(ws_connect, timeout=backoff)`（:575-578）」、「aiohttp ws_connect（:565）」等行号
  证据: 实际在 main_logic/cross_server.py:580-583（wait_for）与 :581（ws_connect）；结论正确、引用偏移，不影响设计。
- [设计 1（dumb-relay）] 「utils/game_route_state.py:154-166 register_voice_transcript_handler 反向注册钩子」
  证据: 该行号区间实际是 `_get_supersede_lock`/`_get_active_game_route_state`（:148-172）；反向注册钩子的机制存在（docstring :23-30 描述），行号错。

#### B.1.3 对话机制（v1）

**评审结论**：设计 1：串门即 external route（reuse-max）——以它为骨架，嫁接设计 2 的清洗/删除钩子/socket 宽限/通用记忆键、设计 3 的 typing 指示/回家仪式台词（改走 stream_text）/人类称呼不出境。

**三份设计的打分与理由**（正确性/约束契合/复杂度/回归/产品，1~10，复杂度与回归越低越好）：

- 设计 1：串门即 external route（reuse-max）: 正确8 约束8 复杂6 回归5 产品7 省流8
  file:line 逐条核实几乎全中（takeover 闸、mirror 三函数、cross_server 旁路、game 蓝图、role 'tool' 零生产者、source 透传、Electron 桥透传）；D7 的自起会话担忧反而被证实可行。扣分：打断后半句会留在隔离历史（_streaming.py:1825）未处理；handle_interruption 行号错；注册表泛化虽逐字节等价但改动 websocket_router 三处 + proactive 守卫 + crud，回归面中等；rename 新守卫会连带改变 game 路由在飞时的行为。带宽最省（整句才发，<0.15KB/s），首字延迟 3–5s 略高。
- 设计 2：干净新原语 VisitSession + 转录本重建（clean-primitive）: 正确8 约束8 复杂7 回归6 产品7 省流8
  delete 钩子放 _unregister_and_cleanup_character_slot 是三份里唯一放对位置的；分隔符中和（={3,}→=）+ nonce 信封、ordinary_memory_enabled 通用键、10s 本地 socket 宽限 + 状态重放、300ms delta 合并都是可直接嫁接的好点子。扣分：每轮 connect() 重建历史让 token 成本 2–3 倍；要求中继实现 seq 全序 + based_on_seq 拒绝 + 回显 + resync（中继不再「笨」）；新 role 'guest' 触发 React 重建与三上下文验证；改 manager.py/lifecycle.py/mirror_meta/service.py 四个 core 文件；B 的人类不能对自家猫娘说话；A 侧回家也无声。
- 设计 3：中继持令牌 + 仪式感（experience）: 正确6 约束6 复杂8 回归6 产品9 省流7
  产品体验最好：正在输入指示、hold_ms 像打字、回家/送客一句仪式台词带 TTS、ending_soon 提示、收件人 chip、人类称呼默认不出境。但三条关键断言站不住：delete 路径不经 shutdown()（僵尸 takeover）、prompt_ephemeral 会把串门指令与台词抄送到插件 conversations store、dispatcher=None 让语音转写漏进普通路径。中继要跑令牌状态机 + 保留 64 条 + 冷却计时，握手轴负担最重；notify.py 加 kwargs、lifecycle.shutdown 加钩子、service 加守卫、React 加 role——四层 core 改动。先 ack 后显示每句多一次 RTT（跨区 +150–300ms）。

**评审驳回的断言**：

- [设计 3（experience）] 退出路径 #8：delete 经 remove_one_catgirl → _unregister_and_cleanup_character_slot 调 shutdown() → request_visit_teardown 钩子自动收尾串门
  证据: app/main_server/character_runtime.py:2092-2101 的 _unregister_and_cleanup_character_slot 只调 _stop_character_thread（:2021-2047，仅 cancel sync task）和 _cleanup_character_dicts（:2049-2063，del role_state[k]），从不调用 rs.session_manager.shutdown()；mgr.shutdown() 唯一调用点是 _init_character_resources 替换旧 manager（:1899）。挂在 lifecycle.shutdown() 上的 teardown 钩子对删除路径不生效，删角色会留下 takeover=True 的僵尸状态与悬空中继连接。设计 2 把钩子放在 _unregister_and_cleanup_character_slot 内是对的。
- [设计 3（experience）] 用 prompt_ephemeral 生成开场/nudge/告别/回家台词，对主会话与插件零泄漏（列为未核实假设 #3）
  证据: main_logic/omni_offline_client/_lifecycle.py:557-565 在 prompt_ephemeral 流出首个 chunk 时 _fire_bus_task(_publish_conversation_turn(instruction, turn_type='proactive_instruction'))，:917-925 再抄送 reply；唯一闸 _bus_copies_closed 由 close() 置 True、由 connect() 复位为 False（_streaming.py:142）。串门会话若走 prompt_ephemeral，指令与台词会以 source='proactive' 进所有插件的 conversations store。stream_text 路径没有这条抄送（critic.md 已核）。仪式台词必须改走 stream_text（HumanMessage 带系统头）。
- [设计 3（experience）] start_visit 置 mgr._takeover_active=True 且 _takeover_input_dispatcher=None 即可（语音本就不允许，dispatcher 不需要）
  证据: turn.py:1428-1431 只有 dispatcher 非 None 才拦截语音转写；为 None 时转写直落 :1484-1505 的普通路径（_note_user_turn、last_user_message_time、插件总线 _publish_user_utterance_to_plugin_bus、_inject_pending_user_directives）。若串门开始时独立 ASR 麦克风路由仍活着（manager.py:296-299 注明 input ownership 与 response backend 独立），转写会泄漏到插件总线并推动 mini-game 关键词钩子。设计 1 的「dispatcher 对任何转写返回 True 并发 VISIT_VOICE_UNAVAILABLE」才是完整对偶。
- [设计 1（reuse-max）] 隔离会话 handle_interruption 在 _streaming.py:1001，且「A 屏幕转录不显示半句」即等价于半句不进历史
  证据: handle_interruption 在 _lifecycle.py:856-862（→cancel_response :836），_streaming.py:1001 只是 retry 循环里的守卫注释。更要紧的是：流循环在 generation 失效时 break（_streaming.py:1228-1229）后，已累积的 assistant_message 仍在 :1825 append 成 AIMessage 进 _conversation_history——持久隔离历史方案必须在打断后显式弹掉这条尾部 AIMessage，否则下一轮 LLM 会看到一句「自己说过但对方没听到」的半句。设计 1 未处理这一点。
- [设计 1（reuse-max）] D7 未核实假设 #2：trigger_agent_callbacks 路径可能不会自动起文本会话，汇报要等下次用户开口
  证据: 这条担忧不成立（对设计有利）：proactive.py:1536-1543 在 websocket 已连接且 session 非 OmniOfflineClient 时 await self.start_session(ws, new=False, input_mode='text') 再投递。回家汇报在主会话不存在时会自动起文本会话并立刻说出来。
- [设计 2（clean-primitive）] 每轮 connect() 重建指令 + 全量前情块，OpenAI-compat 前缀缓存仍命中，token 代价可控
  证据: 其自报数字已承认每轮 1.3k–7.3k、上限 40 句 × 4k ≈ 160k 输入 token/侧；而 game session_pool 的既有做法（session_pool.py:303-317 单实例持久历史）与 append_context（context_append.py:428-437 直接 history.append）都证明持久历史可用。前缀缓存是否命中取决于厂商与「前情块只在尾部增长」这一未在代码中保证的假设（SystemMessage 每轮整体重建，任何召回块变动都会破坏前缀）。这不是错误，但「零过滤规则」的收益被每轮 2–3 倍 token 抵消，不应作为默认。
- [设计 2 / 设计 3] 新增 React role 'guest' 的回归面可控，只需 React→宿主→后端顺序上线
  证据: role 'tool' 已具备全部需要的性质且零生产者：zod 枚举含 tool（message-schema.ts:188）、独立 .message-bubble-tool/.avatar-tool 类且与 assistant 同侧（MessageBubble.tsx:22-45；styles.css:1075/1154/6590）、refreshReactAssistantAvatars（app-chat-adapter.js:1184）与 resolveCurrentAssistantAvatarUrl（geometry-and-messages.js:1504）都只碰 assistant、static/app 里只有导出器读 'tool'（app-chat-export.js:445）。新 role 要重建 gitignore 产物并在三宿主上下文验证（frontend-chat-surfaces 报告 constraints），而记忆 frontend-ci-coverage-gaps 指出 react-neko-chat 不进 PR CI——这是可以避免的回归面。

#### B.1.4 记忆与安全（v1）

**评审结论**：设计 1（零 schema：group_chat + platform=neko_visit）作骨架，嫁接设计 3 的闸门（冒名/帧完整性/出入站对偶清洗/往返上限/错误码脱敏）与设计 2 的 flush 条件、request_id 去重、只读 scoped_subjects 枚举端点

**三份设计的打分与理由**（正确性/约束契合/复杂度/回归/产品，1~10，复杂度与回归越低越好）：

- 设计 1：零 schema group_chat+neko_visit，pair_id 累积，digest+segments 双形态: 正确8 约束9 复杂5 回归2 产品7 省流-
  表 1.3 逐条对照全部核实为真；写形态用对了（digest 单形态含 assistant 行、segments 只放对端 user 行）；三个 subject 与 QQ [群,成员] 完全对偶；两个开关落 ALLOWED_CONVERSATION_SETTINGS + _USER_OWNED_FIELDS 是仓库现成的「用户独占」机制。错在释放钩子位置（crud.py release 在前）和 409/400 不对偶；标题「群聊记忆」是产品面小瑕疵。memory/ 与 app/memory_server 零改动，回归面最小。
- 设计 2：新 kind=visit 一次做对: 正确8 约束7 复杂6 回归4 产品7 省流-
  对 segments 无 role、单形态无 token 闸、rendering.py:713 分支、header 表 set 相等守卫的判断都对；flush 四条件与 request_id 去重是好点子。但「群主体会进身份池」被证伪，改 memory/scopes.py、routes.py Literal、rendering.py、prompts_memory 三表 + 新只读端点要附回归报告；只建一个 subject 丢掉对端人类画像；开关放 core_config.json 绕开了现成的 _USER_OWNED_FIELDS 保护。收益（标题/标签语义正确）可用 prompts_memory 前缀选表以 1/5 成本拿到。
- 设计 3：威胁模型倒推，segments-only + 串门日记: 正确5 约束8 复杂7 回归3 产品7 省流-
  闸门总表 G1-G10、冒名闸 G4、帧完整性 G8、错误码不带 base_url（A3）、猫↔猫往返上限、出入站同一清洗函数——都是其它两份没有的好东西。但核心写路径错了：segments 段里塞 assistant 行会把本机猫娘的话记成对端/亲人的事实；max_tool_iterations=0 被钳成 1；桥放 L3 的分层理由不成立且导致放弃召回工具；peer_home_id 建立在不存在的鉴权身份上。日记 /scoped_facts source=ai_disclosure 是唯一能让「我做了什么」入库的路径，但多一次 LLM 调用。

**评审驳回的断言**：

- [设计 3] §3.2 写形态一律走 segments 批形态，且每段 input_history 里同时放对端的 user 行和「我的回话」assistant 行
  证据: memory/facts.py:2396-2471 _cap_speaker_message_bodies 对段内每条消息只取 getattr(msg,'content') 渲染成 '> body'/'| line'，完全不看 msg.type；:2559-2627 段首只有一个 speaker_label。段里的 assistant 行会被当成该段发言人（对端猫娘/对端人类/本机亲人）说的话去抽取。FACT_EXTRACTION_BATCH_PROMPT（prompts_memory.py:1699+ 第 26 行）明说「各段发言人不是 {LANLAN_NAME} 本人」。设计 3 的三段全都混入本机猫娘回复，会把她的话写成别人的事实。
- [设计 3] 串门客户端 OmniOfflineClient(..., max_tool_iterations=0) 关闭工具轮
  证据: main_logic/omni_offline_client/_client.py:240 self.max_tool_iterations = max(1, int(max_tool_iterations))，0 被钳成 1；真正关掉工具的是 tool_definitions=None，参数值 0 无效且误导。
- [设计 3] 主进程 scoped 客户端必须放 main_routers/visit_router/memory_bridge.py（L3），因为「同时编排 main_logic 与 memory 的代码只能放 main_routers 或 app」
  证据: scripts/check_module_layering.py:86-98 memory 与 main_logic 同为 L2 且允许同层无环互引；main_logic 已有 greeting.py:38-39、notify.py:199-264、proactive.py:331-642、tool_calling.py:381 十余处 import memory.*，memory/ 零处 import main_logic。桥只 import memory.scopes/memory.recall_render 不构成编排；放 L3 反而让 main_logic 侧的工具 handler 无法调用（设计 3 因此被迫放弃召回工具改成每轮硬注入）。
- [设计 2] 复用 group_chat('neko_visit', id) 时 subject_identity 会把 `neko_visit:<id>` 送进身份池，未来「绑定账号」UI 能把串门域折进别人
  证据: memory/subject_identity.py:185-213 participant_key 对 group_chat（actor 为 None）根本不调 _account_of/身份池；:215-256 expand_subject 对 group_chat 恒返回 (subject,)。只有 group_participant 主体会查 entity_of，且 :232-239 跨平台账号被过滤——要折叠必须有人把两个 neko_visit:* 账号绑进同一 entity，而唯一的绑定 UI 是 QQ 插件（memory_bridge.py:412-480，platform='qq'）。「未来隐患」对群主体不成立，对成员主体也只在同平台内。
- [设计 2] 测试守卫必须同步：test_group_prompt_localization.py:372 的 kind 元组加 'visit'
  证据: tests/unit/test_group_prompt_localization.py:372 是 `for kind in (...): assert kind in RECALL_ENTRY_ENTITY_LABEL`，只断言三种 kind 在表里，加新 kind 不会红；真正会红的是 test_participant_memory_and_display_name.py:461-480 的 set 相等（缺任一语言即红）。
- [设计 1] §2.3/#10 角色释放钩子放在 character_runtime._unregister_and_cleanup_character_slot 之前做 final flush（2.5s 上限），「必须在 notify_memory_server_reload/release_character 之前」
  证据: main_routers/characters_router/crud.py:802（rename）与 :1666（delete）都在 remove_one_catgirl / 改名写盘**之前**先 await release_memory_server_character(old_name, hold_derived_task_admission=True)；character_runtime.py:2092 的 slot 清理在这之后才跑。钩子放 character_runtime 时围栏已经拉起（runtime.py:113 排空 2.0s 后 503），final flush 必失败。钩子必须挂在 crud.py 两个 handler 里、release 调用之前。
- [设计 1] 未核实假设 #2：_resolve_trust_source/_stamp_resolved_trust 对陌生 platform 'neko_visit' + tier='none' 可能 422/500，需要退到不传 speaker_id
  证据: memory/trust_store.py:778-808 resolve_trust 只有三条弃权条件（id 畸形/该平台 legacy barrier pending/无 tier 与 base），缺 ledger 条目按 (0.0,0) 正常聚合；:390-392 barrier_pending 对未登记平台返回 False；routes.py:1648-1673 解析不到只是不写 speaker_trust 键，从不抛错。传 tier='none' + 合法 speaker_id 是安全的，假设已核实为真，无需退路。
- [设计 1] rename 守卫「串门进行中 → 409」与语音守卫对偶
  证据: crud.py:743-757 语音守卫返回的是 400（JSONResponse status_code=400），不是 409；对偶应同用 400，或改用 409 时需写明为何与既有守卫不同。
- [设计 3] §2.2 pair 建模：S_visit=group_chat('neko_visit', peer_home_id)，peer_home_id 从「中继鉴权的对端安装身份」派生，对方猫娘 actor 再由 home+猫娘名哈希
  证据: brief 已核实身份只有 local_user_id(可能未登录)/client_id(X-Client-Id 不带 proof)/Steam64；「安装身份」不稳定（重装即变），且用猫娘名参与哈希意味着对方改名就变成新人。与设计 1/2 一致的做法是让中继颁发角色级 ASCII id，本轴不能用不存在的鉴权身份当基石（设计 3 自己在未核实假设第 1 条也承认）。

### B.2 v2 视频 / 传输轴三方案评审（2026-09-26）

#### B.2.0 为什么 v1 的视觉 / 中继两轴赢家被整轴推翻

owner 第一轮反馈（2026-09-12）同时否掉了 v1 两轴赢家的前提：「2 fps 不行、fps 怎么也得 30、画质可以低」否掉 WebP-alpha 图片帧（v1 lite 档 15 fps、空闲 2 fps，说话峰值 45~65 KB/s ≈ 360~520 kbps 却只有 15 fps）；「600 kbps 这一档、国内腾讯或阿里 WebRTC、国外 GCP 中转、中继自己部署不一定划算」否掉自建票据鉴权区域中继（`local_server/visit_relay_server`）。第二轮又加了「握手前必须核验社区身份、管理员可封禁」。因此 v2 对视频 / 传输 / 身份重新出 3 份独立设计比选，其余两轴（对话、记忆）保留 v1 骨架、按第二轮拍板重写具体条目（B.2.7）。

#### B.2.1 三个候选是什么

三份设计共享同一组事实与同一批 owner 硬要求（30 fps / ≤600 kbps / 大陆 TRTC、海外 GCP / Servers 核验身份 / 记忆绑社区身份），分歧只在「vendor SDK 跑在哪、文本 / 控制走哪」：

- **设计 1：SDK 在 Pet 主页面 + preload 放行**。lanlan_frd preload 给 `PetWebSocket` 加异 host 直通（`new URL(args[0]).host !== location.host` 时返回原生 WebSocket）+ 能力旗 `__NEKO_WS_PASSTHROUGH__`，老壳 fail-closed；文本走 display socket 的 `NKVC` 二进制帧（`websocket_router.py:789-800` 与 `app-websocket.js:3058-3066` 两条热路径各加一个 if）；需要 PC 先发版并维护壳 × 后端偏斜矩阵。
- **设计 2：同源 iframe，零 PC 改动**。vendor SDK、取帧打包、接收解包全部跑在 Pet 页内嵌的同源 iframe `/visit/transport`（子 frame 无 preload，`nodeIntegrationInSubFrames` 全仓零命中），拿到原生 `WebSocket` / `RTCPeerConnection`；iframe 经独立 WS `/api/visit/transport/ws` 连本机后端；文本 / 控制走同一房间的 vendor 数据通道，可靠性由两侧后端 outbox 兜底。
- **设计 3：视频走 vendor，文本走后端↔中继**。视频与设计 2 相同交给 vendor；文本 / 控制不走数据通道而走一条我方可靠 WS 中继——(a) 自建 VM，或 (b) 让 Servers 承担长连接 rooms 状态机；仍需 PC preload 补丁，但有纯文本降级。

#### B.2.2 五维打分（1~10，复杂度 / 回归风险越低分越高；合成稿 §0.2 原表）

| 维度 | 设计 1（SDK 在 Pet 主页面 + preload 放行） | 设计 2（同源 iframe，零 PC 改动） | 设计 3（视频 vendor，文本走后端↔中继） |
|---|---|---|---|
| 正确性（file:line / SDK / 计费 / 平台） | 7（5.16.0 过期、声网 3.36 算错、main.js:919 / ipc-router:68-72 行号错、重连 35 s 不准、许可判断错） | 8（抽查全中；身份票 10 min TTL 与 30 s 重连复验自相矛盾） | 8（npm 5.20.1 / ISC 对；main.js:555-558、prompts_memory:3868「按前缀」不准） |
| 契合 owner 两轮硬要求 | 8（30 fps / 600 kbps / TRTC+GCP / OD-01/05 全满足；老壳 fail-closed 整个串门不可用） | 9（同上全满足；零 PC 依赖） | 7（视频满足；文本仍要一个中继——(b) 让 Servers 承担长连接房间状态机成在飞硬依赖，(a) 自建 VM 正是 owner 说「不一定划算」的东西；海外推荐 Cloud 先于 owner 点名的 GCP） |
| 复杂度（低 = 高分） | 5（页面转发 + NKVC 二进制帧 + 两条热路径 if + PC PR + 偏斜矩阵） | 6（两个文档 + 一次同步跨 realm 调用 + 一个新 WS 端点；页面仍转发数据通道） | 5（vendor + 中继两套传输、Servers 要实现 rooms 状态机、OSS 中继目录仍要维护） |
| 回归风险（低 = 高分） | 5（websocket_router 二进制分支、app-websocket Blob 分支、preload 全量 WebSocket 包装器三处热路径） | 8（零热路径改动、零 PC 改动；只有 live2d-core 一行与懒建 iframe） | 7（无二进制分支改动；仍需 PC preload 补丁，但有纯文本降级） |
| 产品体验 | 7（视频文本同一会话；老壳直接不能串门；TRTC 8 KB/s 顶到时 delta 暂停） | 7（同一会话；iframe 透明 / 命中要实测 T3/T4；失败退设计 1 只损失前端两 PR） | 7（视频挂了文本还在、老壳纯文本可用、中继盖章可作举报证据；但文本与视频两条链路可各自断、Servers 宕机在飞文本即死、文本多 50~100 ms） |
| **合计** | **32** | **38** | **34** |

三份设计的 file:line 与 vendor 事实纠错见附录 A.2.2（1）；打分表里的「正确性」扣分项就是那 10 条。

#### B.2.3 赢家理由

**赢家：设计 2。** 决定性理由只有一条链：owner 第一轮明说「中继自己部署不一定划算」，第二轮又要求「握手前核验社区身份」——设计 2 用「Servers 只做一次性 HTTP 签发（vendor 凭证 + Ed25519 身份票）+ 对端在数据通道上互验票据」满足身份要求，运行期不依赖任何我方长连接服务；设计 3 把这份可靠性换成了一个必须有人运维（或让闭源 Servers 承担）的 WS 房间服务；设计 1 与设计 2 共享除「SDK 跑在哪」之外的全部决策，而设计 2 少了一个闭源 PR、一个偏斜矩阵和两条热路径改动。代价如实写在 OD-27 风险段：iframe 透明与命中（T3/T4）、同任务取帧（T2）、自定义 `http://<LAN IP>` 后端非安全上下文时 vendor SDK 不可用（T11）——T1~T5 任一失败即退设计 1，后端 PR 完全通用，只损失前端 PR-10 / PR-11。

#### B.2.4 嫁接自落选方案的部件（合成稿 §0.3）与裁决后的状态

| 来源 | 部件 | 裁决后状态（2026-09-26） |
|---|---|---|
| 设计 1 | 身份票 `exp` 与 vendor 凭证同 TTL（原提 2 h，修掉设计 2 的 10 min 自相矛盾） | 「票与凭证同 TTL」保留，数值改 **40 min**（`exp = iat + 2400`；TRTC `expire=2400`、LiveKit `ttl=40m`），因为 2 h 让被封账号继续持有有效凭证、且是唯一服务端可强制的账单上限（A.2.3 F-04 / F-05；裁决 C.2） |
| 设计 1 | vendor userId 按房派生 `role[0]+'_'+sha256(id|visit_id)[:24]`（26 字符，落 TRTC `[a-zA-Z0-9_-]` ≤32 字节） | 采纳，`id` 定为 `visit_uid`（裁决 C.3） |
| 设计 1 | `peer_char_id = 'c_'+sha256(peer_uid|char_tag)[:24]`（定长，代替设计 2 的裸拼接） | 采纳（裁决 G.1） |
| 设计 1 | 档位表 sd600 / hd1200 / fhd2400（16 倍数尺寸，按打包后面积判 TRTC 档） | 采纳（OD-06 v2）；sd600 唯一发布 |
| 设计 1 | 拥塞阶梯只缩裁剪不动 fps | 采纳；最低档码率 260 → **300 kbps**（不低于 TRTC 标清带下限），并注明 libwebrtc QP 缩放器可能自行降分辨率、接收端以 videoWidth/Height 观测（裁决 D.7） |
| 设计 1 | 三道能力门在懒加载 SDK 之前判定 | 采纳；顺序改为建房 / 入房时**先**建 iframe → 能力门 → 通过后才向 Servers 领凭证，失败不耗配额不占 takeover（裁决 D.4） |
| 设计 1 | TRTC cmdId 1/2/3 分流 + 分片信封 `{v, r, m, i, n, p}` + 数据通道令牌桶 5 KB/s | 采纳；每片 ≤1000 B 按字节明写、`txt` ≤800 B 留转义余量、并列条数桶 20 条/s（桶 10）、超限排队不丢（裁决 B.2） |
| 设计 1 | A 侧隐藏 / 遮挡 / 模型管理器覆盖的行为表 | 采纳；hide-all 行改写：Pet 窗 `backgroundThrottling:false` 下 `document.hidden` 不一定为 true，取帧是否停止只看「postrender 是否还来」（裁决 §I） |
| 设计 3 | npm 事实：trtc-sdk-v5 5.20.1（ISC）、livekit-client 2.22.3（Apache-2.0，`dist/livekit-client.umd.js`） | 采纳（OD-28）；实施时以包内 LICENSE 文件复核一次 |
| 设计 3 | 「串门本地静音」静在 `speakerGainNode`（analyser 之后，`app-audio-playback.js:1478-1486`） | **删除**：音频图事实正确，但该开关在 d4 已删（TTS 合成再静音白烧配额）；单一 `visitVoiceEnabled`（裁决 F.2） |
| 设计 3 | LiveKit Cloud → GCP 自建的盈亏点推导（≈2,570 房·小时/月） | 采纳为海外上线节奏；数字按 MB/GB 十进制重算约 2,500~2,700（0.27 GB 不是 0.264 GiB），结论「月 >≈2,500 房·小时切 GCP」不变（裁决 A、D.8） |
| 设计 3 | 对方亲人用 `participant('neko_visit', peer_uid)` 人级主体跨对累积 | 采纳（owner OD-05 本意；裁决 G.1） |
| 设计 3 | 自家猫娘预算 6 句/min、40 句/场 | 采纳（与 d4 §4.1 一致；裁决 F.1） |
| 设计 3 | `mirror_meta.is_mirror_event_memory_disabled` 加显式 `memory_enabled` 键 | 采纳（裁决 G.6） |
| 设计 3 | debrief「记成日记」经 `append_context(source='visit.diary')` | **不采**：debrief 流程与写入路径沿 d5（裁决 G.2） |
| 设计 2（基底自带） | host 定序 `order` + `ack{seq, order, stale}` | **删除**：全序与陈旧判定采 d4 Lamport `lp` + `reply_to`（裁决 B.1；d4 §4.5 三选一表明文否决 host 权威序） |
| 设计 2（基底自带） | 「后端重启 outbox 落盘回放不丢不重」「关机 `leave{shutdown}` ≤1 s」 | **删除**：凭证 / 票据 / 隔离会话都不落盘，后端重启 = 这场结束；Electron 先销毁窗口再关后端，`leave` 发不出（裁决 E） |

#### B.2.5 设计 1 为何落选

设计 1 与赢家共享除「SDK 跑在哪」之外的全部决策，落选只因为它把 vendor SDK 放进了有 preload 的 Pet 主页面，而 `pet-websocket-bridge.js:333-346` 的 `PetWebSocket` 不看 URL 就把任何新 WebSocket 当成后端 socket（`_activeWs = ws`、向 Chat 窗发 CONNECTING、后端帧按 stale 丢），所以它必须先改闭源壳：preload 加异 host 直通 + 能力旗，PC 先发版，老壳 fail-closed 整个串门不可用，再维护一张壳 × 后端偏斜矩阵。文本走 display socket 的 `NKVC` 二进制帧又要碰 `websocket_router.py:789-800` 与 `app-websocket.js:3058-3066` 两条在飞语音热路径（虽只各一个 if），加上 preload 全量 WebSocket 包装器，三处热路径改动让回归风险得 5 分。正确性上它的调研快照最旧（trtc-sdk-v5 5.16.0 vs 实际 5.20.1；「腾讯商业条款」vs 实际 ISC；声网 3.36 元 vs 单向 2.1 元；main.js:919 vs :928；ipc-router:68-72 引错；LiveKit 重连 35 s）。合计 32 分，三者最低。但它不是被否掉：它是 **T1~T5 任一失败时的退路**（只重写前端 PR-10 / PR-11，后端 PR-01~09 与 PR-12~16 通用），它的档位表、拥塞阶梯、能力门、cmdId 分流 / 信封 / 令牌桶、vid / peer_char_id 派生、A 侧隐藏行为表全部被嫁接进合成稿（B.2.4）。

#### B.2.6 设计 3 为何落选

设计 3 的视频部分与赢家相同，分歧在文本 / 控制：它不信任 vendor 数据通道的「尽力可靠」，要一条我方可靠 WS 中继，换来三个真实优点——视频挂了文本还在、老壳可纯文本串门、中继可对每句盖章作举报证据。落选因为代价直接撞 owner 原话：(a) 自建 VM 正是第一轮说「不一定划算」的东西，还要维护一份 OSS 中继目录；(b) 让闭源 Servers 承担长连接 rooms 状态机，则 Servers 从「一次性签发」变成在飞硬依赖，Servers 宕机在飞文本即死，且 Servers 排期本仓库既做不了也测不了。此外它仍需 PC preload 补丁（文本中继 WS 若开在主页面同样被劫持）、视频与文本两条链路可各自断（要多写一套失败模式）、文本多一跳 50~100 ms。合计 34 分。它的 npm 事实、Cloud → GCP 盈亏点节奏、`participant` 人级主体、6/40 预算、`memory_enabled` 显式键都被嫁接（B.2.4）；「speakerGainNode 静音点」与「append_context 日记路径」两项被裁决删除或改按 d5。放弃中继盖章后，举报证据链改为双侧 outbox / spool JSONL（带 `seq/lp/ts`）+ `GET /api/visit/transcript` 导出 + Servers `POST /api/visit/reports`——合成稿如实写明「无第三方盖章」是设计 3 相对赢家的真实优势，v2 接受这个损失。

#### B.2.7 对话轴与记忆轴：独立单稿，未做三方比选

- **对话轴（d4）**与**身份 / 记忆 / 生命周期轴（d5）**在 v2 各只有一份稿，没有像视频 / 传输轴那样出三份比选。原因：owner 第二轮对这两轴的拍板已经把方向定死（默认流式、默认口型跟本地 TTS、触发后自然收尾回家不可打断、30 s 判死、记忆绑社区身份、逐句落盘、回家 debrief 问要不要记），剩下的是执行细节；把三路对抗核验（code / platform / product）的火力集中在单稿上，比再写两份落选方案更能压出错误。代价是这两轴没有「落选方案」记录，只有核验修正记录（附录 A.2.2、A.2.3）。
- **d4 内部仍做了局部比选**：§4.5 对「无中继时的排序」列三选一——host 权威序（每行一个来回、host 掉线无序、不对称代码）/ 纯 `reply_to` 链（只是偏序，落盘需额外规则）/ **Lamport `lp` + 侧位平局**（每条消息 +1 个 int，`reply_to` 另配陈旧判定）——裁决 B.1 采 Lamport，并据此删掉合成稿的 host 定序。d4 各条目「备选」栏被否的项：OD-08 的 N=10 或 L=60（更长自嗨更贵）、收尾期间人类文字排队到回家后重发（收件人已变）；OD-15 的 `voice_play_start` 当分句信号（turn 级会漏）、单 speech_id 整行 + `__tts_sentence_done__`（送达 ≠ 播放且 ws_bistream 无）、`visitVoiceMuted` 静音借口型（白烧配额）；OD-21 的默认关（v1）、token 级 delta（消息数 ×5~10 撞 30 条/s）、ack 按片（消息数翻倍）。d4 自己的 `line{n,h}` + `line_req` 补洞模型则被裁决 B.2 换成「`text{final}` 全文必达」（理由见附录 A.2.5 第一行）。
- **d5 内部的两处建模分歧**由裁决取舍：对方亲人 `group_participant('neko_visit', pair_id, 'u_'+uuid)` 按对（d5）vs `participant('neko_visit', peer_uid)` 人级跨对（合成稿 / 设计 3）→ 取人级（G.1）；`sub` = 裸社区 uuid（d5）vs HMAC 派生不透明 `visit_uid`（合成稿）→ 取 HMAC（C.1，对端拿不到裸 uuid，Servers 可反查）。d5 被三路核验改掉的其余项：debrief 超时默认 `key_points` → `ask_later`、issue 总开关默认 True → False、`App.tsx` → `FullChatSurface.tsx:3088`、≈3 人日 → ≈4 人日、LiveKit 53~61 s → 约 44 s + 抖动、`utils/social_base.py:15` → `utils/social_base.py:12`、`state.json` 在 `scope:'all'` 时一并删（附录 A.2.2（2））。d5 采纳且裁决保留的：TTL 40 min、±300 s 容差、`config_dir/visit_spool/`、页面宽限 20 s、关机预算 3 s、`GET /api/visit/pubkeys`、OD-09 / OD-11 人话骨架、OD-17 逐句 spool、OD-16 debrief 流程与写入路径、OD-31（原 d5 OD-27）`memory/scoped_client.py` 上提。
- **OD-03 复用路径**（reuse_paths）也是单稿核对而非比选：它逐条核了 v1 §3.9 对照表（行号 `runtime.py:2075` / `postgame.py:1277` 精确；「注册表」实为复用模式新建机制；`group_participant` 字面量文件数 6 → 7），并回答了 owner「为什么不复用 game / QQ 群聊路径」（结论进 OD-03 v2 补充问答与 §3.10 两行，裁决 H）。

#### B.2.8 vendor 与部署候选的落选项

| 候选 | 落选理由 | 来源 |
|---|---|---|
| 阿里 ARTC（大陆主选） | 自定义画布轨只能占屏幕共享槽（与真实屏幕共享互斥，远端按 track type 2 渲染）；SDK 钉死 `degradationPreference = maintain-resolution`（拥塞先丢帧，与 30 fps 硬要求相反）且无公开覆盖；H.264 only；数据通道要求已推媒体或服务端开关；竖版 320×896 落哪档按「不高于 720×480」的宽高比较无依据（896 > 480，需工单）；无免费额度；若判 480P 档 0.012 + 0.006 = 0.018 元/分 → 1.08 元/房·小时，判 720P 则 1.80 元 | research_artc.md |
| 声网（大陆） | 无标清档：HD ≤921,600 px 28 元/千分钟 = TRTC 标清 2×；单向视频 host 收 HD 28 + guest 音频 7 → 0.035 元/分 → 2.1 元/房·小时；`sendStreamMessage` 未公开文档化（1 KB / 30 pps / 6 KB/s），RTM 另计费 | research_agora.md |
| TRTC 国际站（海外） | 与大陆账号体系完全隔离（不能共享 SDKAppID）→ 两套后端签发；无标清档，HD $3.99/千分钟 | research_trtc.md；https://intl.cloud.tencent.com/document/product/378/80429 |
| 海外一上来就 GCP 自建（跳过 LiveKit Cloud） | 多一台机 + 域名 + 证书 + 压测 + 运维：e2-standard-4 us-west1 $97.84/月、东京 $125.51/月；LiveKit 只公布 c2-standard-16 基准（150 pub / 150 sub 720p = 85% CPU），默认 400 轨/CPU → 4 vCPU 名义 1,600 轨对 500 房 1,000 轨刚够、必须压测；月 <≈2,500 房·小时时 Cloud Ship $50/月更便宜且零运维。GCP 保留为稳态目标（owner 点名） | research_livekit-gcp.md；https://livekit.com/pricing |
| mediasoup / Janus / ion-sfu 自建 SFU | 只给 SFU 内核（无信令 / 鉴权 / TURN，要自己写房间服务）；Janus GPL；ion-sfu 无人维护；LiveKit server（Apache-2.0）三者兼备 | research_livekit-gcp.md |
| 自建 WebCodecs 视频中继（v1 视觉轴设计 3 的思路） | 回到自建视频中继，owner 第一轮已否；且 WebCodecs `alpha:'keep'` 在 Chromium 只剩 discard，alpha 仍要自己打包 | research_chromium-webrtc.md；OD-02 v2 备选 |
| 色键 / 并排打包（代替堆叠 alpha） | 色键丢半透明发丝与阴影；并排打包同面积无本质差别，堆叠让 alpha 走亮度通道保全分辨率 | OD-02 v2 备选；research_chromium-webrtc.md |
| Servers 对跨区直接放行（「允许 + 警告」默认） | 大陆 → GCP / LiveKit Cloud 连通无证据、TRTC 海外节点无表、国际站账号隔离；首发 fail-closed 403 `cross_region_unsupported`，T9 实测后再由 owner 决定 | 附录 A.2.3 platform m7；裁决 D.2 |

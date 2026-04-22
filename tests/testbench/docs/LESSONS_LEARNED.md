# 本项目沉淀的代码设计与开发经验 (P00-P25 立项)

> **定位**: 本文档是 N.E.K.O. Testbench 项目 P00-P25 立项期累积的
> 设计原则与工程经验的**抽象提炼**. 源材料是 AGENT_NOTES §4 的 77+ 踩点
> 案例 + §3A 47 条横切原则 + P24_BLUEPRINT 五轮审查的 13 条元教训 +
> P24 Day 9-E 二轮翻转的 3 条元教训 (L23/L24/L25).
>
> **目标读者**: (a) 本项目未来阶段的 agent (查阅原则); (b) 其它 AI 辅助
> 的大型软件项目设计者 (借鉴经验). 与三份老 docs 的区别: AGENT_NOTES 是
> **案例档案** (具体怎么踩的), 本文档是**抽象沉淀** (为什么会踩 + 怎么防).
>
> **配套 cursor skills** (在 `~/.cursor/skills/` 独立存在, 不依赖本项目):
>
> - `audit-chokepoint-invariant` — "Intent ≠ Reality" 差集审查方法论
> - `single-writer-choke-point` — 多源写入收敛 helper 的模式
> - `event-bus-emit-on-matrix-audit` — 前端事件总线漂移检测
> - `semantic-contract-vs-runtime-mechanism` — 测试生态对接生产系统时分离
>   "语义契约层" vs "运行时机制层" 的评估方法 (P25 立项后补)
>
> 本项目内看到某条原则违反时, 对应 skill 名已在章节里标出, 其它项目照样可用.

---

## 1. 核心方法论 (5 条, 超项目价值)

这 5 条方法论来自 P24 整合期的五轮审查, 适用于**任何规模 > 3 个月的 AI
辅助软件项目**, 不限于本项目.

### 1.1 Intent ≠ Reality

**文档原则描述的是 Intent (作者想保护什么), 实现代码描述的是 Reality (当
前所有路径是否真的都过了守护). 两者有 gap 是默认的, 不是例外.**

具体表现:
- 文档写 "X 必须在 Y 守住", 实际 grep 发现 Y 只被 2/5+ 入口调用
- AI 读到 §3A 原则会默认 Intent == Reality, 这是认知错误
- **用户实测 > AI 推断 > 文档原则** 的证据权威度排序

> 本项目实锤: `check_timestamp_monotonic` 声称保护 `session.messages` 单调,
> 实际只守了 2 个手动 router 入口, 其它 5 个 SSE / Auto / SimUser 入口全部
> 绕过 — 用户实测揭穿, 本来 grep 数 = 3 hits 看起来"都是 check 的调用",
> 以为没问题, 实际是数据层静默损坏的架构级 bug.

**防线** (→ skill `audit-chokepoint-invariant`):
1. 每次读 §3A 原则时, **反射性 grep 验证实际覆盖**, 不默认 yes
2. 写 grep 查询时, 查询 "**所有潜在绕过路径**" 而非只查 "守护函数的调用点"
3. 用户反馈 "X 不 work" 时, 若文档说 "X 受 Y 守护", **先怀疑 Y 漏守某个入口**

### 1.2 多源写入是纸面原则成败分水岭

**同一个数据结构只要有多个写入点, 纸面原则几乎必然漏守**.

原因:
- 单源写入时, 原则 = 那一个函数体内的代码, 不可能漏
- 多源写入时, 原则成了 "N 个函数都应该做 X 的社会契约", N 越大漏守越多
- AI 记忆力 / 纸面文档 / agent 轮换都不可靠

**修法不是"再写一遍原则", 是把多源收敛成单源**: 抽 choke-point helper,
让 "绕过" 本身不可能或极不自然.

> 本项目三个典型:
> - **session.messages.append**: 抽 `append_message()` helper, pre-commit block 裸 append
> - **renderAll 漏调**: 抽 `bindStateful()` helper, handler 包装时自动 renderAll
> - **atomic write fsync**: 6 份副本合并到 `pipeline/atomic_io.py` 一处

**识别信号** (触发抽 choke-point):
- 纸面原则连踩 3 次还没被贯彻 → 用代码层强制
- grep 显示某个 "必须守护的不变量" 有 ≥ 3 个写入点 → 抽 helper
- 同族 bug 半年内重现 ≥ 2 次 → helper + pre-commit + smoke 三件套

**→ skill `single-writer-choke-point`**

### 1.3 方法论立即扩大应用 > 推给未来 agent

**发现一个能抓 bug 的方法论后, 在同一轮审查内立即实证 2-3 个扩展应用面**.
列清单推给未来 agent = 价值大幅缩水.

> 本项目实证: §14.3 曾列 5 大扩展应用面但未实证, 第五轮半小时实证就抓出
> 2 条实锤 bug (`memory_hash_verify` 前端 0 消费 / SSE event 散落无枚举) +
> 2 条合规边缘. 如果留给"未来 agent", 按 §14.6 "留空 TODO 4-6 phase
> 半衰期" 的统计, 大概率永远不被回来填.

**操作模式**:
- 每次审查后问 "**这个方法论还能用在哪?**"
- 选 3-5 个候选应用面, **立即取最高怀疑的 1-2 个跑 grep**
- 发现问题立即入当轮的必做清单, 不延后

### 1.4 覆盖度 RAG 灯作为阶段方案自检工具

**任何 ≥ 5 天的开发阶段, 方案定稿前必须过一张 RAG (绿/黄/红) 覆盖度总表**.

定义:
- 🟢 绿 — 规格完整 + 入口实证 + smoke 规划齐
- 🟡 黄 — 有规格但入口 sweep 未彻底或 smoke 待定
- 🔴 红 — 新识别但未覆盖

**为什么需要**:
- "看起来挺全" 不是可验证的完善度
- RAG 数据化 "还剩多少 gap + gap 都有名字" 才能判断方案是否可开工
- 每轮审查后更新 RAG, 数字是否改善 = 审查 ROI

> 本项目: P24 蓝图 5 轮审查后 22 绿 / 10 黄 / 14 红, 14 红分 M/O/B 三档
> (必做/可选/backlog). 这张表让"不再做第六轮"有了量化依据 — 再审下去
> 边际效益负.

### 1.5 新 bug 决策树 (scope creep 控制)

**任何规模 > 1 周的阶段开工前, 必须定义"开工中途发现新 bug 怎么办"的决策树**.

**决策树 (4 档)**:

```
发现新 bug / 新 debt
├── 数据丢失相邻? → 当日 hotfix (不推)
├── §3A 已有原则违反? → 按同族 sweep 一起修
├── 新功能 / UX 改进? → 推 P25 or backlog
└── 架构级 (类 §4.26 #91 级)? → 新开 phase, 不塞本阶段
```

**为什么**:
- 没决策树 → 每个新 bug 都"看起来该塞进去", scope creep 不可控
- 有决策树 → agent 临场按规则走, 不用每次问用户

> 本项目教训: P19 hotfix 5 把 4 个陈年小债塞进主线导致 scope 爆炸
> (§4.23 #81), P20 hotfix 1 Hard Reset 连锁导致黑屏 (§4.26 #87) 都是
> scope 控制失败的例子.

### 1.6 语义契约 vs 运行时机制 (测试生态 OOS 判据)

**测试生态 (testbench / mock harness / staging) 评估"要不要对接一个新的生产系统"时,
必须把该生产系统拆成两列 — 语义契约 vs 运行时机制 — 再判断, 而不是看
"整体架构是否兼容"**.

来源: P24 Day 9-E 道具交互 (PR #769) 第一轮被错判成"三方架构不兼容 → 全系统 OOS",
用户二轮澄清 testbench 定位后翻转 — 代码里 9 个 pure helper + 7 个常量表 + 1 个
纯去重策略, 其实 100% 可复用.

**两列分界**:

| 语义契约 (WHAT) testbench **必须复用** | 运行时机制 (HOW) testbench **几乎不该复现** |
|---|---|
| prompt 模板 / 系统指令模板 | WebSocket / SSE 实时流 / `prompt_ephemeral` |
| 数据形状 `{memory_note, dedupe_key, rank}` | 多进程队列 / `sync_connector_process` |
| 去重策略 / rank 升级规则 / 验证矩阵 | `contextvar` race guard / SID 会话隔离 |
| payload normalizer pure helper | 冷却节流 (600ms/1500ms 点击防抖) |
| 输出格式 (memory 记录怎么落盘) | 多实例 keyed on `client_id` 的并发控制 |

**判据**: 任何一个 `from config.prompts_* import _helper()` 能 import 的纯函数
**几乎一定是语义契约**, testbench 直接 import 复用; 任何带 `contextvar`/`asyncio.Queue`/
`async def handle_*` 的异步状态机**几乎一定是运行时机制**, testbench 不复现.

**Ferrari / 测功机类比** — 这是本项目里解释这条原则最直观的模型:

> 生产环境是法拉利, testbench 是**底盘测功机 (dyno bench)**. 你永远不会在测功机上
> 开上高速 (运行时机制不能搬), 但你**确实**在测功机上测扭矩-转速曲线 (语义契约能搬).
> "测功机不能复现高速 → 就不能测性能" 是这条原则要阻止的反模式.
>
> 测试生态的价值恰恰是**剥掉交付机制, 独立测量被测量的东西**.

**错误 framing vs 正确 framing**:

- 错: "testbench 能复现系统 X 的完整链路吗?" → 不能 → OOS.
- 对: "用户想测量 / 评估什么?" → 再分别问 "这个测量在哪一层?"

典型测量问题 → 对应层位:

| 用户想评估 | 位于哪一层 |
|---|---|
| 系统 X 产生的 memory note 会破坏 memory pipeline 吗? | 语义契约 (note 数据形状) |
| 系统 X 的 prompt 注入在 edge payload 下稳健吗? | 语义契约 (prompt 模板 + 验证矩阵) |
| 系统 X 的去重策略在洪水场景下正确抑制吗? | 语义契约 (去重 pure func) |
| 实时流在 SID race 下会不会串会话? | 运行时机制 (testbench OOS) |
| 多进程队列掉 subprocess 能否幸存? | 运行时机制 (testbench OOS) |

**操作流程** (4 步):

1. **端到端读一遍** PR diff + module + unit-test contract, 把每个函数/类/常量填
   进上面两列表.
2. **问对 scope 问题** (见上表).
3. **设计薄 adapter**: import 纯 helper + embed per-session 状态 (production 放在
   跨进程 cache 的, testbench 放 `session.xxx_cache`) + 写单个 `simulate_foo()`
   handler + 挂测试驱动端点.
4. **写明 OOS 清单**: "本阶段不复现 X 的 WebSocket / 冷却 / 多进程 / SID race,
   这些是交付层机制, 与 testbench 测量目标正交. production 的 unit test 已覆盖."

**反模式**:

- **A. 架构不兼容 → 全系统 OOS**: 表现是"看到 WebSocket + 多进程 + contextvar,
  直接断 OOS". 对治: 拉表重读, 80%+ 的代码通常在 `config/prompts_*.py` 或
  `_helper_func()` 纯层, 能搬.
- **B. 测功机不能开上高速**: 表现是"拒绝接入因为复现不了实时流". 对治: 用户
  不是要你开上高速, 是要你测扭矩, 回到测量目标.
- **C. 导 production 私有 helper 会耦合**: 表现是"担心依赖 `_internal_helper`
  绑死未来重构". 对治: 这**就是想要的**效果 — testbench 会因为 production 改
  模板而 break, 这正是"测试数据没跟上 production"的早期信号. monorepo 里的
  兄弟包 underscore import 是标准 testbench 惯例.
- **D. 复现交付层细节"以防万一"**: 表现是 testbench 长出假 WebSocket / 假队列.
  对治: 那些层 production unit test 拥有. testbench 的职责是"交付成功之后,
  data 和 prompt 进入 memory pipeline 之间".

**决策树**:

```
新 production 系统 X 上线 (或规划中)
│
├── 它产生 testbench pipeline 会处理的 data / prompt / memory 吗?
│   ├── NO → 真 OOS, testbench 无工作量.
│   └── YES ↓
│
├── 把 X 的代码拆语义契约 / 运行时机制两列.
│
├── 语义契约列能作为 pure helper import 吗?
│   ├── YES (典型) → 写薄 adapter + 测试驱动端点.
│   └── NO (罕见, 深度耦合 runtime 状态) → 要求 production 抽 pure core,
│         或 testbench 重写一份 + 与 production unit test 跑双 smoke.
│
└── 写明运行时机制列的 OOS 清单.
```

**配套推论 (L25 = 这条原则的直接副产物)**:

> **"影响评估任务的范围不取决于能否复现运行时, 取决于能否复现语义"**.
>
> 当 testbench 被赋予"评估新系统对对话/记忆的影响"这一任务时, scope 判据
> 不是"运行时复现度", 而是"语义契约复现度". 前者是交付机制, 后者是测量目标
> 所在层.

**→ skill `semantic-contract-vs-runtime-mechanism`**

> **本项目实证** (P24 Day 9-E → P25 立项):
>
> - PR #769 道具交互 `config/prompts_avatar_interaction.py` = 1196 行里 **9
>   pure helper + 7 常量表**, testbench 可直接 import;
>   `main_logic/cross_server.py::_should_persist_avatar_interaction_memory`
>   是**纯去重策略函数**, 可直接 import.
> - Agent Callback (`AGENT_CALLBACK_NOTIFICATION` + `drain_agent_callbacks_for_llm`)
>   和 Proactive Chat (`prompts_proactive.*` 5 变体) 同模式 — 三个系统一套 adapter.
> - 第一轮错判"三方架构不兼容 → 全 OOS", 第二轮重读 + 两列分类, 翻转为
>   "语义层必纳入, 运行时层 OOS, P25 新阶段交付". 新 P25 蓝图完整定义
>   `POST /api/session/external-event` 统一端点 + session-level dedupe cache
>   + dual-mode memory write (session vs recent).

---

## 2. 代码架构设计原则 (项目内提炼, 10 条)

这 10 条是本项目沉淀**可复用**到其它项目的架构原则. 完整 47 条见
`AGENT_NOTES §3A`, 这里挑最通用的 10 条.

### 后端 (3 条)

**2.1 软错 vs 硬错契约严格分离** (A1)

- **软错**: 字段级 (`result.error = "..."` + `status_code=200`) — 让 UI 只重跑失败那条
- **硬错**: 请求级 (`raise HTTPException(4xx)`) — 让 UI 清楚这整次请求都不成

混用 = 批量操作时 N 条都失败被静默吞成 200 绿.

**2.2 单一 choke-point 守护不变量** (→ skill `single-writer-choke-point`)

见 1.2, 不再重复.

**2.3 沙盒 = 目录替换, 不 = 配置隔离** (A2)

Testbench 类 "隔离出一份测试数据" 的架构, 沙盒只该替换**路径字段**
(`docs_dir / memory_dir / ...`), **不替换** API 组配置 / 模型配置 / 密钥.
测试端的 LLM 调用走测试端自己的 resolve, 绝不调主程序 manager.

### 前端 (4 条)

**2.4 state-drives-render, mutation 末尾无脑 renderAll** (B1)

**头号原则**. 任何 `onChange / onInput / onClick` handler 的最后一行默认
就是 `renderAll()`. 三类例外必须注释:
- debounce → "queued into pending render"
- textarea cursor-preserving → edge-trigger 模式
- 纯 form 字段无派生 → "does not affect rendered UI"

> 本项目: 踩 6 次, 证明记忆力不可靠, 必须抽 `bindStateful()` helper.

**2.5 事件总线 emit 前 grep listener, on 前 grep emitter** (B12 双向)

(→ skill `event-bus-emit-on-matrix-audit`)

**2.6 跨 workspace one-shot hint 模式: 协调者 force-remount** (B7)

Workspace 懒挂载不卸载时, 跨页导航带筛选 hint 的**三条路径** (cold / warm-
other / warm-same) 必须都覆盖. "接收方订阅 `ui_prefs:change`" 看似简单
实际在 jsdom 过但浏览器失灵 — 必须走协调者 `consumeHintIfPresent()` 模式.

**2.7 `Node.append(null)` 永远渲染字面量 "null"** (C3)

```javascript
parent.append(renderXxx())  // 若 renderXxx() 返 null, DOM 多一个 "null" 文本节点
```

防线 3 选 1:
- `renderXxx` 改返空 `DocumentFragment` 不返 null
- 调用方 `parent.append(...[...].filter(Boolean))`
- 包一层 helper `safeAppend(parent, ...children)`

### 数据 (3 条)

**2.8 `atomic_write + fsync` 原子写** (F1 + F5)

```python
with tmp.open("wb") as fh:
    fh.write(data)
    fh.flush()
    os.fsync(fh.fileno())
os.replace(tmp, path)
```

- `os.replace` 保 metadata 原子; `fsync` 保内容持久 (闭合"文件存在但 0 字节"窗口)
- 批量瞬时写可复用 `flush`, 不按元素调 (性能)

**2.9 多文件原子组: 非 discoverable 先删, anchor 最后删** (F6)

任何"逻辑实体 = 多物理文件" 的结构 (tar.gz + index.json / memory + db + wal),
删除顺序:
1. 先删 list 端点**扫不到**的文件 (如 tar.gz)
2. 最后删 **anchor** (如 index.json)

反序 → 半完成时 anchor 被扫不到, 剩下文件成孤儿.

**2.9A `@property` 动态计算 vs 直接赋值 (路径字段的沙盒/mock 友好设计)**

(来自 P24 Day 9 `ConfigManager` 同步审计的 meta-lesson L23)

```python
class ConfigManager:
    def __init__(self):
        self.base_dir = ...

    # GOOD — 沙盒替换 base_dir 后自动跟着重定向
    @property
    def cloudsave_dir(self) -> Path:
        return self.base_dir / "cloudsave"

    # BAD — 一次性赋值, 后续替换 base_dir 不影响
    # self.memory_dir = self.base_dir / "memory"
```

**为什么重要**:

- 沙盒/mock 框架 (本项目 `sandbox.py::_PATCHED_ATTRS`) 通常只替换**根路径字段**
- 如果各子字段走"构造期直接赋值", 每次新增一个子字段都要手工同步 `_PATCHED_ATTRS`
  白名单 (`memory_dir` / `cloudsave_dir` / `snapshots_dir` / ...)
- 一旦遗漏同步 → 测试/mock 环境的某个子目录**指向生产环境的真实路径** →
  跨环境污染 (本项目 `logs_dir` 忘同步曾踩过)

**规则**: 任何路径相关的"派生字段"都应该用 `@property` 动态返 `self.base_dir / "..."`,
不用 `self.xxx_dir = self.base_dir / "..."`.

**扩展应用**: 不只是路径 — 任何"依赖某个可替换根状态"的派生值 (`self.api_endpoint`
依赖 `self.environment` / `self.timezone_offset` 依赖 `self.locale`) 都遵循同样
原则: 动态计算 > 一次性赋值.

**本项目实证**: 2026-04-22 Day 9 审计 `ConfigManager` 发现新加的 `cloudsave_dir`
直接采用了 `@property` 模式, sandbox 白名单无需同步就自动走沙盒路径 — 比前辈
`memory_dir` 当年直接赋值后踩过的跨环境污染更健壮.

### 安全 (1 条)

**2.10 Trust boundary 方向判定: 硬拒 vs 软告警**

同一份完整性校验 (hash), 在**读盘路径** (load from own disk) 不匹配 → warn +
diagnostics (用户可能手动改过 archive, 硬拒是 UX 灾难); 在**跨端点路径**
(import from external payload) 不匹配 → 硬拒 (外部来源都有 silent corruption).

**普适规则**: 任何验证字段, 先问 "**这段字节是自家代码刚写出去的, 还是
从外部接收的?**" — 决定 warn 还是 refuse.

---

## 3. 高频 Bug 类型与防线 (项目 8 类, 频率排序)

来自 §14.6.A 分类, 项目最高频的 8 类 bug + 防线模式.

| 类 | 项目次数 | 防线模式 |
|---|---|---|
| **renderAll 漏调** | 6+ 次 | `bindStateful()` helper + dev-only drift detector + pre-commit grep |
| **`i18n(key)(arg)` 误用** | 2+ 次 | pre-commit hook: `rg "i18n\([^)]+\)\("` 零命中 + `_fmt` 后缀命名 |
| **事件订阅漂移 / 0 listener** | 4+ 次 | emit-on matrix audit + `.cursor/rules/emit-grep-listener` (→ skill) |
| **async lazy init 竞态** | 3 次 | Promise cache 模式 (→ skill `async-lazy-init-promise-cache`) |
| **跨 workspace 导航缺步** | 3 次 | 协调者 force-remount 模式 (B7, §4.23 #78 定型) |
| **Grid template 子元素漂移** | 3 次 | (→ skill `css-grid-template-child-sync`) |
| **`Node.append(null)`** | 2 次 | (→ skill `dom-append-null-gotcha`) |
| **`min-width:0` 漏父链** | 2 次 | `.u-min-width-0` utility class + 父链逐层补 |

**共性归因**: **8/10 高频 bug 来自前端**. 前端 "状态驱动 + 事件驱动" 心智
负担 > 后端 "请求-响应", 导致前端 bug 集中.

**派生原则**: 前端比后端更需要**机械化 lint + helper + dev assert**.
后端的 Pydantic / type hint 已经提供了一层守护, 前端 JS 没有, 全靠自律.

---

## 4. 低频高危 Bug 与防线

| Bug | 触发 | 后果 | 防线 |
|---|---|---|---|
| **Hard Reset 事件风暴** | reset 同步 emit 4+ 种事件 → 15+ listener 放大 | **整机卡死黑屏** | 彻底清零操作走 `location.reload()` 不做 surgical patch (§3A B13) |
| **SQLAlchemy engine 缓存 WinError 32** | rewind 时缓存持 SQLite 文件句柄 | 文件锁死, 用户被迫重启 | `_dispose_all_sqlalchemy_caches()` + `gc.collect()` 在 rewind/reset 前主动调 |
| **编码污染 ?? 乱码** | 某编辑工具 GBK/UTF-8 误解码 | 所有中文硬编码字符静默损毁 | 业务 JS 禁非 ASCII 字面量, 文案走 `i18n.js` (独立文件) 或 `\uXXXX` 转义 |
| **BSOD 后 0 字节文件** | `os.replace` 未 `fsync`, 断电 | 永久数据丢失 | 见 2.8 |
| **TOCTOU 删除后 load 空数据** | list 到操作之间有 ~200ms 删除 窗口 | 静默"加载空"回传成功 | 端点同时预读依赖文件, 任一 missing → 400 fail-loud |
| **虚拟时钟回退消息倒序** | 游标设到过去 → SSE append 绕过 check | 下游 dialog_template / UI 时间分隔条错乱 | 写入点 choke-point (见 1.2) |

**共性归因**: **崩溃边界 + 多路径绕过 + 可序列化状态** 三方向.
都回到 choke-point helper + fail-loud 不 fallback + 机械化守护.

---

## 5. AI 辅助开发的特殊注意事项 (3 条)

**AI 作为开发者的特殊性** — 这 3 条是本项目从 77+ 踩点案例里识别出的
"AI 独有的系统性错误", 其它团队人类开发者的项目未必同此.

### 5.1 AI 反复踩的头号坑: 训练数据里的普遍模式 vs 项目自定义 API

> 典型案例: `i18n(key)(arg)` 误用
>
> - 训练数据里 i18next / vue-i18n 等主流库都是柯里化 `t(key)(params)`
> - 本项目自定义 `i18n(key, ...args)` 单次调用
> - AI 凭"直觉"写 `i18n(key)(arg)`, parse 通过但运行期 crash
> - **AI 已踩 2+ 次, 每次修复后下次仍可能重犯**

**防线**:
- 任何"AI 直觉写法" vs "项目规范写法" 偏离处 → 加 pre-commit lint 硬拦截
- 本项目: `rg "i18n\([^)]+\)\("` 必须零命中
- 命名约定给暗示: 函数值 leaf 用 `_fmt` / `_tpl` 后缀 (`selection_fmt`)

### 5.2 纸面原则记忆不可靠 — 必须 choke-point + sweep

(见 1.2) — 这条对 AI 尤其重要, 因为 agent 会轮换, 记忆容易断.

### 5.3 测试环境 (jsdom) 语义差 vs 真实环境

> 典型案例: P17 hotfix 4 方案在 jsdom 全绿但浏览器实测失灵 (§4.23 #77→#78)
>
> - jsdom 不实现 `grid-template` / `min-width:auto` 的 CSS 语义
> - jsdom 事件冒泡顺序和真实浏览器有微妙差异
> - ES module 浏览器缓存不总遵守 no-cache

**防线**:
- jsdom smoke 只作为**第一道过滤**, 不是最终验收
- 新 UI 模块必须**真实浏览器 manual test** 一次
- CSS 布局类 bug 只能 devtools 肉眼排查, 不能期望 jsdom 覆盖

---

## 6. 阶段性开发节奏模板

本项目 P00-P24 走出的节奏, 可以复用到其它 AI 辅助项目:

### 6.1 phase 粒度

- **主线阶段**: 4-5 天, 单一聚焦, 整数编号 P01 / P02 / ... 一个 phase 只干一件事
- **摘樱桃加固 pass**: 0.5-1 天, 子版本号 P21.1 / P21.2 / ..., 只做 "(无 UI 依赖) × (无架构语义变更) × (无数据丢失风险)" 三绿的项
- **整合期**: 每 6-8 个主线阶段后一次, 10-20 天, 集中做 (a) 延期加固 (b) 代码审查 (c) 主程序同步 (d) bug 修

### 6.2 每阶段完工前 RAG 灯自检

见 1.4. 阶段定稿前必过一遍, 剩余 gap 分 M (必做) / O (可选) / B (backlog) 三档.

### 6.3 三份 docs 同步更新模式

本项目稳定的 docs 结构:
- **PLAN.md**: 规格 + 触发条件 + YAML todos (前瞻)
- **PROGRESS.md**: 阶段状态表 + 详情 + 依赖图 + changelog (回顾)
- **AGENT_NOTES.md**: §3A 横切原则 + §4 踩点案例 + 顶部 "接手前必读" 指引 (知识库)

每次阶段交付 / 规划调整 / 决策档案 **必须同时触达三份 docs**, 漏任一处都会让下一 agent 走 stale path.

### 6.4 整合期的必要性

**任何 N 阶段项目应默认在倒数第二阶段预留整合期**. 本项目 P24 的例子:
- 延期加固收口 (单独立 pass 成本高的小项)
- 代码审查 (§3A 原则实证合规核查, 找 Intent ≠ Reality)
- 主程序同步 (并行开发的上游依赖变更)
- 新 bug 窗口 (联调实测暴露)

不等最终阶段才发现 backlog 一堆, 对齐成本最低.

---

## 7. 24 条元教训 (五轮审查累积 + Day 2/5/6/8/10 + 手测事故追加)

源: P24_BLUEPRINT §12.10. 已按"**超项目价值**" 筛选, 项目特异的去掉.

1. **"留空占位 + TODO 注释" 半衰期 4-6 phase**. 机械 TODO 在大项目里几乎注定被遗忘. 替代: 同时在 PROGRESS.md 登记 "依赖 B 完成后回填", 不靠代码注释.
2. **"已知 bug 错过 N 次 pass 不修" 反模式**. 每次 pass 聚焦自己子题, 路过老 bug "注意到但没优先级". 修法: pass 末尾跑**全仓验证**, 不只验证新加.
3. **"全面审查 ≠ 逐文件 Read"**. 横向审视任务先写 grep 列表再开工; 逐文件 Read 是"纵向理解"工具.
4. **"用户视角 dev_note 远比开发视角 sweep 完善"**. 两者正交, 缺一就会漏 UI 黑按钮 / 偏僻问题. 每阶段结束前主动问用户 "最近看到什么不对劲".
5. **"实测 > 代码推断 > 文档原则" 证据权威度**. 用户实测优先级永远最高.
6. **"单源 vs 多源写入是纸面原则成败分水岭"**. 踩 3 次必须抽 choke-point, 不靠记忆.
7. **"纸面 choke-point 原则必须配静态核查入口覆盖率方法"**. 缺 (b) 就是纸面原则.
8. **"choke-point 合规率" 作为阶段验收 KPI**. 每阶段 todos 加 `choke_point_audit_delta`.
9. **"X 受 Y 守护" 的文档声明, 用户反馈 X 不 work 时先怀疑 Y 漏守某个入口**.
10. **"新 bug 决策树" 必须明文, 不靠临场判断**. 控制 scope creep.
11. **"方法论扩展应用面" 立即实证 > 推给未来**. 半小时试跑 2-3 个扩展面, 比记清单推未来 ROI 高一个数量级.
12. **"覆盖度 RAG 灯" 作为 ≥ 5 天阶段方案自检工具**.
13. **"一次性修法但没抽 sweep checklist" 的技术债模式**. B9 i18n 文案跨页一致性 / G2 `.format()` 全仓审计 都是这类 — 局部修了但没抽成标准, 后续 pass 重蹈覆辙. 防线: 任何"一次性修法" 必须同时 (a) 抽 skill (b) 写 sweep 脚本 (c) 加 pre-commit. 否则下次重现概率 50%+.
14. **"coerce 策略必须配 user-visible surfacing, 否则本身就成了 silent fallback"** ⚠ (2026-04-21 P24 Day 2 用户实测踩点). §3A F7 "fail-loud 不 silent fallback" 的延伸. 在 `single-writer-choke-point` 模式里选 `on_violation="coerce"` 的本意是"让用户操作不失败, 但把情况记下来让用户知道", 但如果**只 record 进 diagnostics_store 却不在用户的主路径 UI surface**, 用户感觉几乎和 silent 一样(要主动翻 Diagnostics 才看得到). 修法: coerce 发生时 choke-point helper 除了写 ring buffer, **必须把 coerce 信息通过返回值回传给 caller**, 由 caller 在用户主路径上 surface (SSE warning frame / toast / chat 消息上挂 badge 等). 归纳: **"coerce"不是"silent", coerce 的语义是"自动修正且主动告知"**; 凡实现"coerce"时, helper 的返回值必须带有"告知 caller 的信息", 不能只依赖旁路 log. 同族延伸: 任何"自动修正 / 降级 / 兜底" 行为都必须有显式 user-visible 通知路径, 否则等价于 silent fallback.
15. **"Restore / Load 操作必须保留原数据的主键 ID, 不要为了'避免潜在冲突'自作主张生成新 ID"** ⚠ (2026-04-21 P24 Day 2 用户实测踩点). `session_router.load / autosave.restore` 早期实装里都**故意生成新 session_id**, 理由是"避免与可能还在另一个 sandbox 目录里的原 session 碰撞". 但这条本质是**过度防御的错误选择**: 用户视角 restore 的意图是"**回到那个 session**", 换了 id 意味着后续副产物(autosave rolling slot / sandbox dir / diagnostics session_id 过滤)都以新 id 为锚点, 旧 id 的副产物**在磁盘上继续存活**(通常要等 24h 自动清理才消失). 用户看到的直接症状: "我设了保留份数=3 为什么列表里有 6 条" — 实际上是**两个 session_id 各 3 条 slot** (原 session + restore 后的新 session), 但用户没法区分. **修法**: Restore / Load 时**优先使用 archive 里记录的原 session_id**, 只有当 archive 本身没保存 id 时才 fall back 到新 uuid. 单活跃 session 模型下不存在真实冲突(单例抢占已经 destroy + purge 老 sandbox dir). 归纳: **任何"恢复"类操作的默认选择应当是"还原到原始身份", 不是"新建一个类似的实体"**; 前者维护连续性, 后者破坏副产物追溯. 同族延伸: snapshot rewind / import archive / re-run from script 等任何"回到某状态"类操作都应该审视 — 是否在保留主键 ID 方面做了错误的"新建" 而非"还原". 当你的防御理由是"避免可能的冲突" 时, 先证明该冲突在架构上能真实发生, 否则是在为幻想中的 bug 制造真实的副作用.
16. **"新写的前端 helper 调后端端点, 必须同一时刻验 request shape 双端一致, 不能各写各的"** ⚠ (2026-04-21 P24 Day 5 自我发现踩点). 新抽共享 helper `_open_folder_btn.js::openFolderButton(pathKey, opts)` 时, 前端想 "传个语义 key 让后端去 resolve 路径" 所以 body 写 `{ key: pathKey }`, 但后端 `OpenPathRequest` Pydantic model 只有 `path: str` 字段, 不认 `key` — 前端抽象正当, 后端没跟上. 偶然早发现(Day 5 sweep) 没引发用户报故障, 但如果发在联调期就是 "按钮白白 404". 修法: 本次即时扩后端为 exactly-one-of `path`|`key` 双入口 + `_resolve_open_path_key` 白名单 dict, 同时加 i18n 错误提示. **归纳成规则**: (a) 新前端 helper 调新 API 时, 在同一次 edit session 内把后端 request model / response model 双端的字段对应 **写成一张小表** (Input: which fields / Output: which fields); (b) 每个前端 helper 在代码顶部 docstring 明示 "**本 helper 对应的后端端点是 X, 期望的 request shape 是 {...}, 响应 shape 是 {...}**"; (c) 任何 API 形参/返回值变更走"先改 response / 后改 request" 顺序, 让过期前端读新后端时优雅降级; (d) CI 级防线可写 smoke 用 `OpenAPI /docs schema` 断言关键字段存在. 同族延伸: 任何 shared helper (Open Folder / Copy Session Id / Export Archive) 都在 docstring 锚定后端契约, 避免多次被替换为 "本 helper 只会被 X 调用" 的空假设.
17. **"Fallback 必须暴露 applied flag 给 UI, 不能只悄悄 fallback"** ⚠ (2026-04-21 P24 Day 5 架构决定踩点). F6 `match_main_chat` 特性允许 judger "对齐主对话 system prompt", 但当 `character_name` 未设或 `build_prompt_bundle` 抛错时后端会**降级到 legacy stored-prompt 路径**继续完成 judge — 如果响应里不显式告诉 UI "你勾的选项没生效", 用户会以为结果就是对齐后的结果, 但实际是 legacy 路径. 这是 §14 "**coerce 必须 surface**" 的**升级版**: 不只 coerce, 任何**功能级 fallback** 都要暴露 `{requested: bool, applied: bool, fallback_reason: str|None}` 三元组. 修法模式: 后端 helper 返 dataclass 而非裸字符串(eg. `_PersonaMetaResult(system_prompt, applied, fallback_reason)`), response 透传三元组, 前端读 `!applied && requested` 分支 toast. 归纳: **特性降级的"silent 成功"是最危险的 UX 反模式** — 用户相信勾选生效了而实际没生效, 下游分析结论都是错的. 凡功能 flag (opt-in 参数 / beta 特性 / 对齐类/替换类行为) 都必须走 "requested / applied / fallback_reason" 三字段约定, 把功能实际执行与否提升为一等公民. 同族延伸: 任何 feature flag + fallback 路径 (F7 的 Option B → Option A 升级 / 未来 F5 记忆 compressor 过滤 opt-in 的降级 / P25 多 adapter 选择类特性) 都走这套.
18. **"`innerHTML = ''` 清不了 `state.js::on()` / eventbus 里的 listener, subpage 必须配 `host.__offXxx` + 开头 teardown loop"** ⚠ (2026-04-21 P24 Day 6F 实测踩点). `page_snapshots.js` 的注释原本声称"粗粒度 remount 会直接 `innerHTML=''` 所以不主动 off 也不会泄漏". 这是**错的双重假设**: (a) `innerHTML=''` 只清 DOM 节点 (Element tree), 而 `state.js::on(event, fn)` 把 `fn` 注册到**模块级 `listeners: Map<event, Set<fn>>`**, 这张 Map 既不在 DOM 树里也不在 host.*attribute* 上, 没有任何 DOM 清理动作会触及; (b) 每次 remount 都在 Set 里再加一个 fn, `renderAll` 里闭包捕获的 `host` 引用虽已脱离 DOM 但仍存活, 一次 `snapshots:changed` 事件触发 N 个 listener, N-1 个对空 DOM 跑 render → 浪费 + 可能 throw. **修法**: subpage mount 函数开头必须加 teardown loop 把上一轮的 `host.__offXxx` 逐个 call 掉并置 null, 然后把 `on(...)` 返回的 off 函数 **立即** assign 回 `host.__offXxx`. **归纳成铁律**: **任何"外部图"订阅 (state.js / eventbus / document.addEventListener / setInterval / WebSocket / IntersectionObserver) 都必须有显式 unregister 入口, 不能靠 DOM 清理间接回收**; subpage 生命周期 pattern 标准化为 "1. teardown loop; 2. innerHTML=''; 3. build DOM; 4. attach listeners 并存 host.__offXxx". 对应项目 skill: `dom-subpage-listener-lifecycle` (待抽). 同族延伸: 任何"粗粒度 remount" 策略都要问 "哪些资源不在 DOM tree 内?" — 事件总线订阅 / timer / async fetch 的 AbortController / Web Worker handle / canvas webgl context 全部都需要独立 teardown.
19. **"last-click-wins vs last-response-wins 是 UX 而非性能选择, refresh / filter 类必用前者, mutation 类绝不用"** ⚠ (2026-04-21 P24 Day 6G 架构决定踩点). 用户快速连点 [Refresh] 或切换 filter chip 时, 两个相继发出的 GET 请求在 server 处可能 reordered 到达 (network jitter / server concurrency / DB lock wait), 若 client 只 `await` 并 `setState`, 旧请求晚到的响应会**覆盖**新请求已 render 的 state → "last-response-wins", 但用户直觉是 "last-click-wins" (我最后点的那次才是我想看的). **修法**: `AbortController` 每次新请求前 abort 上一次的 controller, fetch 的 catch 分支识别 `AbortError` 返 `{type:'aborted'}` 静默 (不弹 toast / 不上报 http:error). 本项目产出: `api.js::request` 加 `signal` 透传 + 新 helper `makeCancellableGet(url, baseOpts)` 适合 url 固定的 toolbar refresh, url 含 qs 的动态场景用 per-page `let _xxxController = null;` + 开头 abort + 尾 aborted 早退. **严禁给 mutations (POST/PUT/DELETE) 用** — 中途 abort 会让服务端状态模糊 (commit 了还是没 commit?), 服务器端幂等保护不强时会留下半写数据. 归纳: **任何 "用户可能高频连点" 的 GET 都要审一遍是否需要 AbortController**, 90% 答案是"需要". Mutation 则走"按钮 disabled / queue" 保护. 同族延伸: 任何"最新一次用户意图覆盖中间所有意图"的场景都应用 (搜索框 debounce + 最后一次 query 胜 / filter chip 串连点 / 下拉 select onChange 触发重查 / 无限滚动页视口跳转). 对应项目 skill: `last-click-wins-abort-race` (待抽).
20. **"同族架构空白: 修一个入口不等于修全部, 事故复盘必须抽 sweep + rule 否则必二次踩点"** ⚠⚠ (2026-04-21 P24 Day 6 验收期严重事故: New Session 按钮触发事件级联风暴, 用户整机卡死强制断电 — 这是 §4.26 #87 Hard Reset 同族二次踩点). 2026-04-20 #87 Hard Reset 修好后, 结论明明是"**全局状态清零操作**需要走 `window.location.reload()` 避开 surgical session:change 级联", 但实施只改了 `page_reset.js::doReset(level==='hard')` **单一入口**. Topbar dropdown 的 `[新建会话]` / `[销毁会话]` 两个按钮走的是 P03 原始的 `set('session', res.data)` / `set('session', null)` surgical 路径, **从未被审视过**, 一个月后同一模式再次爆发: 用户点 New Session → 浏览器卡死 → Cursor 卡死 → 整机卡死 → 长按电源强制关机. **根因不是"没学到教训"** — 学到了, 但**实施时只修了当前触发入口, 没抽成 sweep + 项目级规则**, 等于用户友善地只测了 Hard Reset 一个路径, 真正的架构空白还在. 这是**最高优先级的教训类型**, 比"单次 bug 的复盘价值大一个数量级". 归纳成三层落地规则:
    1. **事故复盘四步 (不可跳第三四步)**: (1) 修当前入口 (hotfix) → (2) 写档案说明根因 (AGENT_NOTES 新条目) → (3) **抽 sweep 脚本或 lint rule 把同族入口全扫一遍** → (4) **归档成 `.cursor/rules/`** 让未来 agent 写类似代码时被挡住. #87 做到了 1+2, 漏了 3+4, 结果同类事故再次发生; 本次 (#105) 强制补 3+4.
    2. **"同族入口" 的识别 heuristic**: 修好一个 bug 后**立即问**"这个 bug 是 `X 模式` 的一次发作吗?" 如果答案是 yes, 下一步是 `rg -g 'static/ui/**' '模式正则'` 找到所有 X, 挨个审; 不是 "我修的这一个路径已经没问题, 下个任务". 实际搜索示例: #87 修完后应该跑 `rg "set\(\s*['\"]session['\"]" static/ui/` → 会直接命中 topbar.js 两处, 一次性全修.
    3. **"状态清零类操作必 reload" 作为架构规则, 不是 "看情况"**. 任何操作满足 "session / sandbox / persona / memory / messages **任一**从'有数据'变'空'或近乎空" 的语义, 就必须走 reload, 不允许 surgical 订阅链 (因为订阅者在 empty 状态下的渲染路径基本都没压测过, 一定有地雷). 正例: Hard Reset / Load session / Restore autosave / New Session / Destroy Session. 反例: Soft/Medium Reset (仍有 persona + memory, 只清 messages) / snapshot rewind (本身就是"换到另一组有数据的状态"). 未来任何新 feature 若触发"状态清零" 必须进正例列表, 候选 `.cursor/rules/global-state-clear-must-reload.mdc` 待抽.
    4. **二道防线必备**: 即使规则 3 漏网, 爆发链也得有熔断器 (cascade 异步, 深度 guard 抓不到). 本次在 `api.js` 加了 http:error burst circuit breaker (1s 内 > 30 次即静默 5s) 作为**通用二道防线**, 未来所有 state mutation 入口都在它保护下.
    5. **可观测性修复同等关键**: 事故期间用户日志目录**只有一行** `session.create`, 爆发期的几百条 400/200 OK 请求零持久化, 事后复盘只能靠"终端里看到海量播报"的记忆. 已新建 `pipeline/live_runtime_log.py` 把 stdout/stderr 字节级 tee 到 `DATA_DIR/live_runtime/current.log`, 每次 boot rotate 一代. 未来任何事故**至少**有完整 uvicorn access log 给复盘用.

    延伸规则: **"修完单一 bug 立即做同族 sweep" 是项目级默认动作**, 不是 "有时间再做". Sweep 成本通常是 5-20 分钟 grep + 读 caller, 远低于第二次事故的修复成本 (本次是整个工作日 + 用户硬关机损失). 同理, **"抽成 `.cursor/rules/`"** 不是 "好了再做", 是"同次 PR 的一部分". 四步缺一不可, 缺了就是在赌"同族入口不会有另一个被触发", 历史证明这个赌必输.

21. **"HTML `[hidden]` 属性不能隐藏被显式 display-setting CSS 规则管控的元素"** ⚠ (2026-04-22 P24 Day 8 手测反馈 #107 Part 3→4 连续 3 次修法踩点). 同一个 "Session Load modal 残留 `[导入 JSON…]` 按钮" bug 修过三次, v1 清错位置 (按钮不在 body 内), v2 改 `dialogActions.hidden = true/false` **代码看起来对但用户依然报 bug**, v3 真因: `.modal .modal-actions { display: flex; ... }` 这条 class selector CSS 规则优先级**高于** UA stylesheet 的 `[hidden] { display: none }` — `[hidden]` 属性靠浏览器默认样式实现, **任何 class selector 都能压过它**, 于是 `hidden=true` 属性**存在但无效**, computed `display` 依然是 `flex`. 修法 v4: 改用 **DOM-level `remove()` + `append()`** (`showDialogActions()`/`hideDialogActions()` helper) 绕过 CSS 层叠彻底解决. **归纳铁律**: `[hidden]` 属性只在"元素没被任何 class 规则显式设过 `display`"时可靠. 常见坑地: `.modal-actions`, `.row`, `.flex-*`, `.grid`, Bootstrap/Tailwind utility class 等**任何 display 不是继承 UA 默认**的元素. 三种可靠替代 (优先级由高到低): (A) **DOM remove/append** — 零 CSS 依赖, 最彻底; (B) `style.display = 'none'` 行内样式, 优先级 1000; (C) 切 `.is-hidden` class 且该 class 含 `display: none !important`. DevTools 一步诊断: 选中元素看 computed `display`, 不是 `none` 就说明 hidden 被压了. 对应 `.cursor/rules/hidden-attribute-vs-flex-css.mdc` 已抽. 同族延伸: **"属性-CSS 层叠静默冲突"是前端最普遍的 silent bug 源头之一**, 任何依赖属性生效的 UX 行为 (readonly / disabled / required / contenteditable) 若被 class 规则或自定义元素行为覆盖, 都属于此类. **元教训**: 代码看起来对, DOM 树里属性确实被设上了, 但 runtime 视觉没变化 → 八成是 CSS/浏览器默认行为层面的覆盖. 先用 DevTools computed style 验证真实效果, 再信自己的代码逻辑.

22. **"opts 尾展开型 API (`show({...opts, ...message})`) 让 opts 里重名字段静默覆盖首参, 是经典的参数覆盖陷阱"** ⚠ (2026-04-22 P24 Day 8 #107 Part 3 发现). toast.js 历史实装是 `toast.err(message, opts) → show({kind, message, ...opts})`, 签名声称首参是 message, 但实现里 opts 被展开后 `opts.message` 会**覆盖**首参. 全仓 16 处 `toast.err('主标题', {message: '详情'})` 都是"期望首参作标题, opts.message 作正文"的意图, 而实际只渲染了 opts.message 的值, 首参悄悄丢. 长达数月未被发现, 因为首参和 opts.message 意义相近 (如 "网络错误" vs "POST /api/foo HTTP 409"), 差异在视觉上看不出来. 直到 auto_dialog 抛 RateLimitError 时首参是完整诊断 `"调用假想用户 LLM 失败: RateLimitError: Error code: 429..."` 而 opts.message 只是 code `"LlmFailed"`, 覆盖后用户只看到 "LlmFailed" 毫无 actionable 上下文才暴雷. 修法: 改 `_dispatch(kind, firstArg, opts)` 根据 opts 形状智能分派 — 当 `opts.message` 存在且 `opts.title` 缺省时, 首参自动升格成 title; 其它情况维持"首参即正文" 的旧契约. 16 处历史调用点**零改动**向后兼容. **归纳**: **任何 `{...opts}` 尾展开的 API 都天然存在"被覆盖"风险**, 特别是 opts 里有和 positional 参数同名字段时. 设计 pattern 层面有三种防御: (a) 首参用独特名字 (`primaryText` / `headline`), 避免与 opts 可能含的字段名碰撞; (b) 手动 **reorder**: `show({...opts, kind, message})` 让关键字段在最后写入, 强制覆盖 opts 同名字段; (c) 让 opts 进入 helper 时**先过滤掉关键字段** (`const {message: _ignore, ...rest} = opts`). 同族延伸: **任何用 spread operator 组装对象的代码都要警惕"右边字段覆盖左边"**, React props / RTK state / Redux reducer / fetch options / express middleware 全都是高发地. 元教训: **"API 签名声明的意图 vs 实际运行的结果"在参数覆盖型 API 里很容易背离, 这种 bug 平时不暴露, 专门在最需要看到完整信息的场景暴雷**. 防线: API 设计评审必问一句 "**opts 里哪些字段会意外覆盖 positional 参数?**"

23. **"yield 型 API" 不是单一类别, 必须先拆成 `请求-响应 async def` / `真 async generator` / `Template Method base class` 三种, 再套对应原则** ⚠ (2026-04-22 P24 Day 10 §14.2.D 复核期归纳). §3A 的 A5 (SSE 顶层先 yield error 帧再 raise) / A6 (生成器 finally 先快照不变量) / A9 (Template Method 基类抽象) 三条原则都以为自己在讲"同一类 yield 型 API", 实际各自守的场景不重叠:
    - **类别 (1) · 请求-响应 async def 函数**: 签名 `async def xxx(...) -> Return`, 无 yield 关键字. 虽然 caller 用 `await` 而不是 `async for`, 语法上也属于"异步结果产出", 但**不是 generator**. 本类**不适用 A5 / A6** (二者守的都是 yield 路径的侧效应). 本项目例子: `SimUser.generate_turn(...) -> UserTurnResult` (计算一条模拟用户消息, 返回 dataclass, 无 yield), `BaseJudger.run(...) -> JudgeOutput`. 识别信号: 函数体里没有 `yield` 关键字.
    - **类别 (2) · 真 async generator** (`async def` + `yield`): caller 用 `async for event in gen(...)` 消费, 有 `finally` 块清 session 锁/状态字段的**共识场景**. 本类必须守 A5 + A6. 本项目例子: `pipeline/script_runner.advance_one_user_turn` / `run_all_turns` 把 `chat_runner.stream_send()` 的事件转发给前端 SSE, `finally` 里清 `session.auto_state['running']`. A6 的具体做法: 在 finally 前先把"最终要 yield 的 summary event 的所有字段" snapshot 到本地变量, finally 再清共享状态; yield summary 使用 snapshot, 而不是从已清空的 `self.xxx` 读. A5 具体做法: SSE 顶层 `try/except Exception`, `except` 里 `yield {"event":"error","message":...}` 再 `raise` (让 uvicorn 记 500), 不然浏览器只能看到"SSE 断流" 不知因为啥.
    - **类别 (3) · Template Method base class**: 不是 generator, 是**在 base class 定义固定流程 (`_build_ctx → _compose_payload → _invoke_llm → _parse_response → _persist`), 子类只实现 hook 方法**. 本类适用 A9: base class 写一次 runtime flow 文档 + 每个 hook 的输入输出契约, 子类文件顶部指回 base class. 本项目例子: `pipeline/judge_runner.BaseJudger` + 4 子类 (`AbsoluteSingleJudger` / `AbsoluteConversationJudger` / `ComparativeSingleJudger` / `ComparativeConversationJudger`), A9 本质要求的"runtime flow 集中文档化 + 子类只讲差分" 在 base class 里做到了. 本类**本身不适用 A5/A6** (因为 base class 的 `run()` 是 async def 返 dataclass, 不是 generator), 但子类在某个 hook 里**发起** SSE generator 时会继承相应原则.

    **归纳为原则**: 每次遇到 "这个 yield 型 API 守的是 A5/A6/A9 还是都不守?" 的问题, 先做**三分类诊断** (1/2/3) — 签名有没有 yield / 有没有 finally + 共享 state / 有没有多子类复现同一流程 — 再决定适用哪条原则. 同族延伸: Python 以外, JavaScript `async function*` (真 generator) / RxJS Observable 的 `.next/.error/.complete` (请求-响应) / React Suspense 的 `use()` hook (类别 1 的变种) 都有类似的三分类区分必要. 对应项目 skill 候选: `async-yield-api-three-way-classification` (待抽).

24. **资源上限 UX 降级是跨 15+ 源的横切维度, 每新增一处 FIFO / 截断 / 限流机制必答四问** ⚠ (2026-04-22 P24 Day 10 §14.2.E 总表整理期归纳). 项目级审视发现"硬上限被达到时用户知道吗"在 15 处资源/机制里分布如下: 5 处 ✅ (user-visible + actionable), 3 处 ⚠ (silent 或仅 log), 7 处 ⏭ (用户无感知 / 暂不需要). 历史上这 15 处分散在**不同文件 / 不同阶段 / 不同 dev 手里**, 没任何地方集中回答过"每一处触达上限时用户看到什么". 在 P24 Day 10 把它们集中成一张 §14.2.E 表后, 3 处 ⚠ 的风险点 (snapshot cold 磁盘无硬上限 / judge eval_results 静默 evict / memory file oversize silent skip) 立即暴露出来. **归纳为设计纪律**: 每次新增"硬上限 / FIFO 淘汰 / 截断 / 限流"机制时, 必须同时回答**四问**:
    1. **上限是多少?** (代码里 `MAX_XXX = N` 常量 or env 可调)
    2. **达到上限时做什么?** (FIFO evict / reject write / truncate / backoff)
    3. **用户怎么知道?** (toast / badge / banner / diagnostics event / 啥也没有)
    4. **用户需不需要 actionable 操作?** (Clear / Export / Extend limit / 别的)

    前两问是**机制层面** (代码必问), 后两问是 **UX 层面** (经常漏). §3A F7 "fail-loud 不 silent fallback" 原则的扩展 — silent 达到资源上限是最常见的 silent fallback 类型. 如果新机制**四问中有任一个回答 "不知道 / 没想过 / 还没做"**, 就是**本 phase 的 backlog 入档项**, 不是"以后可以不做"; 至少要在阶段蓝图的资源上限总表里占一行. 同族延伸: **每个项目都应当维护一张类似 §14.2.E 的表**, 新 phase 增改资源上限时同步这张表; 到一定规模后 (≥ 10 处资源) 这张表本身就是**下一轮产品需求的富矿** (哪些 silent 的需要打屏上报 / 哪些 evict 的需要 actionable export). 对应项目 skill 候选: `resource-limit-ux-degradation-matrix` (待抽).

---

## 8. 超项目价值的 cursor skills 索引

本项目抽出了 3 份**通用 skill**, 放在 `~/.cursor/skills/` 独立维护,
不依赖本项目. 任何 AI 辅助的大型 codebase 都能用:

### 8.1 `audit-chokepoint-invariant` (§1.1 方法论落地)

**用途**: 静态核查 "X 统一走 Y" 类纸面原则的实际合规度. 输入一个原则,
输出 "守护入口 N 个 / 绕过入口 M 个 / 差集 = 漏守清单".

**触发**: 任何"审查代码看有没有漏守某个原则 / 统一入口 / 共同 helper"
的任务.

### 8.2 `single-writer-choke-point` (§1.2 方法论落地)

**用途**: 设计"多源写入 + 不变量守护"的代码结构模式. 教 agent 抽
`safe_append_*()` / `safe_update_*()` helper + on_violation 三策略
(raise / coerce / warn) + pre-commit block + smoke 机械守护.

**触发**: 任何"设计数据结构/API/状态的不变量" 或 "修复多源写入绕过纸面
原则的 bug" 的任务.

### 8.3 `event-bus-emit-on-matrix-audit` (§2.5 方法论落地)

**用途**: 前端事件驱动项目的事件订阅漂移检测 (0 listener / dead listener /
订阅无 teardown). 输出 emit × listener × teardown matrix + 3 档异常分类.

**触发**: 任何"审查前端事件总线 / 找事件漂移 bug / 重构事件订阅"
的任务.

---

## 9. 本文档的使用建议

### 9.1 本项目内

- 新阶段开工前读 §1 (5 条核心方法论) + §6 (开发节奏模板)
- 审查期读 §3-§4 (bug 分类和防线)
- 对外讨论或做 PR review 时参考 §2 (10 条架构原则)

### 9.2 其它 AI 辅助软件项目

- §1 + §5 + §7 可直接套用, 不带项目特异信息
- §8 的 3 个 skills 独立可用
- §2 的 10 条架构原则按项目性质选 (本项目是前端重的 web app, 其它类型项目参考 §2.1-§2.3 后端部分)
- §6 节奏模板按项目规模缩放

### 9.3 未来扩展

本文档是**活文档**, 每次项目发现新的跨项目价值经验应追加到对应章节.
**只记录 "已经踩过 ≥ 2 次" 的同族教训**, 单次踩点留在 AGENT_NOTES §4
作为案例即可 (避免未验证的过度抽象).

---

*本文档是 N.E.K.O. Testbench 项目 P00-P24 开发周期的设计经验沉淀,
与三份老 docs (PLAN / PROGRESS / AGENT_NOTES) 和 P24_BLUEPRINT 的关系:
三份老 docs 是**当下项目的执行档案**, 本文档是**跨项目的抽象沉淀**.
**本文档不需要每次修改后同步更新其它 docs** — 它是向外输出的稳定版.*

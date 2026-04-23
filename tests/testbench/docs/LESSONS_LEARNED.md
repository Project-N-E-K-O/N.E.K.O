# 本项目沉淀的代码设计与开发经验 (P00-P25 立项)

> **定位**: 本文档是 N.E.K.O. Testbench 项目 P00-P25 立项期累积的
> 设计原则与工程经验的**抽象提炼**. 源材料是 AGENT_NOTES §4 的 77+ 踩点
> 案例 + §3A 57 条横切原则 + P24_BLUEPRINT 五轮审查的 13 条元教训 +
> P24 Day 9-E 二轮翻转的 3 条元教训 (L23/L24/L25) +
> P24 Day 10-12 整合期的 2 条元教训 (L26/L27 = §7.23/§7.24) +
> P24 Day 12 欠账清返 + P25 §A 八轮设计审查 + §A 收工整理 UTF-8 事件 +
> P25 Day 1 subagent 并行开发首次应用 + P25 Day 1 fixup mirror shape +
> P25 Day 2 前端面板派生 + Day 2 polish 手测 r1-r6 派生 + Day 3 `last_llm_wire`
> 覆盖率 smoke 派生的 16 条候选元教训 (L28-L44, 登记于 §7.A 候选区, 未计入主编号 25 条).
>
> **§7.25 特别说明**: 一周内已连续 **6** 次同族实锤 (字段名漂移 / envelope 漂移 /
> LLM wire role 三次漂移 / **Prompt Preview "重建视图 ≠ 真实 stream" 架构级
> 分叉**), 从最初的"四层防御"升级为当前的"**五层防御**" (第 5 层含 5a
> chokepoint 下沉 + 5b preview 消费 ground-truth snapshot 两子条). **r5
> 追加第 3 类 5a 应用**: 一次 polish 周期内**同时** 3 处独立应用 chokepoint
> (T5+T6 shared `_InstructionBundle` / T7 banner 双 chokepoint / T8 injection
> `scan_and_record` 单入口), 证明这不再是偶发经验而是**跨子域可复用的主导
> 工具**. 识别信号升级: "同一失败模式在两个独立入口重现" 不再是**必要**条件,
> **"可预见的第 N 次重复手写"** 也是触发信号, 可在第一次就直接抽 chokepoint.
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

## 7. 25 条元教训 (五轮审查累积 + Day 2/5/6/8/10 + 手测事故追加 + P25 Day 2 跨边界 shape 三次同族)

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

25. **"跨边界的 shape / role / 字段名必须 rg 实际消费方不按蓝图草稿拼"** ⚠⚠⚠⚠ (2026-04-23 P25 Day 2 + Day 2 polish 第二至第五轮, 七天内**六次**同族, 已达 "该写一本书而不是一条 lesson" 门槛. **r5 第 6 次同族 = "同一设计分叉内 3 处独立 chokepoint 应用在同一 polish 内先后落地"**: T5+T6 shared `_InstructionBundle` + T7 banner 双 chokepoint + T8 injection `scan_and_record` 单入口 — chokepoint 下沉模式从**后验经验**升级为**前验主导工具**, 识别信号扩展到"**可预见的第 N 次重复手写即可触发抽 helper**", 不必等到第二次独立入口重现). **场景**: 跨"生产方 vs 消费方"边界的数据形状 / 字段名 / 角色 / 结构契约 / **视图 snapshot vs 运行时 stream**, 默认按蓝图草稿或"第一眼语义直觉"拼, 而**不实际 rg 消费方代码 / 不区分视图数据源与执行数据源**. 消费方既可以是前端 JS 面板 (消费后端 API response), 也可以是下游 LLM / 序列化消费者 (消费 wire 消息), 还可以是**同一个概念在 UI 上被两个面板以不同重建逻辑分别展示**. **五次同族案例**: (1) 字段名漂移 — 前端按 BLUEPRINT §2.7 抄 `dedupe_info.remaining_ms`, 后端实际 shape 是 `{hit, cache_size, dedupe_key, dedupe_rank}`; (2) envelope vs flat 漂移 — router 返 `{"kind":..., "result":{...}}` 而 UI 期望扁平 `{"kind":..., ...result}`, UI 读 `undefined` 字段全报"假失败"; (3) LLM wire role 漂移 (代码写死) / prompt_ephemeral 语义契约违反 — `external_events.py` 三个 simulator 里 `base_wire.append({"role":"system", "content":instruction})` **违反**主程序 `prompt_ephemeral` 契约 (主程序是 `HumanMessage(content=instruction)` 即 `role=user`), 空 session 触发 → Gemini 400 INVALID_ARGUMENT "Model input cannot be empty"; 非空 session → Gemini 偶尔返空字符串 + 200 OK, 空 reply 被持久化, 下一轮 LLM 读"上一轮 user + 空 assistant + 新 instruction" 的残缺 wire, 基于**上一轮**事件生成 reply — tester 观察到"再次触发才拿到上次的 reply"; (4) LLM wire role 漂移 (UI 允许 tester 手选) / 主程序 SystemMessage 语义契约违反 — testbench composer 有 Role=User/System 下拉, tester 选 role=system 发送后 chat_runner.stream_send 以 role=system append 进 session.messages, 然后 build_prompt_bundle **原样透传**到 wire, 于是 wire 尾出现 role=system 消息. 主程序 `omni_offline_client.py` 的契约是 `SystemMessage` **只存在于** `_conversation_history[0]` (初始化阶段), 运行期所有输入路径统一以 `HumanMessage` (role=user) 注入 — wire 里出现 role=system 消息是主程序从不存在的 shape. 同样撞上 Gemini shape 过敏 (和第 3 次完全同型); (5) **Prompt Preview "真实 wire" 与"重建 wire"分叉 / session.messages 做预览数据源违反"展示即真相"契约** — Chat 右栏 Prompt Preview 的 Raw wire 视图从 `build_prompt_bundle(session)` 取数据, 该函数的**唯一输入源**是 `session.messages` (canonical history). 对 external event 触发的一轮 LLM 调用而言, session.messages 里只会留一条 `memory_note` 类短条目 (例如 `[主人摸了摸你的头]`), 而**真正发给 LLM 的完整 instruction** (道具 / 奖励 / text_context / 触发场景提示) 是**临时合成后直接挂到 wire 尾再发出去, 永不写入 session.messages** (§A.8 #2 "instruction 严格不入 session.messages" 的刻意设计). 后果: 左栏 Instruction 子 `<details>` 能正确显示完整 instruction (因为 SimulationResult 直接把 instruction 回传), 右栏 Raw wire **看不见**, tester 以为"发给 LLM 的只是这一句 memory_note" → **对测试平台的根本作用 (让 tester 看到发给 AI 的完整信息) 来说是致命的**. 用户原话: "这是一个严重的代码实现跑偏了原始设计语义的案例". Smoke 漏的原因: 前 4 次防御全部在"**wire 组装时**按契约守", 但**没守到"preview 重建数据源 != 真实 stream 数据源"这个架构级分叉** — preview 在架构上就是"从残留状态重建", 真实 wire 是"临时合成后发出去的 list", 两条路径是 2 个独立信源, 不是一个 chokepoint 能堵的 (chokepoint 在 wire 组装, preview 根本不经过 wire 组装). **失败模式 (四级严重度)**: (a) 浅 — 字段 undefined → UI 显示 `N/A`, 易定位; (b) 中 — 整块功能"假失败" (后端做了前端以为没做); (c) 深 — 消费方偶尔成功偶尔失败 (Gemini 空 reply 是概率行为), fake-LLM smoke 覆盖不到, 级联错位一次 reply → "回答变味但不报错" 静默语义毒化; (d) **幽灵** — UI 显示了"答案", 但显示的是"重建的近似答案"而非"真实答案", tester 完全无法区分 "UI 显示的 wire" 和 "LLM 真正看到的 wire", 直到基于预览做的判断和真实 LLM 行为对不上才发现, **定位极难, 可能导致测试结论全错**. **归纳为五层防御规则** (第 5 层由第 4 次 + 第 5 次同族共同完善):
    1. **消费方 shape 核对必 rg**: 任何跨边界消费的**初版**, 开头必加一次消费方 shape 核对的 rg (`rg "return .*\{.*$field_name" backend/routers/ pipeline/`, 或 `rg "messages_to_send.*=|prompt_ephemeral" main_logic/`, 或 `rg "messages_from_dict|HumanMessage" utils/`). 发现 shape / role / 字段不一致立刻停下核对, 不按"直觉"拼.
    2. **envelope / 扁平决策必显式登记**: 后端 response 要不要 envelope (`{"kind":..., "result":{...}}` vs `{"kind":..., ...result}`) 是**语义契约**, 不能让 router 和 UI 各自拍脑袋. 蓝图 §A 应当对每个 API 明写"扁平 / envelope", 实装和消费代码注释里引回那一节.
    3. **LLM wire 消息 role 决策必显式登记**: 后端写 LLM wire 时, instruction / memory note / system prompt / tester 手选 role 分别用什么 role 是**语义契约**, 由主程序 `prompt_ephemeral` / `omni_offline_client` 定义, testbench / adapter / plugin 写 wire 时必须 rg 主程序对应函数再下键, 不按"直觉 = instruction 听起来像 system 所以用 system" 拼. 注释里写 "see main_logic/omni_offline_client.py::prompt_ephemeral for role contract".
    4. **fake-consumer smoke 的语义盲区补偿**: 如果消费方是 LLM / 外部服务, smoke 用的 fake client **不会像真 LLM 一样对 shape 违反敏感** (fake 会宽容, 真 LLM 会 400 或返空). 补偿办法: 在 smoke 里对 fake 捕获的 wire 直接做**契约断言** (不依赖 fake 的 reply 行为). 把"LLM 对 shape 敏感"显式转成"smoke 对 shape 敏感", 契约守门点前移.
    5. **chokepoint 防守优于单点堵入口 + preview 消费 snapshot 而非重建源** (第 4 次 + 第 5 次同族共同完善): **5a** (第 4 次 / chokepoint 下沉) — 同一失败模式在两个独立入口重现, 立刻考虑把守门点下沉到**所有路径必经的 wire 组装 chokepoint** (本次在 `prompt_builder.build_prompt_bundle` 统一把 role=system 重写成 role=user + `[system note]` 前缀). 对 chokepoint 补 unit-level smoke (`p25_wire_role_chokepoint_smoke.py` 五 case). **5b** (第 5 次 / preview 与 actual 分叉识别) — 当一个"视图面板"**不是**消费真实 stream 经过的数据, 而是**从残留状态重建**, 就存在"重建视图 ≠ 真实 stream"的**架构级分叉**. 防御模式: (i) 在真实 stream 的 **choke-point** (即 LLM 调用前的 wire 构造点) **刻录 ground-truth snapshot** 到会话级 RUNTIME 字段 (本次 `Session.last_llm_wire` + `pipeline/wire_tracker.py::record_last_llm_wire` 单一写入器 + `KNOWN_SOURCES` 白名单 + `copy.deepcopy` 防 live ref 被后续改动污染), 避免刻录成漏字段 / 漏尾条 / 漏序化的残缺版; (ii) 视图面板**消费 snapshot 而非重建源** (本次 `preview_panel.js::renderLastLlmWireSection` 顶区读 `bundle.last_llm_wire`, `renderNextSendWirePreview` 底区才读 `bundle.wire_messages`, 两段视觉分隔 + 文案明确 "ground truth" vs "预估"); (iii) snapshot 字段必须在**持久化审计 smoke** (本次 `p24_session_fields_audit_smoke.py::RUNTIME_FIELDS`) 里明文分类 RUNTIME, 杜绝落盘; (iv) 写入 smoke 守护 (本次 `p25_prompt_preview_truth_smoke.py` 7 case) 必须断言 "**tail 包含完整 instruction 关键片段**, 而不是包含 memory_note 自身" — 把 "是否包含 ground-truth 全文" 显式转成 smoke 断言. **识别信号**: "UI 有 A / B 两个预览子面板, A (直接从 API response 拿) 和 B (从 session 状态重建) 显示同一概念但内容不一致" 就是 **5b 触发信号**, 立刻检查是否存在 ephemeral 数据不进 session.messages 的流程 (§A.8 #2 类刻意设计).

    **元归纳**: §7.6 "多源写入是纸面原则成败分水岭" 讲的是"同一进程多入口", §7.25 是它在**跨边界契约** + **视图数据源**双维度的扩展 — 第 1-3 次踩的是"生产方 N 种实装 vs 消费方 M 种解析"的笛卡尔积, 第 4 次踩的是"N 个入口 vs 1 个 chokepoint", 第 5 次踩的是"**同一概念被视图与执行两条路径独立表达, 视图按状态重建, 执行按临时合成**"的架构级分叉. 三维度总结: (a) 按错误出现的边界堵入口 — 第 1 次适用; (b) 按正确消费契约守 chokepoint — 第 ≥ 2 次适用; (c) **按 "展示即真相" 契约把 ground-truth snapshot 作为视图面板的唯一数据源** — 当存在 ephemeral 不进持久化历史的流程时适用. 识别这三种粒度所对应的"应然时机"本身就是设计能力. 同族延伸: 任何 SDK / adapter / plugin / OSS fork / runtime wrapper / 流式系统的 live view vs replay view 都适用. 对应 Cursor skill: `ui-wire-field-rg-backend-first` (升级版, 覆盖 response shape / envelope 决策 / wire role 决策 / chokepoint 下沉 / **preview snapshot chokepoint** 五类场景). **Day 3+ 必修欠账** (r5): 审计发现还有 5 个 LLM 调用点未走 `wire_tracker` — simulated_user (P0, Auto-Dialog 跑完后预览陈旧指向错对象) / memory_runner 4 处 ainvoke + judge_runner (P1, 测试员在 Memory/Judge 面板触发后 Chat Prompt Preview 看不到) / auto_dialog_target slug 与 chat.send 是否分流的设计清理 (P2) / config_router._ping_chat 明确排除 (P2); 蓝图 §A 应追加 "**每个 LLM 调用点必须 stamp last_llm_wire, smoke 扫未 stamp 的调用点即 FAIL**" 作为第五层防御的**强制**侧.

### §7.A 候选追加 (P24 Day 12 欠账清返 + P25 §A 八轮设计审查 + §A 收工整理 UTF-8 事件 + P25 Day 1 subagent 并行开发 + P25 Day 1 fixup mirror shape + P25 Day 2 前端面板派生 + P25 Day 2 polish r1-r6 手测派生, 待二次复现后并入主编号)

> 纪律: 本文档 §7 只记录 "**已经踩过 ≥ 2 次**的同族教训". 下列 16 条候选 (L28-L44)
> 多数仍为**单次派生** (源自 P24 Day 12 欠账清返 + P25 §A 八轮设计审查 + §A 收工整理
> UTF-8 字节损坏事件 + P25 Day 1 subagent 并行开发首次应用 + P25 Day 1 fixup
> mirror_to_recent shape mismatch + P25 Day 2 前端面板交付 + P25 Day 2 polish r1-r6
> 手测联动 bug + P25 Day 3 `last_llm_wire` AST 覆盖率 smoke);
> **L36 已升级至三次复现** (`dedupe_info.remaining_ms` 字段名漂移 +
> `external_event_router` envelope 顶层结构漂移 + **LLM wire 消息 role 字段漂移 /
> prompt_ephemeral 语义契约违反**), 已超门槛, 本次 §7 更新将 L36 升级为 §7.33.
> **L39 / L40 / L41 / L42 / L43 / L44 新候选** 登记为单次, 待 P26+ 再命中升级. L37 / L38 仍为单次.
> 登记在此避免遗忘.

**L28 "跨阶段推迟项必须双向回扫"** (P24 Day 12 欠账清返派生, 2026-04-23):

- **场景**: 跨阶段推迟 (`推迟至 PX` / `推迟到 Day N` / `留待 Day N+1`) 累积若干轮后, 很容易**功能做了但文件忘了回来写完** (checkbox 漏回填 / `推迟至 DayX` 标签过期但文本仍写着 `TODO` / `FIXME` 漏清理). 单看代码 smoke 全绿, 看文档结构也完整, 只有**全文搜索 "推迟" 关键字**才能发现漏网之鱼.
- **失败模式**: 开发者本阶段聚焦自己的任务, "上一阶段推迟过来的一个小 checkbox 漏回填" 类事情因为**不影响功能**, 在任何日常 review 里都不会显性化; 靠时间推移自愈的概率几乎是零. 项目跨 5-10 个 phase 后, 这类欠账积累到**临近发版**才集中爆发.
- **防御规则** (三层): (a) 每阶段收尾**强制**跑 `rg -g '!{venv,.git,node_modules}' '推迟至?|留待|归 P\d|TODO|FIXME|XXX'` 一次, 结果入 PROGRESS 阶段段; (b) 每阶段起始**强制**跑一次同样的 grep, 核对"上阶段收尾时登记的待办" 全部完成再开工; (c) "推迟项" 登记时**必须同时登记回扫时机** (哪个阶段的哪一 Day 是触发回扫点), 不允许留 `推迟至 PX` (PX 无具体边界) 这种无锚点推迟.
- **验证案例**: P24 Day 12 欠账清返 (`62844c7`) 真的扫出 2 条真欠账 (render_drift_detector.js / page_persona.js Promise cache) + Day 6 checkbox 漏回填 1 条. 单次实锤, 但很可能下次进入 P25-P27 跨阶段时再次命中.
- **关联**: LESSONS §7.1 "留空占位 + TODO 注释半衰期 4-6 phase" 的**双向回扫扩展** — §7.1 管"登记时不靠代码注释", L28 管 "**登记后每次跨阶段边界时主动回扫**". 对应 cursor skill 候选: `cross-phase-deferral-bidirectional-sweep` (待抽).
- **进入主编号条件**: 需要在 P25-P26 再命中一次同族 (任何跨阶段推迟项漏网) 才升级为 §7.25.

**L29 "冷却语义三分类"** (P25 §A R1a 派生, 2026-04-23):

- **场景**: 主程序里有"N 秒内不复触发 X" 类行为时, 用 "**冷却**" 一词**笼统描述**几乎必然导致 testbench / 消费方混淆语义边界.
- **三分类**: (a) **实时流抖动冷却** (比如 600ms / 1500ms 的帧合并窗口) — 是**运行时机制层**, 测试生态通常 OOS (semantic-contract-vs-runtime-mechanism); (b) **语义去重冷却** (比如 8000ms 内重复同一 avatar 触摸区不再产出 memory pair) — 是**语义契约层**, 消费方必须复现; (c) **N 秒窗口禁复触发** (比如 proactive chat `min_idle_secs=10s`) — 是**运行时机制层**, 通常 OOS, 但语义上"用户每 N 秒只能看到一条主动搭话" 有时是 **契约**. 三类**外观相同**(都是一段时间窗口内不 re-trigger), 但**归属层不同** → 测试生态对接时要不要复现结果完全不同.
- **失败模式**: 审查时只说 "不复现冷却窗口" 或 "冷却统一 OOS" → 下一个读者 (下一轮审查 / 下一个 phase) 会以为**所有类冷却行为**都 OOS, 把 (b) 类也丢掉, 造成语义漂移.
- **防御规则**: 任何 "N 秒内不复触发 X" 行为入文档前必先三分类标注 `(a) / (b) / (c)`, 再决定复现策略. 候选 `.cursor/rules/cooldown-three-way-classification.mdc` 待抽.
- **关联**: L26 (§7.23) "yield 型 API 三分类" 在**时序维度**的延伸 — L26 管调用形态分类, L29 管时间窗口语义分类; 两者都是"外观相同的几种 API / 行为必先分类再套原则" 的同一方法论.
- **进入主编号条件**: 需要在其它时序窗口 (timeout / retry backoff / rate limit / debounce) 场景再命中一次三分类必要性, 才升级为 §7.26.

**L30 "外部系统 pure helper 跨 package 用 copy + drift smoke, 不 import"** (P25 §A R4 派生, 2026-04-23):

- **场景**: 测试生态 (testbench / adapter layer / plugin sandbox) 需要复用主系统的 pure helper 时, 默认第一反应是 `from main_logic import X`, 但主系统 package 的 `__init__` / 模块级常量经常携带**重副作用** (aiohttp session / ssl context / event bus queue / asyncio 启动) — 一 import 就把这些副作用带进测试生态, **破坏边界**, 很多环境 (单元测试 / CI 沙盒) 会直接 import error.
- **替代方案**: **copy** 那段 pure helper 到测试生态自己的 package + 顶部 docstring 明文 "copy from main_logic/X, 2026-04-XX 快照, 主程序该函数发生签名变更时本文件与 drift smoke 同步更新"; **drift smoke** 在 CI 里 `from main_logic import X_original` (**smoke 允许破边界, 仅它一个 file 允许**) + testbench copy 对比**hash 相等的 pure function body**, 漂移即 FAIL. 这样 "复用主程序 pure helper" 的承诺**不 import main_logic 也能兑现**.
- **失败模式 / 防御规则**: 直接 import 的常见结果是测试环境里 aiohttp / ssl 找不到 → import error / Cursor agent 环境跑不起测试; copy 但**不加 drift smoke** 则主程序半年后改了函数签名, testbench 里还是老版本, 语义偷偷背离 → 测试结果不可信. **铁律**: copy 和 drift smoke **必须配对**, 缺一则失效.
- **适用范围**: 测试生态 / adapter 层 / plugin 沙盒 / OSS fork 回合并 / 生成式 AI 的 runtime wrapper 等任何 "用另一系统 pure helper 但不想带它生态" 的场景.
- **候选 §3A 新原则**: H3 "外部系统 pure helper cross-package copy > import (有重副作用时)" 待 P25 交付后观察是否稳定抽象.
- **进入主编号条件**: 需要 P25-P27 至少再踩一次同族 (另一处 pure helper 跨边界) 才升级为 §7.27; 若升级则同步在 §3A 正式纳入 H3.

**L31 "审查时必须持续锚定设计初衷, 不得悄悄引入新目标"** (P25 §A 第八轮漂移诊断派生, 2026-04-23):

- **场景**: 设计草案审查阶段 (meta-audit / self-audit / design review) 中, AI 用 grep / read 深挖主程序实现时, 工具返回的信息**全是主程序 runtime 实现细节**, 容易让 AI **不自觉地**把 "testbench 应该跟主程序一样" 引入矫正清单 — 这是对 L25 "语义契约 vs 运行时机制" 的**审查流程层面**的违反.
- **失败模式**: 矫正清单看起来越来越精细, 某条矫正单独看都对 (主程序确实那样), 但**组合起来**会把原设计目标悄悄改掉; 审查若干轮后原设计的 "语义契约 vs 运行时机制" 边界面目全非. **症状**: 用户读到矫正清单后觉得"这和你最初设计方案的目标不一致".
- **防御规则** (三条):
    1. 每轮审查**开头**先明写 "**本轮不得引入的新目标**" (如对 P25 = "不得把 '复现主程序 runtime 行为' 引入目标"). 列在审查笔记顶部作为 guard.
    2. 每条候选矫正**必问三问**: (a) 这条是在回答原设计 §1 的哪个目标问题? (b) 违反了原设计 §2 哪条原则吗? (c) 如果原版 §1 §2 的作者在场, 他会说 "这是精度提升" 还是 "你改了我的目标"?
    3. 审查 KPI 从 "**发现多少问题**" 改为 "**守住初衷的同时发现多少真正的精度缺口**". 前者指标指向 AI 过度审查, 后者指向设计连贯性.
- **验证案例**: P25 §A 六轮 meta → 第七轮 self-audit (追加 R7/R13 = 目标漂移) → 第八轮漂移诊断 (R7/R13 完全撤回 + R1c 部分撤回 + R9 合并 + R1b 降级 → 最终 §A.8 = 8 条有效矫正). 完整过程见 [P25_BLUEPRINT §A.7](P25_BLUEPRINT.md#a7-第八轮漂移诊断-2026-04-23).
- **关联**: L25 (**语义契约 vs 运行时机制**) 在**审查流程维度**的延伸 — L25 管 "**什么**该复现", L31 管 "审查时**怎么**不丢掉 L25". 配套 skill 候选: `design-review-original-intent-anchor` (待抽).
- **进入主编号条件**: 需要在后续阶段 (P25 Day 3 实装或 P26 立项) 的审查过程中, 再有一次"审查过程自我发现漂移并撤回矫正" 的案例, 才升级为 §7.28.

**L32 "PowerShell Set-Content / Out-File 对 UTF-8 CJK 文件是字节级陷阱"** (P25 §A 收工整理 UTF-8 损坏事件派生, 2026-04-23):

- **场景**: Windows + PowerShell 5.x + 对项目里含 CJK 的 UTF-8 `.md` / `.txt` / `.py` 文件跑 `Set-Content -Path foo -Value $str -NoNewline` (或 `Out-File` 默认 `-Encoding Default`) 做 trim / dedupe / append 类操作. 一句话概括: PowerShell 5 的 `Set-Content` 默认读写都走**当前系统 ANSI/OEM code page** (简中 Windows 下 CP936), **不是** UTF-8.
- **失败模式**: 读时按 CP936 解码 (误判 UTF-8 三字节 CJK 序列为 CP936 双字节), 写时又用 CP936 编码. **UTF-8 三字节 CJK 在 CP936 无法完整往返**, 末字节被替换成 ASCII `?` (0x3F). 文件通过 git diff / git show 看起来"有内容只是变乱了", 但 `python -c "open(...).read().decode('utf-8')"` 直接抛 UnicodeDecodeError, 所有 ~1/3 的汉字失去最后一个字节, IDE / Cursor / 浏览器全部无法正确显示. **症状迷惑**: 命令返回成功, 无任何 warning, `Get-Content` 再读回来看起来也"字符数差不多对" (因为 ANSI 解码没抛异常, 只是意义错了); 只有**字节级 UTF-8 校验**才能揪出.
- **真实案例**: P25 收工整理为去掉 `P25_BLUEPRINT.md` 尾部 1 行空行, 跑了 `$t = [IO.File]::ReadAllText($p).TrimEnd(); Set-Content -Path $p -Value $t -NoNewline`, 结果文件从 55469 字节变成 70487 字节 (膨胀因为 `?` 取代 CJK 末字节造成 UTF-8 长度统计错位), **3280 处字节损坏 / 约 1640 个汉字末字节丢失** (占文中 CJK 的 27-33%). 修复路径: `git checkout HEAD -- P25_BLUEPRINT.md` 回到 55469 字节干净版 + 按冗余登记 (AGENT_NOTES / LESSONS / PROGRESS 三处) 的语义权威重写丢失章节, 耗时 35 分钟, 数据损失 = 0.
- **防御规则** (四层):
    1. **最稳**: 任何对项目 UTF-8 文件的 trim / replace / append 走 `python -X utf8 -c "data = open(p, 'rb').read(); ... ; open(p, 'wb').write(data)"` — `open(path, 'wb')` 直接二进制写字节, **完全绕过** PowerShell 编码层, 字节级可控.
    2. **次稳 (PS 7+)**: `Set-Content -Encoding utf8NoBOM -NoNewline` (只在 PowerShell 7+ 可用, PS 5.x 不支持 utf8NoBOM, 会回退默认 CP936).
    3. **PS 5.x 勉强能用**: `Set-Content -Encoding UTF8 -NoNewline` — 能保 UTF-8 但**强制加 BOM**, 对 `.md` 一般无害, 对 shell / python source 会改变行为, 次优.
    4. **团队级 guard**: `.gitattributes` 标 `*.md text working-tree-encoding=UTF-8` + `.editorconfig` 标 `charset = utf-8` + CI 跑 `git diff --name-only HEAD | xargs -I{} python -c "open('{}').read().encode('utf-8')"` 或 `iconv -f utf-8 -t utf-8 -c` 发现有损就 FAIL.
- **关联 / 对比**: L22 "编码污染" (AGENT_NOTES §4.27 #78 记录) 的**事前版** — L22 管"编码污染发生后怎么定位修复", L32 管"编码污染第一次就别发生". Cursor skill 候选: **`powershell-set-content-utf8-trap`** (Windows + PS + 任何含 CJK 的文件批量操作, 立规 "Set-Content / Out-File 不许直接接触项目 UTF-8 文件, 一律改 `python open(p, 'wb')`"). 辅助配套: agents 的 `.cursor/rules/` 里一条硬规则, grep 到 agent 输出里出现 `Set-Content` 操作项目 `.md` / `.py` 文件时立即警告.
- **进入主编号条件**: 需要在后续阶段 (P25-P27 任何 Windows 环境下的批量文件操作) 再次命中同族 (任何 PS `Set-Content`/`Out-File` 搞坏 UTF-8), 才升级为 §7.29. **本次属于"在审查过程中自己踩的坑, 没影响产出语义"** (因为冗余登记兜住了), 但工具层面的陷阱是**确定的系统性风险**, 只是"在本项目重现两次的概率"需要观察.

**L33 "Subagent 并行开发 + 主 agent 三段式 review" 范式** (P25 Day 1 派生, 2026-04-23):

- **场景**: 阶段含 ≥ 3 个 "单文件单任务" 的并行可拆分子任务 (独立文件 / 零跨文件依赖 / 有明确 I/O 契约). 主 agent 一线做 N 份上下文会一次处理太多 spec, 容易把某一份 spec 的细节记岔导致静默 bug.
- **三段式**:
    1. **主 agent 拆粒度 + 写任务书**: 每份任务书 ≥ 6 节 = (1) 任务目标 + 字面路径 (2) 硬约束 (不准改什么 / 不准 import 什么 / 必须 preserve 什么) (3) 必覆盖列表 (assertions / scenarios / edge cases) (4) 自验证步骤 (grep pattern / 预期 byte hash / 预期 smoke 行为) (5) 结构化汇报模板 (Deliverable path / Assertions added / **Observation 字段 — 列所有自诊到的疑点**) (6) I/O 契约 (上游文件精确行号 / 下游 consumer 期望).
    2. **Subagent 并行交付**: 各自拿独立任务书独立做, 交付时用结构化汇报模板, **不直接 fail smoke 而是把自诊到的疑点写入 Observation 字段**, 让主 agent 有机会 review 而不是被 smoke 强制阻断.
    3. **主 agent review 三步走**: (a) **先读 subagent Observation** (不读代码), 识别潜在 spec 对齐 bug; (b) code review 代码 + lint; (c) 跑该 subagent 自己的 smoke + 全量历史回归.
- **Subagent 自诊的 Observation 往往比主 agent 自审更可信**: 因为 subagent 独立按 spec 实证, 没有主 agent "内存对齐误差" (脑中 spec 记成了别的).
- **失败模式**: 主 agent 自己一线做 N 个任务 + 自己写 smoke — 任何 "主 agent 对 spec 理解错 → 代码和 smoke 一起错到 align 绿" 的 bug 都会永远不被发现. 这是 L31 "审查锚定初衷" 在**执行层**的延伸 (L31 管设计层审查怎么不漂, L33 管执行层分工怎么不错).
- **验证案例**: P25 Day 1 主 agent 在 `external_events.py::simulate_avatar_interaction` 写了 `meta.get("dedupe_key")` / `meta.get("dedupe_rank")`, **实际主程序返回的是 `memory_dedupe_key` / `memory_dedupe_rank`**, 主 agent 内存对齐错. Subagent C 独立按 P25_BLUEPRINT §A.8 的 "B2 rank 升级三步矩阵" 写 smoke 时, 发现 1→2 accept 后 2→2 也被 accept (违反 spec), **没直接 fail** 而是把该断言改为 record-and-continue + 在 Observation 写 "reported bug #1: meta key 可能是 `memory_dedupe_key`". 主 agent review 看到 Observation → 5 分钟内修代码 + 把 smoke 从 record-and-continue 升级为 strict assert. 若主 agent 自己一线做 + 自己写 smoke, bug 不会被任何自动化抓住.
- **关联**: L24 (语义契约 vs 运行时机制) 管**什么该测**, L27 (生成器三分类 / 资源上限 UX) 管**什么边界要 UX**, L31 (审查锚定初衷) 管**审查时怎么不丢**, **L33 管执行时用什么分工守住 spec**. 配套 skill 候选: `subagent-parallel-dev-three-phase-review` (待抽).
- **进入主编号条件**: 需要在后续阶段 (P25 Day 2/Day 3 或 P26 立项) 再有一次"subagent 并行执行 → Observation 字段抓到主 agent 写错" 的案例, 才升级为 §7.30.

**L33.x "Subagent handoff 必须显式交付文件 + 完成标志"** (P25 Day 2 polish 第二轮 meta-bug 派生, 2026-04-23):

- **场景**: 主 agent 派一个或多个 subagent 并行做任务后, 因为 `AwaitShell` 不支持 subagent id, 只能靠"估等时间 + 读 transcript 目录"轮询, 容易出现两种失败: (a) 主 agent 等不够就以为 subagent 没启动, 误重新派任务浪费资源, **可能盖掉原 subagent 已写的输出** (本次 P25 Day 2 polish 第二轮真实踩过 — 主 agent 重复启动了两个已经完工的 subagent, 用户在反馈里指出); (b) 主 agent 等够了但没找到输出位置, 只能靠"猜测 subagent 放到了 tests/testbench/static/ui/chat/..." 再 grep 结果, 效率低且不可靠.
- **失败模式**: 本次浪费了几次 tool roundtrip + 用户吐槽 "你应该建立一套合适的机制来判断 subagent 到底有没有完成工作, 交付在哪里, 而不是发现 wait 没办法使用之后靠干等和靠猜来解决问题."
- **修正协议 (3 件套)**: 本次 P25 Day 2 polish 第三轮建立.
    1. **固定交付目录**: 每个 subagent 写产出到 `tests/testbench/_subagent_handoff/<task-id>.json`. 主 agent **派任务前**决定 `<task-id>` 并写进任务书 (e.g. `ui-layout-r3`, `avatar-context-contract`).
    2. **完成标志**: 同目录下 `<task-id>.DONE` 空文件, 由 subagent **最后一步** touch. 严格顺序 — json 写好之后才 touch DONE, 否则主 agent 读到空 json.
    3. **固定 JSON schema**: `{task_id, status: "ok"|"fail"|"partial", summary, files_changed, lints_clean, smoke_run, known_limitations, followups_for_main_agent, diagnostic_notes}`. 字段不能少, subagent 即使 fail 也要写 (status="fail" + followups_for_main_agent 描述阻塞).
- **主 agent 查收流程**: (a) 一次 `ls` 看 `<task-id>.DONE` 存在? (b) 存在 → 一次 `Read` 读 json → 决定下一步; (c) 不存在 + 未超时 → 继续做自己独立的工作, 循环轮询 (不 block 全局); (d) 超时 → **不重启 subagent**, 按 fail 处理, 主 agent 兜底.
- **关联**: L33 (并行开发范式) 管"怎么分工", L33.x 管"分工结果怎么可靠 handoff". 配套 README: `tests/testbench/_subagent_handoff/README.md` 已建立, 作为协议 SST (single source of truth).
- **进入主编号条件**: 如果后续 P25/P26 再次因 handoff 机制失败浪费资源 (或反之 — 协议防住了一次浪费), 就收集成 §7.x 正式教训.

**L34 "跨进程文件契约层 smoke 必须用消费方反序列化器做 round-trip 断言"** (P25 Day 1 fixup 派生, 2026-04-23):

- **场景**: 测试生态 / adapter 层 / mirror / projection 等**跨进程落地**机制, 把内存对象序列化成 JSON / YAML / SQLite 等**文件级** payload, 供另一进程或将来的自己重新反序列化消费. 序列化 shape 和消费方期望 shape 不一致时, **多数序列化库不会抛异常**, 而是走 fallback 把整个 dict 字符串化 (`HumanMessage(content=str(d))` / `yaml.safe_load` 失败回 `None` / `pickle.loads` 失败直接 crash 对比, 前两类静默, 后一类响) 或部分字段丢失. 下游 compress / facts extract / reflect / query 读到"看起来合法"的数据但语义已经毒化.
- **失败模式**: smoke 只断言 "`len(persisted) == N` / `isinstance(persisted, list)` / `persisted[0]['type'] == 'human'`" 等**浅断言**, 过得了. 真跑下游消费时才暴露. 症状延迟几天或几周 (依赖下游触发频率), 回溯根因很难 (数据已经污染 log 一片, 不知道是写入时污染还是消费时污染).
- **真实案例**: P25 Day 1 `external_events._apply_mirror_to_recent` 初版把 memory pair 写成 testbench 内部 shape `{role: "user", content: [{type: "text", text: "..."}]}`, 主程序 canonical on-disk shape 是 LangChain serialized `{"type": "human", "data": {"content": "..."}}`. `utils.llm_client.messages_from_dict` 对未知 shape 走 fallback `HumanMessage(content=str(d))` 把整 dict stringify 进 content. smoke 只断言了 "len(persisted) == 2" 和 "'type' in persisted[0]", 绿. 用户手测 `B6 proactive + mirror_to_recent` + 手动 trigger recent.compress 才暴露"recent 里的 human message 内容是 `{'role': 'user', ...}` 字面串"的毒数据. 修复: `external_events.py` 改用 `HumanMessage/AIMessage/SystemMessage` + `messages_to_dict()`; smoke `p25_external_events_smoke::D1` 追加 4 条严格断言 (recent_langchain_shape / recent_role_pair / **recent_roundtrip_len / recent_roundtrip_content** — 后两条就是**用消费方反序列化器 round-trip** 再核对 content 是否一致).
- **防御规则** (三层):
    1. **smoke 必做 round-trip**: 任何跨进程文件契约层, smoke 必须 `persisted_bytes = read_file(...); obj = consumer_deserializer(persisted_bytes); assert obj == expected` — **用消费方自己的反序列化器**, 不是测试方自己写个 `assert 'type' in data`. 这才是契约层真正的 "端到端" 断言.
    2. **契约层文档标注**: 跨进程文件的每个字段在代码注释里标**哪个消费方用哪个反序列化器消费**, 便于审查时快速定位"这个字段应该长什么样".
    3. **静默 stringify 探针**: 消费方的反序列化 fallback (如 LangChain 的 `HumanMessage(content=str(d))`) 应加 diagnostic log, 方便在 DEBUG 模式下发现 "本应命中已知 shape 却走了 fallback" 的静默毒化.
- **关联**: L22 (编码污染) 的**契约层扩展** — L22 管字节级编码, L34 管 JSON/YAML shape 级编码. L30 (pure helper copy + drift smoke) 的**补集** — L30 管"跨 package 复用主系统 pure helper 不 import", L34 管"跨进程写文件给主系统消费方消费的 shape". Cursor skill 候选: `cross-process-file-contract-roundtrip-smoke` (场景 = testbench / mirror / projection / ETL / cross-service message queue; 立规 "smoke 必 round-trip via consumer deserializer, 严禁浅断言").
- **进入主编号条件**: 需要在后续阶段 (P25 Day 3 / P26 立项) 再有一次跨进程文件契约层 shape 漂移被 round-trip smoke 抓住的案例, 才升级为 §7.31.

**L35 "蓝图 > 代码时按代码走 + 显式登记"** (P25 Day 2 前端面板派生, 2026-04-23):

- **场景**: 设计阶段蓝图草稿 (文字描述 API / 字段 / 枚举值) 和最终**实装代码**不一致时 — 可能是蓝图起草时笔误 / 主程序后续调整 / 评审轮次没同步. 阶段执行期 (Day N 实装) 遇到歧义, agent 默认按蓝图照抄会**把一个已经删掉的枚举值重新引入**或**测试不存在的 payload 场景**.
- **真实案例**: P25_BLUEPRINT Day 2 §237 列 avatar tool_id 含 `{lollipop, fist, hammer, hand}`, 但实装的 `config/prompts_avatar_interaction.py::_AVATAR_INTERACTION_ALLOWED_ACTIONS` 只有 `{lollipop, fist, hammer}` 三种, `hand` 从未出现在代码里. 前端面板开发时如果按蓝图做 4 tab, tester 点 `hand` 触发后后端 `_normalize_avatar_interaction_payload` 返 `invalid_payload`, UX 差且**无语义价值** (hand 根本不存在不是 bug). 取舍: 按代码做 3 tab + 面板代码注释 + AGENT_NOTES §4.27 #111 登记 "蓝图写了 hand, 代码未实装, 按代码走".
- **失败模式**: 不登记直接按代码做 → 下一轮 agent 读蓝图又补 hand 回来 → 打回; 或者按蓝图做 → tester 实际使用时抱怨 "UI 给了 hand 按钮点击失败". 两条路都不对.
- **防御规则** (两条):
    1. **蓝图 vs 代码不一致 = 代码胜出** (代码是实装, 蓝图是草案, 且蓝图起草时间早于代码定稿).
    2. **显式登记取舍**: AGENT_NOTES 对应阶段条目写一段 "蓝图写了 X, 代码实装为 Y, 我们按 Y 做, 因为 …", 下一轮 agent 不用再重复这道判断.
- **关联**: L31 (审查锚定初衷) 的**执行阶段扩展** — L31 管审查时不漂, L35 管执行时蓝图歧义处理. 细分场景 = L31 之审查产出的蓝图本身**事后被发现有歧义**时怎么办. Cursor skill 候选: `blueprint-vs-code-when-disagree` (立规 "代码胜出 + 显式登记取舍").
- **进入主编号条件**: 需要在后续阶段再有一次"蓝图草稿和实装代码不一致按代码走" 的案例, 才升级为 §7.32.

**L36 "跨边界 shape / role / 字段名必须 rg 消费方"** — **已升级到主编号 §7.25** (2026-04-23 P25 Day 2 polish 第二轮后): 三次同族案例 (字段名漂移 / envelope vs flat / LLM wire role prompt_ephemeral 契约违反) 在 72 小时内累积达门槛, **已从 §7.A 候选区正式升级为 §7.25**. 完整论述、案例和四层防御规则见 §7.25. 本条目保留作为"从候选区升级到主编号"的**流程示例**, 让下一位 agent 看到候选条目可以怎样通过多次复现升级.

**L37 "UI 页命名 vs store 语义漂移" (容器名没跟内容扩展)** (P25 Day 2 subagent C 发现, 2026-04-23):

- **场景**: 诊断 / 审计 / 日志 / 监控类**共享 ring buffer / store** 被多个后端路径写入, 每个路径独立决定 level (info / warning / error / fatal). 初期所有写入方都只写 error 级, 前端页面也命名为 "Errors". 后续新功能陆续把 warning 级 (如 security override audit) 加进同一 store — 还能接受; 再后来 info 级审计事件 (如 P25 外部事件仿真) 也往里塞, 这时**容器名 (Errors) 已经语义漂移**: 前端页面叫 Errors 且 intro 文案写 "最近出了什么问题", 但实际内容一半是 audit trail (info 级).
- **失败模式**: tester 打开 Errors 页看到三条 `avatar_interaction_simulated` 等 info 级条目, 第一反应是"系统报错了?"——但这其实是成功的仿真动作. 语义信任被破坏, 排查真正的 error 时增加噪声. 同时这类"顺手塞 info 进 errors store"在代码评审阶段看起来无害 (ring buffer 容器通用), 漂移是时间叠加的, **难在单 PR 层面发现**.
- **真实案例**: P25 Day 2 `pipeline/external_events.py::_record_and_return` 用 `diagnostics_store.record_internal(..., level="info")` 往 ring 写仿真成功事件, 导致 Errors 页冒出三条 info. 修复分三步: (a) `diagnostics_store.list_errors` 加 `include_info: bool = False` 参数, 默认过滤掉 info (尊重容器**名字**的语义契约); (b) API 层 `GET /api/diagnostics/errors` 把 `include_info` 作为 opt-in query 透传, 保留"全看"能力 (因为该 ring 仍是**唯一**统一查询入口); (c) UI 加 "包含 info 级" 复选框, 默认关, 勾选后走 `include_info=true` 路径. Errors 页回归"错误专用", 同时不丢审计能力.
- **防御规则 (四层, 按触发先后排)**:
    1. **命名检测**: 任何名为 "errors" / "failures" / "alerts" / "warnings" / "incidents" 的 store / table / ring / API 路径, 允许的 level 集合必须**在源代码的 docstring 里明写** (例 `# 本 store 只允许 level in {error, warning, fatal}`). 写入方 PR 要过这个 docstring.
    2. **容器级 assert 守护**: store 的 `_push(entry)` 或等价 sink 加 `assert entry.level in ALLOWED_LEVELS, f"{entry.op} 写 {container_name} 但 level={entry.level} 不在白名单"`, 运行时 FAIL_LOUD.
    3. **入口级 opt-in**: 如果实在需要 info 也走这条路 (避免再造一个 store), 必须设计 `include_info` 类的 opt-in 参数, **默认行为不变**. 这是本次 P25 Day 2 采用的 "不破坏语义契约前提下折衷" 方案.
    4. **UI 端对齐**: 前端消费方**独立检查** endpoint 返回的 level 分布, 和页面命名做一次心智对齐. 若发现 "endpoint 名叫 /errors 但返回一半是 info", 必须 surface 给用户 (本次做法: Errors 页加 `include_info` chip, tester 知道自己正在看的是 "含 info" 的视图).
- **关联**:
    - L1 (ring buffer 满了怎么办?) 的**语义命名扩展** — L1 管"满了怎么办", L37 管"装进来的东西是不是配得上容器名".
    - L14 "coerce 必须 surface" 的**容器级扩展** — L14 管单条记录的默认值要可见, L37 管整类记录的默认 filter 要可见 (Errors 页的 include_info chip 就是 L14 的 surface 实例).
    - semantic-contract-vs-runtime-mechanism skill 的**命名层版本** — 那条 skill 管"语义 vs 机制不要混", L37 管"语义 vs 容器名不要漂".
- **候选 skill**: `ui-page-name-vs-store-content-drift` — 触发条件: "任何名为 X 的 store / ring / API 开始接收不属于 X 语义的记录", 输出决策三选一 (改名 / 分家 / opt-in filter), 模板包含 docstring + assert + query param + UI chip 四层.
- **进入主编号条件**: 需要在后续阶段再有一次"某 store / 页面名称语义漂移"(比如 `warnings` 表开始收 info / `alerts` endpoint 开始返回 debug) 才升级为 §7.34.

**L38 "自动刷新列表的 sub-<details> 展开态必须单独持久化 (和父 entry 的 toggledKeys 同级)"** (P25 Day 2 手测发现, 2026-04-23):

- **场景**: 前端有**自动刷新列表**页 (日志 / 错误 / 任务 / 事件流), 每条 entry 内部嵌套若干可展开 sub-`<details>` (如 "原始 JSON" / "trace digest" / "详细信息"). 初版的父 entry 展开态用 `toggledKeys: Map<key, bool>` 持久化 (跨 auto-refresh 保留), 但 sub-details 直接 `el('details', {open: false}, ...)` 裸写 — 每次 auto-refresh 重建整棵子树, 刚点开的子菜单被收回, tester 要读的详细 payload 刚看半秒就消失.
- **失败模式**: 只在**触发 auto-refresh 的场景**下暴露 (5s 周期或 filter 切换后), 单元测试 / smoke 在同一秒内断言根本看不到. tester 手测时"每 5 秒就折叠一次"的体感非常明显, 但**很容易被当成"顺手点一下再打开"的小烦恼**, 不主动报 bug. 如果不是 log 量大到 tester 必须长时间盯着某一条, 可能整个 phase 都不会被发现.
- **真实案例**: P25 Day 2 tester 同时复现**两处**: `page_logs.js` 的 "原始 JSON" 子菜单 + `page_errors.js` 的 "trace_digest" / "detail" 子菜单, **三处子菜单共用同一种 naked <details> 写法**. 修复: 两个 page 都加 `openSubDetails: new Set()` 字段 + `buildStickyDetails(state, subKey, summary, content)` helper (各 page 自己持一份 helper, 不 hoist 到 `_dom.js`, 因为它耦合 page state shape). 父 entry 切换 filter / 分页 / 换 session 时调统一的 `clearEntryCaches(state)` 一并清 `toggledKeys` 和 `openSubDetails`, 防 Set 无界增长 (L11 精神: 前端 map/set 不能无界).
- **防御规则**:
    1. **同族扫描义务**: 凡前端出现 `toggledKeys` 类 "父 entry 展开态持久化" 机制的页面, 必须同时扫一次**该 page 的所有 sub-`<details>`** (也就是 renderEntry / renderItem 内部的所有嵌套 `<details>`). 写 `toggledKeys` helper 当天就决定: (a) sub 们要不要持久化 (按 "auto-refresh 频率 × 子菜单内容量" 决定), (b) 如果要, 共用 `openSubDetails: Set` + `buildStickyDetails(state, key, ...)` 模式.
    2. **同族 helper 对齐**: 当多个 page 出现同种模式 (`toggledKeys` + `openSubDetails` + `clearEntryCaches`) 时, helper 名称和 state field 名称**三处对齐**. 本次 `page_logs.js` 和 `page_errors.js` 都起名 `clearEntryCaches(state)` + `openSubDetails: new Set()` + `buildStickyDetails(state, subKey, summary, content, {extraClass})`, 便于下一个写同类 page 的人直接抄.
    3. **clear 同步规则**: 凡切 filter / 分页 / 换 session 等 "entry 集合可能变" 的路径, 在调 `toggledKeys.clear()` 的同行必调 `openSubDetails.clear()`. 建议抽 helper `clearEntryCaches(state)` 统一管, 两处 cache 绑一起.
- **关联**:
    - L11 (前端 map/set 无界增长) 的**具体化场景** — L11 说不能无界, L38 给出具体 "auto-refresh 列表的 sub-details 持久化" 这一族的正确形式 (`clearEntryCaches` helper 同步清).
    - L33 (subagent 并行 + 三段式 review) 的**复查实证** — tester 手测直接报 bug 比 smoke 能抓到的更早, 但**一旦写进 lessons 就能让下一个类似 page 的开发者避坑**; 这也是"为什么文档化很值" 的一个具体例子.
- **候选 skill**: `auto-refresh-list-sticky-sub-details` — 触发条件: "写 auto-refresh 列表页且 renderEntry 内有 sub-<details>", 模板包含 `openSubDetails: Set` + `buildStickyDetails` helper + `clearEntryCaches` 统一清理函数, 强制文档化 "auto-refresh 频率 × 子菜单价值" 的决策记录.
- **进入主编号条件**: 需要在下一个写 auto-refresh 列表页的 phase (P26+) 至少再命中一次同族, 才升级为 §7.35.

**L39 "out-of-band write 共享 store 必须配对 emit + 对应 listener 识别 reason 白名单"** (P25 Day 2 polish 第二轮手测派生, 2026-04-23):

- **场景**: 前端有 N 个后端写入路径都写同一个**共享 store** (本项目 = `session.messages`, 其它项目 = 购物车 / 通知列表 / 文件树 / 订单列表等). 主路径 (本项目 = `/chat/send` SSE) 走 streaming handle 自己直接维护 DOM, 没有全量刷新; 但**旁路写入路径** (本项目 = `POST /api/session/external-event`, 其它项目 = 管理员后台插消息 / 定时任务插通知 / WebSocket 推送) 返回一个**一次性同步响应**, 后端 **`append_message` 已写 store, 但前端没有任何 event 通知 UI 刷新**. UI 看起来"没反应", 必须 F5 才看到新数据.
- **失败模式 (两种, 都常见)**:
    1. **纯漏 emit**: 旁路 router 只返一个 HTTP 200, 前端没事件可订阅, UI 完全不知道 store 变了. 这是本次 P25 Day 2 polish 第二轮遇到的模式.
    2. **emit 存在但 listener 不识别 reason**: 已有 `store:changed` 类泛事件, 但主路径 `/chat/send` 自己在 DOM 上增量做, **不希望**全量刷新 (否则清掉还没 append 完的 streaming DOM 节点, 或与 SSE 回调产生竞态). 所以主路径自己 emit 时标 reason=`stream` 或根本不 emit, 而旁路 emit 时标 reason=`external_event` 之类. listener 必须**读 payload.reason 过白名单**, 否则主路径意外触发 listener 会**擦掉 streaming 节点**或**产生竞态**.
- **真实案例**: P25 Day 2 polish 第二轮手测: 用户触发 external event → 后端 `_record_and_return` 里 `append_message(role="assistant", ...)` 成功写 `session.messages`, autosave log 有, 右侧 wire 面板有, **但 chat 区 UI 没有新消息**, 必须 F5 才看到. 根因: `static/ui/chat/external_events_panel.js::onInvokeClicked` 只调了 `toast.ok(...)`, **没 emit 任何事件**; `static/ui/chat/message_stream.js` 也**没订阅 `chat:messages_changed`**. 修复: (a) panel 加 `import { emit } from '../../core/state.js'` + 成功后 `emit('chat:messages_changed', { reason: 'external_event' })`; (b) `message_stream.js` 加 `const offMessagesChanged = on('chat:messages_changed', (p) => { if (p?.reason !== 'external_event') return; if (!store.session?.id) return; refresh(); })`, **严格按 reason 白名单**, 不处理 `stream` / `inject` / `script` / `auto_dialog` / `local_edit` / `local_delete` / `local_truncate` / `local_patch_timestamp` 等本来就有自己 DOM 维护路径的 reason (否则擦 streaming 节点 / SSE 竞态). destroy 里必须 `offMessagesChanged()`, 防 listener 泄漏.
- **为什么这很容易漏**: 后端 `append_message` 是**唯一写入点** (choke-point, 见 §3A 相关), 看代码时每次 grep `append_message` 都确认"写了". 但 "前端怎么知道写了" 这个**跨层信号传递**, 在纯后端 review 时看不见, 在纯前端 review 时又看不到后端改了什么. 只有**手测 "触发操作后 UI 是否自动更新"** 这种**完整用户链路**才能抓到. 这是 `event-bus-emit-on-matrix-audit` skill 的**经典盲区**: emit 和 listener 可能都在但 reason 不对齐, 或者 emit 缺位但 router 返成功响应时前端不会怀疑.
- **防御规则 (四条)**:
    1. **store 写入路径全覆盖 emit 表**: 对任何"多入口写入同一共享 store"的数据结构 (session.messages / notifications / file tree / orders / ...), 项目 shared docs 里维护一张 **"写入路径 × emit reason × 谁订阅"** 的二维表. 新加一条写入路径时, 必须同时加一行到表里, 并决定 reason 归属的 listener 白名单. 本次 P25 Day 2 polish 的实际表 (精简):

        | 写入路径 | emit reason | message_stream 订阅? | 理由 |
        |---|---|---|---|
        | `/chat/send` (SSE) | *不 emit (或 reason='stream')* | ❌ | 自己 beginAssistantStream 维护 DOM |
        | `/chat/inject_system` | reason='inject' | ❌ | composer 本地直接 append DOM |
        | `/chat/messages` (手动) | reason='local_edit' | ❌ | 目前前端未使用该入口 |
        | `external-event` (P25 新加) | reason='external_event' | ✅ | 无 handle, 必须 refresh |
        | **未来**: 脚本注入 / 批量导入 / 跨进程推送 | 新 reason / 复用 external_event | ✅ 新入白名单 | 同属"无 handle 旁路写入" |

    2. **listener 必须按 reason 过白名单**: `on('chat:messages_changed', (p) => { if (p?.reason !== 'whitelist_reason') return; ... })`, **永远不允许**"listener 不看 reason 直接 refresh" — 那会击中主路径的 streaming DOM / 产生竞态.
    3. **destroy 必须 off 所有 on**: 每 `const offX = on('evt', ...)` 必配 `destroy() { offX(); }`, 防 listener 泄漏 (L11 精神: 订阅也是无界增长源). 本次 `message_stream.js::destroy` 就从 `offSession(); offResults();` 扩到 `offSession(); offResults(); offMessagesChanged();`.
    4. **手测门槛**: 新加 "旁路写入 API" 时, 手测脚本必须包含 **"触发 API 后 UI 是否自动更新"** 一条. 这是 smoke 难以覆盖的 (需要完整 DOM + 事件总线, 纯 TestClient 测不到), 必须在 PROGRESS 的 Day N 手测门槛里显式列.
- **关联**:
    - **event-bus-emit-on-matrix-audit skill** 的**具体应用** — 那条 skill 管 "audit 整个事件总线找 emit × listener × teardown 漂移", L39 是它在"共享 store 多入口写入"场景的具体实例化, 补充了 "**reason 白名单**" 这个子维度 (原 skill 主要管 0 listener / dead emit, 没细化 reason 白名单).
    - **single-writer-choke-point skill** 的**跨层扩展** — 后端 append_message 是单写点, 但**"前端如何知道写了"**也是 single-writer 的一部分: "N 个后端路径都经过 append_message → 那么 append_message 或它的 N 个调用方必须配对 emit". 两种落地方案任选: (a) 集中在 `append_message` 顶部 emit (最省心, 但 reason 粒度不够), (b) N 个调用方各自 emit + 统一 reason 表 (当前采用, 因为不同调用方 reason 明显不同).
    - L11 (前端 map/set 无界增长) 的**订阅版** — 订阅本身是另一种需要 bounded 的集合, destroy 忘 off = listener 泄漏.
    - L33 (subagent 并行 + 三段式 review) 的**反向例证** — subagent 做 UI 模块时如果**不拿到**全局 emit × listener 表, 做出来的模块很容易就是"自己这块成功但忘了 emit". 本次 P25 Day 2 subagent B 做 external_events_panel 就是这个情况. 防御: 给 subagent 的任务书里**必须包含** "你的模块是否写共享 store? 如是, 配对 emit 什么 reason? 该 reason 的 listener 白名单加到哪?".
- **候选 skill**: `shared-store-out-of-band-write-emit-pairing` — 触发条件: "后端新加一条 API 路径写入已有 shared store (有其它路径同时在写)", 模板包含 写入路径×reason×listener 表 + reason 白名单 listener + destroy off + 手测门槛.
- **进入主编号条件**: 需要在下一个后端新加"旁路写入共享 store"的 phase (P25 Day 3+ / P26) 再命中一次同族, 才升级为 §7.36.

**L40 "info 级诊断条目 smoke 必须显式指定 `level=info`, 因为 list_errors / GET /errors 默认过滤 info"** (P25 Day 2 polish r5 smoke 调试派生, 2026-04-23):

- **场景**: 诊断 ring buffer 存 error / warning / info 三种 level 条目, 页面叫 "Errors" 所以 `list_errors` 默认 `include_info=False` (见 L37 命名漂移防御). 后端写入任何 level 的事件统一用 `diagnostics_store.record_internal(..., level=...)`, 但 smoke test 或外部脚本读取时用 `GET /api/diagnostics/errors` 不带 `level` 或 `include_info=true` 就**什么都看不到 info 级条目**.
- **失败模式**: smoke 新增一个 info 级诊断 op (例 `chat_send_empty_ignored` / `prompt_injection_suspected` / `avatar_interaction_simulated`) 时, 契约断言 "事件应被记录"直接 `client.get("/api/diagnostics/errors")` 过滤列表 → 列表为空 → 断言失败. 调试时很容易怀疑"是不是 append 根本没跑", 实际上是**写了但被 API 默认过滤掉**. 如果后端已用 `list_errors(include_info=False)`, 这是**设计即预期** (L37 第 3 条 opt-in), 但 smoke 作者常常忘.
- **真实案例** (三次同族):
    1. P24 Day 7 L37 首次踩 (tester UI): Errors 页空如也但后端确实写了外部事件 simulated info, 修复 = 加 "include_info" chip.
    2. P25 Day 2 polish r2 (smoke 作者): r2 smoke 验证 avatar simulated info 触发, 首版 `_get("/api/diagnostics/errors")` 不带 level, 空列表 → 改成 `params={"level": "info"}` 或 `params={"include_info": "true"}`.
    3. P25 Day 2 polish r5 (smoke 作者): `p25_r5_polish_smoke.py` **R5E** 验证 `chat_send_empty_ignored` 空消息诊断, 首版 `_get("/api/diagnostics/logs")` 根本不是诊断 store 而是**另一份 ring buffer**; 次版 `_get("/api/diagnostics/errors")` 又忘带 `level=info`, 空列表; 终版 `params={"level": "info", "op_type": "chat_send_empty_ignored"}` 才拿到条目. **R5G** 验证 `prompt_injection_suspected` 同理.
- **防御规则 (三条)**:
    1. **smoke 断言 info 级条目三件套**: 任何 smoke 断言某 `op_type` 的 info 级诊断被记录时, API 请求必须**同时携带** `level="info"` (或 `include_info="true"`) + `op_type=<精确 op>` + `session_id=<如果绑定>`. 三件套缺一不可, 尤其 `level` 和 `op_type` (前者突破 opt-in filter, 后者精准过滤避免串扰).
    2. **smoke 初步失败时的诊断顺序**: 遇到"diagnostics 列表为空"时, 按以下顺序 debug: (a) 查后端是否真的 `record_internal`了 (grep `record_internal.*<op_name>`); (b) 检查 `level=` 参数 (错把 info 写成 warning?); (c) 检查 smoke 的 GET URL (是 `/errors` 还是 `/logs`? 别混淆); (d) 检查 smoke 的 query params (有没有 `level=info`?); (e) 检查 ring buffer 是否已满被挤出 (P21 Day 3 `diagnostics_store` 默认容量 500).
    3. **区分两个 ring**: `logs` (rolling journal, 持久化 JSON 行) ≠ `errors` (in-memory ring buffer, 前端 Errors 页显示). `diagnostics_store.record_internal` 只写 `errors` ring; `logs` 文件由另外的 structured logger 写. smoke 必须看清自己断言的是哪一个.
- **关联**:
    - L37 "UI 页命名 vs store 语义漂移" 的**smoke 侧后果** — L37 管"容器名 Errors 应该默认只显示 error", L40 管 "然后 smoke 要怎么测 info". 二者互补: L37 管设计侧 invariant, L40 管测试侧已知.
    - L14 "coerce 必须 surface" 的**测试镜像** — L14 管"默认不可见的 coerce 要 surface 给用户", L40 管"默认过滤的 info 要让 smoke 显式 opt-in", 本质都是"默认行为掩盖了信息, 必须显式打开".
    - semantic-contract-vs-runtime-mechanism skill — 语义契约 "Errors 页只看 error" vs 运行时机制 "record_internal 支持 info / warning / error"; smoke 必须清楚自己测的是哪一层.
- **候选 skill**: `diagnostic-info-level-smoke-quirk` — 触发条件: "smoke 断言某诊断 op 被记录且该 op 是 info 级", 模板包含 API URL 选择表 + 必传 query params + 失败后 debug 顺序.
- **进入主编号条件**: 需要在 P26+ 再命中一次"smoke 断言 info 诊断但忘带 level=info" 才升级为 §7.37.

**L41 "UI 高频便捷操作必须走后端专用 shortcut 端点, 不要前端手工组装契约"** (P25 Day 2 polish r6 [保存到最近对话] 快捷钮派生, 2026-04-23):

- **场景**: 用户经常做一项"组合多步" 的 UI 操作, 想用一个按钮一键完成. 天真做法 = 前端 JS 直接把 session state 翻译成后端现有端点 (比如 `PUT /api/memory/recent`) 接受的 shape, 好像"没写后端就完活了".
- **失败模式**:
    1. **shape 耦合**. 前端会学会后端 on-disk 格式 (比如 LangChain canonical `{type, data:{content}}`). 当后端调整 shape 时前端静默跟随或悄悄打破.
    2. **invariant 漏**. 像 `SOURCE_EXTERNAL_EVENT_BANNER`, `role ∉ {user, assistant, system}`, `content 全空白` 这些过滤规则只有一处源——后端 `prompt_builder` / `chat_messages.py` —— 前端"快捷钮" 独立实现时经常漏 1-2 条. 漏 banner filter 会导致 banner 进 recent.json 下次 `/chat/send` 从 recent 读**重新污染 wire** (r5 T7 双 chokepoint 设计此时被第二次绕过).
    3. **测试难**. 前端 jsdom + MSW 远比 TestClient 打 FastAPI 路由繁琐 + 静态解析 JS 契约 ≠ 静态类型系统保护.
    4. **第二个 caller 必暴毙**. 当第二个 UI 入口想做同样操作 (比如 CLI 工具 / 后续 script runner / 导出子系统) 它会复制第一份前端逻辑, 产生第二份漏 filter 的实现. 这是 L33 single-writer chokepoint 的反面.
- **正确做法**: 新建后端专用 shortcut 端点 `POST /api/<domain>/<action>_from_<source>`. 端点在后端一处完成 (a) shape 适配, (b) 过滤 filter invariant, (c) 原子写入, (d) 返回 `added / skipped` 结构化明细给前端做 toast. 前端只做 UX (confirm / toast / error branch), 不碰 shape. smoke 直接打 TestClient 端点而非经过 UI.
- **真实案例** (P25 polish r6 派生): 用户要"Chat 页一键 [保存到最近对话]". 先想过前端直接组装 LangChain shape 调 `PUT /api/memory/recent`, 三秒内识别出 banner filter + role filter 都只有后端 `chat_messages.py` / `prompt_builder.py` 认识, 前端重建风险大. 改成新建 `POST /api/memory/recent/import_from_session` + `_session_messages_to_recent_dicts(session)` 纯函数 helper (filter + shape 各一次) + 前端 `message_stream.js::saveToRecent()` 只做 confirm / `expectedStatuses: [404, 409]` / toast 渲染 added/total/skipped. smoke 新建 `p25_r6_import_recent_smoke.py` 6 契约 (R6A-R6F) 走 TestClient, 第一跑暴露 `store.session_operation()` 无 session 抛 LookupError 映射 500 的小坑, 修为 `_require_session()` 前置拿 clean 404. 全量 15/15 绿. 关键: **过滤逻辑只在 helper 一处**, 即使未来 CLI 工具或批量导入想做同样事情, 调同一端点就行, banner filter 等 invariant 一处改全域生效.
- **防御规则 (四条)**:
    1. **识别信号**: 任何 "UI 一键操作 = 多步后端契约组合" 的请求都应该优先在后端开新端点. 对照信号 = "前端需要拼一个超过 2 层的 dict" / "前端需要知道 content 有效性规则" / "操作需要原子性但前端做的是 read-modify-write".
    2. **端点命名模板**: `POST /<domain>/<target>/<action>_from_<source>` (e.g. `/memory/recent/import_from_session`) 明确表达"来源-目标-动作" 三元组, 不与已有 CRUD 冲突.
    3. **过滤 + shape 各在一处**: 新端点必须有一个 pure helper 负责"读 session/state → 过滤 → 转 shape → 返 list + skipped dict". helper 纯函数易 unit test, 易被第二个 caller 复用.
    4. **smoke 必覆盖过滤边界**: 每条过滤规则 (banner / empty / unsupported_role / ...) 各一个 contract case. 单独验证 `skipped.<reason>` 计数精确, 不能只验 happy path.
- **关联**:
    - §7.6 多源写入 / L33 single-writer chokepoint 的**UI 侧表达** — L33 讲"同一进程多入口", L41 讲"UI 便捷操作想绕过后端 chokepoint". 是同一方法论下的具体化.
    - §7.25 L36 跨边界 shape — L36 管"消费方 shape 必 rg 生产方", L41 管"**UI 便捷操作不要自己做跨边界 shape 拼装, 让后端端点做**". 本质是"跨边界复杂度归属问题"的不同切面: L36 定位跨边界风险, L41 规定应该在哪一侧处理.
    - r5 T7 banner 伪消息双 chokepoint — T7 已确立"banner 写 + 读各一个 chokepoint", L41 是该原则的 UI shortcut 专门扩展: 任何新写入路径不能绕过 banner filter.
- **候选 skill**: `ui-shortcut-via-backend-endpoint` — 触发条件: "用户要求加一个 UI 便捷按钮做多步操作", 反模板 = "前端直接组装后端 shape", 正模板 = "新建 `/<domain>/<target>/<action>_from_<source>` 端点 + pure helper + 过滤边界 smoke".
- **进入主编号条件**: 需要在 P26+ 再命中一次 "UI 便捷操作被冲动地前端实现然后漏过滤 / shape 耦合" 才升级为 §7.38.

**L43 "LLM 调用点契约 (stamp / shape / source 白名单) 用 AST 静态扫 + NOSTAMP sentinel 允许白名单 escape-hatch"** (P25 Day 3 `last_llm_wire` 覆盖率 smoke 派生, 2026-04-23):

- **场景**: 当一个 chokepoint helper (比如 `record_last_llm_wire(session, wire, source, note)`) 被多个 call site 调用时, **漏调一处** = 该路径触发后 UI preview / debug panel 显示**前一次残留**快照, 用户看到的是"过时的真相" — 这比完全无 preview 更危险, 因为它**伪装为当前事实**.
- **防御方式对比**:
    - **(a) rg 文本扫 `ainvoke|astream`**: 快但不准, 会误报 `wire_tracker` 自己对 LLM 的抽象调用, 不识别 `.invoke([HumanMessage(...)])` 变体.
    - **(b) runtime 断言**: 在 `record_last_llm_wire` 入口加计数 / 在 chokepoint 校验 session 是否更新. 问题: 只能在 ainvoke 跑了以后才知道漏没漏, 单元测试跑真 LLM 慢 + 需要所有路径都被执行才能 100% 覆盖, 部分路径 (Auto-Dialog simuser + 真 judge 模型) 在 smoke 套件里不走真实 API.
    - **(c) AST 静态扫 (推荐)**: 扫所有 `<xxx>.ainvoke(...)` / `<xxx>.astream(...)` / `<xxx>.invoke(...)` call, 用 AST 父节点映射找 enclosing `FunctionDef` / `AsyncFunctionDef`, 在同一 body 内扫 `record_last_llm_wire(...)` 是否存在. 秒级跑, 不依赖网络, 不依赖 mock 配置, 可被 smoke 套件独立跑.
- **NOSTAMP escape-hatch**: 有些 LLM 调用**合法不应 stamp** — (i) helper 抽象 (callers 各自 stamp 因 note 内容依赖 caller 的 kind-specific 字段), (ii) connectivity ping 类调用 (不是会话 turn, stamp 会污染 last_llm_wire). 这类用代码内 `# NOSTAMP(wire_tracker): <justification>` sentinel 标记 + smoke 扫 lookback_lines (比如 10 行) 内有 sentinel 即跳过. **不走独立 allowlist config 文件** — 让审查员一眼看到"这个调用为什么不 stamp", 首次启动日志出 "N NOSTAMP site(s) allowlisted" 让 reviewer 审白名单增长.
- **失败模式 / 验证案例** (P25 Day 3 派生):
    - 原始 Day 2 polish r4 只在 `/chat/send` + 3 外部事件路径挂 stamp, `memory_runner` 4 preview.* + `judge_runner._call_llm` + `simulated_user.generate_simuser_message` 6 处**漏**. Prompt Preview 显示"上一次 /chat/send 的 wire", 用户触发 memory 操作后去 preview 看到的是**陈旧的 chat wire**, 不是刚刚发给 LLM 的 memory prompt.
    - 写 `p25_llm_call_site_stamp_coverage_smoke.py` 后初跑暴露 2 个合法 NOSTAMP (`_invoke_llm_once` + `_ping_chat`), 各自补 sentinel + 注释后绿.
    - 第一版 `NOSTAMP_LOOKBACK_LINES=3` 不够 (`_ping_chat` 的 justification 注释 5 行), 扩到 10. 这是 smoke 自己的**可配置窗口**设计教训 — 窗口不能小于合理注释长度.
- **防御规则 (四条)**:
    1. **AST > rg**. Chokepoint 覆盖率检查用 `ast.parse` + `iter_parent_map` 找 enclosing function, 比 rg 行级匹配更准. 对 `<xxx>.method(...)` 类调用模式, AST 准确识别 attribute access, rg 会被同名方法误伤.
    2. **source 字面量白名单 双轨**. AST 还要扫 `record_last_llm_wire(source=<literal>)` 的字面量 `source` ∈ `KNOWN_SOURCES` + 每次 `KNOWN_SOURCES` 只在 chokepoint module 一处声明 — 防两套白名单漂移 (PROGRESS 和 code 各一份).
    3. **NOSTAMP sentinel 走代码内注释, 不走独立 config**. "白名单移到哪个文件"本身是审查成本 — sentinel 紧贴被放行的那行 LLM 调用, 审查员读那行代码就自动读到 justification. config 文件与被放行代码分离, 审查员容易忽略.
    4. **Lookback window ≥ 合理注释长度**. `NOSTAMP_LOOKBACK_LINES` 至少能覆盖多行 justification 注释 (本项目选 10). 太小会把合法 NOSTAMP 识别为漏 stamp.
- **关联**:
    - §7.25 L36 "跨边界 shape / role / 字段名必须 rg 消费方" 的**静态守护方法论** — L36 讲消费方 shape, L43 讲生产方 chokepoint 调用覆盖; 两者都是"静态扫优于运行时发现".
    - §7.6 "多源写入是纸面原则成败分水岭" + L33 single-writer chokepoint 的**覆盖率验证** — chokepoint helper 存在 ≠ 所有 writer 都调. L43 给 "所有 writer 都调 chokepoint" 这个纸面原则**一个静态扫的保证**.
    - L40 "info 级诊断 smoke 必须显式 `level=info`" — 同族"smoke 自己可能成为漏网区域, 必须对 smoke 自己的参数有纪律".
- **候选 skill**: `llm-call-site-stamp-coverage-smoke` — 触发条件 = "codebase 有 chokepoint helper 多处 call, 漏调静默失败时 UI/UX 显示陈旧数据". 模板: AST 扫 `<method>.ainvoke/astream/invoke` + 同 body 找 chokepoint call + NOSTAMP sentinel + source literal whitelist + KNOWN_SOURCES single declaration.
- **进入主编号条件**: 需要在 P26+ 再命中一次 "chokepoint 覆盖率漏检导致 UI 显示陈旧数据" 或 "另一种 chokepoint (非 LLM wire) 需要类似 AST 覆盖率静态守护" 才升级为 §7.38.

**L44 "wire / preview 面板按消费域分区, 避免跨域 stamp 污染 Preview Panel"** (P25 Day 2 polish r7 派生, 2026-04-23):

- **背景**: Day 3 (L43) 给 6 处 LLM 调用 (memory 4 + judge 1 + simuser 1) 统一补了 `last_llm_wire` stamp 追求**全面覆盖**. 看似纸面上正确 ("每次 LLM 调用都留痕"), 实际 Chat 页 Preview Panel 显示**最新 stamp**, 跑完一次 `recent.compress` 后用户回 Chat 页以为看到的是"下次对话 AI 的 prompt", 实则是"记忆总结 LLM 的 prompt" — **全面 stamp + 单一展示面板 = 语义漂移**.
- **本质**: chokepoint + 全面覆盖 解决 "写入侧纸面原则不漂移", 但没解决 "读出侧展示应该按消费域分区" — **生产和消费在 chokepoint 后必须再分一次**.
- **r7 根治架构**:
    1. **Chat 页白名单过滤** — `preview_panel.js::CHAT_VISIBLE_SOURCES = {chat.send, auto_dialog_target, avatar_event, agent_callback, proactive_chat}`. 非白名单 stamp (如 `memory.llm` / `judge.llm`) 存在但不渲染, 回退预估 wire + hint 引导去对应页面.
    2. **每个非 Chat 域必须有独立 [预览 prompt] 按钮**, 调 **pure preview endpoint** 不调 LLM 不 stamp. r7 交付: `POST /api/memory/prompt_preview/{op}` (调 `build_memory_prompt_preview()` dispatcher → 4 个 `_build_*_wire()` helper) + `POST /api/judge/run_prompt_preview` (调 `build_judge_prompt_preview(judger, inputs)`). 两者都**共享真实 run 80% 代码** (验证 → 构 ctx → 渲染 prompt → 前置 preamble), 只差 `client.ainvoke` 那一步 — 契约一致性由代码路径共享天然保证.
    3. **"不被测的域" 直接 NOSTAMP**. r7 把 `simulated_user.generate_simuser_message` 改回 NOSTAMP — SimUser 是"对话来源", 不是"被考察对象", 它的 wire 对 tester 无价值, stamp 只会污染 Chat Preview Panel 让 tester 看不到"真正在测的那条". 识别准则: 如果一个 LLM 调用的 prompt **tester 从未需要审视**, 那它就不应该 stamp — 哪怕它也是 LLM 调用.
- **L36 / L43 / L44 三者层次**:
    - **L36 §7.25** (生产侧): 单条 wire 内**字段 shape / role / 字段名**不漂移 (跨边界反序列化).
    - **L43** (chokepoint 覆盖): 所有 writer 都**调 chokepoint 留痕** (静态 AST 扫 + NOSTAMP escape).
    - **L44** (消费侧): chokepoint 已经留痕了, 但**展示面板不是所有 stamp 都该展示**. 按"消费域" (对话 / 记忆 / 评分 / ...) 分区, 每个域有独立预览入口 + chat-only 白名单过滤 + 非白名单回退引导.
- **教训**: 不要在 "写入侧 chokepoint" 和 "读出侧展示面板" 之间假设一一对应. chokepoint 的职责是"不丢", 展示面板的职责是"按用户意图过滤显示". **chokepoint 全面覆盖 ≠ 展示面板全面展示**, 两者都对, 但中间必须有一层过滤 (白名单 / 域标签 / 按钮入口).
- **规则** (升级到主编号前先记):
    1. **写入侧 chokepoint 追求全面覆盖** (L43). 但必须**同时**定义 "展示面板的消费域白名单" (L44 第 1 条).
    2. **pure preview endpoint 架构** 比 "调一次 LLM 顺便显示 prompt" 好得多: tester 查看 prompt 不必付 2-10s LLM round-trip, 也不会触发副作用 (不写 `session.last_llm_wire`, 不写 diagnostics, 不吃 LLM 额度).
    3. **共享 helper 保证 preview 与 actual run 不漂移**. Preview 的实现**必须**和 actual run 共享 prompt 构造代码 (L36 §7.25 第 5 层 chokepoint 下沉的"跨接口"变体). 新加一个域时, 应 **抽出 `build_X_prompt_preview()` 和 `run_X()` 共享的构造函数**, 而非 preview 自己复制粘贴构造逻辑 (否则下次 prompt 格式升级, actual 改了 preview 没改, 悄悄漂移).
    4. **"不被测的域" 主动 NOSTAMP + 注释解释**. 识别信号 = "这个 LLM 调用的 prompt tester 从没反馈过想审查" → 直接 NOSTAMP, 不要"为了 chokepoint 覆盖率好看"也加 stamp.
    5. **Preview 按钮的位置要跟随"交互阶段"而非"功能分类"** (r7 2nd pass 2026-04-23 派生). r7 初版把 Memory 每个 op 的 `[预览 prompt]` 挂在外层按钮行 (紧贴触发按钮, "功能分类"语义的"触发操作 / 预览操作" 并列展示); 用户反馈更自然的交互是: 点 trigger 按钮打开参数 drawer → 填好参数 → 在 drawer 底部同时看到 `[执行] [预览]` — 即**按"交互阶段"分区**: `(a) 选择 op` 阶段只显示 op 触发按钮不显示 preview; `(b) 填参数` 阶段参数还没填完 preview 只会返回默认/空值无意义; `(c) 参数填完` 阶段在 drawer 底部同时暴露 `[执行] [预览]` 让 tester 决定跑不跑. 核心原则: **UI 元素的暴露时机要和它依赖的数据准备好的时机对齐**, 否则 tester 会在"数据还没准备好"时点按钮看到空/默认结果, 形成"按钮不可信"的负印象. 技术实装: 预览按钮 click handler **不清 drawer** 只弹 modal, 这样 tester 能"预览 → 微调参数 → 再预览 → 真跑"全在一个 drawer 里. 评分页的 Run + 预览并排布局是例外 — 因为评分的"参数"不在 drawer 内而是在主页面 (schema / target 选择), 参数准备和触发位置重合, 两按钮天然共在同一交互阶段.
- **关联**:
    - §7.25 L36 "跨边界 shape" 的**展示侧对偶** — L36 管跨边界生产→消费的 shape 不漂移, L44 管**展示面板按消费意图分区**: "即使数据对了, 展示在哪一页也必须按用户意图过滤".
    - L43 "LLM 调用点 chokepoint 覆盖" 的**读出侧对偶** — L43 管写入覆盖, L44 管读出分区. 两条是"chokepoint 架构"的两个半页, 只有 L43 没 L44 = "全面 stamp 但 Chat 页被跨域 stamp 污染".
    - §7.6 "多源写入是纸面原则成败分水岭" + L33 single-writer chokepoint 的**展示侧扩展** — L33 讲"多入口统一 writer", L44 讲"单 writer 多消费域时展示必须分区".
- **候选 skill**: `preview-panel-domain-partition` — 触发条件 = "codebase 有 Preview Panel / Debug Panel 展示 last-X 类单点状态, 且 X 有多个 source 域". 模板: (1) 定义 `<panel>_VISIBLE_SOURCES` 白名单; (2) 非白名单 source 回退到预估数据 + 显式 hint 引导; (3) 每个非默认域有独立"预览"按钮调 pure preview endpoint; (4) pure preview endpoint 与真实 run 共享构造 helper.
- **进入主编号条件**: 需要在 P26+ 再命中一次 "chokepoint 全面覆盖但 Preview Panel 展示域污染" 场景 (例如 diagnostics panel / error panel / snapshot panel 等) 才升级为 §7.38/§7.39.

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

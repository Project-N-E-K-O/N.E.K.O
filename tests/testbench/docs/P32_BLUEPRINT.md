# P32 代码线索 (Code Leads · 由记忆分析反推主程序记忆代码可疑点) — 蓝图

> **Single Source of Truth**. 本文件是 P32 阶段的**唯一权威规格**. `PLAN.md` 条目仅作索引; `PROGRESS.md` / `AGENT_NOTES.md` / `CHANGELOG.md` 描述均以本文件为准.
>
> **定位**: 在「记忆系统分析」workspace 新增第 4 个子页「代码线索 (开发者)」, 把 P29 系统概况**已算出的机械不变量类**发现 + 2 个新增确定性检查 (ID-DUP / EVT-DUP), **确定性映射**成"值得排查主程序哪个模块/写入路径"的**导航级线索**. 全程只读、无 LLM、离线、确定性.
>
> **本功能成败在 UI, 不在算法**: 后端给的是**弱线索**, 而代码人员天然倾向把"系统指出的问题"读成"确诊 bug". UI 的首要职责是**持续、不可跳过地压低置信预期** (见 §3). 一旦让人误以为"这里一定有 bug", 就会去改没问题的代码.
>
> **阶段号 / smoke 前缀**: 沿用「阶段号与 smoke 计数器有意分叉」约定. 阶段号 **P32**; 新增 smoke 接续单调计数器: 后端 `p45_`、前端 jsdom `p46_` (现有最高 `p44`).
>
> **动因 (2026-07-15, 用户下达)**: 引文 —— "接下来进一步讨论从记忆系统反推主程序记忆代码缺陷这个问题. 和相关人员沟通之后决定还是做: 做一个专门给代码相关人员的子页面, 点开之后高亮警告强调反推不完全可靠, 然后给出几条通过反推得到的潜在问题." + 追加要求 —— "必须严守一贯设计规范, 必须通读以往经验教训 (LESSONS §1.7); 特别重视 UI, 向使用者特别强调相关问题."
>
> **与 P30 的关系**: 本阶段是 P30 交付的可行性裁决 [MEMORY_CODE_INFERENCE_FEASIBILITY.md](./MEMORY_CODE_INFERENCE_FEASIBILITY.md) 的**受控落地**: 只做 §2.1「机械不变量类」的**导航级线索**, §2.2「内容质量类」(矛盾/冗余/归因/晋升漂移/比率/留存) **永不**纳入代码反推.

---

## 1. 目标与边界

### 1.1 核心目标

新增子页 `code_leads`, 展示由记忆分析**确定性反推**出的"主程序记忆代码排查线索". 数据来自单一只读聚合 chokepoint `pipeline/memory_code_leads.py::build_code_leads(character)`:

1. 复用 P29 `build_overview(character)` **一次** 的 `findings`, 映射其中**机械不变量类**码为线索.
2. 追加 2 个 P29 未覆盖、可确定性判定的检查: **ID-DUP** (主键重复) / **EVT-DUP** (events.ndjson 重复 event_id).
3. 内容质量类 findings **一律排除**, 只按类目计数展示 ("我们主动没把它们算成代码问题").

### 1.2 范围内 (In-Scope)

- **单一只读聚合纯函数** `build_code_leads(character) -> dict`: 见 §2. 复用 `memory_lineage._memory_dir` / `_read_json` (沙箱感知读) + `build_overview`. 无 LLM、离线、确定性.
- **单一端点** `GET /api/memory/code_leads`: 镜像 `/overview` —— `_require_session()` (404) + `_require_character(session)` (409) + `asyncio.to_thread(build_code_leads, character)`. **纯读, 不取会话锁, 不触 autosave, 不调 LLM** (L63).
- **前端子页** `static/ui/memory_trace/code_leads.js`: 注册进 `workspace_memory_trace.js` 的 `PAGES` (第 4 项); nav 标题带「(开发者)」后缀. UI 五要素见 §3.
- **文案全部入 i18n** (`memory_trace.nav.code_leads` + `memory_trace.code_leads.*`), 统一执行 §3 措辞纪律.

### 1.3 范围外 (Out-of-Scope, 明文约束)

- **不对内容质量类下代码结论** (§2.2 铁律): 矛盾/冗余/归因/晋升漂移/比率/留存**永不**产出代码线索.
- **不自动改主程序代码 / 不复现主程序 runtime**: 只读磁盘 WHAT, 不碰运行期 HOW (可行性 §3 证据面缺口). testbench 只读边界不变.
- **不调 LLM**: 只用 `build_overview` 的规则 findings + 确定性文件扫描. 离线、零 token、确定性.
- **不做游标单调性检查**: 主程序 events.ndjson 记录为 `{event_id:uuid4, type, ts:isoformat, payload}`, **无 seq/游标单调整数字段** (真正的游标在 `cursors.json` / `memory/cursors.py`, 属可行性 §4 未来受控项). 本期以 EVT-DUP (重复 event_id) 替代.
- **不做 source_id 直查 (原设想的 D5)**: 概况 D2 已用溯源边定案"断裂晋升", 直查 `source_id` 会重现 p39 O5 守护的 merge 假阳性 (§7.25「别重算 chokepoint 已定关系」). 本期依赖既有 D2.
- **不引入新依赖**: 只用标准库 + numpy (已是硬依赖).

### 1.4 与可行性裁决的一致性

可行性 §2.1 (可反推·机械不变量) 明列: 悬空引用 / 断裂晋升 / 多向量空间 / 向量损坏·过期 / (事件流) 重复·非单调. 本期落地其中"当前数据形状能确定性判定"的子集 (D2/D4/E2/E3/E4/D1 + ID-DUP + EVT-DUP), 明排 §2.2. 每条线索仍是**导航级弱证据 + 需人工确认**, 与裁决 §3「当前只能给导航级线索」一致.

---

## 2. 后端契约

### 2.1 聚合纯函数

```
build_code_leads(character: str) -> dict
```

返回:

```
{
  "character": str,
  "leads": [ lead, ... ],                 # 机械不变量线索, 按强度排序
  "excluded_content_findings": [ {code, category, count}, ... ],  # 内容质量类, 仅计数
  "embedding_status": "ran" | "unavailable",   # 无向量→E* 无法检查 (缺席≠通过)
  "evt_status": "ran" | "unavailable" | "truncated",  # events.ndjson 检查状态
  "warnings": [str, ...],                 # 透传 build_overview meta.warnings
  "generated_at": isoformat,
}
```

`lead` 形状:

```
{
  "code": str,               # D2/D4/E2/E3/E4/D1 或 ID-DUP/EVT-DUP
  "invariant": str,          # 违反的不变量 (i18n key 由前端按 code 渲染)
  "strength": "high"|"medium"|"low",
  "suspect_modules": [str],  # 指向的主程序模块/写入路径 (疑似方向, 非精确定位)
  "missing_evidence": [str], # 缺哪些运行期证据才能坐实 (可行性 §3)
  "count": int,
  "examples": [ {id, label}, ... ],   # 仅 id/label, 结构化
  "needs_human_confirm": True,        # 恒为 True
}
```

### 2.2 映射表 (白名单 `MECHANICAL_LEAD_CODES`)

对 `build_overview` findings 里下列 code 生成线索; **suspect_modules 已核对主程序真实布局** (见 §附):

| code | 现象 | suspect_modules (真实路径) | strength |
|---|---|---|---|
| `D2` | persona `source==reflection` 无 promoted_from/merged_from 溯源边 | `memory/reflection/promotion.py` · `memory/reflection/promotion_merge.py` | medium |
| `D4` | 反思引用已硬删事实 (`source_fact_ids` 指向不存在 fact) | `memory/facts.py` 删除路径 · `memory/reflection/persistence.py` | high |
| `E2` | 向量过期 (`embedding_text_sha256` 与当前 text 不符) | `memory/embeddings.py` · `memory/embedding_worker.py` | medium |
| `E3` | 向量损坏 (维度/数值非法) | `memory/embeddings.py` · `memory/_embeddings/schema.py` | high |
| `E4` | 多向量空间 (混入不同维度/模型) | `memory/embeddings.py` · `memory/_embeddings/profiles.py` | high |
| `D1` | 游离反思 (无有效来源事实) | `memory/reflection/synthesis.py` | **low** (也可能只是还没跑反思, 非纯代码信号) |

### 2.3 全码分类表 (LR-2 · 全族差集, smoke Y9 守无静默漏分)

`build_overview` 现发出的所有 code, 每个显式归类:

- **→ 线索 (lead)**: `D2` `D4` `E2` `E3` `E4` `D1(low)`.
- **→ 排除 (仅计数, 内容/运营类, 明排代码反推)** — 与 `memory_overview` 实际发出的码集逐一核对: `A1` `A2` `A3` (冗余) · `B1` `B2` `N1` (矛盾/待核对) · `C1` `C2` (归因) · `D3` (游离事实·info) · `E1` (缺向量·运营) · `F2` `F3` `F4` (流水线比率) · `G1` (晋升漂移) · `H1` `H2` `H3` (留存).
- **未来新码**: 白名单外默认归"排除", 对上游新增 finding 免疫 (绝不误升为代码线索). smoke Y9 断言"每个实发 finding 要么进 leads 要么进 excluded, 无静默漏分".

### 2.4 两个新增确定性检查

**ID-DUP (主键重复)** — 读原文件, 三容器形状分别取 id (grep 实证, 见 §附):
- `facts.json` / `reflections.json` = 对象数组 → 取顶层元素 `id`.
- `persona.json` = `{entity: {facts: [{id}, ...]}}` → 取**嵌套** `persona[entity]["facts"][].id` (顶层键是实体名, 非 id).
- 同一集合内 id 重复 → 线索 (uuid/id 分配或写入去重 bug; 溯源图会静默折叠 dup 从而掩盖). strength high. suspect_modules: 对应写入模块 (`memory/facts.py` / `memory/reflection/persistence.py` / `memory/persona/persistence.py`). 缺字段/空文件 → 跳过不报错.

**EVT-DUP (重复 event_id)** — 读 `_memory_dir(character)/events.ndjson`:
- 逐行解析 (行上限 `_EVT_SCAN_MAX_LINES = 20000`, 主程序 10k 行触发 compaction, 超限截断扫描 → `evt_status="truncated"`).
- 检测重复 `event_id` (uuid4 不可能自然碰撞 → 重复=append/reconcile 写入 bug). strength high. suspect_modules: `memory/event_log.py`.
- 附带统计无法解析/缺 event_id 的坏行 (info 级弱信号, 计入 lead.count 说明或 warnings).
- 文件缺失 → `evt_status="unavailable"` (**未检查、不代表通过**).

### 2.5 诚实状态 (LR-1 · 核心承诺 · fail-loud 不 silent)

- `embedding_status = "ran" if build_overview meta.generated_with_embeddings else "unavailable"`. 无向量时概况根本不发 E2/E3/E4 → 线索缺席**不等于**无向量 bug; 状态位让 UI 区分"未检查 vs 通过".
- `warnings` 透传 `build_overview` 的 `meta.warnings` (坏/空记忆文件的软降级信号).

### 2.6 端点

```
GET /api/memory/code_leads
```

1. `session = _require_session()` → 404 `NoActiveSession`.
2. `character = _require_character(session)` → 409 `NoCharacterSelected`.
3. `return await asyncio.to_thread(build_code_leads, character)`.

**纯读裁决**: 不用 `session_operation` (避免 autosave 副作用, L63); 与 `/overview` `/lineage` 同构.

---

## 3. 前端 UI (本功能重点 — 防误导是第一优先级)

新文件 `static/ui/memory_trace/code_leads.js`, 导出 `mountCodeLeadsPage(container, ctx)`, 注册进 `workspace_memory_trace.js` 的 `PAGES` (embedding 之后). nav 键 `memory_trace.nav.code_leads`, 标题带「(开发者)」后缀.

### UI-1 不可跳过的红色警告 (页首固定)

页面最顶部固定 danger 红色警告框 (`.code-leads-notice--danger`), **非折叠、不可关闭**, 6 个要素逐条列点: ① 这些是**排查方向/线索, 不是 bug 报告**; ② 反推**不完全可靠**, 可能假阳性; ③ 每条**需人工在主程序代码里确认**; ④ 本页**绝不自动改任何代码**、testbench 只读; ⑤ **内容质量类问题不在此反推**, 只看机械不变量; ⑥ 真链接 (`<a href="/docs/code_leads_guide" target="_blank">`) 指向面向使用者的 [代码线索使用说明](./code_leads_guide.md) (进 `_PUBLIC_DOCS` 白名单). **不**直接链内部裁决文档 `MEMORY_CODE_INFERENCE_FEASIBILITY.md` —— 内外分离, testbench 对外文档面绝不泄漏蓝图/phase 内部术语.

### UI-2 措辞纪律 (全站文案红线)

- **禁用词** (线索卡文本内): "bug"/"缺陷"/"错误"/"确认存在"/"检测到问题" 等确定性断言.
- **必用词**: "疑似"/"线索"/"值得排查"/"方向"/"需人工确认"/"不确定".
- 卡片标题用**祈使排查句式** ("建议排查: 删除事实时是否级联清理引用它的反思"), 而非结论句式. 文案集中 i18n 统一审校. (注: 页首免责声明**刻意**含"不是 bug 报告", 故禁用词只约束线索卡, 不约束免责声明 — smoke V3 仅扫线索卡.)

### UI-3 证据强度可视化 (降低而非抬高信心)

- 每条线索一个强度 chip (`high/medium/low`), 配色**克制** —— 即便 high 也不用危险红大色块, 用中性描边 + 文字; low 明显弱化. chip 旁常驻小字"(仍需人工确认)".
- 强度语义 tooltip: "high=违反几乎必是写入/迁移代码问题; low=也可能只是还没跑某道工序, 非纯代码信号".

### UI-4 线索卡结构 (每条自带"怀疑"脚手架)

单卡固定分区: ① code 徽章 + 违反的不变量; ② 强度 chip +「(仍需人工确认)」标; ③ **指向的主程序模块** (疑似方向, 非定位); ④ **缺哪些运行期证据才能坐实** (引可行性 §3); ⑤ 建议排查动作 (祈使句); ⑥ count + examples (仅 id/label).

### UI-5 排除区 + 诚实状态 (诚实的两面)

- **排除区** (线索下方常驻): 明示"以下类别**故意不做代码反推**", 列被排除的内容质量类计数 + 说明"属数据/模型/配置层, 请走记忆运营而非改代码".
- **空态诚实**: 无线索时显示"未发现机械不变量违反 → **无代码排查线索 (≠ 代码一定没问题)**", 绝不显示"代码没问题".
- **状态行**: 显式显示 `embedding_status` / `evt_status` (ran / unavailable=未检查非通过 / truncated) + 渲染 `warnings`.

### 其它 (前端 LESSONS)

- **子页 teardown (LR-3 · §7.18)**: `mountCodeLeadsPage` 返回 teardown, `state.js::on(session…)` 的 off 存 `host.__offMemoryAnalysis` (workspace 已有该 wiring), 再挂载前 teardown; 不靠 `innerHTML=''` 回收订阅.
- **null 追加守护 (LR-4 · §2.7)**: 渲染用 `filter(Boolean)` / DocumentFragment, 禁 `append(null)`.
- **刷新竞态 (LR-10 · §7.19)**: 切会话/手动 reload 的 GET 用 stale-guard (per-page token) 防旧响应覆盖.
- **i18n (LR-7 · §5.1)**: 参数化文案用**函数值** + `i18n('key', count)` **单次调用**; **严禁 `i18n('key')(count)`**.
- **CSS**: `testbench.css` 加 `.code-leads-*` + `.code-leads-notice--danger` 红色固定变体 + 强度 chip 克制配色.
- 复用既有 phase 处理 (loading / no_session / no_character / error), 与兄弟子页一致.

---

## 4. 测试

### 4.1 后端 `smoke/p45_memory_code_leads_smoke.py`

- **Y1** 机械发现 (D4 悬空引用) → 线索映射, suspect_modules 指向真实文件.
- **Y2** 内容质量发现 (矛盾/冗余) **不进** leads, 进 excluded 计数.
- **Y3** ID-DUP: facts.json 主键重复 → 高强度线索; persona 嵌套 id 重复也命中.
- **Y4** EVT-DUP: events.ndjson 写重复 event_id → 高强度线索 / 全唯一 → 无 / 缺文件 → `evt_status=unavailable`.
- **Y5** 端点 happy: 200 + 形状 (leads/excluded/embedding_status/evt_status/warnings).
- **Y6** 无会话 → 404; 无角色 → 409.
- **Y7** 干净记忆 → 空 leads + 诚实空态标志.
- **Y8** (LR-1) 无向量角色 → `embedding_status=unavailable`, 不因缺 E* 而谎报通过.
- **Y9** (LR-2) 全码分类: 富 fixture 下每个 finding code 要么进 leads 要么进 excluded, 无静默漏分类.

### 4.2 前端 `smoke/p46_memory_code_leads_ui_smoke.mjs` (jsdom)

- **V1** 子页注册 + nav 标题带「(开发者)」.
- **V2** 页首红色 danger 警告框存在、固定、非折叠不可关闭、含 6 要素 + 可行性链接.
- **V3** 措辞纪律: **仅扫线索卡文本**不含禁用断言词; 卡标题祈使句式.
- **V4** 每条线索卡含强度 chip +「(仍需人工确认)」+ 缺失证据分区 + 指向模块.
- **V5** 排除区常驻显示被排除计数 + "故意不反推"说明.
- **V6** 空态显示"无线索≠代码没问题".
- **V7** 状态行 (embedding_status / evt_status ran/unavailable).

> **LR-8 · §5.3 jsdom≠浏览器**: 红色警告的视觉醒目度 / CSS 层叠 (§7.21 `[hidden]` vs flex) 必须由用户真实浏览器手测一次; p46 仅第一道过滤.

---

## 5. 文档与版本 (对齐 P30/P31 回写清单)

- 本蓝图 + 内部裁决文档 `MEMORY_CODE_INFERENCE_FEASIBILITY.md` 属**开发者文档** → 留在 `health_router.py` `internal_only_docs`, **不进 `_PUBLIC_DOCS`**.
- **交付后追加 (2026-07-15, 相关人员反馈)**: ① 子页无法向下滚动 → `.memory-code-leads` 补 `flex:1 + min-height:0 + overflow-y:auto` (原缺 overflow, 被父级 `overflow:hidden` 截断). ② 顶部警告里的文档指引原是纯文本文件名、难以找到 → 新写**面向使用者的干净说明** `docs/code_leads_guide.md` (内外分离: 不泄漏蓝图/phase 内部术语), 进 `_PUBLIC_DOCS` (key `code_leads_guide`), 警告 ⑥ 改为真 `<a href="/docs/code_leads_guide" target="_blank">`. p26 D5/D6 仍绿 (超集校验 + `.md` basename); p46 V2 改断言真链接 href.
- 更新可行性文档 §5: P32 已落地导航级线索版; 订正 §2.1/§3 (游标真身=cursors.json, 本期用 EVT-DUP 替代).
- USER_MANUAL 加简短一节, 显著标注「面向代码相关人员, 普通测试员可忽略」+ 强 caveat.
- CHANGELOG v1.12.0 (用户可见新子页 → MINOR) / PLAN P32 / PROGRESS P32 / AGENT_NOTES P32 / ARCHITECTURE_OVERVIEW (新端点 + 子页).
- LESSONS_LEARNED: 已加 **§1.7** (设计前必读经验教训) + §7.A 候选 (反推须核对真实 schema).
- `config.py`: `TESTBENCH_VERSION` 1.11.0→1.12.0 + `TESTBENCH_PHASE` + `TESTBENCH_LAST_UPDATED`.

---

## 6. §A 开工前设计自审门禁 (沿用 P24/P25/P27-P31 元审惯例 · 6 轮已收敛)

> 方法: 对照 3 锚点逐条思想实验 —— (1) 冻结横切原则 + LESSONS §7; (2) 实证的主程序/testbench 真实代码; (3) 用户原始意图. 产出矫正 SR#/RV#/LR# 已回写正文; 末轮 L31 语义漂移诊断 + RAG 停机.

### 第 1 轮 — 内容正确性 (SR-1..6, 真实代码实证)

- **SR-1【高危已消解】游标单调性不成立**: events.ndjson 无 seq/游标字段 (`{event_id:uuid4,type,ts,payload}`). 换 EVT-DUP (重复 event_id).
- **SR-2【高危已消解】D5 直查 source_id 冗余且重现假阳性**: 概况 D2 已用溯源边定案 (§7.25「别重算 chokepoint 已定关系」; p39 O5 守 merge 不误报). 换 ID-DUP + 依赖既有 D2.
- **SR-3** suspect_modules 核对真实 memory 布局改正. **SR-4** 排除计数改白名单 (对新码免疫). **SR-5** UI 禁用词 smoke 只扫线索卡 (免责声明刻意含"bug"). **SR-6** 丢 D5 后 `build_code_leads` 无需二次 lineage 构建.

### 第 2 轮 — 规范符合性 (RV-1..6)

- 补齐本 §A 门禁 · LESSONS 回写 · /docs 路由 (蓝图进 internal_only_docs 非 _PUBLIC_DOCS) · 版本元数据 (`TESTBENCH_LAST_UPDATED`) · 订正可行性文档 (游标真身=cursors.json).

### 第 3 轮 — LESSONS 逐条 (LR-1..10)

- **LR-1【核心承诺】不 silent fallback (§1.1 + §7.14/§7.17)**: `embedding_status`/`evt_status` + 透传 `warnings`, UI 区分"未检查 vs 通过". **LR-2** 全码分类表 (§7.2/§7.20/L61). **LR-3** 子页 teardown (§7.18). **LR-4** `append(null)` 守护 (§2.7). **LR-5** 沙箱感知读 (§2.3, 复用 `_memory_dir`/`_read_json`). **LR-6** events 扫描行上限 (§7.24). **LR-7** `i18n(key,...args)` 单调用 (§5.1). **LR-10** 刷新 stale-guard (§7.19). **LR-8** jsdom≠浏览器→用户手测 (§5.3). **LR-9** RAG 表 (§1.4).
- 复核已满足: §2.1 软硬错 (404/409 硬, 状态位软) · §7.25 (D2 非 source_id) · L63 (只读不取锁) · §1.6 语义契约 vs 运行时 (只反推 WHAT) · §2.10 (读自家盘软告警).

### 第 4 轮 — L31 语义漂移诊断

- 用户初衷三点 (子页/高亮警告不可靠/几条线索) 逐条对应第 4 子页 / UI-1..3 / 映射+2 检查. **无漂移**.
- 反向守护: 不得膨胀成"自动改代码 / 复现 runtime / 内容质量类下代码结论 / 通用记忆校验引擎". §2.2 排除线 + 只读边界双护栏.

### 第 5 轮 — 产品语义对抗 (两面失败)

- **面 A 过度信任→改对的代码**: UI-1/2/3 全套压低置信 + `needs_human_confirm` + `missing_evidence`.
- **面 B 沦为噪音→全体无视**: 只收高/中置信机械不变量, 明排低信噪内容质量类, 干净记忆诚实空态, `embedding_status`/`evt_status` 让"没线索"可信.
- **面 C 被当验收章**: 空态/文案明示"无线索≠代码没问题、≠未检查项通过".

### 第 6 轮 — 边界与降级完备性

- ID-DUP 三容器形状 (grep 实证); examples 隐私 (本地只读不外发, USER_MANUAL 注明勿截图外传); 降级矩阵全 user-visible (404/409/软降级+warnings/embedding_status/evt_status/truncated).

### RAG 覆盖表 (§1.4 停机判据)

- 🟢 后端映射 + 白名单排除 + 全码分类 · ID-DUP/EVT-DUP · 诚实状态 · 只读端点 · UI 五要素 · p45(Y1-Y9)/p46(V1-V7) · 文档回写 + §A —— 规格完整 + 真实代码实证 + smoke 规划齐.
- 🟡 无. 🔴 无 (唯一遗留"红色警告醒目度需用户真实浏览器手测"= 验收动作, 非规格 gap).
- **停机**: M 档全绿、无 🔴, 已做 6 轮边际收益转负 → 审查收敛, 开工.

---

## 附: 关键决策摘要 (给未来 Agent)

- 第 4 子页「代码线索 (开发者)」; 只反推**机械不变量类** (D2/D4/E2/E3/E4/D1 + ID-DUP/EVT-DUP), **永排**内容质量类.
- **UI 是重点**: 不可跳过红色警告 + 措辞纪律 + 克制强度 + 诚实空态/状态 —— 压低置信预期, 防"弱线索被当确诊 bug".
- 后端纯读 (`build_overview` 一次 + 沙箱感知文件扫描), 无 LLM, 端点不取锁 (L63).
- 两个原设想检查 (游标单调/D5) 经真实代码证伪, 换 EVT-DUP/ID-DUP (SR-1/SR-2); suspect_modules 用真实路径.
- 诚实状态 `embedding_status`/`evt_status` 是核心承诺: "没线索" 必须能区分"没 bug"还是"没查" (LR-1).

# P29 记忆系统概况自动分析 (Memory System Overview) — 蓝图

> **Single Source of Truth**. 本文件是 P29 阶段的**唯一权威规格**. `PLAN.md` 条目 `p29_memory_overview` 仅作索引; `PROGRESS.md` / `AGENT_NOTES.md` 描述均以本文件为准.
>
> **定位**: 「记忆系统分析」(顶层 workspace `memory_trace`, 标题 *Memory Analysis 记忆系统分析*) 的**第三个子页**, 但**逻辑上是入口 / 第一页** (用户原话: "或者说其实这才是真正的第一个模块"). 子页 id `overview`, 子页菜单项 **「系统概况」**. 它是一个**概览 + 自动排查**仪表盘: 一眼看清当前角色记忆系统的运行状况与**潜在问题**, 再从每个发现一键**下钻**到已建的两个详情子页 (记忆溯源 P27 / 向量空间 P28)。
>
> **阶段号 / smoke 前缀**: 沿用 P27/P28 "阶段号与 smoke 计数器有意分叉"约定. 本阶段取**阶段号 P29**; 新增 smoke 接续单调计数器, 后端 `p39_`、前端 jsdom `p40_` 起 (现有最高 `p38`).
>
> **动因 (2026-06-30)**: 用户下达第三子页需求 (引文): "做记忆分析系统的第三个子模块……记忆系统概况自动分析模块。这是一个概括性信息子页面。记忆系统相关人员希望能通过这个页面来快速分析记忆系统的运行情况, 尤其是希望能找到记忆系统当前存在的问题与不足。希望能用这个子页面来**自动排查记忆中的矛盾记忆、多次反复无意义的记忆重复以及其他可能存在的记忆系统潜在问题**。相关人员在这个页面阅览相关概况之后, 再去之前写好的这两个子页来具体查看详情。你应该全面考虑, 基于我们现在能拥有的记忆数据, 能够作出什么样的最大化的智能化自动分析, 并为用户提供清楚、简洁的信息图景。"

---

## 1. 目标与边界

### 1.1 核心目标

让记忆系统相关人员在一个 **"概览卡片 + 自动发现清单"** 的仪表盘子页里, 不用先翻两个详情子页, 就能回答:

1. **现状 (State)**: 当前角色记忆**由什么组成**、**规模多大**、**向量覆盖如何**?(facts/reflections/persona/corrections/对话各多少, 嵌入了多少/缺多少/过期多少, 分了几个簇。)
2. **问题 (Issues)** —— 用户最关心的: 这套记忆**当前有哪些潜在问题与不足**? 自动排查并按严重度列出:
   - **多次反复无意义的记忆重复** (冗余: 近重复对 / 反复写入的重复簇);
   - **矛盾记忆** (按 §6 三层: 已落盘的纠正/否决 = 真矛盾; 同实体同主题对 = 待核对候选; 经 LLM NLI 判定后才标"矛盾" —— 因为 **相似 ≠ 矛盾**, 向量只做检索);
   - **归因质量问题** (反思声明的来源与语义最近的事实不一致 —— 漏源/虚源);
   - **流水线产出与停滞** (抽取产出率 / 事实吸收率 / 晋升率 / pending 积压 / 否决率 —— 诊断抽取·反思·晋升三道工序哪环掉链);
   - **晋升保真度** (人设条目 vs 其来源反思的语义漂移 —— 晋升时被改歪/夹带);
   - **留存质量** (重要性错配 / 低质事实 / 陈旧僵尸事实);
   - **结构问题** (游离未吸收的事实 / 悬空引用 / 断裂的晋升链);
   - **嵌入健康问题** (覆盖率低 / 向量过期 / 混入多个向量空间 / 向量损坏)。
   > 这些发现除按"问题类型"列出, 还各带一个**记忆功能环节 (extract/dedup/reflect/promote/correct/embed/structure)** 标签, 可按工序排查 (§4.0)。
3. **下一步 (Drill-down)**: 每个发现都给一个**"去查看详情"**按钮, 跳到对应详情子页并定位到相关记忆 / 模式 (记忆溯源聚焦某节点; 向量空间切到重复/聚类/语义源对比模式)。
4. **智能化概括 (可选 LLM)**: 一键 **「生成 AI 体检报告」**, 把上面所有结构化信号喂给记忆模型, 产出一段**简洁的自然语言诊断 + 优先级建议**; 失败优雅回退到纯规则结论。

> **一句话**: 本页是记忆系统的**体检仪表盘 + 问题入口**, 不是又一个详情视图。它的产出是"**信息图景 + 可下钻的问题清单**"。

### 1.2 范围内 (In-Scope)

- **「记忆系统分析」第三子页** `overview` (挂进 `workspace_memory_trace.js` 的 `PAGES`), 置于数组**首位**成为该 workspace 的**默认子页** (它是入口)。
- **纯只读聚合后端**: 新增**单一聚合层** `pipeline/memory_overview.py`, **复用并组合**已建的两个 chokepoint —— `memory_lineage.build_lineage_snapshot` (结构/状态信号) 与 `embedding_space` 的 `_build_space`/`build_duplicates`/`build_clusters`/`build_bridges` (向量信号) —— 再**派生**"问题发现 (findings)"。前端**照画**, 不二次推导 (沿用 P27/P28 原则)。
- **问题发现引擎 (规则层)**: 把上述信号按 §4 的**检测规则**算成一组带**严重度 (ok/info/warn/bad)** 的 finding, 每个 finding 带**计数、简短解释、下钻目标**。
- **智能化层 (LLM, 按需)**: ①**AI 体检报告** (`POST /api/memory/overview/ai_report`); ②**矛盾候选 LLM 裁决** (`POST /api/memory/overview/contradictions`)。均复用 `memory.llm` wire-stamp 范式, 永不抛、优雅回退 (见 §5)。
- **跨子页下钻联动**: finding → `ctx.goTo('lineage', {focusNodeId})` / `ctx.goTo('embedding', {mode, threshold, selectId})`。为此**小幅扩展** `embedding_space.js` 的 mount 使其消费 `ctx.opts` 设定初始 mode/阈值/选中 (向后兼容)。
- **诚实数据闸门**: 0 向量角色 —— 向量类发现降级为"需导入已嵌入角色"引导, **结构类发现 (来自 lineage) 仍照常工作**。

### 1.3 范围外 (Out-of-Scope, 明文约束)

沿用 P22/P24/P25/P27/P28 "防无限扩张筐"哲学:

- **不改主程序**: 不动 `memory/*.py`、不写新字段、不 emit 新事件。只消费现有持久化数据 + 两个已建聚合器。
- **不写回记忆**: 纯只读分析。**不**因为"发现了矛盾/重复"就自动合并/删除/改写记忆 (修复仍走 Setup → Memory 四子页人工操作)。本页只**发现与导航**, 不**执行修复**。
- **不冒充"矛盾"判定**: 向量相似度**高 ≠ 矛盾** (高 cos 只说明话题接近, 可能是一致、互补或冲突)。没有 LLM 裁决时, 一律称**"高相似待审候选"**, 绝不标"矛盾"; 唯一能直接断言的"矛盾"是 `persona_corrections.json` 里**已落盘的人设纠正** (真事件)。详见 §6。
- **不复现主程序 runtime**: 不模拟 embedding worker / evidence 半衰期 decay / FTS5。本页用的是磁盘数据形状 (WHAT), 非运行期计算 (HOW)。
- **不引入前端图库 / 构建链**: 仪表盘用原生 `el()` DOM 卡片 + 进度条; 不加 d3/plotly/chart.js/npm/webpack。少量占比条/环形用 inline SVG 或 CSS。
- **不做跨 session / 多角色对比 / 趋势时间线**: 只看当前 active session 当前角色的**当前快照** (无历史趋势; 主程序未持久化体检历史)。
- **不重算两个子页已有的东西**: 散点坐标 / NxN 矩阵热力图等**详情**留在 P28; 概况页只取**汇总数字 + 问题清单**, 不在本页再画散点/矩阵。
- **不做"健康分"黑箱评分**: 不输出一个易误导的 0–100 综合分; 改用**透明的分维指示板 + 需关注项计数** (见 §4.6 决策)。

### 1.4 现有数据能支撑到什么程度 (诚实声明)

| 目标 | 数据支撑 | 表现 |
|---|---|---|
| 组成/规模/状态 | `memory_lineage` 的 `meta.counts` + 各 node `status`/`meta` | ✅ 直接汇总 (对任何角色都可用, 不需向量) |
| 反复无意义重复 | `embedding_space.build_duplicates` (cos≥阈值) + `build_clusters` (高内聚同型大簇) | ✅ 有向量才行; 无向量 → 该类发现引导导入 |
| **矛盾 — 已落盘 (L0)** | `persona_corrections` + `rejected` 反思 + 被 suppress/merged 的 persona | ✅ **真矛盾/纠正事件**, 直接列 |
| **矛盾 — 候选 (L1)** | 同实体 + 同主题 (向量高相似带 ∪ 同簇 ∪ 共享 tag) 的对 | ⚠ 仅"待核对候选" (检索信号, **非**判定) |
| **矛盾 — 判定 (L2)** | 记忆模型 NLI 判定候选对 | ⚠ 唯一能断言"矛盾"; 需配 memory 模型 |
| 归因质量 (漏源/虚源) | `embedding_space.build_bridges` 的 `missing_in_declared`/`extra_in_declared`/`agreement` | ✅ 有向量 + 有 facts&reflections 才行 |
| 结构问题 | lineage `nodes/edges` 派生 (absorbed/孤立/悬空/pending/断链) | ✅ 不需向量 |
| 嵌入健康 | `embedding_space._build_space().health` (embedded/missing/stale/corrupt/dims) | ✅ 即使 0 向量也能给"覆盖率 0%"结论 |
| 智能诊断/矛盾裁决 | 记忆模型 (`memory` 组配置) | ⚠ 需用户配好 memory 模型/API; 否则回退规则结论 + 明确提示填哪个 API (复用 P28.5 修复范式) |
| 历史趋势 / 体检打分历史 | **无持久化** | ❌ 只给当前快照, 不画趋势 |

---

## 2. 数据与机制基础 (信号清单)

> 权威来源: `pipeline/memory_lineage.py` (结构/状态) + `pipeline/embedding_space.py` (向量) —— 二者已是各自领域的**只读 chokepoint**, P29 在其之上做**组合 + 派生**, 不绕过、不重复读盘逻辑。

### 2.1 来自 `memory_lineage.build_lineage_snapshot(character)` 的信号

- `meta.counts`: `messages / recent_memos / facts / reflections / persona / corrections`。
- `meta.sources_present`: `events_ndjson / time_indexed_db / trace_provenance` (有无对话语料 / 事件流)。
- `meta.file_warnings` + `meta.corpus_warnings`: 读盘软错。
- `meta.node_budget.truncated`: 图是否被节点预算截断。
- node 级 (`nodes[].type/status/meta`):
  - fact: `meta.absorbed` (是否被吸收) / `meta.importance` / `meta.tags`。
  - reflection: `status` (pending/confirmed/promoted/rejected…) / `meta.source_fact_ids` / `meta.feedback`。
  - persona_entry: `status` (active/suppressed) / `meta.source` / `meta.source_id` / `meta.merged_from_ids` / `meta.protected`。
  - correction: `meta.old_text` / `meta.new_text` (已记录的矛盾)。
- edge 级: `source_fact` (fact→reflection) / `promoted_from` / `merged_from` / `corrects`。

### 2.2 来自 `embedding_space` 的信号

- `_build_space(character).health`: `total/embedded/missing/stale/corrupt` + `dims_present` + `primary_dim` + `primary_count` + `other_space_count` + `numpy_ok`; 顶层还有 `umap_available`。
- `build_duplicates(character, threshold)`: `pairs[{a,b,score,a_type,b_type,same_type,…}]` + `count` + `capped` + `candidates`。
- `build_clusters(character)`: `algo` + `n_clusters` + `noise_count` + `clusters[{cluster,size,medoid_id,label,member_ids,samples}]` + `assignments`。
- `build_bridges(character)`: 每条反思的 `semantic_top` / `missing_in_declared` / `extra_in_declared` / `agreement` + `fact_count` / `reflection_count`。

> 这些**已存在且经 smoke (p37/p38) 锁定**, P29 直接调用即可, 无需新读盘/新数学。

### 2.3 派生信号 (P29 新增, 纯函数)

仅基于 §2.1/§2.2 的产出做集合/计数运算, 不碰原始 JSON:

- **游离事实**: `fact.absorbed==false` 且其 id 不在任何 `reflection.source_fact_ids`。
- **悬空引用**: `reflection.source_fact_ids` / `persona.source_id` / `merged_from_ids` 指向不存在的节点 id。
- **停滞反思**: `reflection.status == "pending"` 的数量 (可选: 结合 `created_at` 估"久未晋升")。
- **断裂晋升**: persona `source=="reflection"` 但 `source_id` 缺失/悬空。
- **重复度**: 至少有一个近重复伙伴的条目占 `embedded` 的比例。
- **重复簇**: `clusters` 中 `size ≥ R` 且成员近重复占比高、同 type 的簇 (= 反复写入同一主题)。
- **冗余代价**: `近重复簇/对`估算的"可去重条数" = 参与近重复的条目数 − 其代表(去重后)数; 给"约 N 条冗余, 占 X%"。
- **高相似待审候选**: `cos ∈ [SUSPECT_LOW, DUP_THRESHOLD)` 且**同实体** (优先同 type) 的对 —— 疑似冲突/可合并, 待人工或 LLM 判。
- **晋升漏斗比率** (诊断三道工序): `被反思引用的 fact 数 / facts 总数` (抽收率)、`reflections / facts`、`晋升 persona / reflections` (晋升率); 任一环比率异常低 → 该工序在掉链。
- **晋升保真度漂移**: 对每条 `source=="reflection"` 的 persona, 取其 `source_id` 反思, **二者都在主向量空间**时算 cosine; `< FIDELITY_DRIFT` 者计入"晋升后语义偏离"。(纯用现成向量, 无 LLM)
- **未解决矛盾**: `persona_corrections` 中 `old_text` 仍出现在当前 persona 文本里的条数 (= 纠正未生效/旧说法仍在); 反之 `new_text` 已落地 = 已解决。
- **抽取产出率**: `facts 总数 / 对话回合数` (有 `time_indexed.db` 时); 过低=抽取近乎停摆, 过高=可能过度抽取噪声。
- **留存质量**: 超短/空 fact、无 entity、未打标比例; 高 `importance` 却游离未吸收 (重要的没留住); 低 `importance` 却被反复重复 (噪声被放大)。
- **陈旧事实**: `created_at` 很旧且从未被任何反思引用/晋升触及的 fact (长期僵尸记忆)。
- **结论可信度 (元诊断)**: 本次分析覆盖了多少数据 / 缺了什么 (无向量、无对话 db、无 events.ndjson、读盘软错), 用于在页面上诚实标注结论局限。

---

## 3. 架构

### 3.1 后端 (只读聚合, 单层 chokepoint, 组合既有聚合器)

新增 `tests/testbench/pipeline/memory_overview.py`:

- `build_overview(character) -> dict` 纯只读函数 (同步, CPU-bound)。内部:
  1. 调 `build_lineage_snapshot(character)` 取结构/状态信号 (§2.1)。
  2. 调 `embedding_space._build_space(character)` 取 health + 内存矩阵; 在其上调 `build_duplicates` / `build_clusters` / `build_bridges` 取向量信号 (§2.2)。为避免重复 `_build_space` 多次 (它读盘+解码), **P29.1 优化**: 在 `memory_overview` 内一次 `_build_space`, 把矩阵传给轻量内部 helper, 或直接复用现有函数 (各自再 `_build_space` 也可接受, 数据量有限 + 已 `to_thread`)。首版可直接复用现有函数, 简洁优先; 若实测慢再下沉共享。
  3. 跑 §4 检测规则 → `findings[]` (带 severity / category / count / drill)。
  4. 组装 `cards` (概览卡片数字) + `findings` + `meta` (coverage, warnings, gates)。
- 返回 envelope (前端 verbatim 渲染):

```
{
  cards: {
    composition: { facts, reflections, persona, corrections, messages, recent_memos },
    coverage:    { total, embedded, missing, stale, corrupt, embedded_pct },
    space:       { primary_dim, primary_count, other_space_count, umap_available, numpy_ok },
    clusters:    { algo, n_clusters, noise_count, largest_cluster_size },
    pipeline:    { absorb_rate, reflect_rate, promote_rate,   # facts→reflections→persona 漏斗
                   pending, rejected, oldest_pending_age_days,
                   extract_yield, redundancy_waste_pct },
  },
  findings: [
    # stage ∈ extract|dedup|reflect|promote|correct|embed|structure  (按功能环节, §4.0)
    { id, stage, category, severity, title, count, detail, drill:{page,opts}, sample_ids:[...] },
    ...
  ],
  meta: {
    character, attention_count,          # severity≥warn 的发现数 (= "需关注项")
    gates: { has_embeddings, has_lineage_db },
    confidence: {                        # 元诊断: 结论基于多少数据 / 缺了什么 (§4.0 / 派生)
      analyzed_total, embedded, has_conversation, has_events, missing_pieces:[...],
    },
    warnings: [...],                     # 读盘/语料软错聚合
  }
}
```

端点 (追加到 `routers/memory_router.py`):

| 端点 | 作用 | 备注 |
|---|---|---|
| `GET  /api/memory/overview` | 概览 + 规则发现 (无 LLM) | **`await asyncio.to_thread(build_overview, character)`** (CPU-bound, 见下) |
| `POST /api/memory/overview/ai_report` | LLM 体检报告 (按需) | `session_operation` 包裹 (stamp `memory.llm`), 永不抛 |
| `POST /api/memory/overview/contradictions` | LLM 裁决高相似候选 (按需) | 同上; body 传候选对集合或由后端重算 |

> **必守教训 (P28.5 修复, 2026-06-30)**: 重 CPU (PCA/cosine/聚类) 的读端点**必须 `asyncio.to_thread`**, 否则同步计算阻塞 uvicorn 单事件循环, 导致**其它子页/工作区一起卡"加载中"**。`/overview` 聚合了多项向量计算, 是阻塞重灾区, 强制 `to_thread`。

#### 3.1.1 实现对账注意 (自审发现, 防坑)

- **`_build_space` 重复开销**: `build_duplicates` / `build_clusters` / `build_bridges` 各自内部都会再跑一次 `_build_space` (读 3 JSON + 解码全部向量)。`build_overview` 若依次调它们 = `_build_space` 跑 3–4 次。数据有界 + 已 `to_thread`, **MVP 可接受**; 若实测慢, 给这三个函数加可选 `space=` 注入参 (一次 `_build_space` 传入复用), 不改其对外签名默认行为。
- **除零守卫**: 所有比率 (吸收率/晋升率/否决率/抽取产出率/重复度/覆盖率) 分母为 0 时返回 `None` 并在 UI 显示"—", 不计入 finding。
- **晋升率定义 (防 >1)**: `promote_rate = 被≥1条 persona 以 source_id 指向的"不同反思"数 / reflections 总数` (按不同反思去重, 避免一反思促成多 persona 致比率虚高)。
- **抽取产出率 (F5) 的对话回合数**: lineage `counts.messages` 受 `node_budget` 截断, **不可**当真值; 用 `conversation_corpus.load_conversation_corpus(character)` 的真实回合数 (它已做只读打开 + try/finally cleanup 释放句柄, Windows 锁安全); 无 `time_indexed.db` → F5 不出。
- **G1 晋升保真度的部分覆盖 (诚实)**: 仅当 persona 条目**与其 `source_id` 反思都在主向量空间**时才能算 cosine; 来源反思缺向量/不同维 → 该条跳过 (计入"无法核验"而非"漂移")。
- **LLM 端点的计算位置**: `ai_report` / `contradictions` 进 `session_operation` 取 session+character 后, 先 `await asyncio.to_thread(build_overview, character)` (或重算候选) 再 `ainvoke` —— 重算在线程、LLM 在事件循环, 锁内不阻塞 (镜像 `cluster_labels`)。

### 3.2 前端 (仪表盘子页)

- 子页文件: `static/ui/memory_trace/overview.js` (`mountOverviewPage(host, ctx)`), 挂进 `workspace_memory_trace.js` 的 `PAGES` **首位**。
- 布局 (自上而下, 单列或两列响应式, 纯 `el()` + CSS):
  1. **顶部概览卡片区** (`cards`): 组成 / 覆盖率 (占比条) / 向量空间 / 聚类 —— 大数字 + 一行说明。
  2. **需关注项横幅**: `attention_count` 个需关注 (按严重度上色); 0 个时显示"未发现明显问题"。
  3. **问题发现清单** (`findings`, 按 severity 再按 category 排序): 每条 = 图标(严重度) + 标题 + 计数徽章 + 一行解释 + **[去查看详情]** 按钮 (下钻)。
  4. **智能化区**: **[生成 AI 体检报告]** 按钮 → 调 `ai_report`, 展示返回的自然语言诊断 (失败显示规则结论 + 原因, 复用 P28.5 的"告诉用户填哪个 API"范式); 矛盾候选 finding 内联 **[用 LLM 判定矛盾]** 按钮 → `contradictions`, 把候选对标注为 矛盾/重复/互补/无关。
- 状态 (owned, 驱动重渲): `data` (overview payload) / `phase` (loading/ready/no_session/no_character/error) / `aiReport` / `aiReporting` / `aiWarnings` / `contradictionVerdicts` / `contradicting`。
- 刷新触发: `session:change` + `active_workspace:change=='memory_trace'` + 手动 [刷新] (与其它子页一致)。**异步守卫**: 复用既有模式; 切走后到达的迟到响应不得写 detached host (沿用 P27/P28 teardown)。

### 3.3 复用资产

- `el()` DOM 工厂 / `api` 客户端 / `toast` / i18n 子树 / two-col subnav 外壳 (已 PAGES 驱动)。
- 后端两个聚合器全部复用; `session_operation` / `record_last_llm_wire` / `_llm_for_memory` (LLM 层)。

---

## 4. 概况内容设计 (卡片 + 发现目录)

> 这是本页的"内容图景"。卡片给**现状数字**, 发现给**问题与下钻**。所有阈值放 `config.py` 常量 (§10)。

### 4.0 按记忆功能环节诊断 (stage view, 核心组织视角)

> 用户诉求是"排查记忆系统**相关功能**"。除了按问题类型 (A–E) 列发现, 每条发现再带一个 `stage` 标签, 让使用者能按**记忆系统的工序**审视"哪道功能在出问题"。前端可按此分组/筛选。

| 工序 (stage) | 它负责 | 该工序的诊断发现 |
|---|---|---|
| **extract 抽取** | 对话 → 事实 | F5 抽取产出率 · H2 低质事实 (超短/无 entity/未打标) |
| **dedup 去重** | 合并重复 | A1 近重复对 · A2 重复簇 · A3 重复度 · **A4 冗余代价 (约 N 条可去重)** |
| **reflect 反思** | 事实 → 反思 | F1 事实吸收率 · F4 否决率 · C1/C2/C3 归因质量 · D3 停滞反思 |
| **promote 晋升** | 反思 → 人设 | F2 晋升率 · F3 pending 积压(最老年龄) · **G1 晋升保真度漂移** · D5 断裂晋升 |
| **correct 纠正** | 推翻旧说法 | B1 已记录矛盾/纠正 · **N1 未解决矛盾 (纠正未生效)** · B2/B3 矛盾候选/判定 |
| **embed 嵌入** | 文本 → 向量 | E1 覆盖率 · E2 过期 · E3 多空间 · E4 损坏 · E5 无向量闸门 |
| **structure 完整性** | 引用一致 | D1 游离事实 · D2 悬空引用 · H1 重要性错配 · H3 陈旧事实 |

> 同一条发现既属某"问题类型 (A–E/F–H/N)"也属某"工序 (stage)"; 两套标签同一份 findings, 前端可二选一分组。

### 4.1 概览卡片 (cards)

| 卡片 | 显示 | 来源 |
|---|---|---|
| 组成 | facts / reflections / persona / corrections / 对话(messages+memo) 计数 | lineage `meta.counts` |
| 向量覆盖率 | `embedded / total` (占比条) + missing/stale/corrupt 小字 | embedding health |
| 向量空间 | 主空间 `维度·条数`; 若 `other_space_count>0` 提示"另有 N 条属其它维度空间" | embedding health |
| 聚类 | `n_clusters` 簇 + `noise_count` 离群 + 最大簇 size; 标 `algo` (hdbscan/cosine_cc) | build_clusters |
| **流水线漏斗** | facts → reflections → persona 的转化率 (吸收率/晋升率) + pending 积压 + 否决率; 一眼看出卡在哪道工序 | lineage counts/status/edges + 派生 |
| **结论可信度** | 本次分析覆盖了多少数据 / 缺了什么 (无向量·无对话·无 events) | `meta.confidence` |

### 4.2 发现目录 (findings) —— 自动排查规则

每条 finding: `{stage, category, severity, title, count, detail, drill}` (stage 见 §4.0)。severity ∈ {info, warn, bad}; 只有 warn/bad 计入 `attention_count`。

**A. 冗余与重复 (redundancy / dedup)** —— 对应"多次反复无意义的记忆重复"
| id | 规则 | severity | 下钻 |
|---|---|---|---|
| A1 近重复对 | `build_duplicates(thr=DUP_THRESHOLD)` 的 `count`; ≥`DUP_WARN`→warn, ≥`DUP_BAD`→bad | info/warn/bad | 向量空间 ④重复 (带 threshold) |
| A2 重复簇 | clusters 中 `size≥CLUSTER_REPEAT_MIN` 且同 type 占比高的簇数 (= 反复写入同主题) | warn | 向量空间 自动聚类 |
| A3 重复度 | 有近重复伙伴的条目占 embedded 比例 ≥`DUP_RATIO_WARN` | warn | 向量空间 ④重复 |
| A4 冗余代价 | 估"约 N 条可去重 (占 X%)" = 参与近重复者 − 其代表数; 量化去重环节欠收敛 | info/warn | 向量空间 ④重复/聚类 |

**B. 矛盾记忆 (contradiction)** —— 三层判定, 见 §6 (相似只检索, NLI 才判定)
| id | 规则 | severity | 下钻 |
|---|---|---|---|
| B1 已记录矛盾/纠正 (L0) | `corrections` + `rejected` 反思 + 被 suppress/merged 的 persona 计数 —— **真矛盾事件** | warn (>0) | 记忆溯源 (聚焦 correction / 被否决反思) |
| N1 未解决矛盾 (correct) | `corrections` 中 `old_text` **仍存在**于当前 persona 的条数 (= 纠正未生效/旧说法仍在) | warn/bad | 记忆溯源 (聚焦该 persona 条目) |
| B2 待核对候选 (L1) | 同实体 + 同主题 (高相似带 ∪ 同簇 ∪ 共享tag) 的对数, 按极性/取值线索排序; 措辞固定**"待核对"** | info/warn | 向量空间 ⑤矩阵(子集)/⑥; 内联 [用 AI 判定矛盾] |
| B3 AI 判定矛盾 (L2, 按需) | LLM NLI 判 candidate→contradiction 的对数; **唯一**可断言"矛盾" | warn/bad | 内联展示裁决 + 理由 |

**C. 归因质量 (attribution)** —— 来自 bridges
| id | 规则 | severity | 下钻 |
|---|---|---|---|
| C1 反思漏源 | `missing_in_declared` 非空的反思数 (语义最近的事实未被声明为来源) | info/warn | 向量空间 ⑥语义源 vs 结构源 |
| C2 反思虚源 | `extra_in_declared` 非空 (声明的来源语义不近/未嵌入) | info/warn | 向量空间 ⑥ / 记忆溯源 |
| C3 归因脱节 | `agreement==0` 的反思 (声明来源与语义 top 完全不交) | warn | 向量空间 ⑥ (聚焦该反思) |

**D. 结构问题 (structure)** —— 派生自 lineage
| id | 规则 | severity | 下钻 |
|---|---|---|---|
| D1 游离事实 | absorbed=false 且未被任何反思引用的 fact 数 | info/warn | 记忆溯源 (聚焦) |
| D2 悬空引用 | source_fact_ids / source_id / merged_from_ids 指向不存在 id 的条数 | warn/bad | 记忆溯源 |
| D3 停滞反思 | status=pending 的反思数 | info/warn | 记忆溯源 |
| D4 被否决反思 | status=rejected 的反思数 | info | 记忆溯源 |
| D5 断裂晋升 | persona source=reflection 但 source_id 缺失/悬空 | warn | 记忆溯源 |

**E. 嵌入健康 (embedding)** —— 来自 health
| id | 规则 | severity | 下钻 |
|---|---|---|---|
| E1 覆盖率低 | `embedded_pct < COVERAGE_WARN` (且 total>0) | warn | 向量空间 ①体检 |
| E2 向量过期 | `stale>0` (改文后未重嵌) | warn | 向量空间 ①体检 |
| E3 多向量空间 | `other_space_count>0` (混了不同维度/换过模型) | warn | 向量空间 ①体检 |
| E4 向量损坏 | `corrupt>0` | bad | 向量空间 ①体检 |
| E5 无向量闸门 | `embedded==0` (total>0) | bad | 引导: Setup→Import 已嵌入角色 |

**F. 流水线产出与停滞 (throughput)** —— 诊断抽取/反思/晋升三道工序的"转化率"(与 D 的计数互补: D 看完整性, F 看比率)
| id | 规则 | severity | 下钻 |
|---|---|---|---|
| F1 事实吸收率低 | `被反思引用 fact / facts` < `ABSORB_RATE_WARN` (反思没在消化事实) | warn | 记忆溯源 |
| F2 晋升率低 | `晋升 persona / reflections` < `PROMOTE_RATE_WARN` (反思晋升不动) | warn | 记忆溯源 |
| F3 pending 积压 | pending 反思数 + 最老 pending 年龄 (天) ≥ 阈值 | info/warn | 记忆溯源 |
| F4 否决率高 | `rejected / reflections` ≥ `REJECT_RATE_WARN` (反思质量差/判定过严) | warn | 记忆溯源 |
| F5 抽取产出率异常 | `facts / 对话回合` 过低或过高 (需 `time_indexed.db`) | info/warn | 记忆溯源 (对话泳道) |

**G. 晋升保真度 (fidelity)** —— 用现成向量, 无 LLM
| id | 规则 | severity | 下钻 |
|---|---|---|---|
| G1 晋升语义漂移 | `cosine(persona, 其 source 反思) < FIDELITY_DRIFT` 的条数 (晋升时被改歪/夹带) | warn | 记忆溯源 (聚焦该 persona↔反思) |

**H. 留存质量 (retention quality / structure)**
| id | 规则 | severity | 下钻 |
|---|---|---|---|
| H1 重要性错配 | 高 `importance` 却游离未吸收的 fact 数; 或低 `importance` 却被反复重复的条数 | warn | 记忆溯源 / 向量空间 ④重复 |
| H2 低质事实 | 超短/空 / 无 entity / 未打标 的 fact 比例 ≥ 阈值 | info/warn | Setup → Facts |
| H3 陈旧僵尸事实 | `created_at` 很旧且从未被任何反思引用/晋升触及的 fact 数 | info | 记忆溯源 |

### 4.3 闸门与软处理

- `embedded==0` (E5): **向量类**发现 (A 重复 / A4 / B2 候选 / C 归因 / E1-E4 / **G1 漂移**) 整体降级为"需先有向量"占位 + 导入引导; **不依赖向量的发现仍照常输出**: B1/N1 矛盾/纠正、D 结构、F 流水线比率 (F5 需对话 db)、H 留存质量 —— 即 0 向量角色仍能排查抽取/反思/晋升/纠正/结构问题。
- `total==0` (空角色): 所有发现归零, 显示"该角色暂无记忆"。
- numpy 不可用 / 读盘软错: 进 `meta.warnings` + `meta.confidence.missing_pieces`, 仪表盘照常渲染可得部分, 并明示结论局限。

### 4.4 排序与"简洁"硬约束

- findings 先按 severity (bad>warn>info) 再按 category 固定序; 默认**只展开 warn/bad**, info 折叠进"更多 (N)"。
- 卡片区永远在最上, 一屏内给出"信息图景"; 不堆砌长列表 (用户硬要求: 清楚、简洁)。

### 4.5 与"健康分"的取舍 (决策)

不做 0–100 黑箱总分 (易误导、不可解释)。改用: 顶部 **"需关注项 N 个"** (= warn/bad finding 数) + 各 finding 自带严重度色。透明、可解释、可下钻。

---

## 5. 智能化层 (LLM, 按需, 永不阻塞)

> 复用 P27.3 / P28.5 的 `memory.llm` wire-stamp 范式 (`record_last_llm_wire(source="memory.llm")` + `update_last_llm_wire_reply`), 走 `session_operation`。**永不抛、失败优雅回退、并把失败原因告诉用户** (复用 2026-06-30 P28.5 修复: 没配模型时回 "请先在 Settings → Models → memory 填好 base_url 与 model。")。

### 5.1 AI 体检报告 (`POST /overview/ai_report`)

- 后端重算 `build_overview` (信任后端, 不信前端视图), 把 **cards + findings 的结构化摘要 + 少量样本文本** (每类问题取代表性 1–3 条, 截断) 拼成 prompt, 让 memory 模型产出: **一段简洁诊断 (现状一句话) + 按优先级的 3–6 条建议** (每条指明问题类别 + 建议动作, 如"合并 X 条近重复事实"/"复查 Y 反思的归因")。
- 返回 `{ method:"llm"|"rule", report:文本, warnings:[...] }`; LLM 不可用 → `method="rule"` + 用规则结论拼一段模板文本 + warning 说明原因 (填哪个 API)。
- prompt 只喂**摘要 + 截断样本**, 控制 token; 不把整库记忆灌进去。

### 5.2 矛盾候选 NLI 判定 (`POST /overview/contradictions`) —— §6 的 L2

- 输入: §6.2 L1 的"待核对候选对" (后端按 §6.2 规则重算, 或前端回传 id 对后端取文本)。
- **NLI 式 prompt** (非简单相似判断): 给每对 `(A, B)` 连同 entity、`created_at` 先后、L1 命中的属性/极性线索, 要模型判 `verdict ∈ {contradiction, duplicate, complementary, unrelated}` + **冲突属性** + 一句理由 + (若 contradiction) 哪条更晚/更可能是更新。返回 `{method, verdicts:[{a,b,verdict,attribute,reason,newer}], warnings}`。
- **唯一**能把候选升格为"矛盾"的路径; 失败/未配模型 → `method="rule"` + 候选**仍停留在"待核对"**, 界面不出现"矛盾"。
- 判定结果**只展示**, 不写回记忆 (修复人工)。

### 5.3 纪律

- 两个 LLM 端点都 `to_thread` 无关 (本身 async await ainvoke); 但其内部若先 `build_overview` 取候选, 该步重 → 也应 `to_thread`。
- wire stamp 走 `memory.llm`, 不污染 chat preview (沿用 P27.3 / p25 分区 smoke)。
- LLM 输出**不参与规则发现的计数**, 只作叠加注解 —— 保证无 LLM 时页面完整可用。

---

## 6. 矛盾记忆的判定逻辑 (核心, 重新设计)

> **动因 (2026-06-30, 用户反馈)**: "矛盾和相似的关系似乎不能这么简单地画等号, 再仔细考虑一下矛盾记忆的判定逻辑。" —— 完全正确。本节据此重做: 把**检索 (找出可能谈同一件事的对)** 与 **判定 (它们是否真冲突)** 彻底分开, 向量只承担前者。

### 6.1 为什么纯向量判不了矛盾 (机制层面, 必须讲清)

- embedding 是 **STS (语义文本相似) 向量**, 衡量"在不在谈同一件事", 对**否定/极性近乎不变**: "主人喜欢咖啡"与"主人**不**喜欢咖啡"在向量上**非常接近** (cos 很高), 因为二者共享几乎全部词与主题。
- 所以**高相似 = 同主题**; 而同主题里同时混着 **重复(一致) / 互补 / 矛盾** 三种关系, 向量**无法区分**。→ 高相似只能当**检索/召回**信号, **绝不能当判定**。
- "A 是否与 B 冲突"本质是 **NLI (自然语言推理: contradiction / entailment / neutral)** 任务, 需语义推理, 只有 **LLM** (或**已落盘的人工/系统判定事件**) 能可靠给出。

### 6.2 三层判定 (L0 已落盘 → L1 检索候选 → L2 NLI 判定)

**L0 — 已落盘真矛盾/纠正 (ground truth, 直接可断言, 不靠推断)**
磁盘上已记录的"冲突已发生并被处理"的事件:
- `persona_corrections.json` `old_text→new_text`: 人设被纠正 (旧说法被新说法推翻)。
- reflection `status=="rejected"` (+ `feedback`): 被否决的反思 (系统/用户判其不成立)。
- persona entry `suppress=true`, 或被 `merged_from_ids` 取代的旧条目: 被压制/合并的旧说法。
→ 列为 **"已记录的矛盾/纠正"**, 高置信。

**L1 — 候选检索 (recall; 找"可能谈同一事项"的对, 只缩小范围, 不判定)**
多信号召回 "值得核对的对" (组合):
- **同实体** (entity 相同): 矛盾通常发生在同一主体的同一属性上。
- **同主题** (检索信号): 向量高相似带 `cos∈[SUSPECT_LOW, DUP_THRESHOLD)` **∪** 同一聚类簇 (`build_clusters`) **∪** 共享 tag。← 向量在此**只做"召回同主题"**, 不做判定。
- **廉价极性/取值线索 (仅用于排序, 绝不单独判定)**: 一方含否定词 (不/没/非/无/别/from…to 反义) 另一方不含; 或二者就同一属性给出**不同数值/日期**。命中则**抬高该候选的核对优先级** (更可能是矛盾), 但**不据此断言** ("我不讨厌咖啡"含"不"却不矛盾)。
- 输出措辞固定为 **"涉及同一事项、待核对的记忆对"**, **严禁**写"矛盾"或"疑似矛盾"。

**L2 — 矛盾判定 (precision; NLI/LLM, 唯一能断言"矛盾"的层)**
- 把 L1 候选对 (+ 它们的 `created_at` 先后、entity、L1 命中的属性/极性线索) 喂记忆模型做 **NLI 式判定**: 对每对判 `关系 ∈ {矛盾 contradiction, 一致/重复 duplicate-or-entailment, 互补 complementary, 无关 unrelated}` + **冲突属性** + 一句理由 + (若矛盾) 哪条更可能是更新/更晚。
- **仅** L2 判为 contradiction 的对 → 标"矛盾"。失败/未跑 → 候选停留在"待核对", 不升格。
- 端点 `POST /api/memory/overview/contradictions` (§5.2)。

### 6.3 概况页如何呈现 (诚实措辞)

- 发现里固定为: **"已记录的矛盾/纠正 X 条"** (L0, 实证徽章) + **"待核对的同事项记忆对 Y 对"** (L1, 中性措辞)。
- 点 **[用 AI 判定矛盾]** 跑 L2 → 候选被标为 矛盾/重复/互补/无关 (带理由)。**只有此时**界面才出现"矛盾 N 对"。
- UI 常驻一行 hint: "向量相似只代表'在谈同一件事'; 是否真冲突需 AI 判定或人工核对。"

### 6.4 铁律

- 无 L0 证据且无 L2 判定时, **页面不出现"矛盾"字样**, 只有"待核对候选"。
- 向量相似度衡量话题接近, 非真值冲突 (§6.1) —— UI 明文点出, 防误读。
- 判定结果**只展示, 不写回记忆** (修复人工)。

> **派生原则**: 关系判定类分析必须区分 **检索信号 (similarity/召回)** 与 **判定信号 (NLI/推理)**, 不可用前者冒充后者。这是对 P27 §2.2 "诚实分层"在"矛盾"这一具体关系上的深化。

---

## 7. 跨子页下钻联动

- finding 的 `drill = {page, opts}`:
  - `page='lineage'`, `opts={focusNodeId}` → 复用 P27 既有 `mountLineagePage` 的 `pendingFocusId` (已支持, 见 `workspace_memory_trace.js`)。
  - `page='embedding'`, `opts={mode, threshold?, selectId?, cluster?}` → **P29 扩展** `embedding_space.js` mount 读 `ctx.opts`。**mode 取值必须用真实 id** (核对 `embedding_space.js`: `setMode` 接受 `'scatter' | 'duplicates' | 'matrix' | 'bridges'`; "最近邻"不是 mode 而是 `selectPoint(id)`; "自动聚类"不是 mode 而是散点上的 `toggleCluster` 开关):
    - `opts.mode='duplicates'` (+ `opts.threshold`) → A1/A3/A4 下钻;
    - `opts.mode='scatter'` + `opts.cluster=true` → A2 重复簇 (进散点并开聚类开关);
    - `opts.mode='bridges'` → C 归因系列;
    - `opts.mode='matrix'` → B2 候选子集;
    - `opts.mode='scatter'` + `opts.selectId=<id>` → 进页选中该点 (拉最近邻) — 用于按某条记忆下钻。
  - **扩展点**: mount 末尾依 `ctx.opts` 调既有 `setMode/toggleCluster/selectPoint`; 无 `ctx.opts` 时行为不变 (默认散点)。向后兼容, 不动既有 smoke 的默认路径。
- 子页切换沿用 `selectPage(id, opts)` 既有签名 (已透传 opts 到 mount 的 `ctx.opts`, 见 `workspace_memory_trace.js` L90-94)。
- 本页**只导航不改数据**; 跳过去后由目标子页自行拉取与渲染。

---

## 8. 分期 (用户选 "all" → 规则版 + LLM 层一次性交付)

> 用户决策: 一次把规则版与 LLM 层都做完 (含 AI 体检报告 + 矛盾 NLI 判定)。仍保留内部里程碑以便审阅, 但**同一批交付**。

- **P29.1 规则版概况 (闭环底座)**: 后端 `memory_overview.py` (`build_overview` 组合两聚合器 + §4 全部规则发现, 含矛盾 L0 真矛盾 + L1 待核对候选) + `GET /overview` (`to_thread`)。前端 `overview.js` 仪表盘 (卡片 + 需关注项 + 发现清单 + 下钻按钮), 挂为**默认子页**。扩展 `embedding_space.js` 消费 `ctx.opts` (下钻定位 mode/阈值/选中)。闸门/软处理齐全。→ 无 LLM 也完整可用。
- **P29.2 LLM 层 (同批交付)**:
  - `POST /overview/ai_report` + 前端 [生成 AI 体检报告] (失败回退规则结论 + 告知填哪个 API);
  - `POST /overview/contradictions` (§6 L2 NLI 判定) + B2 内联 [用 AI 判定矛盾] (失败 → 候选仍"待核对", 不出现"矛盾")。
- **smoke**: `p39` (后端: 规则发现全覆盖 + LLM 两端点无模型时 non-500/回退) + `p40` (前端: 仪表盘 + 下钻 `ctx.goTo` + 两个 LLM 按钮数据流)。

落地顺序: 后端纯函数 + 端点 (`to_thread` + LLM stamp) → 前端 → smoke 全绿 → 文档同步 (USER_MANUAL 两→三子页 + p26 D14 + CHANGELOG/config/PROGRESS/AGENT_NOTES)。

---

## 9. 测试与文档同步

### 9.1 smoke (前缀 p39 后端 / p40 前端)

- `p39_memory_overview_smoke.py` (后端, TestClient): 用手造 fixture (含: 一组近重复事实 / 一条 `old_text` 仍在 persona 的 persona_correction / 一条 source_fact_ids 悬空的反思 / 一个 absorbed=false 游离 fact / 一条由反思晋升但文本被改歪的 persona / 高 importance 游离 fact / 部分缺向量) 断言:
  - cards (含 `pipeline` 漏斗比率) 计数正确; coverage 正确; `meta.confidence.missing_pieces` 正确反映缺失;
  - A1/A4 重复与冗余代价命中; B1 纠正 >0 且 **N1 未解决矛盾** 命中 (old_text 仍在); C/D 系列命中; E 覆盖率/stale 命中;
  - **F 流水线比率** (吸收率/晋升率/否决率) 数值正确; **G1 晋升漂移** 命中被改歪的那条; **H1 重要性错配** 命中;
  - 每条 finding 带正确 `stage` 标签; `attention_count` = warn/bad 发现数;
  - 0 向量角色: 向量类 (A/A4/B2/C/E/G1) 降级、结构/流水线/纠正类 (B1/N1/D/F/H) 仍在; 空角色: 全零不崩;
  - `ai_report` / `contradictions` 端点在无模型时 **non-500** 且 `method="rule"` + warning (复用 P28.5 范式)。
- `p40_memory_overview_ui_smoke.mjs` (jsdom): 子页挂载不抛 / 卡片渲染 / 发现清单条目 = mock payload / 点 [去查看详情] 触发 `ctx.goTo` 带正确 page+opts / [生成 AI 体检报告] 调端点并渲染 / 0 向量空态引导 / teardown 不漏 listener。

### 9.2 既有 smoke / 文档同步 (新增第三子页 + 改默认子页 → 必同步)

- **⚠ 改默认子页会破坏 p33 (自审发现, 必改)**: `p33_memory_trace_ui_smoke.mjs` **直接** `mountMemoryTraceWorkspace(host)` 后就断言 lineage 的 5 泳道/节点, **依赖默认子页=记忆溯源**。把 `overview` 设为 `PAGES[0]` 后默认会落在概况页 → p33 全崩。**修法**: p33 mount 前补一行 `localStorage.setItem('testbench:memory_analysis:active_subpage','lineage')` (镜像 p38 已有的 seed 'embedding')。p38 已 seed 'embedding', **不受影响**。
- **D14 不锁 memory_trace (自审确认)**: `p26_docs_endpoint_smoke.py` D14 只校验 setup/evaluation/diagnostics/settings 的 `PAGES` 子页数, **不含 memory_trace** → 加第三子页**不会**直接踩 D14。但仍按 `docs-code-reality` 纪律更新手册 (下条)。
- `testbench_USER_MANUAL.md`: §2 workspace 表与目录里 "记忆系统分析 … 两个子页" → **三个子页** (系统概况 / 记忆溯源 / 向量空间); §2.5 引文"目前有两个子页"→三; 新增 **§2.5.0 系统概况** 小节 (放在 2.5.1 之前, 因它是入口)。改中文文档**只用 UTF-8 编辑工具** (StrReplace/Write), 禁 PowerShell Set-Content (L32 编码纪律)。
- `CHANGELOG.md` 新版本条目 (新子页 = MINOR, 如 v1.9.0); `config.py` `TESTBENCH_VERSION` + `TESTBENCH_PHASE`; `PROGRESS.md` / `AGENT_NOTES.md` 收尾条; `PLAN.md` 加 `p29_memory_overview` 索引。
- **新 smoke 的 seed**: `p40_*` mount 前 seed `localStorage` 为 `'overview'`; fixture 角色须能跑出各类 finding (见 §9.1)。

---

## 10. 命名 / i18n / 配置

- 子页 id: `overview`; navKey: `memory_trace.nav.overview` = **「系统概况」** (可改)。置于 PAGES 首位 → 新用户默认进概况页。
- i18n 新增 `memory_trace.overview.*` 子树 (卡片标题 / 各 finding title&detail 模板 (`_fmt` 函数型) / 严重度词 / 空态 / AI 报告按钮与回退提示 / 矛盾分层措辞)。
- `config.py` 常量 (集中可调):
  - `OVERVIEW_DUP_THRESHOLD=0.95` (近重复, 对齐 embedding `DUP_THRESHOLD_DEFAULT`);
  - `OVERVIEW_SUSPECT_LOW=0.80` (高相似待审下界);
  - `OVERVIEW_DUP_WARN / OVERVIEW_DUP_BAD` (近重复对计数阈);
  - `OVERVIEW_DUP_RATIO_WARN=0.15`;
  - `OVERVIEW_CLUSTER_REPEAT_MIN=4` (重复簇最小 size);
  - `OVERVIEW_COVERAGE_WARN=0.5`;
  - `OVERVIEW_ABSORB_RATE_WARN=0.3` / `OVERVIEW_PROMOTE_RATE_WARN=0.1` / `OVERVIEW_REJECT_RATE_WARN=0.5` (流水线漏斗比率阈, F1/F2/F4);
  - `OVERVIEW_PENDING_AGE_WARN_DAYS=14` (F3 pending 积压年龄);
  - `OVERVIEW_FIDELITY_DRIFT=0.6` (G1 晋升保真度: persona 与来源反思 cosine 低于此判漂移);
  - `OVERVIEW_MIN_FACT_CHARS=4` (H2 低质事实下限) / `OVERVIEW_UNTAGGED_RATIO_WARN=0.6`;
  - `OVERVIEW_STALE_FACT_DAYS=30` (H3 陈旧僵尸事实);
  - `OVERVIEW_NEGATION_CUES=[...]` (L1 极性排序用的否定词表, 仅排序不判定);
  - `OVERVIEW_AI_SAMPLES_PER_CAT=3` / `OVERVIEW_AI_PREVIEW=80` (喂 LLM 的样本上限/截断)。

---

## A. 设计决策记录 + 开工前设计审查 (Design Review Gate)

### A.1 关键决策

| 决策点 | 结论 |
|---|---|
| 定位 | 第三子页, 但**逻辑入口** → 置 PAGES 首位为默认子页 (用户语义) |
| 后端 | **复用并组合** lineage + embedding_space 两个既有 chokepoint, 新增 `memory_overview.py` 只做"组合 + 派生发现", 不重读盘/不重数学 |
| 矛盾检测 (用户反馈后重做, §6) | **检索 ≠ 判定**: 向量高相似只做 L1 检索 (措辞"待核对"); 真矛盾来自 L0 已落盘事件 (纠正/否决/压制); 唯有 L2 NLI/LLM 判定才标"矛盾"。**相似 ≠ 矛盾** |
| 重复检测 | 复用 build_duplicates + build_clusters; 重复簇 = 反复无意义重复 |
| 健康分 (用户确认) | **不做黑箱总分**; 用透明"需关注项计数 + 分维严重度" |
| LLM (用户选 all) | AI 报告 + 矛盾 NLI 判定**同批交付**; 永不阻塞, 失败回退 + 告知填哪个 API (P28.5 范式) |
| 分期 (用户选 all) | P29.1 规则版 + P29.2 LLM 层一次性交付 |
| 默认子页 (用户确认) | 「系统概况」置 PAGES 首位为默认; 老用户保留上次 |
| 诊断视角 (用户追加 "补充") | 发现除按问题类型, 再带**功能环节 stage** 标签 (§4.0), 可按 抽取/去重/反思/晋升/纠正/嵌入/结构 排查; 新增 F 流水线产出比率 / G 晋升保真度漂移 / H 留存质量 / N1 未解决矛盾 / A4 冗余代价 / 结论可信度元诊断 —— 全部基于现成数据+向量, 无需新页 |
| 修复动作 | **不做** (只发现+导航, 修复走 Setup 人工) — 守只读边界 |
| 性能 | `/overview` 聚合多项向量计算 → **强制 `asyncio.to_thread`** (P28.5 事件循环阻塞教训) |

### A.2 设计审查矫正 (回写正文)

| # | 维度 | 风险 | 处置 |
|---|---|---|---|
| R1 | 矛盾判定 (用户反馈: 相似≠矛盾) | 把"向量相似"误标成"矛盾", 误导相关人员 | §6 重做: 检索(L1)/判定(L2)分离 + 向量仅 STS 不含极性 (§6.1) + 真矛盾来自 L0 已落盘事件; 无判定不出现"矛盾" |
| R2 | 事件循环阻塞 (P28.5 教训) | `/overview` 同步重算阻塞全界面 | §3.1 强制 `to_thread` |
| R3 | LLM 必 stamp (L43 / p25) | AI 报告/裁决漏 `record_last_llm_wire` 致 p25 stamp smoke 红 | §5.3 强制 `source="memory.llm"` + KNOWN_SOURCES |
| R4 | 单一 chokepoint (L36 §7.25) | 前端从两个 payload 自拼发现 → 漂移 | §3.1 发现只在 `build_overview` 派生, 前端 verbatim; smoke 锁 shape |
| R5 | 文档-代码-smoke 一致 (D14) | 新增第三子页破坏 USER_MANUAL "两个子页" + D14 | §9.2 必同步 (grep 后改) |
| R6 | 只读边界 (L: 不写回) | "自动排查"易滑向"自动修复/合并" | §1.3 明文 OOS, 仅发现+导航 |
| R7 | 简洁硬要求 (用户) | 发现清单堆成长列表 | §4.4 卡片在上 + 默认只展开 warn/bad + info 折叠 |
| R8 | 闸门 (P28 §2.4) | 0 向量角色页面空白无引导 | §4.3 向量类降级引导, 结构类仍跑 |
| R9 | 下钻契约 | embedding 子页当前不读 ctx.opts, 下钻无法定位 mode | §7 扩展 mount 读 ctx.opts (向后兼容) |
| R10 | 默认子页变更 | 改 PAGES 首位影响老用户 LS 持久化 | 仅影响新用户默认; 老用户保留上次 (无破坏); 文档说明 |

### A.3 设计初衷锚定 (防漂移)

| 用户原话 | 是否守住 |
|---|---|
| "快速分析记忆系统运行情况" | ✅ 概览卡片一屏图景 |
| "找到当前存在的问题与不足" | ✅ 发现清单 + 需关注项 |
| "自动排查矛盾记忆" | ✅ §6 三层 (L0 已落盘真矛盾 / L1 待核对候选 / L2 NLI 判定); 不把相似当矛盾 |
| "多次反复无意义的记忆重复" | ✅ A1/A2/A3 (近重复对/重复簇/重复度) |
| "其他可能存在的潜在问题" | ✅ C 归因 / D 结构 / E 嵌入健康 |
| "再去之前写好的两个子页查看详情" | ✅ 每发现一键下钻 (§7) |
| "最大化的智能化自动分析" | ✅ 规则发现 + 可选 LLM 体检报告/矛盾裁决 |
| "清楚、简洁的信息图景" | ✅ §4.4 简洁约束 + 透明指示 (非黑箱分) |
| (用户没要) 自动修复记忆 | ✅ 明文 OOS — 不漂移 |
| (用户没要) 历史趋势 | ✅ 明文 OOS (无持久化) |

### A.4 派生元教训候选

- **"聚合的聚合"应复用下游 chokepoint, 不重复读盘/重算**: 概况页是 lineage+embedding 两个 chokepoint 的再聚合, 必须站在它们肩上 (调用其纯函数), 否则三处对"什么是一条 fact / 怎么算相似"会漂移。(本阶段 §3.1 派生。)

### A.6 第二轮自审 — 实现前对账 (2026-06-30, 已把假设逐条对真实代码核验)

| # | 自审项 | 核验结论 | 处置 |
|---|---|---|---|
| S1 | 复用 `_require_session`/`_require_character`/`session_operation`/`SessionConflictError`/`_wrap_conflict` | ✅ 与 `memory_router` 现状一致 (cluster_labels 即模板) | 直接照搬 |
| S2 | 重读端点 `to_thread` 是否既有范式 | ✅ space/neighbors/bridges/dup/matrix/clusters 全是 | `/overview` 同样 `to_thread` (§3.1) |
| S3 | **改默认子页破坏 p33** | ❗ p33 直接 mount workspace 并断言 lineage, 依赖默认=lineage | §9.2: p33 补 seed `localStorage='lineage'`; p38 已 seed embedding 不受影响 |
| S4 | D14 是否锁 memory_trace 子页数 | ✅ 否 (只锁 setup/eval/diag/settings) | 不踩 D14; 仍更新手册 (§9.2) |
| S5 | 下钻 mode id 是否真实 | ❗ 真实是 `scatter/duplicates/matrix/bridges`; 最近邻=`selectPoint`、聚类=`toggleCluster` 开关 | §7 已按真实 id 重写, 加 `cluster`/`selectId` opt |
| S6 | `_build_space` 被 dup/clusters/bridges 各跑一次 | ⚠ overview 调它们 = 3-4 次解码 | §3.1.1: MVP 接受 + 可选 `space=` 注入 |
| S7 | 比率类除零 / 晋升率>1 / F5 回合数取真值 | ⚠ 易错 | §3.1.1: 除零守卫 + 晋升率按不同反思去重 + F5 用 conversation_corpus 真值 |
| S8 | G1 保真度需两端都有向量 | ⚠ 部分覆盖 | §3.1.1: 缺向量计"无法核验", 不误报漂移 |
| S9 | LLM 端点锁内重算阻塞 | ⚠ | §3.1.1: 锁内 `to_thread` 重算再 `ainvoke` (镜像 cluster_labels) |
| S10 | 0 向量角色下游函数是否安全 | ✅ dup/clusters/bridges 都 `if matrix is None` 返回空 | 闸门 §4.3 成立 |
| S11 | 矛盾 L1 措辞不得出现"矛盾"字样 | ✅ §6 铁律 | 实现时 i18n key 命名与文案双重把关 |

**门禁结论**: 以上 S3/S5/S7/S8/S9 已回写正文 (§3.1.1 / §7 / §9.2)。无阻断性问题, **可开工**。

---

## B. 决策已确认 (2026-06-30)

| 决策 | 结论 |
|---|---|
| 默认子页 + 命名 | ✅ 「系统概况」置 PAGES 首位为默认子页; 老用户保留上次 |
| 分期 | ✅ 选 "all": P29.1 规则版 + P29.2 LLM 层 (AI 报告 + 矛盾 NLI 判定) **一次性交付** |
| 健康分 | ✅ 不做黑箱总分, 用透明"需关注项 + 分维严重度" |
| **矛盾判定** | ⚠ 用户反馈"相似≠矛盾, 重新考虑判定逻辑" → 已重做 §6 (检索 L1 / 判定 L2 分离; 真矛盾来自 L0 已落盘事件; NLI 才断言矛盾)。**待用户认可此新逻辑后开工。** |

> **开工门禁**: 用户认可 §6 重做的矛盾判定逻辑后, 即按 §8 一次性实现 P29.1+P29.2。

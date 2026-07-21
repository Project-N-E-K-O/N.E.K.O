# P30 记忆分析导出 (Memory Analysis Export) + 反推可行性裁决 — 蓝图

> **Single Source of Truth**. 本文件是 P30 阶段的**唯一权威规格**. `PLAN.md` 条目仅作索引; `PROGRESS.md` / `AGENT_NOTES.md` / `CHANGELOG.md` 描述均以本文件为准.
>
> **定位**: 给「记忆系统分析」(顶层 workspace `memory_trace`) 的**系统概况**入口子页 (P29 `overview`) 加一个**一键脱敏记忆导出**能力 —— 把当前角色的**脱敏原始记忆** + **我们已算出的二级分析结论**打成一个可分享的 ZIP, 供记忆系统相关人员离线分析/传递. 同时交付一份**"由记忆分析问题反推主程序记忆代码缺陷"的可行性裁决文档** (本期只裁决, 不落实现).
>
> **阶段号 / smoke 前缀**: 沿用 "阶段号与 smoke 计数器有意分叉" 约定. 本阶段取**阶段号 P30**; 新增 smoke 接续单调计数器: 后端 `p41_`、前端 jsdom `p42_` (现有最高 `p40`).
>
> **动因 (2026-07-15, 用户下达)**: 引文 —— "相关人员对于记忆分析系统, 希望加入一个快捷的记忆导出功能. 这个功能需要一键去除隐私敏感数据, 来导出干净的、可用于共享分析的记忆数据文件, 其中希望包含我们现有得到的记忆分析结论和原始数据. 数据应该能被方便解码分析, 也方便共享传递." + "是否能通过我们记忆分析出的问题来进一步……反推 (主程序的) 记忆系统的代码中可能存在哪些问题……我对此表示一定的怀疑, 我要求你先调查可行性." 用户后续决策: 隐私走**可配置分级** (默认标准档假名化); 格式走 **ZIP, 分 `raw_data/` + `analysis/` 两文件夹**; 反推**只出可行性文档**.

---

## 1. 目标与边界

### 1.1 核心目标

在 P29 系统概况子页放一个 **[导出记忆分析]** 按钮, 记忆系统相关人员一键得到一个**可分享 ZIP**:

1. `raw_data/` —— **脱敏后**的原始记忆 (对话语料 / recent / facts / reflections / persona), 机器可读 JSON.
2. `analysis/` —— 我们**已经算出**的二级结论 (P29 概况 overview / P27 溯源 lineage / P28 向量空间 health+duplicates+clusters+bridges), 机器可读 JSON + 一份人读 `summary.md`.
3. `manifest.json` + `README.md` —— 自描述元信息 (角色/时间/脱敏档位/各文件说明/已知局限) + 中文说明.

一键即导出 (默认 `standard` 脱敏档), 可选小模态改档位与是否含对话语料. 全程**离线、零成本、不触 LLM、不改主程序、不写回记忆**.

### 1.2 范围内 (In-Scope)

- **单一聚合导出层** `pipeline/memory_export.py`: 纯只读、session-agnostic、无 LLM. **复用**已建 chokepoint —— `build_overview` (P29) / `build_lineage_snapshot` (P27) / `embedding_space` 的 `build_space_view`/`build_duplicates`/`build_clusters`/`build_bridges` (P28) / `load_conversation_corpus` (P27.0) —— 再读四文件 (`recent`/`facts`/`reflections`/`persona`), 组装 bundle, **最后一步统一脱敏**, 用标准库 `zipfile` 打成 ZIP bytes.
- **脱敏 chokepoint 扩展** `pipeline/redact.py`: 在既有 `redact_secrets` 之上新增 `redact_export_bundle(bundle, *, tier)` —— 身份一致假名化 (全层同一张 map) + strict 整层撤除原始转录. 三档 `minimal` / `standard` / `strict`.
- **单一导出端点** `GET /api/memory/export`: 返回 `application/zip` attachment. 404/409 语义与 `/lineage`·`/overview` 完全一致. CPU-bound (聚合多项向量计算) → `asyncio.to_thread`. 短锁读 (见 §3.2).
- **前端**: P29 概况子页工具栏 [导出记忆分析] 按钮 (一键默认 standard) + 小模态 (档位单选 + 含对话语料开关) + **固定显示的黄色高亮"记忆脱敏说明"块** (R-UIExplain, 务必阅读, 不折叠). `fetch`+`blob` 下载. 文案全部入 i18n.
- **反推可行性文档** `docs/MEMORY_CODE_INFERENCE_FEASIBILITY.md` (开发者向): 裁决 + 可/不可反推分类表 + 证据面缺口 + 未来受控设计草图 + PLAN 未来 phase 占位. **不落任何反推实现**.

### 1.3 范围外 (Out-of-Scope, 明文约束)

- **不改主程序**: 只消费现有持久化数据 + 已建聚合器. 不动 `memory/*.py`.
- **不写回记忆 / 不落盘到 sandbox**: 导出是**纯读 + 内存打包**, ZIP bytes 直接经 HTTP 发回浏览器, 服务端不留临时文件.
- **不触 LLM**: 导出只用**非 LLM 聚合器** (`build_overview` 的规则层是同步纯函数; **不**调 `build_ai_report` / `judge_contradictions`). 保证确定性、离线、零成本、不依赖 memory 模型配置.
- **不做真值内容清洗**: 不对自由正文做正则 PII 涂抹 (与 `redact.py` 冻结原则冲突, 见 §5 与 §A R3). 脱敏只做**确定性身份假名化** + **strict 整层撤除原始转录**.
- **不实现反推**: 本期只裁决可行性并写文档. 不加"由 finding 生成代码缺陷报告"的任何端点/按钮.
- **不引入新依赖**: 只用标准库 `zipfile` / `json` / `io`. 不加 pandas / 第三方打包库.
- **不做跨 session / 多角色 / 历史趋势导出**: 只导当前 active session 当前角色的当前快照 (与 P29 一致).

### 1.4 现有数据能支撑到什么程度 (诚实声明)

| 导出内容 | 来源 | 支撑度 |
|---|---|---|
| 脱敏原始记忆 (recent/facts/reflections/persona) | 直接读四文件 | ✅ 任何角色可用 |
| 脱敏对话语料 (turns) | `load_conversation_corpus` (time_indexed.db + recent.json) | ✅ 无 db → 只出 recent 部分, 记 warning |
| 系统概况结论 (cards/findings) | `build_overview` (规则层, 无 LLM) | ✅ 0 向量角色仍出结构/流水线类结论 |
| 溯源图 (nodes/edges) | `build_lineage_snapshot` | ✅ 不需向量 |
| 向量分析 (health/duplicates/clusters/bridges) | `embedding_space` | ⚠ 无向量 → 各视图返回空 + health 标覆盖率 0%, 记 warning (graceful degradation, 非错误) |
| AI 体检报告 / 矛盾 NLI 裁决 | 需 LLM | ❌ **明文不含** (导出零成本离线, 见 §1.3) |
| 反推代码缺陷结论 | —— | ❌ 本期不实现 (只裁决, §7) |

---

## 2. 打包内容清单 (bundle 装配)

> 权威装配顺序: **① 收集 → ② 组装 bundle (dict) → ③ 最后一步统一脱敏 (§5) → ④ zipfile 写盘为 bytes**. 脱敏放在最后, 覆盖所有文本字段 (含 analysis 里嵌的 finding detail / persona 摘要), 且用同一张假名 map 保证跨层一致 (§5 R-Consistency).

### 2.1 raw_data/ (脱敏原始记忆)

| 文件 | 内容 | 采集 |
|---|---|---|
| `raw_data/recent.json` | recent.json 原文 (LangChain 消息 list) | 读盘 |
| `raw_data/facts.json` | facts.json 原文 (fact dict list) | 读盘 |
| `raw_data/reflections.json` | reflections.json 原文 | 读盘 |
| `raw_data/persona.json` | persona.json 原文 (dict) | 读盘 |
| `raw_data/conversation_corpus.json` | `load_conversation_corpus(character)` 的 `{turns, sources, counts, warnings}` | 聚合器 |

> 四文件读盘复用 `memory_router._read_json` 的容错语义 (缺失 → 空值). **不含** `time_indexed.db` 原始二进制 (只导其解出的 turns 文本). 对话语料受 `include_corpus` 开关控制 (默认含); strict 档整层撤除其正文 (§5).

### 2.2 analysis/ (二级结论)

| 文件 | 内容 | 聚合器 |
|---|---|---|
| `analysis/overview.json` | `build_overview(character)` = `{cards, findings, attention_count, meta}` | P29 |
| `analysis/lineage.json` | `build_lineage_snapshot(character)` = `{nodes, edges, meta}` | P27 |
| `analysis/embedding_health.json` | `build_space_view` 的 `meta` (health/dims/counts; **不含** 2D 坐标 points, 体积大且无分析价值) | P28 |
| `analysis/embedding_duplicates.json` | `build_duplicates(character, threshold=默认)` | P28 |
| `analysis/embedding_clusters.json` | `build_clusters(character)` | P28 |
| `analysis/embedding_bridges.json` | `build_bridges(character)` | P28 |
| `analysis/summary.md` | 人读概述: 角色/规模/需关注项计数/各 finding 标题与计数 (从 overview 派生, 不重算) | 派生 |

> 每个向量视图 graceful degradation: 0 向量 → 聚合器已返回空结构 + warning, 原样收进 bundle, 并汇总进 `manifest.json.warnings`.

### 2.3 顶层自描述

| 文件 | 内容 |
|---|---|
| `manifest.json` | `{kind, schema_version, generated_at, character, redaction:{tier, identity_map_size, corpus_included, strict_transcript_omitted}, files:[{path, description}], counts, warnings, limitations}`. **绝不含**假名→真名的反查表 (§5 铁律). |
| `README.md` | 中文说明: 这是什么 / 文件夹结构 (英文名 → 中文含义映射) / 脱敏档位对照与诚实限制 (镜像 §6 说明) / 如何解码分析 / 已知局限. |

- `kind = "testbench_memory_export"`, `schema_version = 1`.
- ZIP 内**文件夹用英文名** (`raw_data/` / `analysis/`), 中文含义在 `README.md` + `manifest.json.files[].description` 里映射 (用户确认, §A).

---

## 3. 架构

### 3.1 后端聚合层 (纯函数, 无 LLM, session-agnostic)

新增 `tests/testbench/pipeline/memory_export.py`:

- `build_export_bundle(character, *, include_corpus=True) -> dict` —— 收集 §2.1/§2.2 全部内容为一个未脱敏 bundle dict (含 `raw_data` / `analysis` / `manifest` 骨架 + 采集期 warnings). 纯读, 同步, CPU-bound.
- `pack_export_zip(bundle, *, tier, include_corpus) -> tuple[bytes, dict]` —— **先** `redact.redact_export_bundle(bundle, tier=tier)` (§5, 最后一步统一脱敏, 返回脱敏 bundle + 假名 map size + strict 撤除标记), **再**用 `zipfile.ZipFile(io.BytesIO(), "w", ZIP_DEFLATED)` 把各 JSON (`json.dumps(..., ensure_ascii=False, indent=2)`) 与 `README.md`/`summary.md`/`manifest.json` 写入对应文件夹, 返回 `(zip_bytes, manifest)`.
- `export_memory_analysis(character, *, tier, include_corpus) -> tuple[bytes, str]` —— 顶层入口: `build_export_bundle` → `pack_export_zip`, 返回 `(zip_bytes, filename)`.
- `memory_export_filename(character, tier, *, display_name=None, now=None) -> str` —— **友好名** `NEKO testbench_记忆导出_<角色标识>_<YYYY-MM-DD>.zip` (2026-07-15 用户要求, 弃用早期 `tbmemory_..._<ts>` 编号)。角色标识**按档位分层** (L65 跨层一致): `minimal` 用真实展示名 (该档本就不假名化); `standard`/`strict` 用中性占位 `角色`, **文件名本身不泄漏**已在包内假名化的身份。保留 Unicode (中文) 字母, 仅去文件系统非法字符。
- 常量: `MEMORY_EXPORT_KIND` / `MEMORY_EXPORT_SCHEMA_VERSION` / `MEMORY_EXPORT_TIERS = ("minimal","standard","strict")` / `MEMORY_EXPORT_DEFAULT_TIER = "standard"`.

### 3.2 端点 (追加到 `routers/memory_router.py`)

```http
GET /api/memory/export?redaction=<tier>&include_corpus=<bool>
```

- 声明在动态 `/{kind}` 路由**之前** (与 `/lineage`·`/overview` 同, 否则被当成 memory kind).
- 校验 `redaction ∈ MEMORY_EXPORT_TIERS`, 非法 → 400 `UnknownRedactionTier`.
- `_require_session()` (404 `NoActiveSession`) + `_require_character(session)` (409 `NoCharacterSelected`) —— 完全复用现有 helper.
- **纯读, 不取会话锁** (与 `/overview`·`/lineage` 一致——它们读同一批聚合器且不加锁): 直接 `_require_session` + `_require_character` + `await asyncio.to_thread(export_memory_analysis, ...)`. **不走 `session_operation`**, 因为它会在退出时触发 autosave (一次会话槽写入) —— 那与 "read only, no writes" (§1.3) 冲突, 也会无谓阻塞并发 chat. identity 名从 `session.persona` 读取.
- 返回 `Response(content=zip_bytes, media_type="application/zip", headers={"Content-Disposition": _content_disposition(filename)})`; 因友好名含中文, `_content_disposition` 同时给 ASCII `filename=` 兜底 + RFC 5987 `filename*=UTF-8''<pct-encoded>` (现代浏览器优先取后者)。

### 3.3 前端 (P29 概况子页, 不新增子页)

- 在 `static/ui/memory_trace/overview.js` 的 `renderToolbar()` 的 `mov-toolbar-right` 里, 于 [刷新] 前加 **[导出记忆分析]** 按钮 (仅 `phase==='ready'` 时可用).
- 点按钮 → 打开小模态 (纯 `el()` DOM, 无第三方): 脱敏档位单选 (minimal/standard/strict, 默认 standard) + 含对话语料开关 (默认开) + **固定显示的黄色高亮"记忆脱敏说明"块** (务必阅读, **不折叠**, R-UIExplain, §6) + [导出] / [取消]. (初版用 `<details>` 折叠, 用户指正"务必阅读的内容藏在折叠里不合理" → 改为固定黄色高亮 callout.)
- [导出] (直接 `fetch`, **不走** `api.js` 因它会 JSON.parse; 仿 `session_export_modal.js`): 顺序**很关键** ——
  1. **先** `window.showSaveFilePicker` 弹"另存为"让用户选保存位置 (2026-07-15 用户要求)。**必须在任何 `await fetch` 之前**取句柄: 该 API 要求 transient user activation, 若先 await 网络请求会耗尽激活态 → 抛 `SecurityError` 被吞 → 静默回退成普通下载、弹不出窗 (踩坑 L66)。`suggestedName` **前端算** (`NEKO testbench_记忆导出_<角色标识>_<本地日期>.zip`; 角色名由 overview 传入, 因 `store.session` 不缓存 persona 名); 用户取消 (`AbortError`) → 保留模态不报错不 fetch。
  2. **后** `fetch('/api/memory/export?redaction=..&include_corpus=..')` → `resp.blob()` → 有句柄则 `createWritable().write().close()` 写入; 无句柄 (Firefox/Safari/非安全上下文/API 缺失) 回退 `<a download>`, 文件名从 `Content-Disposition` 的 `filename*=UTF-8''` 优先解析 (中文名) 回退 `filename=`。
  3. `!resp.ok` → 按 content-type 读 JSON 错误并 `errEl`/`toast.err`。
- 一键快捷: 也可让按钮**直接一键导出** (默认 standard, 不弹模态), 模态改由按钮旁的小齿轮/下拉进入 —— 首版取 **点按钮弹模态但默认档已选 standard, 一次 [导出] 即走**, 兼顾"一键"与"可配置" (§A R-UX).
- 文案入 i18n `memory_trace.overview.export.*`.

---

## 4. ZIP 结构 (最终形态)

```text
NEKO testbench_记忆导出_<角色标识>_<YYYY-MM-DD>.zip   # minimal→真实名; standard/strict→占位「角色」
├── README.md                         # 中文说明 + 文件夹映射 + 脱敏说明 + 局限
├── manifest.json                     # 自描述元信息 (无假名反查表)
├── raw_data/                         # 脱敏原始记忆
│   ├── recent.json
│   ├── facts.json
│   ├── reflections.json
│   ├── persona.json
│   └── conversation_corpus.json      # include_corpus=false 时省略; strict 档正文占位
└── analysis/                         # 二级分析结论
    ├── overview.json
    ├── lineage.json
    ├── embedding_health.json
    ├── embedding_duplicates.json
    ├── embedding_clusters.json
    ├── embedding_bridges.json
    └── summary.md
```

---

## 5. 脱敏 (单一 chokepoint, 整包单次统一变换)

> 修正见 §A R3 / R-Consistency. **放弃**对自由正文做 PII 正则涂抹 —— 与 `redact.py` 冻结原则冲突 (模块 docstring 明确拒绝"看起来像 key 的正则启发式"因误报, 且 §3A G1「never filter user content」). 改为**确定性身份假名化** + **strict 整层撤除原始转录**.

新增 `redact.redact_export_bundle(bundle, *, tier) -> tuple[dict, dict]` (返回脱敏 bundle + `{identity_map_size, strict_transcript_omitted, warnings}`), 作为导出的**唯一脱敏出口**:

### 5.1 跨层一致性 (R-Consistency, 用户强约束)

脱敏是对**整个 bundle 的单次统一变换**: 同一 token 在 `raw_data/*` 与 `analysis/*` (含 finding detail / persona 摘要 / summary.md) 中映射结果**完全一致**, 绝不出现"对话里是 A、事实/反思里是 B". 实现: 先构建一张 name→占位符 map, 再对 bundle 内**所有字符串叶子**统一替换 (深走 dict/list). strict 的正文最小化也是**整层统一撤除**, 不做只动某一层的局部涂抹.

### 5.2 三档

- **minimal (A)**: 仅去凭据. `redact_secrets(bundle, extra_keys={"cookie","bearer"})` (`cookie` 不在现有 `SENSITIVE_KEYS`). 保留真实用户名与全部原文. identity_map 为空.
- **standard (B, 默认)**: minimal + **身份一致假名化**. 从 `persona.json` / `session.persona` 取 `master_name` / `character_name` (及显式身份字段) 建 map → 中性占位 (如 `「主人」` / `「角色」`). 守则:
  - **不得**用真实默认名 (如 `NEKO`) 当占位, 以免与真实记忆内容碰撞;
  - 名长 `< 2` 或与常用词/高频子串冲突 → 跳过该名并入 `warnings` (避免把普通词全局涂花);
  - 用**同一张 map 对整个 bundle** (含 analysis) 替换. 除身份外保留全部正文 —— 对话与 facts/reflections 因用同一 map, 天然无 A/B 分歧.
- **strict (C)**: standard + **整层撤除最原始转录**. 把 `raw_data/conversation_corpus.json` 的每条 turn 的 `content` 与 `raw_data/recent.json` 的每条 message content 统一替换为结构占位 `"<omitted len=N role=..>"` (保留条数/角色/时间戳等结构). 这是**整层撤除**而非逐 token 涂抹, 不制造跨层分歧. facts/reflections/persona 的**派生正文默认保留** (它们是抽象结论, 是共享分析的价值本体, 且已受同一身份 map 假名化). 诚实声明见 §6.

### 5.3 铁律

- 脱敏为 bundle 组装**后的最后一步** (§2 装配序 ③), 覆盖 analysis 里嵌的所有文本.
- `manifest.json` 只记 `identity_map_size` (数量), **绝不含**假名→真名反查表.
- `redact_secrets` 现有行为不变 (向后兼容, p22/p24 相关 smoke 不受影响).

---

## 6. UI 脱敏说明 (R-UIExplain, 用户强要求)

导出模态内置一块**固定显示的黄色高亮 "记忆脱敏说明"** (务必阅读, **不折叠**), 用测试员能懂的语言讲清 (文案入 i18n, 同一文案镜像到 ZIP 内 `README.md` 与 tester 指南 `memory_export_guide.md`, **单一文案源**勿三处漂移):

1. **三档行为对照**: minimal 只去凭据; standard 一致假名化身份 + 保留正文; strict 额外整层撤除原始逐轮转录、保留抽象记忆.
2. **跨层一致性保证**: 身份标识在对话与事实/反思中用同一映射, **绝不会**出现"对话是 A、记忆是 B".
3. **诚实限制**: 只有**身份标识**被一致假名化; **自由正文里的个人披露不做自动清洗** (不可靠); strict 仅整层撤除原始逐轮转录, 抽象记忆正文仍在; **对外分享前请自行复核**.
4. **凭据始终移除** (所有档位); embedding 指纹 (若含) 对应假名化前文本.

---

## 7. 反推可行性 (只裁决, 不实现)

新增 `docs/MEMORY_CODE_INFERENCE_FEASIBILITY.md` (开发者向, **不进公共 `/docs` 白名单**):

- **裁决**: 部分可行. 记忆分析发现分两类 —— **机械不变量类** (悬空引用 / 断裂晋升链 / 游离事实 / 多向量空间 / 向量维度不一致 / 事件游标非单调) 与代码路径**有相对明确的对应关系**, 可作为"值得排查主程序某模块"的线索; **内容质量类** (矛盾/冗余/归因漂移/晋升语义偏离) 是数据与模型行为的产物, **无法可靠反推**到具体代码缺陷.
- **交付**: 可/不可反推**分类表** (finding → 是否可反推 → 若可, 指向主程序哪个模块/不变量) + **证据面缺口** (当前 testbench 只有磁盘数据形状 WHAT, 缺运行期 HOW: 无 embedding worker 日志 / 无 decay 轨迹 / 无写入调用栈) + **未来受控设计草图** (若要真做, 需主程序侧补结构化事件/不变量校验钩子) + `PLAN.md` 未来 phase todo 占位.
- **不落**: 任何自动"反推代码缺陷"的端点/按钮/规则. 与用户"先调查可行性再细致考虑"的诉求一致.

---

## 8. 测试 (smoke, 前缀 p41 后端 / p42 前端)

### 8.1 `p41_memory_export_smoke.py` (后端, TestClient + 手造 fixture)

fixture 角色含: 已知 `master_name`/`character_name` + 一组 facts/reflections (正文里嵌角色名) + persona + recent.json + (可选) 一组近重复以让 analysis 出 finding. 断言:

- **ZIP 结构**: 解开后含 `README.md` / `manifest.json` / `raw_data/{recent,facts,reflections,persona,conversation_corpus}.json` / `analysis/{overview,lineage,embedding_health,embedding_duplicates,embedding_clusters,embedding_bridges}.json` + `analysis/summary.md`.
- **可解码**: 每个 `.json` `json.loads` 成功; `overview.json` 含 `cards`/`findings`.
- **各档零泄漏 canary**: 在 fixture 里埋一个**唯一 canary 凭据** (如某 api_key) → 所有档位 ZIP 全文**不含**该 canary. standard/strict 档: ZIP 全文**不含**真实 `master_name`/`character_name` 字面 (已被假名占位).
- **跨层一致性**: standard 档下, 对话 turn 里出现的假名占位与 facts/reflections 里同一身份的占位**一致** (同 token 同映射).
- **strict 行为**: `conversation_corpus.json`/`recent.json` 的 content 已是 `<omitted ...>` 占位 (原始转录撤除); 而 `facts.json`/`reflections.json` 的派生正文**仍在** (只做了假名化).
- **语料 gating**: `include_corpus=false` → ZIP 无 `raw_data/conversation_corpus.json`.
- **manifest**: 含 `tier`/`character`/`files`/`warnings`; **不含**假名反查 (断言无真名出现在 manifest).
- **错误码**: 无 session → 404 `NoActiveSession`; 有 session 无角色 → 409 `NoCharacterSelected`; 非法 `redaction` → 400 `UnknownRedactionTier`.
- **不触 LLM**: 导出路径无 `record_last_llm_wire` / 不调 memory 模型 (静态: `memory_export.py` 不 import LLM 层; 运行期: 导出后 `session.last_llm_wire` 未被写).
- **优雅降级**: 0 向量角色导出成功, 向量 analysis 为空结构 + warning, 不 500.

### 8.2 `p42_memory_export_ui_smoke.mjs` (jsdom, 已交付)

- U1 概况子页 ready 后工具栏含 [导出记忆分析] 按钮; U2 点击弹模态含三档单选 (默认 standard) + 含语料开关 + **固定显示 (非 `<details>`) 的黄色高亮脱敏说明块**; U3 选 strict + 关语料 → `fetch` `/api/memory/export?redaction=strict&include_corpus=false` 并 blob 下载后关闭模态; U4 后端 409 时模态保留并显示错误. (mock `fetch` 返回假 blob; 经 `node` 运行, 不在 `_run_all.py` Python 套件内.)

### 8.3 既有 smoke / 回归

- 不改默认子页, 不动 PAGES → **不破坏 p33/p38**.
- `redact_secrets` 行为不变 → p22/p24 脱敏相关 smoke 不受影响.
- 全量 `uv run python tests/testbench/smoke/_run_all.py` 必须全绿.

---

## 9. 文档同步 (docs-code-reality-grep-before-draft 纪律)

- `testbench_USER_MANUAL.md`: 在 Memory Analysis / 系统概况小节补 [导出记忆分析] 用法 + 脱敏档位对照 + 诚实限制 (对外分享前复核). 中文文档**只用 UTF-8 编辑工具** (StrReplace/Write), 禁 PowerShell Set-Content (编码纪律 L32).
- 新增 tester 指南 `docs/memory_export_guide.md` (进公共 `/docs` 白名单 → `health_router._PUBLIC_DOCS` + `page_about.js` 链接 + `p26_docs_endpoint_smoke` D5/D6 白名单校验须同步): 面向使用者的导出/脱敏说明 (镜像 §6 单一文案源).
- `MEMORY_CODE_INFERENCE_FEASIBILITY.md` + 本 `P30_BLUEPRINT.md`: 开发者向, **不进**公共白名单.
- `testbench_ARCHITECTURE_OVERVIEW.md`: 记忆分析子系统一节补 memory_export 聚合层 + 脱敏 chokepoint 扩展.
- `CHANGELOG.md` 新版本条目 (新特性向后兼容 = MINOR: **v1.9.4 → v1.10.0**); `config.py` `TESTBENCH_VERSION="1.10.0"` + `TESTBENCH_PHASE` + `TESTBENCH_LAST_UPDATED`. (改 `TESTBENCH_VERSION` 须同步 CHANGELOG 加 `## v1.10.0` 段, 否则 p26 D10 红.)
- `PROGRESS.md` / `AGENT_NOTES.md` 收尾条; `PLAN.md` 加 `p30_memory_export` 索引 + 反推未来 phase 占位.
- `LESSONS_LEARNED.md` §7.A 候选 (如"共享导出的脱敏必须整包单次统一变换以保跨层一致" / "分级脱敏须在 UI 诚实说明其不保护什么").
- `docs/README.md` 文档责任矩阵登记新文档.

---

## 10. 命名 / i18n / 配置

- ZIP 顶层 `kind="testbench_memory_export"`, `schema_version=1`; 文件夹英文名 `raw_data/` / `analysis/`.
- i18n 新增 `memory_trace.overview.export.*` 子树 (按钮 / 模态标题 / 三档标签与说明 / 含语料开关 / 脱敏说明面四段 / 导出中 / 成功失败 toast). 8 个 locale 文件全部同步.
- `config.py` 常量 (若需集中可调, 放 memory_export 模块级即可, 避免污染 config): `MEMORY_EXPORT_TIERS` / `MEMORY_EXPORT_DEFAULT_TIER="standard"` / strict 占位模板 / duplicates 默认阈值 (复用 embedding `DUP_THRESHOLD_DEFAULT`).

---

## A. 开工前设计审查门禁 (Design Review Gate)

> 本节记录开工前的多轮自审与用户复核追加的强约束. 门禁通过后方可开工.

### A.1 关键决策

| 决策点 | 结论 |
|---|---|
| 格式 (用户定) | ZIP, 分 `raw_data/` (脱敏原始) + `analysis/` (二级结论) 两文件夹 + manifest + README |
| 隐私 (用户定 d_tiered) | 可配置三档, **默认 standard 假名化** |
| 反推 (用户定 feasibility_doc_only) | 只出可行性裁决文档, 不落实现 |
| 脱敏机制 | 确定性身份假名化 + strict 整层撤除原始转录; **放弃**正则 PII 涂抹 (§A R3) |
| LLM | **明文不用** —— 导出零成本离线, 只用规则聚合器 |
| 脱敏时机 | bundle 组装后**最后一步**统一脱敏, 覆盖 analysis 嵌入文本 |
| 打包 | 标准库 `zipfile`, 内存 BytesIO, 服务端不留临时文件 |
| 端点 | `GET /api/memory/export`, `to_thread`, **纯读不取锁** (同 lineage/overview, 避免 autosave 写副作用), 400/404/409 |
| 前端 | P29 概况子页工具栏按钮 + 小模态 (默认 standard 一次 [导出] 即走) + 脱敏说明面 |

### A.2 多轮自审矫正 (回写正文)

| # | 维度 | 风险 | 处置 |
|---|---|---|---|
| R1 | 单一 chokepoint | 前端/多处各自拼导出 → 漂移 | §3.1 只在 `memory_export.py` 装配; 脱敏只在 `redact_export_bundle` 出口 |
| R2 | 脱敏时机 | 先脱敏 raw 再算 analysis → analysis 里回灌真名 | §2 装配序: 先组装完整 bundle, **最后**统一脱敏 |
| R3 | 正则 PII 涂敏违规 (`redact.py` 冻结原则 + G1) | 正则涂抹误报 + 违反"never filter user content" | §5 放弃正则; 改确定性身份假名化 + strict 整层撤除 |
| R4 | 事件循环阻塞 (P28.5 教训) | 导出聚合多项向量计算同步阻塞全界面 | §3.2 强制 `asyncio.to_thread` |
| R5 | 误触 LLM / 成本 / 非确定 | 若复用 ai_report/contradictions → 需模型配置 + 花钱 + 不确定 | §1.3 明文只用非 LLM 聚合器; §8.1 smoke 断言不触 LLM |
| R6 | 只读边界 | 导出落临时文件/写 sandbox | §1.3 内存打包直发, 服务端不留文件 |
| R7 | 假名反查泄漏 | manifest 若含 map 反查 = 脱敏形同虚设 | §5.3 manifest 只记 map size, 无反查表 |
| R8 | graceful degradation | 0 向量角色导出 500 | §2.2 向量视图空结构 + warning, 不报错 |
| R9 | 前端下载错误分支 | `api.js` 对二进制 JSON.parse 崩 | §3.3 直接 `fetch`+`blob`, 错误按 content-type 读 JSON |
| R-Consistency | **跨层字面一致 (用户强约束)** | "对话 A / 事实 B" 分歧 | §5.1 整包单次统一变换 + 同一 map 全层; strict 整层撤除不做局部涂抹 |
| R-UIExplain | **UI 讲清脱敏 (用户强约束)** | 用户不知每档保护/不保护什么 | §6 模态内置脱敏说明面 + README/guide 单一文案源 |
| R-UX | 一键 vs 可配置 | 弹模态破坏"快捷"体感 | §3.3 点按钮弹模态但默认档已选 standard, 一次 [导出] 即走 |
| R-Docs | 文档-代码-smoke 一致 | 新公共文档破坏 p26 D5/D6 白名单 | §9 memory_export_guide 入白名单须同步 `_PUBLIC_DOCS` + D-check |

### A.3 设计初衷锚定 (防漂移)

| 用户原话 | 是否守住 |
|---|---|
| "快捷的记忆导出" | ✅ 一键按钮默认 standard 一次 [导出] |
| "一键去除隐私敏感数据" | ✅ 凭据始终去 + 身份一致假名化 (§5) |
| "包含现有得到的记忆分析结论和原始数据" | ✅ raw_data/ + analysis/ 双文件夹 (§2) |
| "方便解码分析, 方便共享" | ✅ 机器可读 JSON + README + manifest 自描述 (§4) |
| "分成原始数据和数据分析两个文件夹" | ✅ 严格照做 (§4) |
| "反推……我表示怀疑, 先调查可行性" | ✅ 只出可行性裁决文档, 不实现 (§7) |
| "不论哪个挡位, 对话内容与事实保持同步" | ✅ R-Consistency 整包统一变换 (§5.1) |
| "UI 层面清晰说明脱敏问题" | ✅ R-UIExplain 脱敏说明面 (§6) |
| (用户没要) 自动反推代码缺陷 | ✅ 明文不实现 — 不漂移 |
| (用户没要) 导出时跑 LLM | ✅ 明文离线零成本 — 不漂移 |

### A.4 派生元教训候选

- **共享导出的脱敏必须"整包单次统一变换 + 同一映射全层"**: 先组装完整 bundle 再一次性脱敏, 否则 raw 与 analysis 之间、对话与派生记忆之间会出现同一实体的字面分歧 (本阶段 §5.1 派生).
- **分级脱敏必须在 UI 诚实声明"它不保护什么"**: 不能让分级给用户"高档=绝对安全"的错觉; 明确"只假名化身份, 自由正文披露不自动清洗, 分享前复核" (本阶段 §6 派生).

### A.5 门禁结论

- 冻结原则 (只读 / 不改主程序 / 不写回 / 不落临时文件 / session-agnostic 纯函数 / 单一 chokepoint / 不触 LLM) —— **全部满足**.
- 用户两个次要项已定 (英文文件夹名 OK; strict 默认保留派生正文、只整层撤除原始转录).
- 用户两条强约束 (R-Consistency / R-UIExplain) 已纳入并回写正文.
- **无阻断性问题, 门禁通过, 可开工.**

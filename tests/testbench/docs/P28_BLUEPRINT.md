# P28 记忆向量空间分析 (Memory Embedding Space) — 蓝图

> **Single Source of Truth**. 本文件是 P28 阶段的**唯一权威规格**. `PLAN.md` 条目 `p28_embedding_space` 仅作索引; `PROGRESS.md` / `AGENT_NOTES.md` 描述均以本文件为准.
>
> **定位**: 「记忆系统分析」(顶层 workspace `memory_trace`, 标题 *Memory Analysis 记忆系统分析*) 的**第二个子页**. 第一个子页是 P27 记忆溯源 (Memory Trace / Lineage). 本子页 id 暂定 `embedding_space`, 子页菜单项暂定 **「向量空间」**.
>
> **阶段号 / smoke 前缀**: 沿用 P27 蓝图记录的"阶段号与 smoke 计数器有意分叉"约定. 本阶段取**阶段号 P28**; 新增 smoke 接续单调计数器, 后端 `p37_`、前端 jsdom `p38_` 起 (现有最高 `p36`).
>
> **动因 (2026-06-30)**: 用户在「记忆系统分析」新系统下达第二子页需求 (信号): "记忆系统相关的人告诉我, 每条记忆理论上都附带一个 embedding (向量嵌入), 想基于这个机制做一些工作." 用户要求先调研机制再定方案. 调研结论 (见 §2) + 用户多轮 AskQuestion 决策 (见 §A) 共同固化为本蓝图.

---

## 1. 目标与边界

### 1.1 核心目标

让开发人员在一个 **P27 式"主可视化工作区 + 侧栏功能区"** 的页面里, 基于当前角色记忆的**向量嵌入**回答:

1. **健康 (Health)**: 这角色多少条记忆**已嵌入向量** / 还是 null / 因改文过期? 用的是哪个 `model_id`、几维?
2. **邻域 (Neighborhood)**: 某条记忆在向量空间里**最像哪些记忆** (跨类型 cosine 最近邻)? ——揭示 P27 结构图连不出来的"语义相邻".
3. **全局结构 (Topology)**: 整个记忆向量空间**长什么样** (降维到 2D 看聚类 / 离群)?
4. **冗余 (Redundancy)**: 哪些记忆**高度相似** (可能重复 / 该合并)? ——呼应主程序 `fact_dedup` 的 cosine 碰撞机制.
5. **归因质量 (Attribution sanity, 与 P27 联动)**: 一条 reflection **声明的来源事实** (`source_fact_ids`) 和它**向量上最像的事实**是否一致?

### 1.2 范围内 (In-Scope)

- **「记忆系统分析」第二子页** `embedding_space` (挂在 `workspace_memory_trace.js` 的 `PAGES` 数组), 与"记忆溯源"子页同级, 复用既有 two-col subnav 外壳.
- **纯只读分析后端**: 新增一组只读端点 (单一聚合层), 从沙盒磁盘记忆 JSON 读 `embedding` 三字段, 用 numpy 算降维坐标 / 最近邻 / 相似对 / 矩阵, 前端**照画**(沿用 P27 "后端聚合 chokepoint + 前端 verbatim 渲染"原则).
- **跨类型同一向量空间**: facts / reflections / persona 同模型同维, 混入**一个**空间 / 一个最近邻池, 按类型 (+实体) 上色; 提供"只看某类型"的筛选开关.
- **六个可切换视图 mode** (见 §4): ①体检 / ②散点 / ③最近邻 / ④重复 / ⑤矩阵 / ⑥语义源vs结构源.
- **散点渲染用 `<canvas>`** 自绘 + 自管 pan/zoom/hover 拾取 (上千点性能唯一稳的路, 见 §6); 矩阵/侧栏/详情仍走 DOM.
- **降维**: 默认 **PCA** (仅 numpy, 永远可用); **UMAP 按需安装**后自动升级 (见 §5); 装不上优雅回落 PCA.
- **数据闸门一等公民**: 角色若没 backfill 过向量, ①体检即首屏空态引导 (见 §2.4).

### 1.3 范围外 (Out-of-Scope, 明文约束)

沿用 P22/P24/P25/P27 "防无限扩张筐"哲学:

- **不改主程序**: 不动 `memory/*.py`、不让主程序写新字段 / emit 新事件. 只消费现有持久化数据.
- **不在 testbench 复现 embedding 运行时**: 默认**不**在测试台跑 `EmbeddingService` / `embedding_worker` 做"现场 embed". 本页只分析**磁盘上已有**的向量. ("现场 embed" 属显式扩 Phase 3.0 边界, 见 §5.4 可选未来增强, 默认不做.)
- **不冒充向量**: 没有向量的记忆**不**伪造坐标 / 相似度; 在散点上不出现 (或单列"未嵌入"区), 在体检里计入"缺失".
- **不做图编辑 / 不写回记忆**: 纯只读分析. 不把降维坐标 / 相似度写回记忆 JSON.
- **不引入前端图库 / 构建链**: 不加 d3 / plotly / three / deck.gl / npm / webpack. 散点用原生 `<canvas>` 2D, 矩阵用原生 SVG/canvas, 与 P27 纯原生先例一致.
- **不做跨 session / 多角色对比**: 只看当前 active session 的当前角色.
- **recent / 对话 turn 不进向量分析**: 它们磁盘上**无 embedding** (见 §2.2). "记忆↔对话"向量相似需对 turn 文本现场 embed, 属 OOS.
- **UMAP 重依赖不进默认安装**: 默认零新依赖 (PCA). UMAP 走按需安装 (§5.2), 不绑架未启用该功能的环境.

### 1.4 现有数据能支撑到什么程度 (诚实声明)

| 目标 | 数据支撑 | 表现 |
|---|---|---|
| facts/reflections/persona 的向量 | `facts.json`/`reflections.json`/`persona.json` 持久化 `embedding`(base64 fp16)/`embedding_text_sha256`/`embedding_model_id` 三字段 | 已嵌入 → 进分析; null → 体检"缺失" |
| 向量是否"新鲜" | 重算 `sha256(text)` 与 `embedding_text_sha256` 比 | 不符 → 体检"已过期(改过文)" |
| recent / 对话消息 的向量 | **无字段** | 不进本页 |
| 测试台自生成记忆 | memory_runner **不写** embedding key, 也不跑 worker | 通常 0 向量 → 引导"从主程序导入已 backfill 的角色" |
| 当前 dev 环境能否现场 embed | 实测无 `onnxruntime`/`tokenizers`/`data/embedding_models` | 不能; 本页只读已有向量 |

---

## 2. 数据与机制基础 (调研结论)

> 权威来源: `memory/embeddings.py` (向量契约) / `memory/embedding_worker.py` (异步回填) / `memory/hybrid_recall.py` (cosine 消费先例) / testbench `routers/memory_router.py` + `pipeline/memory_lineage.py`.

### 2.1 每条记忆的向量三字段

```
embedding              : str | None   # base64(little-endian fp16 bytes), L2 归一化向量
embedding_text_sha256  : str | None   # 全文 sha256 hex, 缓存指纹
embedding_model_id     : str | None   # 如 local-text-retrieval-v1-256d-int8-mlen1024
```

- 模型: 本地 CPU ONNX `local-text-retrieval-v1`, last-token pooling, **Matryoshka 可截维** (32/64/128/256/512/768, 默认按内存 auto, 16G+→256).
- 向量**已 L2 归一化** → **相似度 = 点积** (无需再除模长).
- **可直接复用的纯函数** (无需加载模型): `decode_embedding(emb)→np.float32[]`、`cosine_similarity(a,b)`、`parse_dim_from_model_id(id)`、`is_cached_embedding_valid(entry,text,model_id)`. 本页后端 lazy import 这些 (与 `recall_fusion.py`/`evidence_sim.py` 同 adapter 模式).

### 2.2 哪些类型带向量

| 类型 | 文件 | 带向量 | 结构 |
|---|---|---|---|
| facts | `facts.json`(+`facts_archive.json`) | ✅ | list[ {id,text,entity,importance,tags,created_at, embedding...} ] |
| reflections | `reflections.json` | ✅ | list[ {id,text,entity,source_fact_ids,status, embedding...} ] |
| persona | `persona.json` | ✅ | { entity: { facts: [ {id,text, embedding...} ] } } |
| recent / message | `recent.json` / `time_indexed.db` | ❌ | 无 embedding |

### 2.3 生成时机 (为何沙盒常常没向量)

- 写记忆时只写 `embedding: null` 占位, **不阻塞**.
- 唯一 stamp 写入点是后台 `EmbeddingWarmupWorker` (`memory/embedding_worker.py`): warmup 后扫 persona→reflections→facts 分批 embed 回填.
- testbench **不启动** 该 worker, memory_runner 也不写 embedding key → **沙盒自生成数据 = 无向量**. 只有"主程序已 backfill 的角色"导入沙盒才带向量 (随 JSON 整树拷贝).

### 2.4 数据闸门处理 (必做)

- ①体检永远首屏可渲染. 三种结论: **有向量** (正常进各视图) / **0 向量** (大幅引导: "本角色未嵌入向量, 请在主程序开启 vectors 跑出向量后再导入") / **部分向量** (标注"K 条缺失 / J 条已过期", 其余照常分析).
- 散点/最近邻/矩阵/重复仅对**有效向量子集**计算; UI 顶部常驻一条"覆盖率"提示.

### 2.5 维度不一致防护

- 角色记忆若中途换过 `model_id` (换维/换量化), 磁盘上可能**混着不同维度**的向量. 不同维不可比.
- 后端必须**按 `(model_id→dim)` 分组**, 只在**同一向量空间**内算相似度/降维; 选**最大占比的空间**作主分析集, 其余条目在体检里列为"另一向量空间 (K 条)", 不混入点积.

---

## 3. 架构

### 3.1 后端 (只读聚合, 单层 chokepoint)

新增 `tests/testbench/pipeline/embedding_space.py` (纯函数聚合) + `routers/memory_router.py` 追加只读端点. 全部基于 `get_config_manager().memory_dir` 读沙盒 JSON, lazy import `memory.embeddings` 纯函数.

端点 (读路径均 `GET`, 角色取自当前 active session):

| 端点 | 作用 | 返回要点 |
|---|---|---|
| `GET /api/memory/embedding/space` | ②散点 + ①体检合一 | `{points:[{id,type,entity,x,y,text,model_id,dim}], meta:{reducer_used,total,embedded,missing,stale,dims_present,primary_dim,umap_available,other_space_count}}` |
| `GET /api/memory/embedding/neighbors?id=&k=` | ③最近邻 | `{query_id, neighbors:[{id,type,entity,score,text}]}` |
| `GET /api/memory/embedding/duplicates?threshold=` | ④重复 | `{pairs:[{a,b,score, a_type,b_type,...}], threshold}` |
| `GET /api/memory/embedding/matrix?ids=` | ⑤矩阵 (子集下钻) | `{ids:[...], order:[...聚类重排...], cells:[[...cos...]]}` |
| `GET /api/memory/embedding/bridges` | ⑥语义源vs结构源 | `{rows:[{reflection_id, declared:[fact_id...], semantic_top:[{fact_id,score}], missing_in_declared:[...], extra_in_declared:[...]}]}` |
| `POST /api/memory/embedding/enable_umap` | UMAP 按需安装 | `{ok, installed, reducer_available, log}` |

`?reducer=pca|umap` 查询参数控制 `space` 的降维算法 (默认 pca; umap 仅在可用时); 后端用 **cosine 度量**、固定 seed.

> **降维在后端算** (numpy/可选 umap-learn), 前端只拿坐标画 —— 与 P27 "后端产 {nodes,edges,meta}, 前端不二次推导"一致.

### 3.2 前端 (主画布 canvas + 侧栏)

- 子页文件: `static/ui/memory_trace/embedding_space.js` (主编排) + 复用/新增 `embedding_space/*.js` (canvas 散点 / 侧栏面板). 挂进 `workspace_memory_trace.js` 的 `PAGES`.
- 布局: 左侧栏 = mode 切换 + 当前 mode 的功能/筛选/结果; 右主区 = 主可视化 (散点 canvas / 矩阵). 复用 P27 的**交互理念**与侧栏样式, 但散点渲染层用 `<canvas>` (见 §6).
- 状态 (owned, 驱动重渲): `mode` / `reducer` / `typeFilter` / `selectedId` / `threshold` / `view(pan,zoom)` / `phase`.

### 3.3 复用 P27 资产

- two-col subnav 外壳 (已在上一轮把 `workspace_memory_trace.js` 改成 PAGES 驱动) —— 直接加一项.
- 侧栏/卡片/空态 CSS token、`el()` DOM 工厂、`api` 客户端、i18n 子树模式、toast.
- **不复用** SVG pan/zoom 变换 (散点改 canvas, 拾取/变换自管).

---

## 4. 视图清单 (六个可切换 mode)

| # | mode | 输入 | 算法 | 主区 UI | 侧栏 | scale |
|---|---|---|---|---|---|---|
| ① | 体检 health | 全量条目三字段 | 统计 embedded/missing/stale + 重算 sha + 维度分组 | 计数卡 + 缺失/过期清单 | model_id/维度/覆盖率 | O(N) |
| ② | 散点 scatter | 有效向量 | 降维 2D (PCA/UMAP) | canvas 散点, 按类型上色, 点选 | 图例 + 筛选 + 选中详情 | 上千 (canvas) |
| ③ | 最近邻 neighbors | 选中条目向量 | top-k cosine (跨类型) | 散点上高亮 k 条 (可复用②画布) | 排名列表 + 分数 + 跳转 | O(N) |
| ④ | 重复 duplicates | 有效向量两两 | 上三角 cos ≥ 阈值 | 高相似对在散点连线高亮 | 阈值滑块 + 相似对列表 | O(N²) 但只列超阈值 |
| ⑤ | 矩阵 matrix | 选定子集 (筛选/簇) | NxN cos + 聚类重排 | 热力图 (canvas/SVG) | 子集选择 + 色标 | 子集 ≤ ~80 |
| ⑥ | 语义源 vs 结构源 bridges | reflections + facts 向量 | 反思×事实 top-k vs `source_fact_ids` 差集 | 列表/对照 (可连 P27) | 每条反思的"漏/多"标注 | O(R×F) |

通用: 顶部常驻覆盖率提示; 类型筛选 (facts/reflections/persona 多选) 全 mode 生效; 选中条目跨 mode 保持.

---

## 5. 降维引擎 (PCA 默认 + UMAP 按需)

### 5.1 默认 PCA (零新依赖)

- numpy 实现: 对有效向量矩阵中心化 → SVD/协方差特征向量取前 2 主成分 → 2D 坐标. 完全确定性.
- 局限: 线性, 簇可能糊在一起. 作为"永远能跑"的保底与默认.

### 5.2 UMAP 按需安装 (用户决策, §A)

- 默认 PCA; 侧栏一个开关 **"启用 UMAP (按需安装)"**.
- 点击 → `POST /api/memory/embedding/enable_umap`: 后端尝试安装 `umap-learn`:
  1. **优先离线 wheelhouse**: 仓库内预备 wheel 目录/zip (`tests/testbench/vendor/umap_wheelhouse/`, 解压后 `pip install --no-index --find-links=<dir> umap-learn`).
  2. **在线兜底**: wheelhouse 缺失/不匹配 → `pip install umap-learn` (需联网).
  3. 进度/结果 (含失败原因) 回流前端.
- 装好后置 `umap_available=true`, `?reducer=umap` 生效 (**cosine metric** + 固定 `random_state`). 之后默认可改用 UMAP.

> **硬坑 (必须文档化)**: `numba`/`llvmlite` 是按 **Python 版本 + OS + 架构** 编译的二进制 wheel —— 离线 wheelhouse 只对匹配环境有效, 故须"离线优先 + 在线兜底"两层. 安装会改动环境; 默认装进当前解释器 (测试台场景可接受), 更干净的可选做法是装进独立目录 + `sys.path` 注入 (§5.4 备注).

### 5.3 结果缓存

- 降维 (尤其 UMAP) 上千点要数秒; 按 `(角色, primary_model_id, 语料内容哈希, reducer, 参数)` 缓存坐标, 避免每次进页重算. 语料变 (增删改记忆) → 缓存失效重算.

### 5.4 可选未来增强 (默认不做, 需用户显式批准)

- **现场 embed**: 在 testbench 跑 `EmbeddingService` 对沙盒未嵌入记忆 backfill, 或"输入一句话→实时向量召回" (类 `hybrid_recall` cosine 路). 需装 onnxruntime+tokenizers + 下模型, 并显式扩 Phase 3.0 边界.
- UMAP 装进独立 venv/目录 + `sys.path` 注入, 不污染主解释器.

---

## 6. 渲染技术 (scale-to-thousands)

- **散点 = `<canvas>` 2D**: 原生 canvas 画上千点丝滑, 零新依赖. 自管: 世界↔屏幕变换 (pan/zoom)、hover/点选拾取 (最近点命中测试, 量大时用网格 bucket 加速)、按类型上色、选中/邻居高亮、可选 label 抽稀.
- **矩阵**: 子集 ≤ ~80 才全画 (canvas 或 SVG 格子); 上千全量不画 —— ⑤定位为"筛选/聚类后下钻", 非全局主视图 (用户认可).
- **体检/最近邻列表/详情/侧栏**: 走 DOM (`el()`), 量小.
- **重复连线**: 在②散点 canvas 上叠加超阈值对的连线 (canvas 画线, 不进 DOM).
- LOD: 散点点数极大时降采样/聚合绘制; 缩放到局部再显 label.

---

## 7. 与 P27 记忆溯源联动

- **共享"选中记忆"概念**: 散点点选一条记忆 → 侧栏"在记忆溯源里查看"按钮 → 切到记忆溯源子页并聚焦该节点 (走子页切换 + 既有 `selectNode`/`focusId`).
- **⑥语义源 vs 结构源** 是两子页的天然桥: 用向量审视 P27 的 `source_fact_ids` 归因是否合理 (反思最像的事实 vs 它声明的来源).
- 不耦合数据层: 本页独立端点, 不改 `/api/memory/lineage`.

---

## 8. 分期 (外壳一次搭好, 视图分期)

> 用户选"分期". 子页外壳 (mode 切换 + canvas 画布 + 侧栏 + 端点骨架) 一次搭好, 视图分批填.

- **P28.1 MVP (闭环, 用户选 mvp_plus_bridge)**: 子页接入 PAGES + ①体检 + ②散点 (PCA, canvas) + ③最近邻 + **⑥语义源vs结构源 + 与 P27 跳转联动**. 后端 `embedding_space.py` + `/space` + `/neighbors` + `/bridges`. 跨类型同空间 + 类型筛选 + 数据闸门空态. → 完整可用闭环 + 两子页协同.
- **P28.2 (✅ 已交付 2026-06-30, v1.7.0)**: ④近重复 (`GET /duplicates?threshold=` 上三角 cosine 分块, 阈值滑块 + 散点红线连接 + 相似对列表). smoke: p37 E6 + p38 V7.
- **P28.3 (✅ 已交付 2026-06-30, v1.7.0)**: ⑤相似度矩阵 (`GET /matrix?ids=` 子集 NxN cosine + 贪心 seriation 聚类重排, canvas 热力图 + 色标 + 悬停). smoke: p37 E7 + p38 V8.
- **P28.4 (✅ 已交付 2026-06-30, v1.6.0)**: UMAP **联网按需安装** (`POST /api/memory/embedding/enable_umap` → `pip install umap-learn`, 全路径结构化返回 + 永不抛 + 装不上/条目<4 回落 PCA) + 侧栏 PCA/UMAP reducer 切换 + 坐标缓存 (角色/维度/语料哈希/reducer). (离线 wheelhouse 暂不做.) smoke: p37 E2b + p38 V6.
- **P28.5 (✅ 已交付 2026-06-30, v1.8.0)**: 散点增强 — 自动聚类 + 簇标签. 见 §11. 散点 mode 内新增"自动聚类"开关: 后端在**原始高维 cosine 空间**聚类 (HDBSCAN 优先, 无 sklearn 回落纯 numpy cosine 连通分量), 前端按簇上色 + 在簇质心画标签; 簇标签默认 medoid 代表, 另提供 **[用 LLM 概括]** 按需精炼 (复用 P27 `memory.llm` wire 范式, 失败回落 medoid). 端点 `GET /clusters` + `POST /cluster_labels`. smoke: p37 E8 + p38 V9.

每期: 后端纯函数 + 端点 → 前端 mode → smoke → 文档同步.

---

## 11. P28.5 自动聚类 + 簇标签 (散点增强)

> **动因 (2026-06-30)**: 用户提出"散点图能否自动识别聚类, 并给聚类智能化的词条概括". 决策 (AskQuestion): **做**; 聚类 **HDBSCAN 优先, 无 sklearn 回落纯 numpy cosine 连通分量**; 概括 **主走 LLM, 失败回落 medoid**. 它是 ②散点 mode 的**增强开关**, 非新 mode、非新页。

### 11.1 设计要点

- **在原始高维向量上聚类, 不在 2D 投影上**: PCA/UMAP 的 2D 会扭曲距离, 在 2D 上聚类不忠实. 簇在原始 cosine 空间分, 再把簇色叠到既有 2D 散点上。
  - **诚实声明 (UI 点明)**: 高维分出的簇投到 PCA 2D 上可能看着交叠 (UMAP 2D 会分得更开)——这是降维固有, 不是 bug。
- **向量已 L2 归一化** → 欧氏距离与 cosine 单调等价 (`‖a-b‖²=2-2cos`), 故 HDBSCAN 用默认 euclidean metric 即等价 cosine, 无需 precomputed 距离矩阵 (省 O(N²) 内存)。
- **确定性**: HDBSCAN 确定性好; numpy 回落的连通分量按固定阈值, 也确定。

### 11.2 聚类算法 (两层)

1. **HDBSCAN (优先, 需 sklearn ≥1.3 — UMAP 安装时连带装入)**: `sklearn.cluster.HDBSCAN(min_cluster_size=...)`, 自动判定簇数 + 标 **噪声点 (label=-1)**。`min_cluster_size = max(2, min(8, round(sqrt(N)/2)))` 随规模自适应。
2. **纯 numpy 回落 (无 sklearn 时)**: 构造 cosine ≥ 阈值 (默认 `CLUSTER_CC_THRESHOLD=0.55`) 的图, **并查集连通分量**; size≥2 的分量为簇, 单点为噪声。crude 但零依赖、确定。

返回 `algo` 字段标明实际用了哪条 (`hdbscan` / `cosine_cc`)。

### 11.3 端点

| 端点 | 作用 | 返回要点 |
|---|---|---|
| `GET /api/memory/embedding/clusters` | 纯算聚类 (无 LLM) | `{algo, n_clusters, noise_count, assignments:{id:cluster}, clusters:[{cluster,size,medoid_id,label(=medoid文本),samples:[文本...],member_ids:[...]}], meta:{...},warnings}` |
| `POST /api/memory/embedding/cluster_labels` | LLM 概括 (按需) | `{method:"llm"|"medoid", labels:{cluster:概括词}, clusters:[...], warnings}` |

- `cluster_labels` 走 `session_operation` (要 stamp `memory.llm` wire); **一次 LLM call 批量**给所有簇的样本文本, 让模型回 `[{cluster,label}]` JSON; 解析失败 / LLM 失败 → `method="medoid"` + warning, labels 退回 medoid 截断文本。
- 每簇喂给 LLM 的样本: medoid + 最靠近质心的若干条, 上限 `CLUSTER_LABEL_SAMPLES=12`, 每条截断 `CLUSTER_LABEL_PREVIEW=80`。

### 11.4 前端

- ②散点侧栏新增 **"自动聚类"** checkbox。开启:
  - `GET /clusters` → 点按 **簇色** 上色 (噪声点灰), 在每簇质心 (成员 2D 坐标均值) 画 **簇标签** (默认 medoid 截断)。
  - 侧栏出"簇列表" (色块 + 标签 + size) + 一个 **[用 LLM 概括聚类]** 按钮 → `POST /cluster_labels` → 用返回 labels 覆盖标签 (canvas 重画 + 列表刷新)。
  - 关闭 → 恢复按类型上色。
- 仅 ②散点 mode 有此开关 (重复/矩阵 mode 不显示)。簇色板独立生成 (循环调色板), 与类型色互不影响。

### 11.5 配置 / i18n / smoke

- `config.py` 常量: `CLUSTER_MIN_SIZE_*`(自适应上下限) / `CLUSTER_CC_THRESHOLD=0.55` / `CLUSTER_LABEL_SAMPLES=12` / `CLUSTER_LABEL_PREVIEW=80`。
- i18n: `memory_trace.embedding.cluster.*` (开关名 / "用 LLM 概括" / 噪声 / N 个簇 / LLM 失败回落 等)。
- smoke: **p37 E8** (构造两块明显分离的向量 → 断言至少 2 簇 + 同块同簇 + 噪声计数 + medoid 在簇内; 无 sklearn 路径走 numpy cc 也要绿)。**p38 V9** (开关出现 → 勾选拉 /clusters → 点按 cluster 数据流 + "用 LLM 概括"按钮调 /cluster_labels 并更新, 不断像素)。

---

## 9. 测试与文档同步

### 9.1 smoke (前缀 p37 后端 / p38 前端)

- `p37_embedding_space_smoke.py` (后端): 用手造带 base64 向量的 fixture (复用 `stamp_embedding_fields`/`_encode_vector_fp16`) 断言: 体检计数 (embedded/missing/stale 正确) / 维度分组隔离 / 最近邻 top-1 正确 / 重复对超阈值命中 / PCA 输出点数与维度 / 跨类型同池. **降维随机算法只断结构不变量** (点数/维度/必含某些近邻), 不断具体坐标; UMAP 路径固定 seed 或测试只走 PCA 分支.
- `p38_embedding_space_ui_smoke.py`/`.mjs` (jsdom): 子页挂载不抛 / 0 向量空态 / mode 切换 / 点选出最近邻 / canvas 元素存在 (jsdom 下 canvas 2D context 受限, 仅断 DOM 结构与数据流, 不断像素).

### 9.2 文档

- `testbench_USER_MANUAL.md` §2.5: 记忆系统分析现有**两个子页**, 新增 §2.6 "向量空间" 用法 (含"无向量需导入"引导 + UMAP 按需安装说明).
- `p26_docs_endpoint_smoke.py` D14 等若锁了子页数, 同步更新.
- `CHANGELOG.md` 新版本条目; `config.py` `TESTBENCH_PHASE`/版本号; `PROGRESS.md`/`AGENT_NOTES.md` 收尾条; `LESSONS_LEARNED §7.A` 候选 (如"按需惰性安装重依赖"模式).

---

## 10. 命名 / i18n / 配置

- 子页 id: `embedding_space`; navKey: `memory_trace.nav.embedding_space` = **「向量空间」** (可改).
- i18n 新增 `memory_trace.embedding.*` 子树 (mode 名 / 体检文案 / 空态引导 / UMAP 开关 / 阈值等).
- 配置: 阈值默认、最近邻 k 默认、矩阵子集上限、UMAP wheelhouse 路径等放 `config.py` 常量.

---

## A. 设计决策记录 (本轮 AskQuestion 拍板)

| 决策点 | 结论 |
|---|---|
| 路线 | **A: 只读分析磁盘已有向量** (不现场生成) |
| 演示数据 | 用户**从主程序导入已 backfill 的角色**测试 |
| 语料范围 | **跨类型混入同一向量空间** (一个最近邻池, 按类型上色) |
| 规模 | 按**可扩到上千条**设计 |
| 首版视图 | 全部六个做成**可切换 mode**; 但**分期**实现 (MVP = 体检+散点+最近邻 **+ ⑥语义源vs结构源 + P27 联动**) |
| 散点渲染 | **`<canvas>` 自绘 + 自管 pan/zoom/拾取** |
| 降维 | **PCA 默认** + **UMAP 联网按需安装** (`pip install umap-learn` + 完善失败处理, cosine metric, 装不上/失败回落 PCA; 离线 wheelhouse 暂不做) |
| 下一步 | 蓝图过目完毕, **开工 P28.1 MVP (含 ⑥ + P27 联动)** |

## B. 待用户确认 (开工前)

1. UMAP 策略: 采用"PCA 默认 + 按需安装"(本蓝图方案), 还是你更想"默认就装 UMAP"? 若按需安装, 离线 wheelhouse 是否需要我先备 (需告知目标 Python 版本/OS/架构), 还是先只做"在线 `pip install` 兜底"?
2. 子页/视图命名是否采用 "向量空间" + 六 mode 名 (体检/散点/最近邻/重复/矩阵/语义源对比)?
3. MVP 范围 (P28.1 = 体检+散点+最近邻) 是否认可作为第一个可交付?
4. 是否需要 ⑥与 P27 的跳转联动放进早期 (它最能体现"两子页协同"), 还是按 §8 留到 P28.3?

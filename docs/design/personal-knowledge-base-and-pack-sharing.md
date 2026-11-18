# 统一知识库与成品包分享开发方案

> 本文已按“只保留一个知识库”第二版方案收敛。早期独立个人资料库方案由 Git 历史保留，不再作为实施依据。当前实施基线为 `Knowledge_Base` 的 `90b3253a1`。

## 1. 产品边界与已确认决定

- 只有一个知识库：内置、市场和用户导入的资料都是现有 `knowledge` 的 pack，统一检索、索引、任务与容量。
- 只有一个入口：插件管理器既有知识库页面；桌面端通过统一管理器入口打开该页。
- 知识库归 Memory Server；Main 仅保留 `/api/public-knowledge` 薄代理，Plugin 继续承担现有账号、配对和市场桥。
- 新导入个人文档创建的 pack、通过本地上传导入的 JSON/ZIP，默认 `enabled=False`、`auto_context=False`、`local_embedding=True`。启用意味着命中片段可能发给云端模型，导入及启用界面明确说明。市场订阅继续使用既有安装语义；默认关闭约定不扩大到市场订阅。替换已有 pack 保留其启用选择。
- 原件、全文、历史版本和成品包全部保留。不在开发过程中对真实用户目录执行迁移、清理或删除。
- 前台请求全部接纳，但实际模型计算串行。后台常规空闲执行，持续前台压力下允许最小批次保底。
- 不做旧客户端、独立个人库格式或双产物市场发布兼容；统一 pack JSON 仍是文本基础格式，保留 JSON 导入与文本导出。
- 三仓继续各维护现有整体功能分支和 PR，不按 PDF 的示例再拆成多个 PR；不自动合并或部署。

## 2. 目录与进程归属

| 位置 | 职责 |
| --- | --- |
| `knowledge/importing/documents.py` | 有上限的 TXT/Markdown/DOCX/PDF 解析、位置与隐私保护 |
| `knowledge/importing/sources.py` | 原件、不可变全文版本、预览和历史；不存向量 |
| `knowledge/importing/projection.py` | 当前文档版本投影为严格 schema-v1 pack，写入 `doc:` / `ver:` 标签 |
| `knowledge/service.py` / `store.py` / `registry.py` | 既有任务、SQLite FTS/向量、注册表与查询；不增加第二套 Store |
| `knowledge/archive.py` | 用已计算向量导出通用 v2、读取成品种子向量；不调用模型 |
| `utils/knowledge_package.py` | 纯标准库包规范、规格、ZIP 安全与数值检查；市场逐字同步 |
| `memory/_embeddings/spec.py` | 只读模型规格与无截断 token 计数；不加载 ONNX |
| `utils/embedding_contract.py` | 无进程依赖的结构化编码结果和后台延后异常 |
| `app/memory_server/knowledge_routes.py` | 统一 `/internal/knowledge` 管理接口与共享编码器适配 |
| `frontend/plugin-manager/src/views/KnowledgeManager.vue` | 来源、启用、文件预览、历史、导出和分享，沿用八语 |
| `plugin/server/routes/knowledge_market.py` / `knowledge_publish.py` | 现有订阅和新增发布；账号凭据不进入 Main 或页面 |

运行资料归 `ConfigManager.knowledge_dir`：

```text
knowledge/
  registry.json
  knowledge.db
  packs/
  history/<pack_id>/
  jobs/<job_id>.json
  jobs/<job_id>.pack.json
  .staging/
  sources/<pack_id>/
    manifest.json
    originals/<doc_id>/v<n>/original
    versions/<doc_id>.v<n>.json
    previews/
  artifacts/<sha256>.neko-knowledge.zip
```

不再使用 `services/knowledge_library`、独立 `knowledge_library` 资料根、页面、FTS、余弦检索、任务系统或嵌入 HTTP RPC。

## 3. pack 与历史数据

- 导入 pack 的 ID 为 `imp.<uuid>`；前缀便于识别，规则依据 `origin` 字段，不依据字符串前缀。
- `PackRecord.origin` 为 `builtin/market/imported`，只表示出处；`enabled` 决定参与检索，`auto_context` 决定被动注入。
- 旧记录缺少 `enabled` 时按 True 读取；新导入的默认关闭由导入流程显式写入。
- `enabled=False` 同时排除工具检索、turn_context 和 tool_available，但不阻止后台补向量。
- 文档版本只存全文与章节。文本提交即可关键词检索；向量由现有索引器逐块按 text_hash 回填，不用文档级 unchanged 阻止补齐。
- 所有历史版本保留。移除最后一份文档后，保留可恢复的来源记录，不伪装为可检索的活动 pack。
- `sources_managed` 是本机是否实际持有原件历史的能力标记。离线成品不附带原件，不能根据其中的 `doc:` / `ver:` 标签伪造本机历史，也不能追加文件后覆盖既有成品正文；仍可检索和导出。
- pack 更新保留旧 JSON；原件与 source sidecar 不受索引回收影响。提交后的清理失败不能把已成功的任务改成 failed。
- 继续使用单 pack 10,000 chunk、全局 20,000 entry/chunk、200 pack 的现有限额，不凭估算上调。

任务状态沿用 `queued → building → active/failed/cancelled`。激活提交点是 `registry.json` 原子写入；来源侧的 `manifest.json` 仅描述历史，不决定当前可检索版本。

| 中断位置 | 已保留内容 | 重启与恢复动作 |
| --- | --- | --- |
| 预览或原件版本写入后、入队前 | 原件、完整解析版本 | 版本未激活，当前 pack 不变，可重新选择历史 |
| 冻结任务落盘后、构建中 | job、冻结 pack、原 ZIP（若有） | 未命中注册表提交事实则标为 `process_interrupted`，按原 job 重试，不创建新文档版本 |
| 新 pack/SQLite 提交后、注册表提交前 | 新旧文本均保留 | 查询版本闸门不提供不匹配的索引，启动按旧注册表重建 |
| 注册表提交后、任务终态写入前 | 新注册表与不可变正文 | 依据正文摘要、订阅和原 ZIP 身份将任务恢复为 active |
| 成功后的索引刷新或清理失败 | 全部原件、正文、历史和产物 | 保持 active，后台继续恢复可重建索引 |

导出在暂存文件 fsync 后、发布前通过与取消共用的任务锁关闭取消窗口；已接受取消不能再公开成品，提交开始后取消明确返回 False。两种交错均有直接回归。

已用独立进程 `os._exit` 注入验证构建中断、重启及冻结重试；不把它等同于真实断电和所有平台文件系统耐久性验证。

## 4. 嵌入调度与完整分块

- 默认前台兼容既有 `embed/embed_batch`；新 `embed_query` 为前台，`embed_documents` 为后台。
- 记忆回填、统一知识库索引、导入资料与种子向量缺口都显式进入后台 lane。
- `embed_ex` 返回 `ok/unavailable/loading/disabled/busy/timeout/error` 和无正文的状态计数；后台 busy 用 `EmbedDeferred` 延后，不能变成 None 后增加 attempts。
- 调度与知识库接口不调用 `request_load()`。未就绪时文本仍可用，warmup 完成后由原索引循环补齐。
- 后台 token 预算按实际 padding 成本计算：4–8 GiB 为 1,024，8 GiB 以上为 2,048；未知内存采用保守预算，原低于 4 GiB 默认禁用规则不变。
- 后台占空比为 15% / 25%。冷却不占模型槽、不阻塞前台；有后台真正等待、达到 8 次前台且冷却结束后，保底只执行 1 条，剩余保序重新竞争。
- 取消保持真实原生线程 drain；关闭应唤醒等待者，不等漫长后台冷却结束。
- `spec()` / `count_tokens()` 不加载 ONNX；首次指纹和 token 计算须 offload。
- `chunk_bodies(..., fit=...)` 和 `derive_chunks(..., fit=...)` 检查包含标题/摘要的完整输入，超过 token 预算继续切分，不静默截尾；未传 fit 保持现有 v1 哈希规则。
- 文件身份不再把 ctime 当内容变更；权重语义依旧由实际 SHA-256 冻结。

## 5. 通用成品包 v2

格式名 `neko.knowledge-package`，`format_version=2`，后缀 `.neko-knowledge.zip`，固定成员：

```text
manifest.json
pack.json
chunks.jsonl
vectors.f32
```

- `pack.json` 是统一 schema-v1 pack 文本。来源、条目和容量继续按既有 pack 规则校验。
- `chunks.jsonl` 按条目和 chunk_index 记录实际 embedding_text/text_hash；向量按同一顺序为 little-endian float32。
- manifest 固定作品、发布版本、模型规格、每成员大小/摘要及数量。原件、绝对路径、任务状态和模型不进入成品。
- 导出读取 knowledge.db 已有向量，不重算。缺向量或缺可信资源规格时拒绝 v2；v1 文本导出不依赖模型。
- derived DB 只增加规格绑定元数据，保留已有资料与向量；没有真实权重规格记录的旧向量不能冒认已验证 v2。
- 安装仍走统一 import job。资源身份一致且包维度不低于本机维度时截前缀、重新归一化后种入；不兼容或本机更高维时留空交原索引器补算，不补零。
- 包压缩 64 MiB、展开 128 MiB；拒绝 ZIP64、分卷、重复/NUL/路径别名、链接/特殊文件、加密、异常解压、伪造长度、非有限/零范数向量。
- 包摘要只能证明字节完整，不证明作者语义真实；市场账号及审核仍承担内容信任边界。

## 6. API 与统一代理

新增路径均在 `/internal/knowledge`，由既有 Main/Plugin 白名单代理：

| 接口 | 合同 |
| --- | --- |
| GET `sources?pack_id` | 文档列表、当前版本；已移除文档仍能打开历史 |
| GET `sources/versions?pack_id&doc_id` | 不可变历史列表 |
| POST `sources/preview` | raw Blob；文件名/新 pack 名使用 URI 编码的 `X-Knowledge-Filename/Name` 头，避免进 URL 日志 |
| POST `sources/commit` | preview_id、sha256、enabled；新 pack 默认关闭，返回既有 import_commit job |
| POST `sources/remove/restore` | pack_id/doc_id/版本，复用原任务状态和取消 |
| POST `packs/enabled` | pack 级参与检索开关 |
| POST `packs/export` | pack_id、format_version=1/2、version，返回 pack_export job |
| GET `packs/artifacts?artifact_id` | 只接受 SHA-256 身份，受控二进制下载 |
| POST `packs/jobs/retry` | 原 job_id，重用冻结输入；仅失败或取消任务可重试，busy 不生成重试 |

管理搜索的词和条目标题使用 URI 编码的 `X-Knowledge-Query/Title` 头，两跳代理仅对对应路径转发；不把资料文件名、正文、查询词、命中标题或异常原文写入日志与诊断。包展示名与许可是作者原文，不自动翻译。成品包不含原件，因此 PDF/DOCX 原件元数据不会随导出公开。

上传 ZIP 和大资料采用落盘流式接收；代理不解析正文，继续先过 bridge/CSRF/Origin/容量门。Main 不 import knowledge。

## 7. UI、桌面与市场

- 在现有 pack 表展示来源和启用；在同一页预览确认、文档列表/版本恢复、导出与公开分享。
- 对已启用 pack 追加文件须明确提示检索与云端发送范围；公开发布有单独确认，原件不公开。
- UI 文案只进插件管理器八语，删除独立页面的 static/locales 文案及构建登记。
- PC 指向 `/api/agent/user_plugin/dashboard?page=knowledge`，复用现有插件管理器窗口和运行时地址，不固定端口。
- 发布复用现有市场 shares、OAuth、配对和幂等 UUID；订阅安装复用现有 worker。
- 市场描述符只采用明确 protocol_version=2，客户端不把 ZIP 误认成旧 JSON；不做旧客户端或旧 personal 包兼容。市场详情页复用现有配对和订阅 worker，“已交给 NEKO”表示完成传输暂存，不能显示为知识库已安装完成。
- 先部署市场服务/新校验器及网关容量配置，再开放新发布；本地测试不冒充线上 OAuth、PostgreSQL 或生产部署。

## 8. 模块进度与验证

| 模块 | 当前状态 | 验证边界 |
| --- | --- | --- |
| 嵌入基础与完整 token 分块 | 已实现并验证 | 共享编码与记忆影响面通过；真实 tokenizer/ONNX 冷导入、补齐、导出及另一资料根安装通过；大库/低配性能未测 |
| pack 启用、来源、source 历史与复用任务 | 已实现并验证 | 默认关闭、来源能力、历史恢复、杀进程冻结重试、最终导出取消交错及 ZIP 重建种子均验证 |
| 通用 v2 校验、已有向量导出/种入 | 已实现并验证 | 安全/全文绑定/重复文本、真实构建产物跨市场与 native 安装链路通过，无文档重编码 |
| 现有插件管理器 UI 与 PC | 已实现并验证 | UI 62 项、八语与重建通过；三跳浏览器 51 次知识请求、页面错误 0；PC 56 项直接回归通过 |
| 市场 protocol2 与通用校验器 | 已实现，完成配套验证 | 当前版本精确下载、权限、版本冲突和配对安装通过；校验器真实提交及摘要由市场 `source.json` 记录 |
| 删除独立模块和接线 | 已完成 | 精确移除旧源码、构建登记和失效测试；安全/解析/隐私覆盖迁入统一链路，未删除用户资料 |

完成标准：统一页面完成文档导入→预览→默认关闭→显式启用→既有检索→后台回填→导出→另一根安装；关闭 pack 不参与工具/自动注入，busy 不消耗 attempts，重启保留任务与历史。市场发布/下载采用同一包和安装链；独立页面/RPC/第二 Store 不再注册。

测试只使用临时资料根和合成文档；源码改动均配职责注释。每个模块完成后更新本节。真实模型、浏览器、Windows/Linux、生产数据库及正式发行包的验证必须分别陈述，不把模拟或跳过算作验收。

本轮后端直接影响集 904 项通过；其后来源能力/默认关闭及取消窗口收口分别跑对应回归。真实模型小样本可用 `NEKO_REAL_EMBEDDING_ROOT` 指向已有只读模型资产，再运行 `uv run --no-sync pytest tests/unit/test_unified_embedding_real.py`。跨仓产物通过 `NEKO_UNIFIED_PACKAGE_FIXTURES` 指定临时目录，运行 `plugin/tests/unit/server/test_unified_knowledge_native_roundtrip.py`；远端市场传输是受控夹具，Main、Memory、任务与 SQLite 使用真实实现。

浏览器验收使用真实服务和两跳代理，完成预览、默认关闭、启用、替换、历史恢复、文本导出及重新安装，并检查三端 INFO 日志无合成正文、文件名和查询词。临时服务已退出，未操作既有用户服务。插件管理器完整类型检查仍有基线已复现的 11 项 `ModelApi.vue` 问题，未混改；知识库定向检查和生产构建通过。Windows/Linux 真机、正式发行包、最低配置/跨量化质量、完整语音并发及生产 OAuth/PostgreSQL/网关仍需独立验收。

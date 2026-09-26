# 剧本工坊 SDK 框架与结构审查

## 状态与范围

状态：**WS-01—02 已处理，匹配回归通过**；关联 IB-01 也已同步修复。本文是工坊历史结构审查，当前模块职责和正式调用边界以[架构开发文档第2.3节](./neko-theater-architecture.md#23-剧本工坊-sdk-模块-theater_workshop)及[SDK迁移说明](./neko-theater-workshop-sdk-migration.md)为准。实际优化与验收边界统一见[问题与处理 2.125](./neko-theater-issues-and-solutions.md)。审查基线为 N.E.K.O `theater_chat_window` / `201e4fc4a`；下文调用链、行号、隔离探针和建议保留优化前证据，“本轮”及原检查数量均指审查时点，不是现行待办。未修改正式作者项目、安装包或存档。

已核对 [SDK 迁移与接入说明](./neko-theater-workshop-sdk-migration.md)、[SDK 使用说明](https://github.com/Project-N-E-K-O/N.E.K.O/blob/201e4fc4a/theater_workshop/README.md)、[小剧场架构](./neko-theater-architecture.md) 的工坊边界，以及 `host.py`、`sdk/workshop.py`、`contracts.py`、`packages.py`、`model.py`、作者投影、支线和项目 Store 的真实调用链。创作与评分入口用于核对职责和消费者，未重新评估模型效果。

成功标准：指出具体多余步骤或职责混合，以代码链路及隔离探针说明实际代价，保留现有安全和作者语义；不以文件长度、类数量或两仓相似度判定过度设计。

| 编号 | 优先级 | 结论 | 状态 |
| --- | --- | --- | --- |
| WS-01 | P2 | Story Package 导入拼接三个持久化操作，失败后留下半成品项目 | 两端已改为一次最终写入 |
| WS-02 | P3 | 发布身份核对复用带作者软诊断的编译入口，重复分析落入写事务 | 已分离严格编译与作者诊断；实际延迟未测量 |

P2 表示有可复现的失败结果，应安排修正；P3 表示可确定的结构冗余，可在相关代码下次调整时收敛。本轮不提出取消工坊分层、修改模型分工或新增通用框架。

## WS-01：导入通过创建、更新、记回执三次提交完成

**优先级 / 状态：P2 / 已处理；以下为优化前证据。**

### 证据与调用链

- [workshop.py:228](https://github.com/Project-N-E-K-O/N.E.K.O/blob/201e4fc4a/theater_workshop/sdk/workshop.py#L228)：公开 `import_story()` 在外层 Store 事务中编译，随后调用 `Store.import_story()`，最后单独 `record_compile()`。
- [numeric_v2_project_store.py:808](https://github.com/Project-N-E-K-O/N.E.K.O/blob/201e4fc4a/theater_workshop/sdk/numeric_v2_project_store.py#L808)：`import_story()` 先 `create()`，再把旧包投影为 setup，并经 `_update(..., preserve_imported_story=True)` 保存。
- [numeric_v2_project_store.py:340](https://github.com/Project-N-E-K-O/N.E.K.O/blob/201e4fc4a/theater_workshop/sdk/numeric_v2_project_store.py#L340)、[同文件:476](https://github.com/Project-N-E-K-O/N.E.K.O/blob/201e4fc4a/theater_workshop/sdk/numeric_v2_project_store.py#L476)、[同文件:760](https://github.com/Project-N-E-K-O/N.E.K.O/blob/201e4fc4a/theater_workshop/sdk/numeric_v2_project_store.py#L760)：三个步骤分别调用 `_write()`；结果依次是空项目 revision 1、有故事但无编译回执的 revision 2、完整导入结果 revision 2。
- [numeric_v2_project_store.py:299](https://github.com/Project-N-E-K-O/N.E.K.O/blob/201e4fc4a/theater_workshop/sdk/numeric_v2_project_store.py#L299)：`transaction()` 提供写栅栏和互斥锁，没有跨多次 `_write()` 的回滚机制。每次 `_write()` 的原子替换只保护该次文件内容。

隔离探针使用现行正式编译器和测试剧本，包装实例 `_write()`：

| 操作 | 观察结果 |
| --- | --- |
| 成功导入 | 同一项目文件提交 3 次 |
| 在第 2 次 `_write()` 进入时抛出 `OSError` | 调用失败，`list_projects()` 仍返回 1 个 revision 1、`story=None` 的空项目 |
| 在第 3 次 `_write()` 进入时抛出 `OSError` | 调用失败，仍留下 revision 2、有故事、`compile_result=None` 的项目 |

### 实际成本与最小精简建议

这里为了复用完整的“新建”和“编辑”命令，把一次导入拆成三个已经生效的文件状态。除额外序列化、fsync 和替换外，后续步骤失败时调用方得不到成功返回的项目 ID，却已产生新项目；重试还会分配新的 ID，增加半成品辨认与清理负担。

建议仅把**项目初始值构造**提取为无落盘的小函数。导入时在内存完成 ID、setup 投影、原故事、作者元数据和编译回执组装，再在现有受保护事务中一次写入最终项目。普通 `create()` 和普通 `update()` 保留各自公开语义；无需增加通用 Unit of Work、回滚日志或导入状态机。

### 必须保留的边界

- 编译失败仍在创建项目之前返回；不把不合格包写入作者目录。
- 保留导入包的原 story 与 canonical hash；不能为了删掉 `preserve_imported_story` 参数而用 setup 默认值重写包。
- 当前成功导入返回 revision 2；精简写次数不授权静默改变这一对外结果。
- 写栅栏、Store 锁、原子替换、导入来源不变，以及编译后仍须显式 `validate` 才可发布，全部保留。
- 本项针对 `import_story`。完整作者快照 `import_project` 已采用组装后一次落盘，不应一起改写；正式包安装与作者回执分属两份文件的恢复协议也不在本项范围。

### 验收方式

成功导入仅发生一次最终项目写入，返回故事/hash/revision 与现行成功路径一致；组装或最终替换失败后，没有新项目文件，也没有临时文件残留。保留既有导入、发布门禁和维护拒写回归；新增失败验证应沿真实 `_write`/replace 边界，不只验证返回值。

## WS-02：作者软诊断被带入发布身份核对和纯布局保存

**优先级 / 状态：P3 / 已处理；以下为优化前证据，未测得用户可感知的延迟。**

### 证据与调用链

- [sdk/numeric_v2.py:283](https://github.com/Project-N-E-K-O/N.E.K.O/blob/201e4fc4a/theater_workshop/sdk/numeric_v2.py#L283)：作者编译入口先固定隐藏数值投影、调用正式编译器，再无条件执行 `analyze_numeric_v2_story()`，把作者警告追加到编译结果。
- [numeric_v2_analysis.py:638](https://github.com/Project-N-E-K-O/N.E.K.O/blob/201e4fc4a/theater_workshop/sdk/numeric_v2_analysis.py#L638)：存在数值定义时，作者分析分别对作者限幅和每轮约 2 点口径遍历图；此处两种分析用途明确，本身不是重复错误。
- [workshop.py:390](https://github.com/Project-N-E-K-O/N.E.K.O/blob/201e4fc4a/theater_workshop/sdk/workshop.py#L390)：`_compiled_current()` 为核对当前稿 hash，再调用完整作者编译入口。
- [workshop.py:401](https://github.com/Project-N-E-K-O/N.E.K.O/blob/201e4fc4a/theater_workshop/sdk/workshop.py#L401)、[同文件:412](https://github.com/Project-N-E-K-O/N.E.K.O/blob/201e4fc4a/theater_workshop/sdk/workshop.py#L412)：`validate` 和 `_publish_candidate` 消费 bytes/hash/story_id，不消费这次新计算的作者 warnings。
- [numeric_v2_project_store.py:305](https://github.com/Project-N-E-K-O/N.E.K.O/blob/201e4fc4a/theater_workshop/sdk/numeric_v2_project_store.py#L305)、[同文件:485](https://github.com/Project-N-E-K-O/N.E.K.O/blob/201e4fc4a/theater_workshop/sdk/numeric_v2_project_store.py#L485)：纯 editor/stage 保存通过 `_carry_publish_receipts()` 核实原包 hash，同样执行完整作者分析，结果只取 hash。
- [workshop.py:423](https://github.com/Project-N-E-K-O/N.E.K.O/blob/201e4fc4a/theater_workshop/sdk/workshop.py#L423)、[同文件:429](https://github.com/Project-N-E-K-O/N.E.K.O/blob/201e4fc4a/theater_workshop/sdk/workshop.py#L429)：导出和安装的最终候选复核在 Store 事务中进行；纯布局保存也在 [Store._update:404](https://github.com/Project-N-E-K-O/N.E.K.O/blob/201e4fc4a/theater_workshop/sdk/numeric_v2_project_store.py#L404) 的事务内。因此这些路径把并不消费的作者分析带进共享 Store 短时锁及写栅栏。

包装 `analyze_numeric_v2_story()` 并保留原函数执行，观察到：同稿 `validate` 1 次，`export` 1 次，已编译项目仅保存 `editor.node_positions` 也有 1 次。每次结果都不用于对应操作的返回或新报告。这是调用次数证据，不是性能基准；未据此宣称页面卡顿或等待秒数。

### 实际成本与最小精简建议

“产物身份是否仍有效”和“向作者解释路线节奏”被绑在同一入口。后一项图分析随稿件变大需要额外遍历和拷贝，且发布或布局保存不会使用其诊断结果；维护作者诊断时也会牵连这些发布/存储路径。

建议在现有编译适配内部拆出明确的小入口：执行相同作者投影与正式严格编译的核心，以及追加作者 warnings 的显式作者编译。hash 核对使用前者，公开 `compile()` 继续返回原有 warnings。可用一个内部方法完成，无需增加另一套编译器、诊断服务或跨请求缓存。

### 必须保留的边界

- `compile → validate → export/install` 是现行明确业务合同，不能把显式发布复验删掉。
- 当前 revision、非空 hash、canonical bytes、最终安装前重验与同剧本生命周期锁全部保留；不能只相信历史回执。
- 作者隐藏数值投影必须一致，不能让发布核对与原编译得到不同字节。
- 不移除作者节奏/可达性分析，也不削弱循环、路径数量上限和 unknown 语义；只避免在不消费结果的位置重算。
- 不把最终候选核对无条件挪出事务，不用有失效风险的缓存替代当前稿复验。

### 验收方式

同一 fixture 的公开 `compile()` warnings、canonical bytes/hash 与现行完全一致；`validate/export/install` 及有效回执下的纯布局保存不重新执行作者软诊断。真实内容变更、篡改 hash、旧 revision、维护态拒写和安装期间编辑仍按原规则拒绝。大图耗时若要进一步优化，另做基准后再讨论，不由本项推定收益。

## 跨模块关联：IB-01 同源影响已复现

InkAI 后台框架审查的 IB-01 统一记录 JSON 修复器可能改写字符串正文的问题，不另分配 WS 编号。本次主审已独立实跑 SDK 同源的 [json_response.py:88](https://github.com/Project-N-E-K-O/N.E.K.O/blob/201e4fc4a/theater_workshop/sdk/json_response.py#L88)：合法 JSON 中的固定旁白原文 `记录原文：,}；,]` 保持不变；仅在最外层增加尾逗号触发修复后，解析成功，但原文变为 `记录原文：}；]`。

该问题同时影响 SDK 的公共模型响应解析入口，不能只在 InkAI 修复。后续已按 IB-01 同步两端解析器，限定语法修复并验证字符串原文保留；结果见问题与处理 2.125。

## 必要复杂度与未立项观察

- **保留三层职责**：Host 处理本体配置、名字、线程/事件循环与正式安装；SDK 编排作者操作；Store 负责项目提交、revision 和失效规则。没有证据要求撤掉某一层。
- 同根复用、跨进程写者锁、项目长操作排他、迟到结果 revision 检查、取消后等待工作线程结束，各自保护不同边界，不是四套同义锁。
- 发布候选包含不可变字节；编译回执、发布复验回执和安装回执分别表示不同事实，不能合并成一个成功布尔值。
- 主线/支线作者数据、模型候选与正式包校验处于不同输入阶段；类似字段和校验不能仅靠同名就删除。公共 Story Package 字段取舍由公共数据结构审查归口。
- InkAI 仍独立运行，双方共享创作规则继续同步属于明确维护边界。本轮不提出删除旧后台、跨仓动态导入或强制统一前端。
- 同一次正文更新测得 `_normalize_authoring()` 执行 4 次，包含输入参考清理、更新后清理和公开视图投影；暂未证明哪些阶段可以直接合并且不改变兼容行为，也未测得实际代价，**不单独立项**。
- 宿主使用部分 SDK 私有生命周期/安装方法、包网关结果以 `Any` 标注、保留旧 `process()` 包装可作为维护观察；本轮没有据此证明额外业务状态或失效结果，不为了抽象整齐另造框架。

## 验证记录与限制

审查时的隔离探针使用真实编译器和独立临时项目，注入第 2/3 次写入失败并记录残留结果；本机探针文件未随仓库提交，因此这里只保留结果摘要。自定义 `nullcontext` 仅用于结构探针，不代表真实写栅栏验收。`test_sdk_lifecycle.py`、`test_numeric_v2_project_store.py`、`test_numeric_v2.py` 当时共 **99 passed**；作者及包数据均为测试临时目录。

首次运行卡在 tiktoken 编码文件下载，已中止，未计为完成测试。随后使用本机已有且 SHA-256 与 tiktoken 官方加载代码预期值一致的 `o200k_base` 缓存重跑；没有修改仓库或全局配置，没有替换计数算法。

在仓库根目录复现：

```bash
PYTHONDONTWRITEBYTECODE=1 \
.venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/unit/theater_workshop/test_sdk_lifecycle.py \
  tests/unit/theater_workshop/test_numeric_v2_project_store.py \
  tests/unit/theater_workshop/test_numeric_v2.py
```

未运行真实模型、人工体验、完整大图性能基准、冻结发行物或各平台桌面验证。未证明所有生成/评分/支线路线均通过；本轮没有源码修复，因此现有回归通过不等于上述两个问题已解决。

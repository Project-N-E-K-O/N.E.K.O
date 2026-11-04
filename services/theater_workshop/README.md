# 剧本工坊 SDK

无界面的 Numeric v2.2 创作能力，与插件 SDK、小游戏 SDK 独立。主线、续写、节点完善、支线、事实检查、证据复核、文学评分和修订规则来自迁移基线；正式包由 `services/theater/` 的唯一编译器校验。

实现状态：公开SDK（`services.theater_workshop.sdk.__version__ = "0.1.0"`）与本体宿主可用，真实模型生成、安装与演绎已有局部证据；不进入桌面冻结构建（见文末）。共享创作与评改规则按用户要求继续与原独立工作台（InkAI）同步，但SDK运行时不依赖它。设计取舍见[小剧场设计决策记录](../../docs/design/neko-theater-decisions.md) D-30—D-32。

模块职责以[小剧场架构第2节](../../docs/design/neko-theater-architecture.md#2-模块与权限)为准：`host.py`负责本体适配和写入保护，`sdk/workshop.py`负责作者工作流，`sdk/contracts.py`负责输入合同，`sdk/generation/`负责生成与评改，`sdk/numeric_v2*.py`负责作者字段投影、静态分析、支线和项目存储，`sdk/packages.py`负责不可变发布候选。工坊只产出作者项目和 Story Package，不读取玩家 Session，也不参与运行时选路。

SDK 不启动网页或 HTTP 服务，不读取相邻 InkAI 仓库，不修改正常聊天模型。导入模块不打开项目、不取得写者锁、不调用模型。

工坊提示词中的“你”指策划、编剧、摘录或评审模型，剧中人物使用“女主”“玩家”或明确姓名。`cast_names.catgirl_name` 对应女主，`cast_names.player_name` 对应玩家；状态摘录的 `catgirl_state`、`player_state` 分别以对应完整姓名开头，不能用提示词读者的身份代替。公开正文和引文中的人称仍按实际发言者与受话人理解。

## 宿主接入

N.E.K.O 服务进程在需要工坊时显式打开，持有并复用返回实例。项目写入使用当前存储根下的 `theater/workshop/projects/`；同根第二进程会收到 `workshop_root_in_use`。

模型由调用方选择后传入。当前不提供独立的工坊模型设置页面，也不新增隐式默认模型。`model_config` 可取自调用方明确选择的 `ConfigManager.get_model_api_config(...)` 结果；支持 `model`、`base_url`、`api_key`、`provider_type`，另须由调用方补充正整数 `max_input_tokens`。配置作为打开工坊时的快照，不写入作者项目、日志或独立密钥文件。

`max_input_tokens` 是工坊单次请求的完整输入上限，没有默认值。宿主使用本体分词器统计全部消息（包括格式纠正时的原响应）及其 JSON/角色字段，超限在创建客户端前报 `workshop_model_input_budget_exceeded`，不截断剧本、不发送请求。未提供有效上限时报 `workshop_model_input_budget_required`。调用方按所选供应商的上下文容量预留输出及分词差异的余量；这不是供应商原生计数保证，也不改变小剧场演员和复核的既有上限。自行注入 `model_call` 时，其模型适配器负责同等输入预算检查。

```python
import asyncio
from services.theater_workshop.host import open_workshop

async def open_authoring(config_manager, selected_model_config, input_budget):
    return await asyncio.to_thread(
        open_workshop,
        config_manager,
        model_config={**selected_model_config, "max_input_tokens": input_budget},
    )
```

所选模型可使用无密钥的兼容端点；宿主向统一客户端显式传空密钥，不继承进程环境中的凭据。需要鉴权的端点仍由供应商返回认证失败。作者配置中的数值 ID 必须唯一，重复 ID 在保存和编译前的字典投影阶段报 `duplicate_metric_id`，不会静默覆盖较早定义。

不传模型也可以管理、编译现有项目，但生成／评分会报 `workshop_model_required`。同根再次显式传入不同模型会报 `workshop_model_mismatch`，不能在已有请求中间切换模型；先等待 `host.close()` 完成，再用新配置打开。读取同根已有宿主可调用 `open_workshop(config_manager)`。

异步业务使用 `host.call()`，它把同步核心放进工作线程。调用方持有模型配置和宿主生命周期；不要在每次请求结束后关闭其他调用方仍在使用的工坊。

```python
project = await host.call("create_project")
project = await host.call(
    "update_project", project["project_id"],
    base_revision=project["revision"],
    changes={
        "title": "雨后的旧信",
        "setup": {
            "brief": "你回到雨季小镇，与保管旧信的故人重新见面。你们一起核对收信记录，解开当年的误会，并决定今后如何联系。",
            "length_preset": "short",
            "metrics": [],
        },
    },
)
result = await host.call("generate", project["project_id"], base_revision=project["revision"])
project = result["project"]
```

新建生成先确定世界、人物、章节因果与道具框架，再逐幕生成本幕内容和角色规划，最后单独收束结局。框架每幕约100—150字，只作简短规划，完整场景和对白留到逐幕展开。每幕及结局写完后，用一次短调用从本次开场及实际入幕固定原文摘录 `catgirl_state / player_state / environment_state`；普通幕同时摘录猫娘当前顾虑或未决问题，替换写稿时的 `catgirl_situation`，避免另一段描述提前认定行动或重排物理状态。未明确的玩家状态和猫娘顾虑保持未知，不把配角动作移给玩家；不读取前幕状态、整稿或待演目标，关系、认知与发声权限仍保留原规划。相邻主线幕使用已完成前幕与对应出口作为输入，省略进入前幕的旧 `entry_bridge`，避免将其沿用为新的出幕时间或行动。当前桥段只承接出口与接受输入共同保证的移动；尚未取得的接待、许可和结果留在本幕互动中交付。结局只读取世界、人物、基调、结局标识、末幕关系／权限及离幕合同引用的完成事实，不把框架预设结局或末幕待行提议作为完成依据，也不继承末幕入幕姿态、持物、环境陈设和上游延续记录。框架确定后不在局部修订中增删、重排章节。只有全部生成并通过本体编译器检查后才替换项目中的故事。正式编译错误位于节点时仍只重做对应幕；框架字段失败时，携带原框架和编译诊断修正框架，保留已完成的各幕、结局、关系阶段与三方状态。旧版误存为 complete 的失败检查点也可继续修复。

逐幕生成中，后一幕只接收前幕所选完成事实、出口合同、关系与权限，不再把前幕入场姿态、处境、时间或旧桥段当作当前起点。框架先统一时间线，每幕安排新的核心事件；同一次工作的商量、实施及直接回应连贯完成，出口明确邀请下一阶段。此投影不能保证自然语言因果正确，仍须完整实演复验。

当前处境与三方物理状态由必经的开场状态摘录填写；草稿派生字段缺失、主体占位或合成状态超限不会提前阻断摘录。只有当前待摘录幕的合成状态容量检查延后，开场、正文摘要与焦点等其他字段照常检查；摘录后的终稿仍执行完整必填、主体、384-token容量与编译校验，不补造默认状态。同地直接继续允许空桥段，省略独立路程不省略目标开场或玩家授权。

新建逐幕稿将不依赖玩家选择的关键证据、读数或自主开场事件放入entry固定旁白，稳定交付作者原文；玩家决定、谈判结果与未知操作仍留给互动。本模式主动编写这些原文，不要求作者另行逐字指定；开场与原文不重演同一事件。状态摘录读取按序实际展示的entry文本，不读取未触发的condition内容，时间锚也可逐字来自entry原文。末幕通向自然结局时，选入`trigger_fact_ids`且声明`evidence_source=performance`的实际结果，必须同时列入`entry_prerequisite_fact_ids`，由原事实条件保护真实交付后再结束。普通出口只保留作者明确声明、确实不可跳过的许可或交付前提，不把performance完成项一概变成硬锁；前往下一地点属于邀约和转场，不作为来源幕完成条件。只约束当前生成草稿，缺项走原阶段纠正，不自动改写旧包、已完成幕、导入或支线合同，也不新增运行时审核。

逐幕输出示例默认使用普通事实与entry原文触发；编剧按实际主体为角色、NPC或环境结果另加performance来源，为原文展示项另加fixed_narration_id，两种扩展不混用。双方约定与玩家选择不照抄performance来源。跨幕执行分工保留实际选择，可选准备不扩成离幕任务。

每幕三方状态描述开场演完后的同一画面，开场不替玩家接话或执行请求；已知玩家事实也不能预填本幕待玩家决定的结果。单主线、单结局的固定文本只承接各合法选择共同保证的结果，不能在前幕允许不同处置、后幕却默认其中一种已被选中。生成提示与输出示例同时说明主体、时点及既有枚举要求；这些约束仍需模型遵守，不是语义正确性的程序证明。

同场自然结局的 `entry_bridge` 允许空字符串，编译为本体现有的空 `must_deliver` 且不附加桥段文本，不要求为收束编造换场。质量修订计划须列全同一错误实际重复的位置，包含需要同步修订的角色处境；未列字段及明确保留字段仍受原越界保护。

每完成一个阶段即保存检查点；状态摘录前也保存已写正文，摘录失败后同一 `generate` 只续做该幕摘录，不重写正文或已完成前幕。N幕在无需修订时使用2N+3次生成调用（三幕为9次），评分另计。写稿和摘录各最多三次模型调用；摘录沿用原字段与整幕校验，最多600输出tokens，不授予新剧情或权限。调用记录使用 `numeric_v2_story_frame`、`numeric_v2_chapter_generation`、`numeric_v2_ending_generation` 和 `numeric_v2_opening_state_extraction`；失败阶段的修订与用量也计入报告。已有整稿检查点沿用旧续写协议及原三次调用上限，不自动迁移或重新生成。

关系弧的 `progress_opportunity` 随当前幕的 `narrative_focus` 传给演出，明确标为待发生契机；它不是关系提升回执、玩家义务或额外完成门槛。是否产生有限回应仍须结合实际互动、原本人设和原有关系上限；未发生或被拒绝时保留距离与分歧。组合后的叙事焦点也受原384-token字段上限约束。

可选 `character_state.temporal_context` 保存作者入幕时序规划，例如 `{"anchor":"午后","relation_to_previous":"same_day"}`。`anchor` 最多80字，生成时须逐字来自本幕开场或入幕桥段；相对关系只用 `unspecified / same_day / next_day / after_interval`。摘录调用只为时序读取当前桥段，不用它覆盖开场收笔的物理状态。未知时间可在内部摘录中返回空anchor加unspecified，程序省略该未知项，不作为时间事实导出；其他坏形状仍拒绝。写稿时附带的时序会被摘录替换，不为未采用的写稿时序重复改稿。

旧包缺省该对象仍可读取。它不更新Session日期、不改变原确定性场景访问顺序，也不将对白里的未来约定当成现在；Actor、Review和评分读取同一作者规划，实际状态仍以已交付记录为准。现有文本修订权限不扩展至此结构对象。调用次数、600-token摘录额度及重试上限保持；旧三字段摘录检查点可继续当前阶段，不重写已保存正文。

续写时若结构校验未重现错误，首次请求仍带上检查点中已保存的作者／复核诊断；新稿随后按新校验更新问题，不增加调用额度。评分中的结构建议不会自动获得 `optimize_node` 的文本修订权限，也不会自动创建新的生成检查点。

正式编译器定位到具体节点的错误会保存为该幕或结局的修订任务，下一次 `generate` 只重调这些节点；其它正文保留。框架修复按编译错误路径只写回对应的作者字段：前情、双方身份和初始动机使用现有投影关系定位，未报错字段及已完成各幕、结局保持原值；无法定位的全局合同错误仍返回编译错误，不自动猜测该删哪些剧情。修改创作设置会按既有规则使检查点失效；修改期间已有故事不会被未完成的候选覆盖。

每次网络请求不叠加客户端重试，仍为非流式；读取超时按当前阶段输出预算计算（至少120秒，约每40个输出token一秒），连接超时10秒。评分等操作沿用各自输出预算；供应商参数仍由本体统一客户端适配，不下发固定温度值。

姓名从当前本体角色读取；已有候选、节点或支线沿用稿件姓名。小剧场开演时再按当时角色和昵称适配；未告知姓名的剧情仍先称“你”。

角色需要演出的具体计划、期限、条件与选项应自然写入 `narrative / summary`，不能仅保存在 `ordered_goals` 的判定描述中；入幕即应公开的信息放入 `opening_scene`。这些导演信息仍是待演规划，不证明结果已发生，也不应包含其它幕尚未公开的剧情。

## 显式操作

| 操作 | 输入与返回 |
| --- | --- |
| `metric_presets` | 返回可用于作者设置的数值预设，不调用模型 |
| `create_project` / `list_projects` / `get_project` | 创建、列表和完整公开项目视图 |
| `update_project` / `delete_project` | `project_id`、`base_revision`；更新另传 `changes` |
| `allocate_id` | `project_id`、`kind="node" / "route" / "ending"`；返回 `{"id": ...}`，不修改项目或 revision |
| `import_story` | Story Package；创建新的作者项目，不等于导入旧作者项目及报告／检查点 |
| `import_project` | 旧工坊完整项目 JSON 对象；保留原 ID、revision 和作者数据，同编号已存在则拒绝 |
| `generate` | `project_id`、`base_revision`；返回 `project`，实际调用记录见 `usage` |
| `enhance_node` / `optimize_node` | 另传 `node_id`；后者要求当前完整、未过期、已复核的评分报告 |
| `assess_quality` | 返回完整报告所在的 `project`；逐幕事实检查、按需统一证据复核，再文学评分及按需方案复核。评分不改变故事或内容 revision |
| `set_mainline_order` | 另传 `node_ids`；只更新作者主线顺序 |
| `branch_options` / `get_branch_draft` | 分别传节点 ID／候选 ID，返回可用端点条件／候选 |
| `draft_branch_ending` / `draft_branch_path` | 参数见 `sdk/contracts.py` 的两类 Payload；只保存预览候选 |
| `apply_branch` | `project_id`、`draft_id`、`base_revision`；显式应用，重复应用幂等 |
| `compile` | 返回 `project`、不可变 `json_bytes`、`package_hash` |
| `validate` | 对当前编译字节进行进程内严格复验；保存当前 revision/hash 的发布凭据 |
| `assess_runtime_readiness` | 对已编译、复验的当前候选检查目标运行配置；只读、不调用模型，不授予演绎质量通过结论 |
| `export` | 返回不可变 `PublishCandidate`，包含项目 revision、故事 ID、hash 和 `json_bytes` |

生成中的内部编译检查不授予发布凭据；用于导出／安装的 `compile`、`validate` 与发布操作仍须由调用方明确发起。生成成功不会自动评分、文学修订或安装。质量报告应同时检查 `passed` 与问题列表，不能只看总分；静态评分和编译通过都不等于真实转场已验收，联合演绎验证在 SDK 外进行：

事实检查每次只检查一幕，带上全部直接入边来源和出边目标，保留原分支条件、完成合同与原文引用编号；相邻幕只供对照，不把未提供的远处节点当成不存在。主线、支线和结局均须完整覆盖。含 N 个节点的稿件正常使用 N 次事实调用，之后文学评分一次，证据复核和方案复核各按需一次。仅事实输出的 JSON 或字段校验失败时，对该幕增加至多一次 `numeric_v2_fact_review_format_repair`；统一证据复核格式失败也至多增加一次 `numeric_v2_fact_evidence_review_format_repair`。两者均携带完整原响应及准确校验错误，纠正后仍走原严格校验；技术错误和语义不合格不自动重抽。每次调用仍最多 6000 输出 tokens、无客户端重试。再次失败立即停止，`QualityAssessmentError.phase="facts"`，`provider_details.node_id` 指明失败幕，不保存部分评分；重新评分从头检查，不增加评分检查点或自动续审。

事实和证据输出沿用原 JSON 字段。已验证阿里云端点的 `qwen3.8-flash` 工坊首次事实／证据检查使用 2048-token 有界思考，并省略接口 JSON 模式；格式纠正仅处理语法与字段，沿用原 `json_object` 及默认非思考策略。本地仍严格验证完整 JSON、节点覆盖、非空字段、引证及权限。仅接受整份 JSON 外的一层完整代码围栏，不从说明文字中抽取片段或补造截断内容。事实／证据检查的总输出上限仍为含思考在内的 6000 tokens。逐幕写稿采用上文宿主模型策略；文学评分与小剧场演出策略不变。其他模型和端点沿用原 `json_object` 策略及供应商默认思考配置，不增加 Schema 构造或端点探测。格式通过不保证语义正确，未采用逐节点分栏输出。

证据复核只接收原问题、原文引证、节点和待核对字段；原修订方案留在最终报告中，不先喂给证据复核诱导其认可修法。递出待接、单手动作或设备佩戴本身不证明持有者已变化或双手被占用；明确交接、放下后的相反状态仍应报告。道具生命周期规划不统一对应开场时点；开场台词与角色状态相配也不证明玩家授权。事实首审先确认发言主体；开场预写玩家新台词可引用正文和玩家身份举证，不要求另有一对互斥状态。明确已有的往事转述不等于玩家当前新发言。模型可能漏检、误判或返回无效格式，失败不会保存部分质量报告。

```python
pid, rev = project["project_id"], project["revision"]
await host.call("compile", pid, base_revision=rev)
await host.call("validate", pid, base_revision=rev)
# 默认检查产品默认配置；可显式覆盖选项，不读取或修改玩家的设置。
readiness = await host.call("assess_runtime_readiness", pid, base_revision=rev,
                            runtime_options={"review": False})
package = await host.call("export", pid, base_revision=rev)

# 只有这个操作写正式包目录。必须在服务小剧场的同一事件循环调用。
installed = await host.install(pid, base_revision=rev)

# 仅当包已安装而作者回执未保存时，显式核对同一故事及 hash 后补回执。
recovered = await host.install(pid, base_revision=rev, recover_receipt=True)

# 服务停止或明确更换工坊配置时，等待在途操作结束后释放写者所有权。
await host.close()
```

`host.install()` 与小剧场删除／恢复共用生命周期锁，禁止另建事件循环安装。调用取消后仍等在途写入结束才释放锁；取消不保证磁盘动作尚未发生，重新读取项目确认实际状态。

同项目只允许一个长操作。普通读取和编辑仍可进行；编辑推进版本后，旧生成结果被拒绝。维护态、存储根变化、内容 revision 或发布凭据过期均拒绝提交。纯布局调整只有在重新核对包 hash 不变后才承接凭据。

`assess_runtime_readiness` 返回 `neko.workshop.runtime-readiness.v1` 报告，绑定项目 revision、包 hash、目标选项快照（宿主补齐本体默认选项）与配置 hash；不保存报告、不改变导出／安装合同。当前检查范围仅为条件固定旁白：目标配置关闭 `review` 时，离幕必需片段、完成合同引用的片段及其 `after` 依赖无法触发，记为 `blocking` 并给出节点、片段与依赖路径；其他条件片段记为 `warning`。入幕片段不受此检查阻断。

有阻断项时 `status="blocked"`，否则为 `rehearsal_required`；`rehearsal` 始终为 `not_run`，没有“质量通过”状态。开启 Review 后仍需真实演绎验证内容交付、剧情、路线和耗时。直接使用纯 SDK 时必须显式提供包含布尔 `review` 的 `runtime_options`；宿主才负责补齐本体默认选项。检查结果不会自动改写剧本或启用 Review。

## 写入、并发与发布合同

依赖方向：调用方 → `host.py` → `sdk/`；SDK 不反向导入宿主、不初始化 `ConfigManager`、不启动服务。宿主注入模型调用、受保护写入事务、包操作网关和明确的项目根；正式包只由 `services/theater/numeric_v2.py` 编译器裁定，并由 `NumericV2PackageRegistry` 安装。

宿主还通过 `TheaterWorkshop.open(token_counter=...)` 向主线和支线生成校验注入与编译器一致的 token 计数器，校验静默选择的两个字段各不超过 30 tokens。独立调用 `NumericV2Generator` 或 `NumericV2BranchService` 时也应传入该能力；未提供时，静默出口返回校验问题，不反向导入宿主或估算放行。普通出口及旧包读取兼容性保持不变。

**写入保护**

- 作者项目的创建、更新、删除、生成状态、检查点、评分／编译／复验／安装回执都经宿主写入事务提交；保护不放在 HTTP 层，程序直接调用 SDK 也无法绕过。
- 宿主复用全局写栅栏 `cloudsave_writable_transaction`（`utils/cloudsave_runtime/fence.py`）；维护、恢复等禁止写入状态返回现有维护态错误，不写回旧目录、不另建默认目录、不自动重试。
- 模型调用前取得项目 revision、姓名快照和运行根快照，调用期间不持有文件锁；提交前重新核对运行根、可写状态和 revision，根目录变化或稿件已更新时拒绝提交并保留已有项目。
- 安装锁顺序固定：宿主事件循环取得剧本生命周期锁（`numeric_v2_story_session_guard`）→ 工作线程进入可写事务 → 取得 Store 短时锁 → 复验 revision/hash、安装并记录回执。普通项目写入只走后两层；禁止持 Store 锁等待剧本锁，同步文件事务不跨 `await`。

**实例所有权与并发**

- 同一规范化项目根在主服务进程内只有一个 `TheaterWorkshop`／Store，重复获取复用该实例；目录别名按平台规则归一。
- 显式打开可写工坊时用 `portalocker` 取得该根的跨进程排他所有权；第二个写者在任何模型调用或数据变更前得到 `workshop_root_in_use`。锁文件不进作者项目，进程退出由系统释放。纯模块导入不取锁、不建目录。
- 同项目同时最多一个生成、完善、评分、修订或发布操作，重复发起返回处理中，不隐式排队；读取与普通编辑仍可进行，长操作最终提交时重查 revision。
- 关闭时先停止接收新操作，等在途操作完成或明确放弃，再释放所有权。重启发现遗留 `running` 时记为中断，保留故事与检查点，等待调用方显式继续。
- 只保存 `editor`／`stage` 或相同的规范化主线顺序时 revision 仍递增，但原本有效的支线草稿同步更新 `base_revision` 与指纹；正文、设定、标题或主线实际变化使未应用草稿过期，过期草稿不复活。

**发布复验与产物一致性**

| 操作 | 合同 |
| --- | --- |
| 编译 | 对确定的 revision 产生不可变 canonical bytes、非空 hash 与作者 warnings；成功时清除旧复验与安装回执，失败只保存诊断 |
| 复验 | 要求当前 revision 已编译；用正式编译器严格 v2.2 入口重读同一份字节，bytes/hash 必须一致；回执关联当前 revision/hash |
| 导出 | 要求编译与复验成功、revision 正确、hash 非空一致；交付被验证的同一份字节 |
| 安装 | 与导出相同的门禁，再按上面的锁顺序提交；注册表正式校验和“不覆盖同 ID”保留 |

- 两个缺失 hash 都为 `None` 不构成有效凭据；空、失败、过期或不匹配的回执一律拒绝。
- 包字节变化使编译、复验与安装结果失效；只改布局等不影响包字节时，核对 hash 不变后可把凭据关联到新 revision。评分报告按内容指纹独立失效：数值定义或初始值变化、主线顺序实际变化都会清除旧报告与节奏诊断。
- 正式包与作者回执是两份文件，不宣称跨文件事务：包已安装而回执保存失败时明确报告，`recover_receipt=True` 只对同一 story_id 与已验证 hash 补回执，不删除已安装包，不把不同 hash 的已有包当成功。
- 作者静态诊断（不可达、优先级遮蔽、软节奏困难、`route_analysis_unknown`、`route_transition_duplicate`）只进入编译 warnings，不写入包、不阻断导出；`unknown` 不能写成已证明不可达。

## 固定旁白

调用方可在 `changes["story"]` 中编辑某幕的 `story_beat.fixed_narrations`，例如：

```json
{
  "fixed_narrations": [{
    "id": "read_letter",
    "text": "致{{player_name}}：\n愿你一路平安。",
    "trigger": {"type": "condition", "condition": "信封已经实际打开，信纸已能阅读。", "player_handoff_required": false},
    "after": [],
    "required_before_exit": false
  }]
}
```

运行时依据实际演出触发后原样显示，不交给演员改写或朗读。需要确定在入幕公开的重要原文用 `{"type":"entry"}`，不要只依赖目标描述或模型选说；开场及正式换幕由程序插入，关闭 `review` 或设置 `required_before_exit=false` 也会显示，不增加模型调用。终止输入的结局仅支持入幕片段。每幕最多8项、原文合计2000 tokens；超限报错。姓名仅替换两个显式占位符，昵称未披露时使用“你”。`required_before_exit=true` 会在未展示时阻止离幕，普通文案建议保持 `false`。修改后仍需 `compile → validate → export/install`，不能复用旧 revision 的发布凭据。

完整触发、恢复和姓名合同见[架构说明](../../docs/design/neko-theater-architecture.md#34-作者固定旁白)。

若完成条件仅表示这段原文已经展示，可通过 `changes["story"]` 将本幕 `completion_contract.all` 中的对应布尔事实项替换为 `{"fixed_narration_id":"read_letter"}`。运行端直接读取已提交展示记录，模型不再判断此条件；可以与其他 `key/equals` 完成事实混用。确认无其他引用后同步移除冗余事实定义和路线元数据，再重新编译、复验。此字段不改变旁白触发方式，离幕前必显仍用 `required_before_exit`；只表示内容已提交展示，不代表玩家已经阅读。旧包和存档不自动迁移；需使用支持该扩展的本体编译器与运行端。

主线自动生成及续写支持作者完成项 `{"id":"letter_displayed","description":"原文已展示","value_type":"bool","target_value":true,"visibility":"public","fixed_narration_id":"read_letter"}`。带 `fixed_narration_id` 的完成项必须是 `bool`、`target_value=true`、`visibility=public`；引用必须位于同章且不能重复，`exit_plan.trigger_fact_ids` 仍填写 `letter_displayed`。投影自动转换为展示条件，不再创建该项的布尔事实；普通完成项省略 `fixed_narration_id`。支线完成项生成沿用原合同。

新生成支线也必须逐幕提供同形状的 `completion_facts`，对应出幕过渡通过 `trigger_fact_ids` 显式选择决定收束的结果，不再默认最后一项目标足以证明此前动作均已发生。固定原文仍以 `fixed_narration_id` 投影为展示条件；普通完成项投影为有类型的事实。完全缺少新合同的旧保存草稿保留原行为，不自动迁移；部分缺失或损坏的显式合同会报错，不能悄悄退回兼容逻辑。完成合同只约束完成判断与备用邀请，真正必需的选路前提仍由路线 `conditions` 声明；类型与引用校验不能证明剧情因果已充分覆盖。

普通完成项可增加 `evidence_source: "performance"`，导出到对应事实定义后，运行端只接受新正文或已提交演出原文作为证据，拒绝以玩家命令、许可或催促证明角色已经行动。角色与环境实际结果使用此限制；玩家自身选择或动作省略该字段。固定原文展示项继续使用 `fixed_narration_id`，两者不混用。未声明时沿用原来源权限；使用此扩展的包需要支持它的本体编译器与运行端，不自动迁移旧包或存档。

主线生成在投影前检查固定原文数组的形状、触发方式和同幕前置引用；坏片段会将整个 `fixed_narrations` 数组加入定向修订，避免仅改完成项引用却保留损坏资产。结局修订使用仅含 `entry` 的触发示例。仍遵守原三次调用上限；该检查不等于原文与剧情语义、完整编译或文学质量已经通过。

条件触发可显式声明 `player_handoff_required`：只有需要玩家实际递交时填 true，触碰或观察但不改变持有者时填 false；缺省保留旧包保护。false 不授权角色接走物品，也不代替正文复核。模型生成稿仍需检查触发主体、原文与剧情一致性，结构合法不等于演绎质量通过。

## 导入旧作者项目

调用方读取已经停止编辑的原始 `project_*.json` 快照，将完整对象交给 `import_project`。输入必须包含 `_generation_checkpoint` 字段（无检查点时为 `null`）；`get_project` 和旧 HTTP API 的公开视图隐藏了候选正文，不能作为完整迁移输入。

```python
import json
from pathlib import Path

snapshot = json.loads(await asyncio.to_thread(
    Path(selected_project_file).read_text, encoding="utf-8",
))
project = await host.call("import_project", snapshot)
```

接口只写宿主指定的作者目录，不读取、删除或修改来源文件。导入按单项目原子提交；批量调用时逐项核对结果，不承诺跨项目事务。重复 ID 返回 `NumericV2ProjectError("project_already_exists")`，不会覆盖或自动改名。

保留故事、设定、画布、主线顺序、支线草稿、关系／状态弧、道具、评分报告、检查点和原 revision。未完成稿可以导入；格式校验不等于正式包已合格。原 `running` 状态转为 `interrupted`，原检查点和错误信息保留，须显式 `generate` 才继续。

原编译、复验、安装回执移入 `imported_publish_receipts` 保存，当前发布凭据清空。导入后重新调用 `compile`、`validate`，才能 `export` 或 `host.install`。评分及支线仍受原来的内容指纹、revision 和过期规则约束，导入不使旧报告或草稿重新有效。

## 姓名合同

1. 新建生成任务读取当前猫娘名与用户昵称（`主人.昵称`，缺失回退 `主人.档案名`，最后“你”），来源是 `numeric_v2_identity.numeric_v2_authoring_names` 的窄快照。
2. 生成器把完整姓名成对写入 `intro.player_name`／`intro.catgirl_name`，身份描述以对应姓名加中文逗号开头，角色状态以对应姓名明确主体；`owner`、ID、发声状态等结构值继续使用协议枚举。两名必须非空且不同，同名双主角按不歧义合同拒绝。
3. 失败续写沿用候选保存的姓名；向已有项目增加支线、完善、评分或修订沿用项目姓名，避免作者稿混入两组人。
4. 开演时由小剧场按当时的猫娘与昵称投影；程序知道昵称不等于剧情角色已经知道，未披露前称“你”。切换角色可以用新角色开演同一剧本，但不转交另一角色的进行中存档。
5. 替换最长姓名优先、单次替换，避开协议枚举与引用 ID；它不是自然语言实体识别，与普通词同形的姓名和作者自造简称需要人工观察。缺少显式姓名的旧稿须先导入、补齐字段并重新编译发布。

道具生命周期是作者规划。主线 `carry_props` 与支线出口自动加入正式 `must_preserve` 的仅为道具名称和固定用途，持有人、位置及操作结果不自动固化；完整 `key_props.states` 仍保存。需要保留的具体剧情结果由作者显式声明并在演绎时核对，不能把尚未执行的规划当成已发生事实；已安装包须显式修订、复验和发布。

支线生成、支线结局和节点完善保留道具状态的 `node_id` 来源。互斥路线的规划不能合并为同一段已发生历史，当前节点尚待完成的交接也不能提前写进入幕状态。备用邀请和接受按钮只确认下一步安排，不要求重做可能已经完成的本幕动作；桥段、目标开场与角色状态须共同核对，已交付的信息和动作承接结果，不能再次起头。结构编译通过不代表这些自然语言约束已经满足，仍需用真实演绎记录复验。

`transition_contract.must_deliver` 仍必填；无需独立桥段时可显式填 `[]` 并省略 `bridge_scene_narration`，运行时投影为 `bridge_required=false`，允许桥段为空，目标开场仍须交付。自然结局可在合法的一次角色回应后只用目标旁白收束，不要求凑第二次回应；强制目标发声仍保留。非空桥段文案仍要求交付；此声明不增加模型调用，不自动改写旧包，作者修改后须重新编译、复验和发布。

可选布尔值 `transition_contract.accept_input_confirmation_only` 由作者显式声明接受按钮只确认已完成阶段，不承担新的计分、事实交付或回忆任务；省略或 `false` 保持原语义。`true` 要求来源幕有非空 `completion_contract.all`、非空 `fallback_offer` 与 `accept_input`，目标不能是终局，也不能用于 `silent_choice` 或禁止发声的出口。声明不代替运行时的完成、固定旁白交付与邀请回执校验；SDK 导入和完整更新保留它，质量上下文只读展示，自动生成和文学修订不会擅自开启。

可选布尔值 `transition_contract.fixed_entry_only` 表示作者的目标幕 `entry` 原文已完整承担入幕环境和必要反应，且目标 `dialogue_policy` 为 `optional` 或 `forbidden`。主线草稿通过来源 `exit_plan.fixed_entry_only` 声明，支线通过对应 `transition.fixed_entry_only` 声明；编译器校验目标原文与策略。运行时仅在原文尚未展示时启用：来源模型保留当前玩家问题、来源权限和历史，只生成来源回应与桥段，不接收目标开场、状态、认知或原文；程序原样交付目标内容。非终局复用现有推荐补全，可能增加一次模型调用，终局不补推荐。省略、`false` 或已展示原文的再访保持旧流程，不强制所有固定旁白采用此方式。原文只有局部信息、仍依赖额外开场或玩家新选择时不应开启；此声明不改变该幕后续普通回合的发声策略，也不迁移已安装包或存档。

关键入幕可在 `story_beat.entry_player_inputs` 声明 1—3 条作者审核的玩家回复，每条不超过 120 字符；支持纯对白“你愿意再解释一下吗？”、纯动作“（保持安静）”或动作后接对白“（抬眼）你愿意再解释一下吗？”。问话不必为满足格式补动作；动作的主体和前提仍须合法。模板标签、块起始“玩家对白：……”或“【玩家对白】……”等明确格式包装、非字符串及其它坏形状仍拒绝；不会删除包装后猜改正文，正常问话里提及“对白”不受影响。SDK 导入、更新、编译和导出保留该字段，节点完善不会用模型输出覆盖它；自动生成暂不产出。开场及正式目标入幕展示这些回复，普通回合继续动态推荐，终局声明会被拒绝。运行时应用姓名投影并过滤本轮重复项，不补模型调用，也不赋予自动接受邀请或完成任务的权限。旧混合格式继续兼容，新纯对白格式需匹配支持它的运行端。质量评估将其作为待选回复核对边界；格式校验不能代替作者对剧情、信息披露和玩家自主性的审核。

明确选择可以使用路线事实条件 `{"type":"fact_equals","key":"scene:mainline_01:route_decided","value":"山径"}`，放在既有 `conditions.all / any` 中；可与 `metric_compare` 组合。键必须在 `fact_contract.facts` 声明类型和语义描述，值按类型严格相等，缺失不选路。导入、编译和质量评估保留条件；数值可达性分析对此标记 `route_analysis_unknown`，需要实际分别验证选项、改选和回流。现有自动数值支线生成器未扩展为事实分支生成器，不能声称它已自动产出这类分支。

质量检查上下文保留完整 `fact_contract` 和各节点 `completion_contract`，使事实检查、文学评估及证据引用能核对目标值、指定主体与公开结果要求；不授予自动修改合同的权限。完整请求仍经过宿主输入预算，超限明确拒绝，不截断合同。`character_state`、状态弧与 `catgirl_situation` 是作者预期的入幕规划，不是实际游玩记录；首幕可建立合法初始事实，后续玩家动作、持物和决定须依实际历史及当轮授权。状态规划只保留开场计划和合法入路共同支持的结果，不能预写本幕互动或把已交接状态写回进行前。Actor／Review会核对规划与实际记录，但这不是静态语义保证，仍须质量检查及真实排练复验。

运行端复核会保留 Actor 已可见的 `intro.player_identity` 初始玩家身份原文及字段来源，避免仅在合法角色背景中声明的经历缺少复核依据。原文不是本轮玩家行动、完成结果或猫娘／NPC 已知许可；源幕或实际正式目标幕认知受限时继续隐藏，不由复核反馈恢复秘密。问句、引文及尾部否定完整保留并计入现有总预算，超限拒绝发送；不新增剧本字段或事实入账，不将未导出的策划信息自动拼进身份。

新主线生成将必填 `story_protagonist.motivation` 全文附入已有 `catgirl_binding.role_overlay`，与原开场关系说明一同保存，明确标为可随实际经历改变的作者初始动机；它不是玩家义务、完成证明或必须始终坚持的任务。动机不拼进选剧简介的 `intro`，Actor 沿三种入口的既有角色背景读取，认知或记忆受限时继续隐藏；`style_only` 限制现实人格，不禁止剧中动机。Review 使用 `author_initial_role_context` 接收同一可见来源，不能借初始动机抹去实际成长、重提已解决矛盾或突破作者硬边界。

组合后的 `role_overlay` 仍须满足原 384-token 单字段上限，编译超限明确拒绝，不截断完整动机或尾部否定。复核输入纳入原有总预算；新增文本会占用容量，不增加模型调用或提高上限。旧包不补写、不迁移，节点完善、质量修订、分支和 SDK 往返保留已有字段；本改动不自动导出 `world.rules` 等其余策划字段，也不保证模型已正确演出。

关键知识的写稿与检查共用来源规则：在既有背景、摘要／正文、开场及角色合同中区分公开观察、角色知识来源、未公开答案和揭示事件。已有世界规则与证据支持的有限推断、NPC 权限内的确知新答复和低风险即兴仍可保留；当前外观不能反证全部过去，未检查不能当作检查结果，人物猜测不能自动变成对象事实或玩家回忆。未知可以保持开放，无需为每项补固定答案或另建认知台账。规则覆盖五个生成入口和五个检查／修订入口，每请求只发一份；不增加字段、目标、分支、硬锁或调用。此合同不证明所有规则已完整进入运行包，也不代替真实演绎验证。

`completion_contract` 决定完成提示与备用邀请，不能替代路线硬条件。确实不允许跳过的前提（例如管事已核验并放行）还应写入出口的 `conditions`，否则玩家主动请求换幕仍可能走该出口。事实描述须明确完成主体和可见结果，不能把同伴的佐证、玩家的请求或条件句当作第三方已经批准。

出口若依赖本幕查得的具体人物或地点，须在本幕叙事中说明由谁、经何种可演事件揭示哪条线索，不能只在下一幕写出答案。所选完成事实的描述与目标值应对应同一对象；“得到任意人名”不足以支持拜访固定人物。只补已有必要交付，不把待确认身份提前设成已知，也不为普通移动增加完成锁。运行端仍须按实际演出审批，作者规划不自证发生。

新主线生成的 `exit_plan`、新支线每幕出幕的过渡可声明可选 `entry_prerequisite_fact_ids`，例如 `{"trigger_fact_ids":["parcel_found"],"entry_prerequisite_fact_ids":["keeper_permitted"]}`。两个 ID 都须在本来源幕 `completion_facts` 声明，后者还必须是实际 typed fact，不能引用 `fixed_narration_id` 展示项；出口投影为对应 `fact_equals`，值来自该项 `target_value`。两组选择独立：完成项决定收束，必要前提决定能否选择路线，满足前提也不替代玩家换幕授权。未知／重复／错误类型引用报错，不解析自然语言状态推断许可。

缺省或 `[]` 不增加路线条件，旧包和旧草稿不自动迁移；事实条件的有效性仍依赖真实发生的事实与后续撤回。当前支线向导仍要求其来源幕只有一个无条件出口，因此带上述前提的主线幕暂不能作为新增分岔来源，会按既有 `branch_source_unconditional_exit_required` 拒绝，而不丢弃前提。该能力提供必要事实门槛，未把原数值支线生成器扩展为任意事实分岔生成器。

## 存储布局与维护分工

```text
<app_docs_dir>/theater/
├── workshop/projects/        # 作者项目（本 SDK）
└── numeric_v2/
    ├── packages/             # 已安装正式包（小剧场）
    ├── sessions/             # 演绎存档（小剧场）
    └── story_sessions.json   # 恢复索引（小剧场）
```

- 根目录只在显式打开工坊时由宿主按 `ConfigManager.app_docs_dir` 当前存储策略解析，并与写者所有权绑定；存储根变化后停止旧根提交并重新打开，不缓存为永久地址。安装是复制编译产物，编辑器不直接修改正在演绎的包。
- 模型：`open_workshop(config_manager, model_config=...)` 的配置作为实例快照；调用方必须提供 `max_input_tokens`。宿主通过本体统一客户端发请求，单次网络请求客户端重试为 0，保留输出预算与 JSON 校验；不继承旧工坊固定温度；作者端有界思考策略见下一条，不传递给实时演出或判定。逐幕主线的每个阶段最多三次模型调用，额度耗尽保留该阶段检查点等待显式续写；既有整稿生成保留原三次上限。小剧场运行端的输入上限与模型分工不受工坊配置影响。
- 百炼兼容端点的 `qwen3.8-flash` 在框架、单幕与结局写稿时使用已有思考模式，上限为2048 tokens且不超过原输出额度的一半；省略供应商JSON模式，仍由原解析器和本体校验完整JSON，不截取半段或补造截断字段，不增加调用或重试次数。开场状态摘录、旧整稿生成、其他模型与端点沿用原策略，实时演出与判定不受影响。
- 维护分工：`services/theater_workshop/` 是本体创作能力的维护入口；原独立工作台（InkAI）的网页、Flask API、模型设置与跨仓 CLI 在原仓库维护。两端共享创作、事实、证据、文学、方案复核与修订规则须同步：改动时核对两端 `generation/numeric_v2.py`、`generation/runtime_rules.py` 及实际消费者并各自运行测试。两端使用独立作者目录，SDK 不从相邻仓库动态导入，也不修改 `sys.path`。

## 错误与验证

输入校验抛 `pydantic.ValidationError`；版本冲突抛 `NumericV2RevisionConflictError`（含当前 `project`）；项目错误、模型生成错误、评分错误、包错误保留各自类型及稳定错误码。`WorkshopError.code` 表示宿主或生命周期错误。失败阶段已有用量记录可从异常的 `usage` 读取；没有供应商 usage 的尝试标为未报告，不冒充零消耗。

SDK 可单独注入 `model_call` 返回 `ModelReply`／文本／`LLMCallFailure`，用于隔离验证。生产使用宿主提供的写栅栏，不能把测试中的空事务当成正式配置。

```bash
.venv/bin/python -m pytest -q tests/unit/theater_workshop
```

SDK 目前没有界面或 HTTP 调用方，暂不进入 Nuitka 冻结发行物：两个桌面构建工作流在包含 `services` 时显式排除 `services.theater_workshop`，wheel／sdist 也排除 `services/theater_workshop`，`launcher.py` 也不引用它。`services.theater_workshop.release_smoke.run` 仍可在源码环境运行固定模型的失败续写、编译、复验、导出、安装、引擎加载、重开和维护态拒写检查；待工坊有正式入口时再恢复冻结包含与发行检查。

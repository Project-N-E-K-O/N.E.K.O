# 剧本工坊 SDK

无界面的 Numeric v2.2 创作能力，与插件 SDK、小游戏 SDK 独立。主线、续写、节点完善、支线、事实检查、证据复核、文学评分和修订规则来自迁移基线；正式包由 `services/theater/` 的唯一编译器校验。

实现状态：公开SDK（`theater_workshop.sdk.__version__ = "0.1.0"`）与本体宿主可用，真实模型生成、安装与演绎已有局部证据；不进入桌面冻结构建（见文末）。共享创作与评改规则按用户要求继续与原独立工作台（InkAI）同步，但SDK运行时不依赖它。设计取舍见[小剧场设计决策记录](../docs/design/neko-theater-decisions.md) D-30—D-32。

模块职责以[小剧场架构第2节](../docs/design/neko-theater-architecture.md#2-模块与权限)为准：`host.py`负责本体适配和写入保护，`sdk/workshop.py`负责作者工作流，`sdk/contracts.py`负责输入合同，`sdk/generation/`负责生成与评改，`sdk/numeric_v2*.py`负责作者字段投影、静态分析、支线和项目存储，`sdk/packages.py`负责不可变发布候选。工坊只产出作者项目和 Story Package，不读取玩家 Session，也不参与运行时选路。

SDK 不启动网页或 HTTP 服务，不读取相邻 InkAI 仓库，不修改正常聊天模型。导入模块不打开项目、不取得写者锁、不调用模型。

## 宿主接入

N.E.K.O 服务进程在需要工坊时显式打开，持有并复用返回实例。项目写入使用当前存储根下的 `theater/workshop/projects/`；同根第二进程会收到 `workshop_root_in_use`。

模型由调用方选择后传入。当前不提供独立的工坊模型设置页面，也不新增隐式默认模型。`model_config` 可取自调用方明确选择的 `ConfigManager.get_model_api_config(...)` 结果；支持 `model`、`base_url`、`api_key`、`provider_type`，另须由调用方补充正整数 `max_input_tokens`。配置作为打开工坊时的快照，不写入作者项目、日志或独立密钥文件。

`max_input_tokens` 是工坊单次请求的完整输入上限，没有默认值。宿主使用本体分词器统计全部消息及其 JSON/角色字段，超限在创建客户端前报 `workshop_model_input_budget_exceeded`，不截断剧本、不发送请求。未提供有效上限时报 `workshop_model_input_budget_required`。调用方按所选供应商的上下文容量预留输出及分词差异的余量；这不是供应商原生计数保证，也不改变小剧场演员和复核的既有上限。自行注入 `model_call` 时，其模型适配器负责同等输入预算检查。

```python
import asyncio
from theater_workshop.host import open_workshop

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

生成失败的候选及姓名保存在检查点中。调用同一 `generate` 方法显式续写；不会因重启、查询项目或打开工坊而自动重发模型请求。现行主线最多三次模型调用的恢复规则保留；每次网络请求不叠加客户端重试；请求为非流式，读取超时按输出预算放宽（至少 120 秒，约每 40 个输出 token 一秒，如 16000 token 为 400 秒），连接超时 10 秒。节点、评分等操作沿用各自输出预算。用户选择不同供应商后，请求参数遵守本体统一客户端的供应商适配：保留预算与 JSON 校验，不下发旧工坊的固定温度值。

姓名从当前本体角色读取；已有候选、节点或支线沿用稿件姓名。小剧场开演时再按当时角色和昵称适配；未告知姓名的剧情仍先称“你”。

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
| `assess_quality` | 返回完整报告所在的 `project`；先事实检查、按需证据复核，再文学评分及按需方案复核。评分不改变故事或内容 revision |
| `set_mainline_order` | 另传 `node_ids`；只更新作者主线顺序 |
| `branch_options` / `get_branch_draft` | 分别传节点 ID／候选 ID，返回可用端点条件／候选 |
| `draft_branch_ending` / `draft_branch_path` | 参数见 `sdk/contracts.py` 的两类 Payload；只保存预览候选 |
| `apply_branch` | `project_id`、`draft_id`、`base_revision`；显式应用，重复应用幂等 |
| `compile` | 返回 `project`、不可变 `json_bytes`、`package_hash` |
| `validate` | 对当前编译字节进行进程内严格复验；保存当前 revision/hash 的发布凭据 |
| `export` | 返回不可变 `PublishCandidate`，包含项目 revision、故事 ID、hash 和 `json_bytes` |

编译、复验、导出／安装必须由调用方明确发起。生成成功不会自动评分、修订或安装：

```python
pid, rev = project["project_id"], project["revision"]
await host.call("compile", pid, base_revision=rev)
await host.call("validate", pid, base_revision=rev)
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

## 写入、并发与发布合同

依赖方向：调用方 → `host.py` → `sdk/`；SDK 不反向导入宿主、不初始化 `ConfigManager`、不启动服务。宿主注入模型调用、受保护写入事务、包操作网关和明确的项目根；正式包只由 `services/theater/numeric_v2.py` 编译器裁定，并由 `NumericV2PackageRegistry` 安装。

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

运行时依据实际演出触发后原样显示，不交给演员改写或朗读。入幕即展示用 `{"type":"entry"}`；终止输入的结局仅支持入幕片段。每幕最多8项、原文合计2000 tokens；超限报错。姓名仅替换两个显式占位符，昵称未披露时使用“你”。`required_before_exit=true` 会在未展示时阻止离幕，普通文案建议保持 `false`。修改后仍需 `compile → validate → export/install`，不能复用旧 revision 的发布凭据。

完整触发、恢复和姓名合同见[架构说明](../docs/design/neko-theater-architecture.md#34-作者固定旁白)。

若完成条件仅表示这段原文已经展示，可通过 `changes["story"]` 将本幕 `completion_contract.all` 中的对应布尔事实项替换为 `{"fixed_narration_id":"read_letter"}`。运行端直接读取已提交展示记录，模型不再判断此条件；可以与其他 `key/equals` 完成事实混用。确认无其他引用后同步移除冗余事实定义和路线元数据，再重新编译、复验。此字段不改变旁白触发方式，离幕前必显仍用 `required_before_exit`；只表示内容已提交展示，不代表玩家已经阅读。旧包和存档不自动迁移；需使用支持该扩展的本体编译器与运行端。

主线自动生成及续写支持作者完成项 `{"id":"letter_displayed","description":"原文已展示","value_type":"bool","target_value":true,"visibility":"public","fixed_narration_id":"read_letter"}`。带 `fixed_narration_id` 的完成项必须是 `bool`、`target_value=true`、`visibility=public`；引用必须位于同章且不能重复，`exit_plan.trigger_fact_ids` 仍填写 `letter_displayed`。投影自动转换为展示条件，不再创建该项的布尔事实；普通完成项省略 `fixed_narration_id`。支线完成项生成沿用原合同。

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
- 模型：`open_workshop(config_manager, model_config=...)` 的配置作为实例快照；调用方必须提供 `max_input_tokens`。宿主通过本体统一客户端发请求，单次网络请求客户端重试为 0，保留输出预算与 JSON 校验；不透传旧工坊固定温度或 `thinking` 参数。主线生成最多三次模型调用，额度耗尽保留候选等待显式续写。小剧场运行端的输入上限与模型分工不受工坊配置影响。
- 维护分工：`theater_workshop/` 是本体创作能力的维护入口；原独立工作台（InkAI）的网页、Flask API、模型设置与跨仓 CLI 在原仓库维护。两端共享创作、事实、证据、文学、方案复核与修订规则须同步：改动时核对两端 `generation/numeric_v2.py`、`generation/runtime_rules.py` 及实际消费者并各自运行测试。两端使用独立作者目录，SDK 不从相邻仓库动态导入，也不修改 `sys.path`。

## 错误与验证

输入校验抛 `pydantic.ValidationError`；版本冲突抛 `NumericV2RevisionConflictError`（含当前 `project`）；项目错误、模型生成错误、评分错误、包错误保留各自类型及稳定错误码。`WorkshopError.code` 表示宿主或生命周期错误。失败阶段已有用量记录可从异常的 `usage` 读取；没有供应商 usage 的尝试标为未报告，不冒充零消耗。

SDK 可单独注入 `model_call` 返回 `ModelReply`／文本／`LLMCallFailure`，用于隔离验证。生产使用宿主提供的写栅栏，不能把测试中的空事务当成正式配置。

```bash
.venv/bin/python -m pytest -q tests/unit/theater_workshop
```

SDK 目前没有界面或 HTTP 调用方，暂不进入 Nuitka 冻结发行物：两个桌面构建工作流不包含 `theater_workshop`，`launcher.py` 也不引用它（Nuitka 会跟随静态 import，入口保留就等于打包）。`theater_workshop.release_smoke.run` 仍可在源码环境运行固定模型的失败续写、编译、复验、导出、安装、引擎加载、重开和维护态拒写检查；待工坊有正式入口时再恢复冻结包含与发行检查。

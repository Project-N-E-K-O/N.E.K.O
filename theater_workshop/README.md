# 剧本工坊 SDK

无界面的 Numeric v2.2 创作能力，与插件 SDK、小游戏 SDK 独立。主线、续写、节点完善、支线、事实检查、证据复核、文学评分和修订规则来自迁移基线；正式包由 `services/theater/` 的唯一编译器校验。

实现状态：公开SDK与本体宿主已经可用，真实模型生成、安装与演绎已有局部证据；各平台冻结发行物仍待验证。完整测试与质量边界见文末迁移说明。共享创作与评改规则按用户要求继续同步InkAI，但SDK运行时不依赖它。

SDK 不启动网页或 HTTP 服务，不读取相邻 InkAI 仓库，不修改正常聊天模型。导入模块不打开项目、不取得写者锁、不调用模型。

## 宿主接入

N.E.K.O 服务进程在需要工坊时显式打开，持有并复用返回实例。项目写入使用当前存储根下的 `theater/workshop/projects/`；同根第二进程会收到 `workshop_root_in_use`。

模型由调用方选择后传入。当前不提供独立的工坊模型设置页面，也不新增隐式默认模型。`model_config` 可取自调用方明确选择的 `ConfigManager.get_model_api_config(...)` 结果；支持 `model`、`base_url`、`api_key`、`provider_type`。配置作为打开工坊时的快照，不写入作者项目、日志或独立密钥文件。

```python
import asyncio
from theater_workshop.host import open_workshop

async def open_authoring(config_manager, selected_model_config):
    return await asyncio.to_thread(
        open_workshop,
        config_manager,
        model_config=selected_model_config,
    )
```

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

生成失败的候选及姓名保存在检查点中。调用同一 `generate` 方法显式续写；不会因重启、查询项目或打开工坊而自动重发模型请求。现行主线最多三次模型调用的恢复规则保留；每次网络请求不叠加客户端重试，超时为 120 秒。节点、评分等操作沿用各自输出预算。用户选择不同供应商后，请求参数遵守本体统一客户端的供应商适配：保留预算与 JSON 校验，不下发旧工坊的固定温度值。

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

## 错误与验证

输入校验抛 `pydantic.ValidationError`；版本冲突抛 `NumericV2RevisionConflictError`（含当前 `project`）；项目错误、模型生成错误、评分错误、包错误保留各自类型及稳定错误码。`WorkshopError.code` 表示宿主或生命周期错误。失败阶段已有用量记录可从异常的 `usage` 读取；没有供应商 usage 的尝试标为未报告，不冒充零消耗。

SDK 可单独注入 `model_call` 返回 `ModelReply`／文本／`LLMCallFailure`，用于隔离验证。生产使用宿主提供的写栅栏，不能把测试中的空事务当成正式配置。

```bash
.venv/bin/python -m pytest -q tests/unit/theater_workshop
```

正式 Nuitka 工作流已加入 `--include-package=theater_workshop` 和 `scripts/check_theater_workshop_release.py`。该检查通过发行二进制运行固定模型的失败续写、编译、复验、导出、安装、引擎加载、重开、维护态拒写、ID 分配和完整作者项目导入后的重新复验；不会退回源码解释器完成被测业务。

完整边界、迁移基线及已验证／待验证范围见 [迁移文档](../docs/design/neko-theater-workshop-sdk-migration.md)。

# 可回滚的本地插件升级

N.E.K.O. 将再次导入同一个逻辑插件视为“替换安装”，不再生成 `my_plugin_1` 之类的副本目录。安装包中 `payload/plugins/` 下的目录名必须与 `[plugin].id` 完全一致。版本号只是展示和发布元数据；两个版本号相同但内容不同的包，仍然是两份不同的替换输入。

本文是本地插件替换流程的维护者权威文档。具体插件的迁移说明应链接本文，不要重复维护事务规则。

## 用户流程

插件管理器在修改文件前必须先请求安装计划。计划只有三种动作：

| 动作 | 含义 | 用户看到的结果 |
| --- | --- | --- |
| `install` | 目标身份和目录均未被现有插件占用。 | 直接安装。 |
| `upgrade` | 恰好有一个已安装插件与包内身份和目标目录匹配。 | 展示当前版本和目标版本，要求明确确认。 |
| `blocked` | 无法在不产生歧义或风险的情况下替换。 | 修改安装目录前停止。 |

升级确认令牌由插件包内容、目标路径和现有目标目录的完整递归快照共同生成。快照包括相对路径、普通文件内容和符号链接目标，但不会沿链接读取目录外内容。执行安装前，服务器会重新生成计划；确认后包文件或现有目标中的任意文件发生变化时，令牌都会失效，替换会被拒绝。

## 安装所有权

安装计划会标记三种目标所有权：

| 所有权 | 含义 | 替换行为 |
| --- | --- | --- |
| `new` | 目标不存在。 | 可以创建新的受管用户安装。 |
| `managed` | 目标已登记在 N.E.K.O. 的安装台账中。 | 按普通替换流程要求确认。 |
| `unmanaged` | 目录存在，但 N.E.K.O. 没有对应安装声明，通常是手工复制进去的开发工作副本。 | 界面必须明确警告源码、vendor 依赖和资源将被替换；完整目录快照会阻止确认框打开后发生的未确认修改。 |

不要把已安装应用的用户插件目录当作插件仓库的唯一开发工作副本。请在源码 checkout 或独立仓库中开发，构建 `.neko-plugin` 后再导入测试。系统允许在明确确认后覆盖 `unmanaged` 目录，但这是一项破坏性的“转为受管安装”操作。

同一个逻辑插件 ID 只会选择一个候选运行。受管用户版可以覆盖内置候选；系统不会通过给目录追加后缀来制造第二个可执行插件。删除用户安装时会移除其代码和安装声明；如果存在内置候选，则回到内置版。随程序发布的内置文件不会被物理删除。

代码与用户状态使用不同目录：内置代码位于 `plugin/plugins/<id>/`，市场或导入代码位于 `plugin-installations/<id>/`，用户状态位于 `<storage-root>/plugins/<id>/config|data|cache`。Resolver 切换内置版和受管版时只切换代码来源，不改变状态目录；删除受管版也不会删除外部用户状态。“清除用户数据”必须是另一项明确操作。

旧插件可能仍把状态写进代码目录下的 `config/`、`data/`、`cache/`。为兼容这类插件，N.E.K.O. 会在宿主的 `plugin-installations.json` 中记录包自带文件的路径和哈希：未被用户修改的包文件随新包更新或删除，插件运行后新增或修改的文件作为兼容残留继续保留；如果用户手改过包文件，而新包也要写同一路径，安装会以 `PLUGIN_PACKAGE_STATE_CONFLICT` 停止并恢复旧版。新插件不得依赖这层兼容行为，应把只读素材放进 `resources/`，并通过 SDK 把可变状态写入外部用户目录。

这份归属清单不会写进插件源码目录，也不要求作者升级旧包格式。它只能区分文件归属，不是通用的数据 schema 迁移事务；插件仍不能假设 N.E.K.O. 已提供 `pre_upgrade`、`migrate` 或业务数据回滚钩子。

## 升级事务

确认升级后，服务器按以下顺序执行：

1. 判断插件当前是否正在运行。
2. 必要时停止旧插件。
3. 将旧插件目录和 package profile 目录移动到带时间戳的备份位置。
4. 把新包安装到原可执行目录。
5. 校验新安装的插件 ID 和目录仍与已确认计划一致。
6. 将需要保留的 profile 内容合并回新 profile。
7. 如果升级前插件正在运行，则重新启动。
8. 新安装验证并启动成功后清理备份。

有效升级完成后的备份清理失败只记录警告，不会回滚已经可用的新版本。

## 失败与回滚

备份、安装、校验、profile 保留或重启阶段失败时，事务会按相反顺序恢复所有目标。升级前正在运行的插件会尽量从恢复后的旧版本重新启动。

API 将升级失败与回滚状态分开报告：

| `rollback_status` | 含义 |
| --- | --- |
| `not_needed` | 升级成功，没有执行回滚。 |
| `completed` | 升级失败，旧插件和 profile 已恢复。 |
| `incomplete` | 升级失败，且至少一个目录或运行状态未能恢复，需要人工检查。 |

插件管理器不得把 `incomplete` 显示为“恢复成功”。

## 会被阻止的情况

遇到以下情况，安装计划必须保守拒绝：

- bundle 中包含已安装插件或发生可执行目录冲突；
- `[plugin].previous_ids` 指向的旧插件仍然存在；
- 目标目录中的插件 ID 与包内 ID 不同；
- `payload/plugins/` 下的目录名与其 `plugin.toml` 中的 `[plugin].id` 不同；
- 同一个插件 ID 出现在多个目录；
- 单插件包没有且仅有一个插件；
- 可执行目录名、package ID 或自定义根目录逃逸允许范围。

存在冲突的 bundle 不进行整组事务升级，应使用单插件包逐个升级。

## 稳定身份与插件改名

每个可执行插件都应保持以下值一致：

```toml
[plugin]
id = "my_plugin"
entry = "plugin.plugins.my_plugin:MyPlugin"
previous_ids = ["old_plugin_id"] # 可选的旧身份冲突保护
```

安装目录和安装包中的 payload 目录都必须为 `my_plugin`。`previous_ids` 只用于安装时阻止新旧身份并存；它不是运行时别名，也不会自动迁移或删除旧数据。

市场安装还会把市场声明的插件 ID、版本与包内 `plugin.toml` 绑定。ID 或版本不一致时，会在正式落盘和激活前拒绝。客户端提交的冲突策略不能授权创建改名后的可执行副本。

## API 契约

- `POST /plugin-cli/install-plan` 返回动作、插件身份、版本、阻止原因、旧 ID 和确认令牌。
- `POST /plugin-cli/install` 可直接执行首次安装；升级时必须提交 `confirm_upgrade=true` 和当前 `confirmation_token`。
- 被阻止的计划返回 `PLUGIN_INSTALL_BLOCKED`，且不修改文件。
- 缺少确认返回 `PLUGIN_UPGRADE_CONFIRMATION_REQUIRED`。
- 确认令牌过期返回 `PLUGIN_UPGRADE_PLAN_CHANGED`。
- 用户修改过随包状态文件且新版也要写入同一路径时，返回 `PLUGIN_PACKAGE_STATE_CONFLICT`，旧版会被恢复。
- 其他升级事务失败返回 `PLUGIN_UPGRADE_ROLLED_BACK`，详情包含失败 `stage` 与 `rollback_status`。

两个接口都要求管理员权限。面向用户的错误不得暴露包内容、配置值、凭据、确认令牌或不受限的本地绝对路径。

## 维护入口

| 职责 | 权威实现 |
| --- | --- |
| 安装计划分类与确认令牌 | `plugin/server/application/plugin_cli/install_plan.py` |
| 事务、备份、恢复和重启 | `plugin/server/application/plugins/upgrade_support.py` |
| 安装编排与路径策略 | `plugin/server/application/plugin_cli/service.py` |
| HTTP 请求与响应模型 | `plugin/server/routes/plugin_cli.py` |
| 插件管理器确认流程与结果提示 | `frontend/plugin-manager/src/composables/usePackageManager.ts` |

## 验证要求

修改此流程时至少覆盖：

- 首次安装、确认升级、取消确认和过期令牌拒绝；
- 包、目录、旧 ID、重复安装和 bundle 冲突；
- 备份、安装、校验、profile 保留、重启和清理阶段失败；
- 完整回滚与不完整回滚提示；
- 插件及 profile 恢复，包括升级前正在运行的插件；
- 插件管理器交互与多语言 key 一致性。

使用 `plugin/tests/unit/server/` 下的相关后端测试、CLI 工作流集成测试、插件管理器 Vitest、TypeScript 类型检查和前端 i18n 检查进行验证。

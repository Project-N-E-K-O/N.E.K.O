# 可回滚的本地插件升级

N.E.K.O. 将再次导入同一个可执行插件视为“升级”，不再生成 `my_plugin_1` 之类的副本目录。可执行插件目录必须与 `[plugin].entry` 引用的 Python 包保持一致；只给目录追加后缀会导致注册器无法加载插件。

本文是本地插件替换流程的维护者权威文档。具体插件的迁移说明应链接本文，不要重复维护事务规则。

## 用户流程

插件管理器在修改文件前必须先请求安装计划。计划只有三种动作：

| 动作 | 含义 | 用户看到的结果 |
| --- | --- | --- |
| `install` | 目标身份和目录均未被现有插件占用。 | 直接安装。 |
| `upgrade` | 恰好有一个已安装插件与包内身份和目标目录匹配。 | 展示当前版本和目标版本，要求明确确认。 |
| `blocked` | 无法在不产生歧义或风险的情况下替换。 | 修改安装目录前停止。 |

升级确认令牌由插件包内容、目标路径和当前 `plugin.toml` 共同生成。执行安装前，服务器会重新生成计划；确认后包文件或已安装目标发生变化时，令牌会失效，升级会被拒绝。

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

安装目录必须为 `my_plugin`。`previous_ids` 只用于安装时阻止新旧身份并存；它不是运行时别名，也不会自动迁移或删除旧数据。

## API 契约

- `POST /plugin-cli/install-plan` 返回动作、插件身份、版本、阻止原因、旧 ID 和确认令牌。
- `POST /plugin-cli/install` 可直接执行首次安装；升级时必须提交 `confirm_upgrade=true` 和当前 `confirmation_token`。
- 被阻止的计划返回 `PLUGIN_INSTALL_BLOCKED`，且不修改文件。
- 缺少确认返回 `PLUGIN_UPGRADE_CONFIRMATION_REQUIRED`。
- 确认令牌过期返回 `PLUGIN_UPGRADE_PLAN_CHANGED`。
- 升级事务失败返回 `PLUGIN_UPGRADE_ROLLED_BACK`，详情包含失败 `stage` 与 `rollback_status`。

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

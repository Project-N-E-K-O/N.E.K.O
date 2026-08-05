# N.E.K.O 插件替换与用户状态隔离：2–3 天重构提案

> **状态：Proposal（短周期实施稿）**
>
> **日期：2026-08-05**
>
> 本文只描述两三天内应该完成的最小重构。当前代码和测试仍是现行契约；本文不要求实现完整包管理器。

## 1. 最高优先级

两三天内解决当前最不顺眼、最容易继续扩散的三个问题：

1. 插件代码和用户状态混在同一目录、同一个 `plugin.toml` 语义中。
2. Package Manager、插件列表导入和 Market 没有共用同一个替换实现。
3. “升级”被不同入口理解成不同操作，旧版本包甚至可能被版本策略阻止。

本轮完成后的用户结果必须是：

> 用户可以安装任意版本的 `.neko-plugin` 替换同 ID 插件；插件代码和 vendor 更新，用户配置与数据仍然存在；替换失败时旧代码恢复。

这不是 Pacman，也不尝试一次解决依赖锁、签名、历史版本仓库、bundle 滚动升级或通用数据迁移。

## 2. 五个决定

### 2.1 插件目录直接就是安装载荷

保持现有物理布局：

```text
<plugin-root>/
└── lifekit/
    ├── plugin.toml
    ├── config.example.toml   # 新插件可提供
    ├── __init__.py
    ├── vendor/
    ├── ui/
    └── locales/
```

`lifekit/` 整个目录就是 Installed Payload。Python 源码继续直接从这里导入运行。

本轮不增加：

```text
lifekit/installed/
```

物理嵌套会破坏现有 import、静态资源、vendor 和第三方插件路径假设，没有必要。

### 2.2 用户状态放到外部插件状态目录

复用当前 runtime storage root：

```text
<runtime-storage-root>/
└── plugins/
    └── lifekit/
        ├── config/
        │   └── plugin.toml
        ├── data/
        └── cache/
```

所有权只有四条：

| 区域 | 所有者 | 替换插件时 |
| --- | --- | --- |
| Installed Payload | Host/插件包 | 整体替换 |
| Config | 用户/插件 | 不覆盖 |
| Data | 用户/插件 | 不触碰 |
| Cache | 插件 | 本轮不触碰 |

插件可以在 Config、Data、Cache 内使用任意文件格式和目录结构。Host 不理解业务数据。

### 2.3 插件清单与用户配置二分

新插件建议提供两个文件。

`plugin.toml` 只回答“插件是谁”：

```toml
[plugin]
id = "lifekit"
name = "生活助手"
version = "0.2.0"
entry = "plugin.plugins.lifekit:LifeKitPlugin"

[plugin.sdk]
supported = ">=0.1.0,<0.3.0"

[plugin.store]
enabled = true
```

以下内容属于清单：

- ID、名称、版本、入口；
- 作者和描述；
- SDK 与插件依赖；
- UI、i18n 和静态资源声明；
- `[plugin.store]` 等插件能力声明。

`config.example.toml` 只回答“首次默认怎样运行”：

```toml
[plugin_runtime]
enabled = true
auto_start = true

[lifekit]
provider = "open-meteo"
default_city = ""
forecast_days = 3
```

以下内容属于用户配置：

- `[plugin_runtime]`；
- API Key、城市、语言等设置；
- 插件自定义的任意业务表。

首次运行时，如果外部 Config 不存在：

```text
存在 config.example.toml
    → 整份复制为外部 config/plugin.toml

不存在 config.example.toml（历史插件）
    → 整份复制安装目录的 plugin.toml
```

历史配置不做拆表、不重新序列化、不丢注释。外部旧文件即使包含 `[plugin]` 也没有问题，因为 Host 始终忽略外部 `[plugin]`，插件身份只读安装目录的清单。

不增加包格式 `schema_version = 2`。是否存在 `config.example.toml` 已足够区分新旧布局。

### 2.4 插件继续读取一份有效配置

为了不增加插件开发者学习成本，Host 内部组合：

```python
effective = load_external_config()
effective["plugin"] = load_installed_manifest()["plugin"]
```

现有插件继续使用：

```python
cfg = await self.config.dump()
```

规则：

- `config.dump()` 返回 Effective Config；
- `config.update()` 只写外部 Config；
- `[plugin]` 永远来自 Installed Payload；
- `[plugin_runtime]` 和业务配置来自外部 Config；
- 本轮不实现通用配置迁移 hook；插件作者可以在自己的 startup 中迁移。

现有 profiles 暂时保持现状，作为兼容输入继续参与当前配置流程。本轮不重新设计 profiles，也不让它阻塞用户配置外置。

### 2.5 任意版本都走同一个替换事务

版本号只用于 UI 展示，不决定是否允许安装：

```text
目标插件不存在 → install
目标插件已存在且 ID 一致 → replace
```

前端可以显示：

- 目标版本较新：更新；
- 目标版本相同：重新安装；
- 目标版本较旧：安装旧版本。

底层不区分 upgrade、downgrade、reinstall，全部调用同一个 replace 实现。不同 hash、预发布版本和非标准版本号都不额外制造事务分支。

用户要回到旧版本，直接重新安装旧 `.neko-plugin`。本轮不建设历史版本仓库和手动 rollback 页面。

## 3. 最小路径 Interface

引入一个内部 `PluginLayout`，只解决路径猜测：

```python
@dataclass(frozen=True)
class PluginLayout:
    plugin_id: str
    installed_dir: Path
    manifest_path: Path
    config_path: Path
    data_dir: Path
    cache_dir: Path

    @property
    def vendor_dir(self) -> Path:
        return self.installed_dir / "vendor"
```

唯一入口：

```python
layout = resolve_plugin_layout(plugin_id, installed_dir)
```

本轮要停止新增以下代码：

```python
config_path.parent / "vendor"
config_path.parent / "static"
config_path.parent / "pyproject.toml"
```

改为：

```python
layout.vendor_dir
layout.installed_dir / "static"
layout.installed_dir / "pyproject.toml"
```

不用在两三天内清除仓库所有旧推导，但本轮触达的 registry、host、config 和替换链路必须使用 `PluginLayout`。后续再机械迁移其他调用点。

过渡期保留原有 `ctx.config_path`，避免破坏用 `config_path.parent` 查找静态资源的第三方插件。新增的标准路径可以通过 `ctx.paths` 暴露，旧字段以后再废弃。

## 4. 最小替换 Module

不创建完整包管理框架，也不预先设计一组来源 Adapter。复用现有 install plan 和 `perform_safe_upgrade()`，收敛出一个公共替换 Interface：

```python
async def replace_plugin(
    package_path: Path,
    *,
    source_record: object | None = None,
) -> ReplaceResult:
    ...
```

Implementation 只做：

```text
1. 检查包结构和 plugin ID
2. 初始化/确认外部 Config 已存在
3. 检测目标插件目录
4. 如果插件运行中，停止它
5. 把旧 Installed Payload rename 到临时备份
6. 解压新 Installed Payload 到原目录
7. 校验新清单 ID 和目录
8. 启动插件
9. 成功：删除临时备份并更新来源记录
10. 失败：删除新目录，恢复旧目录，恢复旧来源记录和运行状态
```

不进入事务的目录：

```text
Config
Data
Cache
```

启动流程成功就视为替换成功，不新增 health Interface、等待窗口或业务功能探测。

临时备份只服务当前事务：

- 成功后删除；
- 失败时恢复；
- 不长期保存多个版本。

## 5. 三个入口怎样接入

### 5.1 Package Manager

保留现有 plan、确认 token 和确认 UI。把同 ID 目标统一解释为 replace，不再用版本顺序阻止旧包。

为减少前后端改动，现有 `action="upgrade"` 响应字段本轮可以保留作为兼容名称，但 Implementation 必须已经是任意版本 replace。以后再单独改名。

### 5.2 插件列表导入

不能继续直接调用缺少确认字段的 upload-and-install 闭环。

最快修法是复用 Package Manager 已有流程：

```text
上传
→ install plan
→ 目标已存在时显示确认
→ 调用同一 install/replace 后端
```

不要在 PluginList 再实现一套判断和提示。

### 5.3 Market

Market 保留：

- 下载；
- SHA-256；
- Market 来源查找和更新；
- Market 自己的任务进度 UI。

Market 不再独立拥有目录备份、部署和回滚规则，而是把下载完成的 `.neko-plugin` 交给公共 `replace_plugin()`。

两三天内不要求重写整个 `market_bridge.py`。只抽掉与公共替换重复的文件事务，保留现有下载和来源编排。

Market 默认可以推荐较新版本，但后端不能因为版本较旧而拒绝用户明确选择的 artifact。

## 6. Vendor 本轮只做一件事

`vendor/` 属于 Installed Payload：

```text
源码一起替换
源码一起回滚
不与旧目录合并
不放进 Config/Data/Cache
```

保留当前规则：

- Python 依赖声明在 `pyproject.toml [project].dependencies`；
- 包内必须带满足声明的 `vendor/`；
- 用户安装时不执行 pip；
- 插件子进程从本插件 `vendor/` 导入。

本轮不做：

- lock 文件；
- 完整依赖 hash；
- build 自动联网策略；
- OS/CPU/Python ABI artifact 矩阵；
- 每插件虚拟环境；
- 父进程导入架构重写。

这些是独立问题，不阻塞文件替换和用户状态隔离。

## 7. 硬阻止与警告

只对会破坏文件安全或身份确定性的情况硬阻止：

- 包损坏或路径穿越；
- package/plugin ID 不合法；
- 目标目录属于另一个 plugin ID；
- 同一个 plugin ID 已存在于多个目录；
- 单插件包实际包含多个插件；
- bundle 与已安装插件冲突（保持当前行为）。

以下信息只警告，不成为本轮的新权限门槛：

- 目标版本较旧；
- 同版本、不同 hash；
- SDK recommended 不匹配；
- 推测的平台或 Python 兼容风险。

包真正无法导入或启动时，由现有启动失败触发代码目录回滚。

## 8. 两三天实施切片

### Day 1：路径和配置 Seam

目标：不再需要从一个 `config_path` 猜所有路径。

- 新增 `PluginLayout` 与 resolver；
- Config/Data 复用现有 runtime storage root，增加 Config/Cache 路径；
- 支持 `config.example.toml` 首次复制；
- 历史插件整份复制 `plugin.toml`；
- manifest `[plugin]` 覆盖外部旧 `[plugin]`；
- config update 只写外部 Config；
- 补 v1/v2 配置加载测试。

如果 Day 1 无法稳定完成，不能提前进入 Market 重构。

### Day 2：公共 replace

目标：任何版本 `.neko-plugin` 使用一个文件事务。

- 在现有 `perform_safe_upgrade()` 上收敛公共 `replace_plugin()`；
- 删除通用本地安装的版本方向限制；
- Config/Data/Cache 不进入替换目标；
- vendor 随 Installed Payload 替换；
- 启动失败恢复旧目录；
- 测试新版本、同版本、旧版本和失败回滚。

### Day 3：入口接线和清理

目标：用户从任何主要入口得到同一文件结果。

- PluginList 复用 Package Manager 的 plan/confirm/install 流程；
- Market 文件替换调用公共 `replace_plugin()`；
- Market 保留下载、hash、来源记录和任务 UI；
- 统一结果中的 operation/rollback status；
- 跑后端、Market 和 Plugin Manager 定向回归测试；
- 更新当前插件升级文档，不做无关重构。

Day 3 是本轮硬截止。如果时间不足，削减命名清理、文档扩写和非关键兼容美化，不能削减三入口对公共 replace 的接线，也不能留下“路径改了一半、替换还走旧逻辑”的中间状态。

## 9. 完成标准

### 9.1 必须通过

1. 全新 `.neko-plugin` 可以安装。
2. 已安装插件可以被更高、相同或更低版本替换。
3. 新代码和新 vendor 完整落地。
4. 外部 Config 在替换前后内容不变。
5. Data 在替换前后内容不变。
6. Cache 在替换前后内容不变。
7. 历史混合 `plugin.toml` 首次使用时整份复制到外部 Config。
8. 外部旧 `[plugin].version/entry` 不能覆盖新 Installed Payload。
9. 新包启动失败时恢复旧代码和旧 vendor。
10. 替换失败不删除外部 Config/Data/Cache。
11. Package Manager 和 PluginList 使用同一 plan/confirm/install 后端。
12. Market 的文件事务最终调用同一个 replace Implementation。

### 9.2 不属于完成标准

- bundle 更新已有成员；
- 自动 update-all；
- 多版本历史和手动 rollback；
- 通用配置迁移 hook；
- 配置 reset UI 和备份保留策略；
- Profile 最终重构；
- plugin ID 重命名；
- Market 接管手工插件；
- 包签名和发布者所有权；
- Secret Store；
- Python 文件沙箱；
- vendor lock 和跨平台 artifact；
- 清理仓库里所有旧 `config_path.parent`。

## 10. 代码改动集中点

本轮优先触达：

| 责任 | 位置 |
| --- | --- |
| 当前插件根和 storage root | `plugin/settings.py`、`plugin/sdk/shared/core/base_runtime.py` |
| PluginLayout | 新建于插件 Platform Layer 内的单一模块 |
| 插件发现和 Effective Config | `plugin/core/registry.py` |
| 子进程 import/vendor | `plugin/core/host.py` |
| SDK context/config | `plugin/core/context.py`、`plugin/sdk/shared/core/` |
| 配置读写 | `plugin/server/infrastructure/config_paths.py`、`config_updates.py` |
| 安装计划 | `plugin/server/application/plugin_cli/install_plan.py` |
| 公共文件事务 | `plugin/server/application/plugins/upgrade_support.py` |
| 通用安装入口 | `plugin/server/application/plugin_cli/service.py` |
| Market 接线 | `plugin/server/routes/market_bridge.py` |
| PluginList 接线 | `frontend/plugin-manager/src/views/PluginList.vue` |

不新建大型 package-manager 目录，不先迁移所有调用者，不重写现有 CLI 包格式实现。

## 11. 防止再次堆积的规则

1. 新逻辑只能通过 `PluginLayout` 获取跨区域路径。
2. Config/Data/Cache 不得成为 Installed Payload 的备份、删除或 copytree 目标。
3. 版本比较不得阻止用户替换同 ID 插件。
4. Market 和本地入口不得各自实现目录替换。
5. 配置兼容由插件作者负责；Host 不猜业务字段。
6. 本轮遇到非完成标准问题，只记录，不顺手增加 Interface。
7. 没有第二个实际实现前，不为未来来源、存储或包格式创建 Adapter。
8. 每个阶段必须有用户可见的完整结果，不能为了“以后更完整”长期保留双路径。

## 12. 本轮之后再讨论

MVP 合并并稳定后，再单独决定：

- 是否正式要求所有新插件使用 `config.example.toml`；
- 何时废弃旧 `ctx.config_path` 语义；
- profiles 是否保留为首次配置预设；
- 是否提供 Config Reset；
- 是否标准化插件配置/数据迁移 hook；
- bundle 如何替换已有成员；
- vendor 构建与跨平台分发；
- 签名、ID 所有权、Secret Store 和沙箱。

这些决定不能反向扩大本轮两三天的实现范围。

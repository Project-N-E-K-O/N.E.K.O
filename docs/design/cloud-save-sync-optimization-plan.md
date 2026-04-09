# 云存档同步优化实施计划（按当前实现校准）

> 关联说明：旧的 `cloud-save-sync-optimization-overview.md` 已移除，约束已并入本文。
> 本文目标：基于项目当前已经落地的实现，修正文档口径，并把后续实施方向调整为“本地全局唯一 + 云端手动上传/下载 + 单猫娘管理”。

---

## 1. 文档目标

- 明确阶段 0 中哪些基础能力已经完成，避免继续把已实现内容写成“待实现”。
- 明确产品口径已经变化：v1 不再以“启动自动同步 / 自动导入云端覆盖本地”为主路径，而是以“用户手动管理云端角色”为主路径。
- 明确本地存档仍然保持全局唯一，不按 Steam 用户拆分本地运行时目录。
- 明确前端入口要放在角色设置中，点击后进入独立的云存档管理页面。
- 明确 v1 的核心粒度是“单只猫娘上传 / 下载”，而不是整包自动同步。

---

## 2. 当前实现基线（按代码核对）

这一节描述的是当前仓库里已经存在的事实，后续阶段设计必须建立在这份基线上。

### 2.1 已经落地的阶段 0 基础

- 已有稳定的应用数据根目录选择与初始化逻辑，`ConfigManager` 已优先指向标准 app data 目录，并保留旧目录发现与兼容导入能力。
- 已有本地运行时根目录迁移 / 修复机制，可从旧根目录一次性导入到当前确定性根目录，并保留备份。
- 已有本地 `cloudsave/` 导出层、staging 目录、备份目录和 `manifest.json`。
- 已有 `root_state.json`、`cloudsave_local_state.json`、`character_tombstones.json` 这类本地状态文件。
- 已有启动期 bootstrap 逻辑，`launcher` 会先执行本地云存档环境初始化，再进入后续服务启动。
- 已有 `main_server` 启动期 bootstrap 兜底逻辑，因此即使绕过 launcher 直接从源码 / GitHub 环境启动，也会先补做本地 cloudsave 环境初始化。
- 已有全局写保护 / 维护模式恢复机制，可在异常退出后恢复 stale maintenance 状态。
- 已有维护态写保护接入到多条真实写链路，而不只是存在底层能力：
  - 角色重命名 / 删除。
  - 记忆 recent 保存与记忆重命名。
  - 用户偏好与对话设置保存。
  - 启动期 bootstrap 与部分 Workshop 同步链路。
- 已有“运行时真源优先写入”修正，避免在项目目录 fallback 文件存在时误把配置继续写回项目内置位置：
  - `characters.json`
  - `core_config.json`
  - `user_preferences.json`
- 已有本地整包快照导出 / 导入能力：
  - `export_local_cloudsave_snapshot`
  - `import_local_cloudsave_snapshot`
- 已有一套导出内容组织方式，当前已覆盖：
  - `catalog/catgirls_index.json`
  - `catalog/current_character.json`
  - `catalog/character_tombstones.json`
  - `profiles/characters.json`
  - `profiles/conversation_settings.json`
  - `bindings/<character_name>.json`
  - `memory/<character_name>/...`

### 2.2 当前实现的真实含义

- 现在已经完成的不只是“本地云存档基础设施”和“本地快照导入导出能力”，还包括 v1 首版的“单猫娘云存档摘要 + 独立页面 + 上传 / 下载 API + 下载后 reload / rollback 链路”。
- 这套能力已经证明目录切换、迁移、导出层、状态保护、单角色应用和失败回滚基础都已具备。
- 当前用户可见产品形态已经不再只是“整包快照能力”，而是明确转向“用户手动管理单猫娘云端数据”；整包快照现在主要保留为底层基础设施与兜底工具。
- 角色相关的若干关键事务也已经开始按新方向收敛，而不是完全停留在设计层：
  - 角色重命名已联动记忆目录迁移。
  - 角色删除已联动记忆目录删除与 tombstone 写入。
  - 角色变更后已接入现有角色 reload 与 memory server reload。
- Workshop 启动同步已经开始尊重 tombstone，并通过串行锁避免和角色写入流程互相覆盖。
- 云存档摘要接口与页面展示已经开始区分“资源来源”“资源状态”“Workshop 当前设备恢复状态”三层概念，不再把所有 `steam_workshop + 缺资源` 一律视为“必然可恢复”。
- 资源来源和资源状态的判断也不再只依赖旧配置字段，而是会结合当前设备上实际命中的文件路径做反推；旧路径、旧文件名或过期来源元数据在一定范围内可以自动纠偏。
- 源码 / GitHub 启动路径与运行时配置写入路径的一致性也已开始修正，不再完全依赖“只能通过 launcher/打包环境启动”这一前提。
- 已经存在 cloudsave runtime / router / pages / i18n 相关回归测试，因此文档不应再把这些内容写成“纯设计、未验证”。

### 2.3 当前仍未完成的部分

- 角色设置中的云存档入口已经在代码层接入，但当前只保留了角色管理页操作区里的一个全局入口，不再保留角色面板中的第二入口。
- 前端独立云存档管理页面已经有独立路由、模板、脚本和样式文件，也已补齐正式 `cloudsave.*` i18n 键、空页面渲染与本地时间显示；当前剩余的是不同启动方式和真实浏览器环境下的端到端验证，而不是页面本身仍未实现。
- 本地 / 云端摘要展示、单角色上传 / 下载 API、下载回滚链路都已在代码层落地，并已有 unit 级覆盖；当前仍建议继续做真实运行态联调，确认页面点击链路、模板加载、静态资源加载和 reload 提示在各启动方式下一致。
- `provider_available` 字段已经接入摘要接口和上传 / 下载接口分支控制；当前 provider 不可用时，摘要接口只展示本地摘要并禁用云端操作，但仓库中还没有真正接入远端 provider 可用性探测。
- Workshop 恢复状态细分已经在 `GET /api/cloudsave/summary` / `GET /api/cloudsave/character/{name}` 与页面侧落地，但它仍是“基于当前设备 Steam / Workshop 可见状态的即时判断”，不等于对未来永久可恢复性的强保证。
- 云端数据形态已经转向“按单猫娘分片优先，同时兼容旧聚合目录读取”，但整包快照能力仍作为底层基础设施保留。
- `time_indexed.db` 的最终安全 shadow copy 方案仍需要继续收尾和验证。
- 启动链路中 launcher / main_server / memory_server 的最终职责边界还应继续收敛。
- 维护态写保护虽然已接入多条关键链路，但还没有形成“所有潜在角色真源写入点都已统一接入”的最终闭环。

### 2.4 当前必须写死的产品事实

- 本地运行时存档是全局唯一的。
- 本计划不把“同一台设备上不同 Steam 用户使用不同本地运行时根目录”作为 v1 目标。
- v1 只解决“显式手动管理云端角色存档”，不解决“自动按 Steam 用户切分本地目录”。

### 2.5 相关实现节点（按提交历史归纳）

以下提交只列早期关键锚点；云存档页、上传 / 下载接口、i18n、时间显示、Workshop 状态细分和资源来源纠偏等后续收口，应以当前仓库代码与测试现状为准。

- `64033ab4 refactor: bootstrap local cloudsave runtime state`
  - 标志本地 cloudsave bootstrap、状态文件与运行时初始化进一步成型。
- `d48ff1fc Harden cloudsave runtime snapshot safety`
  - 标志本地快照导出 / 导入、单角色导入导出、备份与回滚安全性继续压实。
- `db687345 docs: refine cloud save sync design constraints`
  - 标志云存档设计边界开始明确收敛，不再把旧的自动同步方向继续往前推。
- `ceb913a7 docs: tighten cloud save sync implementation constraints`
  - 标志文档开始强调“单角色、手动触发、边界清晰”的新约束。
- `797c1eb6 feat: multi-window IPC support ...`
  - 提供了独立管理页 / 多窗口页面这一类现有页面模式，后续云存档页应尽量沿用这类已有交互风格，而不是新开一套异构模式。

### 2.6 当前代码状态矩阵

| 能力项 | 当前代码状态 | 备注 |
| --- | --- | --- |
| 本地 cloudsave bootstrap | 已实现 | launcher / main_server / memory_server 均已接入本地初始化 |
| 整包快照导出 / 导入 | 已实现 | 作为底层基础设施保留，不作为 v1 主要用户交互 |
| 单角色摘要构建 | 已实现 | 本地 / 云端摘要、状态分类、动作建议均已有代码 |
| 单角色上传 / 下载内部接口 | 已实现 | 已有单角色导出 / 导入函数与回滚恢复函数 |
| `/api/cloudsave` 路由 | 已实现 | summary / detail / upload / download 已存在 |
| 下载后的 reload / rollback | 已实现 | 已接入 `initialize_character_data()`、memory reload、失败回滚 |
| 云存档独立页面文件 | 已实现 | 路由 / 模板 / JS / CSS 均已存在 |
| 角色管理页入口 | 已实现 | 当前只保留一个全局入口 |
| 云存档页面 i18n | 已实现 | 模板使用正式 `cloudsave.*` 键，语言包已补齐，JS 保持 ASCII-only |
| 页面时间显示本地化 | 已实现 | 前端把 UTC 时间转换为用户本地 24 小时 `YYYY-MM-DD HH:MM:SS` |
| 资源来源 / 旧路径纠偏 | 已实现 | 支持 configured workshop root、Workshop 目录扫描、本地 live2d 目录扫描、resolved path 反推来源 |
| Workshop 恢复状态细分 | 已实现 | 已区分仍订阅、需重新订阅、条目不可用、Steam 不可用等当前设备状态 |
| provider 不可用时的页面降级 | 已实现（静态版） | summary 保留本地项、隐藏云端项，上传 / 下载返回不可用；真实远端探测仍未接入 |
| 远端 provider 可用性探测 | 未完成 | `provider_available` 字段已保留，但真实 provider 状态仍未接入 |
| 真实运行态端到端联调 | 未完成 | 代码与测试已在仓库中，但页面点击链路和不同启动方式仍需继续验证 |
| 云端删除 / 批量同步 / 冲突详情 | 未完成 | 仍属于后续增强项 |

---

## 3. 更新后的 v1 产品口径

### 3.1 本地与云端的职责

- 本地存档继续作为唯一运行时真源。
- 云端不再承担“启动自动覆盖本地”的职责。
- v1 云端改为手动上传 / 手动下载。
- 用户必须在明确操作后才会发生云端覆盖或本地覆盖。

### 3.2 角色粒度

- v1 以“单只猫娘”为主要管理单位。
- 用户可以看到云上有哪些猫娘。
- 用户可以看到哪些猫娘本地有、云上也有，哪些只有本地有，哪些只有云上有。
- 对于同名猫娘，v1 直接视为同一猫娘，不做跨名称识别、不做 rename merge。

### 3.3 前端入口

- 当前代码中已在角色管理页 `chara_manager` 的操作区接入一个全局“云存档管理”入口按钮。
- 当前代码不再保留角色面板中的第二个“打开云存档页”入口，避免角色设置页继续膨胀。
- 入口应打开独立云存档管理页面，而不是在当前表单中继续堆叠复杂操作。
- 该页面负责展示本地与云端的角色对照关系，并提供逐只上传 / 下载操作。

### 3.4 v1 明确不做的事情

- 不做启动自动从云端覆盖本地。
- 不做退出自动全量上传。
- 不做跨名称角色识别。
- 不做大资源文件随云同步。
- 不做一开始就支持“全量双向智能合并”。

---

## 4. v1 云存档边界

### 4.1 v1 同步对象

v1 同步的目标应收敛到“单猫娘恢复所需的小文件与关键状态”，核心包括：

- 角色基础配置。
- 该角色相关的记忆文件。
- 该角色的模型绑定摘要和资源状态。
- 角色删除 / 替换判断所需的最小元数据。

### 4.2 v1 不进入云端的内容

- Live2D / VRM / MMD / Workshop 大资源本体。
- API Key、Cookie、Token、Provider 地址等敏感配置。
- 设备相关窗口、显示器、路径类设置。
- 普通运行态统计和临时状态文件。

### 4.3 资源缺失时的行为

- 云端下载角色后，即使本地没有对应模型资源，也应先恢复角色可见性和配置摘要。
- 当前实现会先按配置中的 `model_ref` 命中文件，再对 Workshop 目录和本地 `live2d` 目录做 fallback 扫描；即使保存的是旧文件名或旧相对路径，只要当前设备仍能找到真实模型文件，摘要也会归类为 `ready`。
- 当前实现会根据最终命中的真实文件路径反推资源来源：
  - 命中 Workshop 目录时，自动归类为 `steam_workshop` 并尽量推断 `asset_source_id`。
  - 命中本地 `live2d` 目录时，自动归类为 `local_imported`。
  - 命中项目 `static` 目录时，自动归类为 `builtin`。
- 页面需要明确标识资源状态，例如：
  - `ready`：本地资源齐全，可直接使用。
  - `import_required`：角色配置可恢复，但模型资源需要用户手动导入。
  - `downloadable`：该角色绑定摘要指向 Workshop 来源，但当前设备仍需结合 Workshop 状态判断是否还能自动恢复，不能直接等同于“必然可恢复”。
  - `missing`：仅恢复了角色配置，资源仍缺失。
- 页面上的资源警告判断的是“当前绑定资源是否真实存在”，而不是“运行时是否还能靠 fallback 临时显示别的模型”。也就是说，角色页面看起来还能显示占位模型，不等于云存档摘要里的绑定资源已经恢复。
- 当资源来源为 `steam_workshop` 且资源缺失时，页面还应继续结合当前设备状态细分提示，例如：
  - 已安装且仍订阅。
  - 本地仍有缓存，但已取消订阅。
  - 已订阅但当前尚未安装完成。
  - 条目仍可访问，但需要重新订阅。
  - 条目已不可访问（作者删除、下架或当前无法访问）。
  - Steam / Steamworks 当前不可用，暂时无法确认。

---

## 5. 目标架构调整

### 5.1 启动期职责

- 启动时只做本地 bootstrap、目录修复、状态恢复和必要的本地迁移。
- 启动时不自动拉取远端并覆盖本地角色数据。
- 启动时也不根据远端状态自动触发角色导入。

### 5.2 产品主流程

1. 用户进入角色设置。
2. 点击“云存档管理”按钮。
3. 打开独立云存档管理页面。
4. 页面拉取本地角色摘要与云端角色摘要。
5. 页面展示本地 / 云端关系：
   - `local_only`
   - `cloud_only`
   - `matched`
   - `diverged`
6. 用户对单只猫娘执行上传或下载。
7. 执行前给出覆盖提示，执行中做备份与校验，执行后刷新页面状态。

### 5.3 推荐的数据组织方向

当前本地整包快照能力保留，但后续正式云端产品形态应逐步转向“按单猫娘分片”。

建议后续云端对象按角色名组织，例如：

- `characters/<name>/profile.json`
- `characters/<name>/binding.json`
- `characters/<name>/memory/...`
- `characters/<name>/meta.json`

同时保留全局目录索引，用于快速展示云端角色清单与状态摘要。

`meta.json` 建议至少包含：

- `schema_version`
- `character_name`
- `payload_fingerprint`
- `updated_at_utc`
- `sequence_number`
- `source_client_id`
- `source_device_id`
- `asset_state`
- `asset_source`
- `asset_source_id`

### 5.4 当前整包快照能力的定位

- 继续保留现有 `cloudsave/` 整包导出 / 导入能力，作为底层基础设施。
- 它适合作为：
  - 本地迁移与修复兜底。
  - 回归测试与调试工具。
  - 后续单猫娘云存档能力的底层拼装件。
- 但它不再是 v1 面向用户的主要交互模型。

---

## 6. 本地与云端的匹配规则

### 6.1 匹配规则

- 同名即视为同一猫娘。
- v1 不做 rename 检测。
- v1 不做跨名称合并。
- 名称比较应继续沿用现有安全校验与规范化规则。

### 6.2 上传规则

- 云端不存在同名角色时，允许直接上传。
- 云端已存在同名角色时，必须显式提示“覆盖云端”。
- 上传前应刷新本地摘要，避免使用过期页面状态做覆盖决策。

### 6.3 下载规则

- 本地不存在同名角色时，允许直接下载。
- 本地已存在同名角色时，必须显式提示“覆盖本地”。
- 下载覆盖前必须先做本地备份。
- 下载失败时必须可回滚到覆盖前状态。

### 6.4 删除与墓碑

- v1 首版可以先不开放云端删除。
- 但本地和云端的数据模型仍应继续保留 tombstone 思路，防止后续扩展时语义断裂。
- 即使前端暂不暴露删除动作，也不能破坏现有 tombstone 保护机制。

---

## 7. 实施级规格补充

### 7.1 页面入口与路由建议

- 入口放在现有角色管理页 `chara_manager` 中。
- 当前代码实现中仅保留一个全局入口：
  - 角色管理页操作区中的“云存档管理”按钮。
- 当前代码不再保留角色设置面板中的第二入口，这一点应视为当前实际产品口径，而不是临时缺失。
- 当前代码已使用独立页面路由 `/cloudsave_manager`。
- 为兼容当前页面层已有的参数命名习惯，页面 query 参数可沿用 `lanlan_name`，例如：
  - `/cloudsave_manager?lanlan_name=<角色名>`
- 当前代码中的全局入口已按现有独立窗口模式打开 `/cloudsave_manager`，并在可用时把当前角色名附带到 `lanlan_name` query 中，便于页面优先聚焦当前角色。
- 页面在“无角色”或“无 query 参数”时也应能正常打开并显示空状态，而不是因为缺少角色名而不渲染。
- 页面内部状态和所有后端 API 字段统一使用 `character_name`，避免继续扩散 `lanlan_name` 命名到新接口层。
- 当前代码已使用独立文件：
  - `templates/cloudsave_manager.html`
  - `static/js/cloudsave_manager.js`
- 页面应提供返回角色管理页的明确入口，默认返回 `/chara_manager`。

### 7.2 页面列表与状态模型

云存档管理页不直接渲染运行时原始文件，而应先消费一层摘要数据。

建议列表接口返回结构：

```json
{
  "success": true,
  "current_character_name": "mao",
  "provider_available": true,
  "items": [
    {
      "character_name": "mao",
      "display_name": "猫猫",
      "relation_state": "diverged",
      "local_exists": true,
      "cloud_exists": true,
      "model_type": "live2d",
      "asset_source": "steam_workshop",
      "asset_source_id": "123456",
      "local_asset_source": "steam_workshop",
      "local_asset_source_id": "123456",
      "cloud_asset_source": "steam_workshop",
      "cloud_asset_source_id": "123456",
      "local_asset_state": "ready",
      "cloud_asset_state": "downloadable",
      "local_workshop_status": "installed_and_subscribed",
      "cloud_workshop_status": "available_needs_resubscribe",
      "local_workshop_title": "示例工坊物品",
      "cloud_workshop_title": "示例工坊物品",
      "local_updated_at_utc": "2026-04-08T10:00:00Z",
      "cloud_updated_at_utc": "2026-04-08T08:00:00Z",
      "local_fingerprint": "sha256:...",
      "cloud_fingerprint": "sha256:...",
      "available_actions": ["upload", "download"],
      "warnings": []
    }
  ]
}
```

状态字段要求：

- `relation_state` 只允许：
  - `local_only`
  - `cloud_only`
  - `matched`
  - `diverged`
- `matched` 的判断应基于单角色有效载荷指纹，而不是只看角色名相同。
- `diverged` 表示同名角色在本地和云端同时存在，但有效载荷指纹不同。
- `available_actions` 必须由后端给出，前端不应自己推导覆盖权限。
- `provider_available = false` 时，当前实现会只返回本地摘要；页面仍应能展示本地角色，但上传 / 下载按钮必须禁用，且不应伪造云端状态。
- `local_asset_source` / `cloud_asset_source` 与对应的 `*_asset_source_id` 必须保留，前端需要分别展示本地与云端的资源来源，不能只展示合并后的主来源。
- `asset_source`、`local_asset_source`、`cloud_asset_source` 当前应优先反映“实际命中的文件路径推断结果”，而不只是旧配置里记录的来源字符串。
- 当对应来源是 `steam_workshop` 时，接口还应补充 `local_workshop_status` / `cloud_workshop_status` 等当前设备视角的恢复状态字段。
- `*_workshop_status` 当前代码已区分：
  - `installed_and_subscribed`
  - `installed_but_unsubscribed`
  - `subscribed_not_installed`
  - `available_needs_resubscribe`
  - `unavailable`
  - `steam_unavailable`
  - `unknown`
- `warnings` 当前设备视角下已存在两类稳定码：
  - `local_resource_missing_on_this_device`
  - `cloud_resource_may_be_missing_after_download`
- `warnings` 当前实现采用“本地优先”合并策略：
  - 本地角色存在时，以本地资源检查结果为准，避免把云端 warning 误显示到当前设备已经恢复好的本地角色上。
  - 仅当角色是 `cloud_only` 时，才展示云端下载后的潜在资源 warning。
- `local_updated_at_utc` / `cloud_updated_at_utc` 是后端返回的 UTC 时间；当前页面显示层已统一转换为用户本地时区下的 24 小时 `YYYY-MM-DD HH:MM:SS`。
- `asset_state = downloadable` 只表示“绑定来源来自 Workshop 且本地未命中文件”，页面必须继续结合 `*_workshop_status` 才能决定是“仍可恢复”“需要重新订阅”“条目已不可用”还是“当前未确认”。

### 7.3 单角色数据范围

v1 单猫娘上传 / 下载只允许触达该角色必要数据，不能把整包快照行为直接暴露给前端。

单角色上传 / 下载应包含：

- `profiles/characters.json` 中该角色自己的条目。
- `bindings/<character_name>.json`。
- `memory/<character_name>/...`。
- 该角色对应的云端 `meta.json`。
- 全局目录索引中该角色的摘要条目。
- 与该角色直接相关的 tombstone 决策信息。

单角色上传 / 下载默认不应包含：

- 其他角色条目。
- `profiles/conversation_settings.json`。
- `catalog/current_character.json`。
- 设备偏好、窗口布局、路径配置。
- 模型本体资源。
- 普通角色卡导出文件。

这条边界必须写死，否则“单猫娘下载”仍可能误覆盖全局配置，重新引入数据丢失问题。

### 7.4 后端 API 实现快照

当前仓库已经新增独立 `cloudsave_router`，前缀为 `/api/cloudsave`，没有继续堆到 `/api/characters` 中。

当前已实现的最小接口集：

- `GET /api/cloudsave/summary`
  - 返回本地 / 云端全部角色摘要、状态分类，以及按当前设备 Workshop 状态补充的恢复状态字段。
- `GET /api/cloudsave/character/{name}`
  - 返回单角色 `item`、`local_summary`、`cloud_summary`、推荐动作、警告与 Workshop 恢复状态细分。
- `POST /api/cloudsave/character/{name}/upload`
  - 请求体建议：`{"overwrite": false}`
  - 语义：将本地该角色上传到云端。
- `POST /api/cloudsave/character/{name}/download`
  - 请求体建议：`{"overwrite": false, "backup_before_overwrite": true}`
  - 语义：将云端该角色下载并应用到本地。

当前代码中已经出现或被文档约束的错误码：

- `CLOUDSAVE_PROVIDER_UNAVAILABLE`
- `CLOUDSAVE_CHARACTER_NOT_FOUND`
- `CLOUDSAVE_WRITE_FENCE_ACTIVE`
- `LOCAL_CHARACTER_NOT_FOUND`
- `LOCAL_CHARACTER_EXISTS`
- `CLOUD_CHARACTER_EXISTS`
- `CLOUD_CHARACTER_NOT_FOUND`
- `NAME_AUDIT_FAILED`
- `ACTIVE_SESSION_BLOCKED`
- `MEMORY_SERVER_RELEASE_FAILED`
- `LOCAL_RELOAD_FAILED_ROLLED_BACK`
- `CLOUDSAVE_UPLOAD_FAILED`
- `CLOUDSAVE_DOWNLOAD_FAILED`

补充说明：

- `GET /summary` 当前在 `provider_available = false` 时只返回本地摘要，不拼装云端角色项；页面应据此进入只读降级态。
- `POST /upload` 当前不会触发本地角色 reload。
- `POST /download` 当前已经串上：
  - 活跃会话阻断。
  - 本地角色存在时的 memory 句柄释放。
  - 单角色导入。
  - `initialize_character_data()`
  - `memory_server /reload`
  - reload 失败后的自动回滚。
- `GET /summary` 与 `GET /character/{name}` 当前已经会调用现有 Workshop 查询能力，对 `steam_workshop` 资源补充“当前设备是否仍订阅 / 是否已安装 / 是否需要重新订阅 / 条目是否不可用 / Steam 是否不可用”等状态。
- 页面当前主要消费 `GET /summary`、`POST /upload`、`POST /download`，并会在真正执行上传 / 下载前先调用 `GET /character/{name}` 刷新单角色最新摘要，避免用旧列表状态误判覆盖决策。
- 当前已有对应回归测试：
  - `tests/unit/test_cloudsave_runtime.py`
  - `tests/unit/test_cloudsave_router.py`
  - `tests/unit/test_cloudsave_pages.py`
  - `tests/unit/test_cloudsave_i18n.py`

约束：

- 上传 / 下载接口必须返回更新后的单角色摘要，前端不应依赖整页硬刷新才能获得状态。
- 接口失败时必须返回机器可判定的错误码，而不是只返回自由文本。
- 新接口不得复用角色卡 `.nekocfg` / PNG 导出链路。

### 7.5 操作决策矩阵

页面行为应按状态固定，不允许前端临时猜测。

- `local_only`
  - 主操作：上传。
  - 下载按钮默认不可用。
- `cloud_only`
  - 主操作：下载。
  - 上传按钮默认不可用。
- `matched`
  - 默认展示“已同步”或“无差异”。
  - 如需强制上传 / 下载，应降级到二级操作并再次确认。
- `diverged`
  - 同时提供上传和下载。
  - 两个操作都必须带覆盖确认。

补充规则：

- 上传不会修改本地运行时，不应触发本地角色 reload。
- 下载会修改本地运行时，必须走备份、应用、重载、刷新链路。
- 若当前角色存在活跃语音会话或其他不能安全热替换的会话，下载必须被阻止或延后，不能直接热覆盖。

### 7.6 覆盖确认与备份策略

下载同名覆盖时，必须提示以下事实：

- 将覆盖本地同名角色配置。
- 将覆盖该角色对应记忆目录。
- 将不会自动下载模型资源本体。
- 将先创建本地备份。

上传同名覆盖时，必须提示以下事实：

- 将覆盖云端同名角色数据。
- 不会改动本地当前角色配置。
- 不会上传模型资源本体。

备份要求：

- 单角色下载覆盖前，至少备份：
  - 该角色在 `profiles/characters.json` 中的原条目。
  - `memory/<character_name>/...`
  - 必要的本地状态快照。
- 备份建议路径：
  - `cloudsave_backups/character-download-<timestamp>/<character_name>/...`
- 如果下载后本地应用失败、角色初始化失败或 memory reload 失败，必须自动从该备份恢复。

### 7.7 下载后的本地应用与重载顺序

单角色下载不是简单写文件，必须和项目当前运行时刷新方式对齐。

推荐顺序：

1. 进入全局 cloudsave fence。
2. 对目标角色创建本地备份。
3. 将云端单角色数据写入 staging。
4. 合并生成新的本地角色条目与目标记忆目录。
5. 原子替换本地文件。
6. 调用角色配置重载逻辑。
7. 调用 memory server reload。
8. 若当前前端正在查看该角色，再决定是否发送页面刷新提示。
9. 任一步失败则自动回滚备份。
10. 全部成功后解除 fence。

与当前工程的直接对齐要求：

- 本地应用后应沿用现有 `initialize_character_data()` 重载链路。
- 记忆相关刷新应沿用现有 `memory_server /reload`。
- 不应新增一套绕开现有重载机制的“云存档专用角色刷新”。

### 7.8 与现有功能的边界

必须明确以下边界，防止互相污染：

- `chara_manager`
  - 仍然是角色配置编辑真源页面。
  - 云存档页只负责对比、上传、下载，不负责常规角色编辑。
- `/api/characters`
  - 继续承担角色 CRUD 和本地配置修改。
  - 云存档接口不要混进这个 router，避免职责膨胀。
- Workshop 同步
  - 仍是资源发现 / 恢复链路，不是云端角色真源。
  - 当前实现已经采用“只补缺失角色、不覆盖已存在角色”的策略，后续云存档设计不应默默改变这个默认行为。
  - 云端下载角色时，不应自动触发 Workshop 覆盖角色配置。
  - 若 `asset_source = steam_workshop` 且本地缺资源，可以给出恢复提示，但必须继续区分“仍订阅可恢复”“需重新订阅”“条目已不可用”“Steam 当前不可用”等不同状态，恢复动作仍应单独触发。
- 角色卡导出 / 导入
  - 继续是人工分享 / 手工备份通道。
  - 不得作为云存档上传 / 下载载体复用。

### 7.9 启动方式与运行方式

无论以下哪种启动方式，v1 云存档语义都应保持一致：

- Steam / Electron 正常启动。
- 打包后的 launcher 启动。
- 开发态直接运行 `launcher.py`。
- GitHub / 源码环境下直接运行 `main_server.py` 或等价开发启动方式。

统一要求：

- 启动时都只做本地 `bootstrap_local_cloudsave_environment`。
- 启动时都不自动从远端拉取并覆盖本地角色。
- 启动时都不自动把当前本地整包推到远端。
- 若远端 provider 不可用，页面应进入只读 / 禁用上传下载状态，但本地角色功能不能受影响。

---

## 8. 分阶段实施计划（按新方向重排）

### 8.1 阶段 0：基础设施

状态：已基本完成。

已完成内容：

- 标准 app data 根目录收敛。
- 旧目录导入 / 修复。
- `cloudsave/` 基础结构。
- `manifest`、staging、备份、状态文件。
- launcher bootstrap。
- main_server 启动期 bootstrap 兜底。
- 全局写保护和异常恢复。
- 多条真实写链路已接入维护态写保护。
- 角色重命名 / 删除已开始与记忆目录、tombstone、reload 链路联动。
- Workshop 同步已开始尊重 tombstone 和串行化要求。
- 运行时配置写入路径已开始从项目 fallback 根纠正到真实运行时根。
- 本地整包快照导出 / 导入。
- 已补一批 runtime / router / pages / i18n 回归测试。

剩余收尾：

- 继续补齐尚未覆盖的回归测试场景。
- 继续核对 `time_indexed.db` 的导出 / 导入安全性。
- 继续压实 launcher、main_server、memory_server 的职责边界。

### 8.2 阶段 1：数据模型从整包导出转向单猫娘视角

状态：已完成 v1 首版实施。

目标：

- 在不破坏当前基础设施的前提下，抽出“单猫娘摘要”和“单猫娘读写单元”。
- 明确单猫娘上传 / 下载所需的最小数据集合。
- 将云端正式数据模型从“整包快照优先”调整为“单角色对象优先”。

交付内容：

- 本地角色摘要构建器。
- 云端角色摘要构建器。
- 同名匹配、状态分类、资源状态分类规则。
- Workshop 当前设备恢复状态细分规则。
- 单猫娘导出 / 导入内部接口。
- 旧路径 / 旧文件名 / 旧来源元数据的运行时纠偏与来源反推。

额外要求：

- 单角色数据范围必须与 `7.3` 保持一致。
- 单角色摘要返回字段必须与 `7.2` 保持一致。

### 8.3 阶段 2：角色设置入口与独立页面

状态：已完成 v1 首版实施。

目标：

- 在角色设置中加入云存档入口按钮。
- 新增独立云存档管理页面。

页面至少应支持：

- 展示当前角色和全部角色的本地 / 云端对照状态。
- 区分 `local_only`、`cloud_only`、`matched`、`diverged`。
- 展示资源来源、资源状态和 Workshop 当前设备恢复状态。
- 对单只猫娘显示上传 / 下载按钮。
- 对“改过模型的角色”“需要手动导入模型”“已取消订阅但本地仍有缓存”“条目已不可访问”这类情况给出不同提示。
- 页面在无角色时也能显示空状态。
- 时间显示已按用户本地时区格式化，不直接暴露原始 UTC ISO 字符串。
- 页面文案已接入正式 `cloudsave.*` 语言键。

建议：

- 页面优先以角色列表 + 状态标签 + 操作按钮的形式实现。
- 不要把这套能力继续塞回现有角色表单，避免角色设置页继续膨胀。

额外要求：

- 路由和参数命名遵循 `7.1`。
- 页面状态与操作矩阵遵循 `7.2` 和 `7.5`。

当前代码落点：

- 页面路由：`main_routers/pages_router.py`
- 页面模板：`templates/cloudsave_manager.html`
- 页面脚本：`static/js/cloudsave_manager.js`
- 页面样式：`static/css/cloudsave_manager.css`
- 角色管理页入口：`templates/chara_manager.html`

### 8.4 阶段 3：单猫娘上传 / 下载能力

状态：已完成 v1 首版实施。

目标：

- 打通前端页面到后端的单猫娘上传 / 下载 API。
- 补齐覆盖确认、备份、回滚、刷新和错误提示。

上传要求：

- 明确上传对象范围。
- 明确是否覆盖云端同名角色。
- 上传完成后刷新云端摘要。

下载要求：

- 明确下载对象范围。
- 下载前备份当前本地同名角色。
- 下载后触发角色配置与记忆重载。
- 资源缺失时不阻断角色恢复，但要给出明确状态提示。

额外要求：

- 后端接口契约遵循 `7.4`。
- 本地应用与回滚顺序遵循 `7.6` 和 `7.7`。
- 与现有角色管理 / Workshop / 角色卡边界遵循 `7.8`。

当前代码落点：

- 路由实现：`main_routers/cloudsave_router.py`
- 单角色导出 / 导入与备份恢复：`utils/cloudsave_runtime.py`
- 关键接口 / 回滚测试：
  - `tests/unit/test_cloudsave_runtime.py`
  - `tests/unit/test_cloudsave_router.py`

### 8.5 阶段 4：后续增强

可延期能力：

- 云端删除角色。
- 批量上传 / 批量下载。
- 按更新时间排序、差异明细展示。
- 冲突详情页。
- 全局设置是否纳入云端的二期评估。
- 与 Workshop 恢复链路更深的联动。

### 8.6 当前剩余联调项

这一节专门描述“代码已存在，但仍需继续验证或收尾”的部分，避免与“未实现”混淆。

- 页面入口到 `/cloudsave_manager` 的实际运行态联调仍需继续验证，尤其是不同启动方式、不同容器环境下的点击链路。
- `provider_available` 目前仍偏静态字段，后续若接真实远端 provider，需要补可用性探测、禁用态展示和降级策略。
- 当前 Workshop 恢复状态判断是“基于当前设备 Steam / Workshop 状态的即时视图”，后续若要做跨设备更稳定的恢复预估，还需要额外的元数据或缓存策略。
- 当前页面主要依赖 `GET /summary` 与上传 / 下载接口；在真正执行上传 / 下载前，会额外调用 `GET /character/{name}` 刷新单角色最新摘要，但尚未进一步展开为单独的详情页交互。
- 当前测试以 unit / 模拟为主，仍建议后续补一轮更接近真实页面与服务启动方式的验证脚本或集成回归。
- `time_indexed.db` 的 shadow copy 安全性与更多异常恢复分支，仍建议继续补边界验证。

---

## 9. 验收标准

### 9.1 文档口径正确

- 不能再把已经落地的阶段 0 内容写成“未实现”。
- 不能再把“自动同步 / 启动自动导入”写成 v1 主路径。
- 不能再把“无需新增前端入口”作为当前结论。

### 9.2 交互符合新要求

- 角色设置中存在云存档入口。
- 点击入口后，无论当前有没有角色、是否附带 query 参数，都应能打开独立云存档页面并显示页面框架或空状态。
- 页面能看到云上有哪些猫娘。
- 页面能看出哪些与本地对应、哪些本地没有、哪些云端没有。
- 页面能区分“Workshop 仍可恢复”“需要重新订阅”“条目已不可用”“当前未确认”与“需要手动导入模型”。
- 页面中的时间应按用户本地时区显示为 24 小时 `YYYY-MM-DD HH:MM:SS`，而不是直接显示原始 UTC ISO 字符串。
- 单只猫娘可以单独上传、单独下载。

### 9.3 安全性达标

- 用户未明确点击上传 / 下载时，不发生远端覆盖或本地覆盖。
- 下载覆盖前必须有本地备份。
- 覆盖失败可以回滚。
- 不因资源缺失导致角色元数据丢失。

### 9.4 与现有工程行为一致

- 单角色下载不会覆盖其他角色条目。
- 单角色下载不会顺带覆盖 `profiles/conversation_settings.json`。
- 下载完成后会走现有角色重载与 memory reload 链路。
- 不同启动方式下，云存档都不会在启动时自动覆盖本地。
- provider 不可用时，本地角色管理和页面打开不应受影响，页面只进入只读 / 禁用云操作状态。

---

## 10. 主要风险与注意事项

本节如果提到“整包快照”“启动自动导入”“聚合 `profiles/characters.json`”等旧方向，仅用于说明当前仍需避免的回退风险，不代表后续继续按这些旧方案实施。

- 当前已有的整包快照能力如果直接当作前端产品能力暴露，会与“单猫娘上传 / 下载”的新要求冲突。
- 如果仍保留启动自动导入思路，容易再次引入角色配置被旧数据覆盖的问题。
- 如果继续沿用聚合 `profiles/characters.json` 作为远端主对象，单角色上传 / 下载会变得复杂且更容易误覆盖。
- 大资源不入云后，必须把“角色可恢复”“资源可用”“当前设备是否仍能通过 Workshop 恢复”三个概念明确区分，否则用户会误判为下载失败或误以为一定还能恢复。
- `downloadable` 当前不再应被理解为“确定可恢复”，它只说明绑定来源来自 Workshop 且本地未命中文件；作者删除条目、用户取消订阅、Steam 不可用等情况都需要继续结合 Workshop 状态字段判断。
- “当前页面还能显示某个 fallback 模型”不等于“角色绑定的真实资源已经存在”；文档和页面提示都必须以绑定资源检查结果为准，不能把显示层 fallback 误写成资源已恢复。
- 本地全局唯一意味着同一台设备上的不同 Steam 用户不会自动拥有隔离本地运行时目录，因此产品文案和预期需要明确。
- 当前页面入口、独立页面路由、API 与测试文件已经在仓库中存在；后续若再调整交互，应优先删掉冗余链路，而不是在现有入口外继续叠加第二套打开逻辑。
- 如果单角色下载仍偷懒复用整包导入逻辑，就有较大概率再次覆盖全局对话设置或其他角色数据。
- 如果前端自己推导 `available_actions`，而不是以后端摘要为准，容易出现覆盖按钮显示错误或状态判断不一致。

---

## 11. 结论

这一节只总结当前应继续推进的方向，不再延续任何已废弃的自动整包同步方案。

- 阶段 0 基础设施已经基本完成，文档应从“是否做基础设施”切换为“如何把现有基础设施转成正确的产品能力”。
- 当前仓库已经具备“角色设置入口 + 独立管理页面 + 单猫娘手动上传 / 下载 + 下载后 reload / rollback”的 v1 首版产品形态。
- 当前 v1 首版已经具备“资源来源展示 + 资源状态细分 + Workshop 当前设备恢复状态细分 + 本地时间显示 + 正式 i18n 接入”的基本产品形态，后续重点应放在真实运行态联调和剩余增强项，而不是回退到旧的自动整包同步口径。
- 本地存档继续保持全局唯一，云端只做用户显式触发的角色级同步，不再以自动整包同步作为 v1 主方向。

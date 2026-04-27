# v0.9 存储位置常驻入口优化设计

## 0. 背景

v0.8 存储位置迁移已经完成首启阻断、路径选择、关闭后迁移、恢复、迁移完成提示和旧目录清理等核心能力。当前入口主要服务于“首次启动或异常恢复时必须先处理存储状态”的场景。

后续还需要一个常驻入口，让用户在应用已经正常运行后也能主动查看当前存储位置，并发起后续迁移。该入口放在“记忆浏览”页面左侧栏中，在“新手引导”区块下方。

本文只描述下一阶段设计，不代表当前代码已经全部实现。

当前代码事实：

- `/memory_browser` 已有阶段 1 的“存储位置”只读入口：展示当前数据位置、保留禁用的“更改存储位置”占位、支持打开当前目录。
- `/memory_browser` 已有阶段 2/3 的常驻迁移管理 modal：ready 状态下可选择或输入目标位置，先调用 `/api/storage/location/preflight` 展示预检结果，再由用户点击“确认关闭并迁移”调用 `/api/storage/location/restart`。
- `/memory_browser` 常驻入口不调用 `/select`；`/select` 继续保留给首启或恢复阻断流程。
- 后端在 ready 状态下已经具备 `bootstrap/select/restart/status/cleanup` 等存储迁移主链路能力。
- 当前 `/api/storage/location/preflight` 已实现为 side-effect-free 预检接口，不写策略、迁移检查点或 root_state，不释放 startup barrier，不触发关闭。
- 主服务受限启动时允许访问 `/memory_browser` 和 `/api/storage/location/*`，但不允许访问普通 `/api/memory/*` 业务接口。因此常驻入口必须先读取存储状态，再决定是否初始化记忆列表和自动记忆整理。

## 1. 目标

- 在 `/memory_browser` 页面提供常驻的“存储位置”入口。
- 入口默认不阻断用户使用，只用于查看状态、打开目录、发起后续迁移。
- 常驻入口沿用 v0.8 的后端迁移规则：当前会话不热切根，改变存储位置必须记录检查点并受控关闭，关闭后迁移或重连。
- 入口位于“新手引导”下方，视觉上属于左侧工具区，而不是主聊天记录编辑器。
- 前端展示和后端状态机继续解耦：前端只提交用户意图，后端负责路径归一化、预检、二次确认要求、检查点和关闭。

## 2. 非目标

- 不在“记忆浏览”页面内直接复制或删除文件。
- 不让 NEKO-PC 直接拥有迁移决策。
- 不让当前会话在点击后立即切换运行时根。
- 不把 `storage_policy.json`、`storage_migration.json`、`anchor_root` 等内部细节直接展示给普通用户。
- 不复用首启阻断覆盖层的自动启动行为，以免打开“记忆浏览”时误触发 storage startup barrier。

## 3. 页面位置

当前页面结构：

- 文件：`templates/memory_browser.html`
- 左侧栏：`.left-column`
- 已有区块：
  - “猫娘记忆库”
  - “新手引导”

新增区块放在“新手引导”区块之后：

```html
<div class="file-list storage-location-section">
    <div class="file-list-title">存储位置</div>
    ...
</div>
```

建议标题使用：

- 中文：`存储位置`
- 英文：`Storage Location`

建议说明文案：

- `当前数据位置`
- `更改存储位置`
- `打开当前目录`

如果存在旧目录清理提示，可显示：

- `有旧数据目录可清理`
- `清理旧数据目录`

## 4. 后端是否“调用页面”

严格来说，后端不应该直接调用、弹出或隐藏存储切换页面。

原因：

- 后端不知道当前用户正在使用浏览器页面、桌面 Pet 窗口还是其他宿主窗口。
- 后端不能替用户完成前端二次确认。
- 后端如果直接写状态并触发迁移，会绕过 v0.8 中“前端展示风险、用户确认、后端再写检查点”的顺序。
- NEKO-PC 设计上只提供宿主能力，不拥有迁移状态机。

后端可以做的是“提供可被前端调用的页面能力和流程能力”：

- 继续由 `main_routers/pages_router.py` 提供 `/memory_browser` 页面。
- 可新增一个独立页面或片段路由，例如 `/storage_location_manager`，但它仍只是前端页面入口。
- 继续由 `main_routers/storage_location_router.py` 提供状态、预检、确认迁移、维护轮询和清理 API。
- 当前代码里：
  - `bootstrap` 已返回 `current_root`、`recommended_root`、`legacy_sources`、`blocking_reason` 等“入口概览”字段。
  - `status` 已返回 `ready`、`lifecycle_state`、`completion_notice`、`migration` 等“维护态/完成态”字段。
- 如果后续希望常驻入口只靠一次请求完成渲染，可再增强 `status`，补充 `current_root`、`recommended_root`、`can_manage_storage_location`、`can_start_migration`、`cleanup_available` 等字段。

因此，正确架构是：

1. 用户打开“记忆浏览”。
2. `memory_browser.js` 先请求后端存储概览；按当前代码事实，第一阶段更适合优先读取 `bootstrap`，必要时再补读 `status`。
3. 前端在“新手引导”下方显示“存储位置”常驻入口。
4. 用户点击“更改存储位置”。
5. 前端打开存储切换 modal 或跳转到一个前端管理页。
6. 后端只在用户确认后执行预检、写迁移检查点、进入维护态并请求受控关闭。

如果后续确实需要由某个后端事件提示用户处理存储问题，也应表现为“前端轮询或订阅状态后展示入口”，而不是后端主动打开窗口。

### 4.1 与记忆浏览现有实现保持一致

常驻入口的默认实施路径应贴合 `memory_browser` 已有实现方式：

- HTML 结构直接写在 `templates/memory_browser.html`。
- 页面逻辑写在 `static/js/memory_browser.js` 现有 IIFE 内。
- 初始化挂到现有 `DOMContentLoaded` 回调中，但必须先执行 `initStorageLocationPanel()`，再决定是否调用 `loadMemoryFileList()`、`loadReviewConfig()`。
- 样式写入 `static/css/memory_browser.css`，复用 `.file-list`、`.file-list-title`、胶囊按钮和浅蓝页面风格。
- 文案继续使用 `data-i18n` 和 `window.t(...)` 双路径。
- 语言切换时，通过现有 `window.i18n.on('languageChanged', ...)` 刷新动态文本。
- 不新增后端主动推页面、WebSocket 推页面或桌面端单独维护的一套迁移状态机。

推荐初始化形态：

```js
document.addEventListener('DOMContentLoaded', async function () {
    const storagePanelState = await initStorageLocationPanel();
    if (!storagePanelState || !storagePanelState.blockingReason) {
        loadMemoryFileList();
        loadReviewConfig();
    } else {
        renderMemoryBrowserLimitedState(storagePanelState);
    }
    ...
});
```

常驻入口按钮建议使用 `addEventListener` 绑定，减少新增 inline `onclick`。现有页面已经有部分 inline 入口（例如关闭按钮、新手引导重置），但新功能没有兼容旧调用的压力，优先使用 JS 统一绑定更容易维护。

补充说明：

- 当前代码已经允许 `GET /memory_browser` 和 `/api/storage/location/*` 在存储受限启动时继续访问，因此“记忆浏览”可以承载存储状态入口。
- 受限启动期间普通 `/api/memory/*` 接口会被 limited-mode guard 拒绝。实现时不能在读取 `bootstrap` 之前无条件调用 `loadMemoryFileList()` 或 `loadReviewConfig()`，否则页面会先显示记忆列表加载失败。
- 这不代表应该在 `memory_browser` 页面自动启动首启阻断 UI；常驻入口仍应保持“用户点击后才进入管理流程”。

### 4.2 可选的独立管理页

如果不希望在 `memory_browser.js` 内承载完整选择 UI，可以新增：

```text
GET /storage_location_manager
```

该页面可以复用存储切换 UI，但必须满足：

- 默认不自动执行 startup barrier。
- 默认不阻断主页面。
- 只在用户点击“确认关闭并迁移”后调用后端写状态接口。
- 不直接复用当前 `static/app-storage-location.js` 的自动启动入口，除非先拆出 `autoStart=false` 的管理模式。

“记忆浏览”里的常驻入口可以打开这个页面，也可以打开页面内 modal。两种方式都可以；从当前页面实现一致性看，第一阶段应优先采用页面内卡片 + modal。独立页面只作为后续拆分复杂 UI 时的备选方案。

这里的“页面”仍然是网页页面，不是桌面端原生迁移弹窗。桌面端差异主要体现在宿主桥能力，例如目录选择器、打开目录和关闭窗口。

## 5. 前端结构与样式修改

本节只描述 HTML/CSS 展示层，不描述后端状态写入。

### 5.1 HTML 结构

在 `templates/memory_browser.html` 的“新手引导”区块后新增 `storage-location-section`。

该区块应与现有“猫娘记忆库”“新手引导”同级，放在 `.left-column` 内，不要放到 `.editor` 主编辑区域。

建议 DOM：

```html
<div class="file-list storage-location-section">
    <div class="file-list-title" data-i18n="memory.storageLocation">存储位置</div>
    <div class="storage-location-card-mini">
        <div class="storage-location-mini-row">
            <span class="storage-location-mini-label" data-i18n="memory.storageCurrentRoot">当前数据位置</span>
            <span class="storage-location-mini-value" id="storage-current-root">加载中...</span>
        </div>
        <div class="storage-location-mini-status" id="storage-location-status"></div>
        <button type="button" class="storage-location-manage-btn" id="storage-location-manage-btn">
            <span data-i18n="memory.changeStorageLocation">更改存储位置</span>
        </button>
        <button type="button" class="storage-location-open-btn" id="storage-location-open-btn">
            <span data-i18n="memory.openCurrentStorageRoot">打开当前目录</span>
        </button>
    </div>
</div>
```

如果左侧栏高度不足，允许该区块跟随页面滚动，不要让它固定悬浮，避免遮挡记忆列表。

第一阶段不需要把完整选择器 DOM 直接写进左侧栏。左侧栏只保留当前数据位置和入口占位，详细预检与确认放在后续 modal 中。

### 5.2 CSS 风格

样式放在 `static/css/memory_browser.css`。

风格应贴合现有记忆浏览页面：

- 使用 `.file-list` 白底、浅蓝边框、20px 圆角的卡片体系。
- 按钮使用胶囊形态，与“新手引导”的重置按钮一致。
- 主按钮 `更改存储位置` 可以使用浅蓝渐变，强调它是可行动入口。
- `打开当前目录` 使用白底蓝边次级按钮。
- 路径文本必须支持长路径换行或省略，不能撑破左侧栏。

不要直接复用 `.auto-review-toggle-btn` 作为存储按钮的唯一类名。可以借鉴它的视觉，但应使用 `storage-location-manage-btn`、`storage-location-open-btn` 等独立类名，避免未来调整“自动记忆整理”开关时影响存储入口。

建议样式约束：

```css
.storage-location-section {
    margin-top: 0;
}

.storage-location-card-mini {
    display: flex;
    flex-direction: column;
    gap: 10px;
}

.storage-location-mini-row {
    display: flex;
    flex-direction: column;
    gap: 4px;
}

.storage-location-mini-label {
    color: #40C5F1;
    font-size: 0.85rem;
    font-weight: 700;
}

.storage-location-mini-value {
    color: #268fb8;
    font-size: 0.82rem;
    line-height: 1.45;
    overflow-wrap: anywhere;
}

.storage-location-mini-status {
    min-height: 18px;
    color: #40C5F1;
    font-size: 0.85rem;
    line-height: 1.4;
}
```

按钮可以复用现有 `.auto-review-toggle-btn` 的视觉语言，但建议增加独立类名，避免后续调整自动整理开关时影响存储入口。

### 5.3 响应式要求

当前 `.left-column` 宽度约 240px，因此：

- 路径值必须 `overflow-wrap: anywhere`。
- 不使用固定宽度文本按钮。
- 按钮文字在窄屏时允许自然换行，最小高度保持 44px。
- 不在该卡片中展示大面积预检表格；复杂预检结果应在弹窗或主区域浮层中展示。

### 5.4 i18n

需要补充 `static/locales/*.json` 中的 `memory.*` 文案。

建议 key：

- `memory.storageLocation`
- `memory.storageCurrentRoot`
- `memory.storageStatusReady`
- `memory.storageStatusBlocked`
- `memory.changeStorageLocation`
- `memory.openCurrentStorageRoot`
- `memory.storageLoadFailed`
- `memory.storageMigrationPending`
- `memory.storageCleanupAvailable`
- `memory.storageManagementUnavailable`
- `memory.storageManagementComingSoon`
- `memory.storageMemoryLimitedState`
- `memory.storageAlreadyCurrentRoot`

动态文案更新方式应参考 `updateToggleText()`：当语言切换时重新写入动态按钮、状态和路径说明，不依赖页面刷新。

## 6. 前端交互逻辑修改

本节描述 `static/js/memory_browser.js` 和可复用存储迁移 UI 的组织方式。

### 6.1 状态加载

页面 `DOMContentLoaded` 后调用：

- 第一阶段必须优先调用 `GET /api/storage/location/bootstrap`
- 需要维护态轮询或迁移完成提示时，再调用 `GET /api/storage/location/status`
- 只有在需要显式读取保留旧目录详情时，再调用 `GET /api/storage/location/retained-source`

原因：

- 按当前代码事实，`bootstrap` 已包含 `current_root`、`recommended_root`、`legacy_sources`、`blocking_reason`，适合渲染左侧常驻卡片。
- 当前 `status` 主要包含 `ready`、`lifecycle_state`、`blocking_reason`、`completion_notice`、`migration`，并不直接返回 `current_root` 和 `recommended_root`。
- `status.completion_notice` 已能提供迁移完成提示与 `cleanup_available`，因此不需要在页面初始化时强制再调一次 `retained-source`。

展示规则：

- 当 `bootstrap.blocking_reason=""` 时，视为可管理状态，显示当前数据位置、“更改存储位置”禁用占位按钮和“打开当前目录”按钮；正常 ready 状态不需要额外显示“存储位置已就绪”。
- 当 `bootstrap.blocking_reason=selection_required` 时，显示“当前需要先确认存储位置”。阶段 1 只展示状态，不自动打开首启阻断 UI，不继续初始化记忆列表和自动整理接口。
- 当 `bootstrap.blocking_reason=migration_pending` 时，显示“正在迁移或等待重启”，禁用更改按钮。
- 当 `bootstrap.blocking_reason=recovery_required` 时，显示“需要恢复存储位置”。常驻管理入口不能绕过恢复流程发起新的迁移。
- 当需要展示维护进度或迁移完成卡片时，以 `status.ready / status.lifecycle_state / status.completion_notice` 为准。

对现有记忆浏览功能的影响：

- `blocking_reason=""` 时，继续按现有行为加载记忆文件列表、当前猫娘、自动记忆整理配置和新手引导。
- `blocking_reason` 非空时，不调用 `/api/memory/recent_files`、`/api/memory/review_config` 等普通业务接口；页面左侧显示存储状态，记忆列表区域显示受限启动占位文案。
- 如果 `bootstrap` 请求失败，存储卡片显示加载失败，记忆浏览可按当前逻辑继续尝试加载；失败原因不应被误报为迁移失败。

### 6.2 更改存储位置入口

点击“更改存储位置”后，建议打开一个页面内 modal，而不是跳转离开“记忆浏览”。

启用条件：

- 只有 `bootstrap.blocking_reason=""` 且后端 `status.ready=true` 时，按钮才允许进入“常驻迁移管理”。
- 在 `/api/storage/location/preflight` 落地前，阶段 1 的“更改存储位置”按钮可以先禁用，或打开只读说明 modal，但不能调用 `/select` 模拟预检。
- `selection_required/recovery_required/migration_pending` 只展示状态和恢复提示，不作为常驻迁移入口。

这里的 modal 指同一套网页 UI：

- 在普通浏览器中，它表现为页面内弹层。
- 在桌面端承载 `memory_browser` 页面时，它仍表现为页面内弹层，而不是桌面宿主单独维护的一套原生迁移弹窗。
- 桌面端可以对“选择目录”这一步提供宿主原生目录选择器，但迁移流程本身仍由网页 UI 和 NEKO 后端负责。

modal 内容复用 v0.8 选择流程：

- 当前路径
- 推荐路径
- 选择其他位置
- 预检卡片
- `确认关闭并迁移`
- 目标已有运行时数据时的二次确认

但需要注意：

- 不能直接引入当前 `static/app-storage-location.js` 的自动初始化版本，因为它会在加载后执行 startup barrier 逻辑。
- 建议先把 `app-storage-location.js` 拆成可复用控制器：
  - `autoStart: true` 用于首页首启阻断。
  - `autoStart: false` 用于“记忆浏览”常驻入口。
  - `mode: "startup"` 用于阻断流程。
  - `mode: "management"` 用于常驻迁移入口。
- 如果不拆模块，也可以先在 `memory_browser.js` 内实现轻量管理弹窗，但必须调用同一套后端 API，不能复制后端判断。

禁止在 `memory_browser.html` 里直接加入当前 `static/app-storage-location.js`：

- 该脚本当前会在加载后设置 `window.__nekoStorageLocationStartupBarrier = init()`。
- 它会自动执行 `beginSentinelFlow()`，适合首页首启阻断，不适合作为常驻管理入口。
- 如果要复用其中 UI，必须先拆出不会自启动的组件或控制器。

因此，阶段 1/2 更推荐在 `memory_browser.js` 内实现轻量 modal；等交互稳定后，再考虑抽取共享控制器。

### 6.3 选择目录

目录选择优先使用 host 能力：

- `window.nekoHost.pickDirectory({ startPath, title })`

如果 host 不可用，再调用：

- `POST /api/storage/location/pick-directory`

网页端只把用户选择提交给后端，最终展示以后端返回的 `selected_root/target_root` 为准。

这一点应继续与现有 `app-storage-location.js` 保持一致：优先走 host bridge，失败后再退回后端目录选择接口，而不是在 `memory_browser.js` 内再发明一套新的平台分支。

与 v0.8 文档的衔接：

- v0.8 设计中“首版目录选择器由 NEKO 后端 `pick-directory` 提供”仍是安全边界描述：后端始终是唯一校验者。
- 当前代码事实已经存在通用 `window.nekoHost.pickDirectory()`。v0.9 常驻入口可以优先使用它提升桌面端体验，但它只负责返回用户选中的路径，不参与归一化、预检、确认或迁移决策。

### 6.4 发起迁移

常驻入口里的迁移流程应是：

1. 用户选择目标。
2. 前端调用后端 `/api/storage/location/preflight` 预检接口。
3. 前端展示预检结果。
4. 用户点击“确认关闭并迁移”。
5. 如果后端提示目标已有运行时内容，前端弹出二次确认。
6. 前端调用后端发起迁移和受控关闭。
7. 页面切换到维护态，等待服务断开和恢复。

如果目标路径等于当前路径：

- 前端显示“当前已在该位置”，不调用写状态接口。

在 `/preflight` 实现前：

- 常驻入口不得把 `/select` 当成通用预检接口。
- 如果临时复用 `/select`，只能在目标路径已由前端判断为“不同于当前路径”后调用，并且必须把这条过渡实现标记为待删除。
- 第一阶段推荐不启用迁移发起按钮，只完成状态展示和打开目录能力。

### 6.5 打开当前目录

按钮优先使用：

- `window.nekoHost.openPath({ path })`

如果 host 不可用，可降级为复制路径或展示路径，不应尝试使用浏览器直接打开本地文件系统。

### 6.6 与首页首启阻断的关系

常驻入口不能改变首页首启阻断语义：

- 首页仍由 `window.appStorageLocation.init()` 决定是否阻断。
- “记忆浏览”入口只在页面已打开后提供主动管理能力。
- 如果当前全局处于 `migration_pending/recovery_required`，入口可以展示状态，但不能绕过阻断继续发起新的迁移。

## 7. 后端逻辑内容修改

本节只描述后端 API、状态机和文件安全逻辑。

### 7.1 保持后端权威

后端继续负责：

- 路径归一化。
- 判断普通自定义目录是否追加 `N.E.K.O` 子目录。
- 判断目标是否已有运行时内容。
- 判断是否需要二次确认。
- 创建 `storage_migration.json`。
- 写入 `root_state=maintenance_readonly`。
- 发起受控关闭。
- 迁移完成后的旧目录清理安全复验。

前端不能直接写：

- `storage_policy.json`
- `storage_migration.json`
- `root_state.json`

### 7.2 建议新增 side-effect-free 预检接口

当前 `POST /api/storage/location/select` 同时承担首启选择、继续当前会话和迁移预检。常驻入口属于已 ready 后的管理行为，为了减少误写状态，建议新增只读预检接口：

```http
POST /api/storage/location/preflight
```

请求：

```json
{
  "selected_root": "/path/from/user",
  "selection_source": "custom"
}
```

响应：

```json
{
  "ok": true,
  "result": "restart_required",
  "restart_mode": "migrate_after_shutdown",
  "selected_root": "/normalized/path/N.E.K.O",
  "target_root": "/normalized/path/N.E.K.O",
  "estimated_required_bytes": 123456,
  "target_free_bytes": 999999999,
  "permission_ok": true,
  "target_has_existing_content": true,
  "requires_existing_target_confirmation": true,
  "existing_target_confirmation_message": "目标路径已经包含现有数据..."
}
```

该接口必须保证：

- 不写 `storage_policy.json`。
- 不写 `storage_migration.json`。
- 不写 `root_state`。
- 不释放 startup barrier。
- 不触发关闭。

它可以复用 `main_routers/storage_location_router.py` 中现有 `_build_restart_preflight()` 和 `validate_selected_root()`。

建议行为：

- 当后端处于 ready 状态且目标不同于当前根时，返回 `ok=true`、`result=restart_required`、`restart_mode=migrate_after_shutdown` 和完整预检字段。
- 当目标等于当前根时，返回 `ok=true`、`result=restart_not_required`、`selected_root`，不写任何状态文件。
- 当 `blocking_reason` 非空、`root_state.mode=maintenance_readonly` 或已有 active migration 时，返回 `409 storage_bootstrap_blocking` 或更具体的 `migration_already_pending`，不写任何状态文件。
- 当路径非法、不可写或空间不足时，按现有校验语义返回错误或 `blocking_error_code`，不写任何状态文件。
- 常驻入口的 `/preflight` 不负责首启继续当前会话，也不负责 recovery rebind；这些仍属于 `/select` 和 `/restart` 的启动/恢复语义。

该接口不建议放到 `main_routers/memory_router.py`。虽然入口位于“记忆浏览”页面，但功能归属仍是 storage location，应继续放在 `main_routers/storage_location_router.py`，避免出现“记忆页路由也能写存储状态”的职责混乱。

补充说明：

- 当前仓库里已实现 `/api/storage/location/preflight`，常驻入口应使用该接口做管理预检。
- 常驻入口不应再临时复用 `/select` 获取 `restart_required` 预检结果，避免把“当前路径继续当前会话”这条分支暴露给 ready 状态下的管理入口。

### 7.3 继续保留现有启动选择接口

`POST /api/storage/location/select` 继续用于首启或恢复阻断场景：

- 选择当前路径并继续当前会话。
- recovery 场景下选择可用旧路径。
- 对不同路径返回 `restart_required` 预检结果。

常驻入口可以在过渡期复用该接口，但实施完成后推荐改为：

- 常驻入口预检：`/preflight`
- 常驻入口确认迁移：`/restart`
- 首启阻断继续：`/select`

原因是当前代码里的 `/select` 在 `selected_root == current_root` 时并非纯预检：

- 它会写入 `storage_policy.json`。
- 它可能释放 startup barrier。
- 它本质上是在执行“继续当前会话”的启动/恢复语义，而不是普通管理页的只读探测语义。

因此，ready 状态下的常驻入口不应把 `/select(current_root)` 当作普通“查看一下会不会迁移”的接口来调用。

### 7.4 发起迁移接口

继续使用：

```http
POST /api/storage/location/restart
```

常驻入口调用时必须传：

```json
{
  "selected_root": "/normalized/path/N.E.K.O",
  "selection_source": "custom",
  "confirm_existing_target_content": true
}
```

当 `requires_existing_target_confirmation=true` 且用户未确认时，后端必须返回 `409 target_confirmation_required`。

常驻入口调用 `/restart` 前必须已有一次成功的 `/preflight` 结果。`/restart` 仍要重新执行路径校验和目标已有内容确认校验，不能信任前端缓存的预检结果。

后端受理后：

- 创建或更新 `storage_migration.json`。
- 设置 `root_state.mode=maintenance_readonly`。
- 触发 `request_app_shutdown()`。
- 返回 `restart_initiated`。

### 7.5 状态接口增强

当前代码里的：

```http
GET /api/storage/location/status
```

已经适合做：

- 维护态轮询
- 迁移完成提示
- `completion_notice.cleanup_available` 展示

但它当前不直接返回：

- `current_root`
- `recommended_root`
- `legacy_sources`

因此在“不改后端接口”的前提下，常驻入口第一阶段不应只依赖 `status` 来渲染左侧卡片，而应以 `bootstrap` 为主。

如果后续希望常驻入口少发请求，可以增强：

```http
GET /api/storage/location/status
```

可额外返回：

```json
{
  "current_root": "...",
  "recommended_root": "...",
  "can_manage_storage_location": true,
  "can_start_migration": true,
  "cleanup_available": false,
  "retained_root": ""
}
```

兼容要求：

- 现有字段不能改名或删减。
- `ready`、`blocking_reason`、`storage.*` 的语义保持不变。
- 新字段只作为常驻入口的展示辅助。

### 7.6 受限启动与管理入口的互斥

当后端处于以下状态时，常驻入口不能发起新的迁移：

- `selection_required`
- `migration_pending`
- `recovery_required`
- `maintenance_readonly`
- 正在释放 startup barrier

后端应返回明确错误：

```json
{
  "ok": false,
  "error_code": "storage_bootstrap_blocking",
  "error": "当前存储状态仍需恢复或迁移，暂时不能发起新的存储位置变更。"
}
```

### 7.7 删除与清理安全

常驻入口可以显示旧目录清理按钮，但删除仍只能走：

```http
POST /api/storage/location/retained-source/cleanup
```

后端必须继续复验：

- 请求路径等于当前后端记录的 `retained_root`。
- `retained_root` 不等于当前运行根。
- `retained_root` 不会误删固定锚点下的 `state/cloudsave/.cloudsave_staging/cloudsave_backups`。
- 只删除迁移运行时条目。
- 清理完成后更新 `storage_migration.json` 和 `root_state.legacy_cleanup_pending=false`。

## 8. NEKO-PC 边界

NEKO-PC 不需要为常驻入口新增迁移状态机。

允许：

- 继续提供 `window.nekoHost.pickDirectory()`。
- 继续提供 `window.nekoHost.openPath()`。
- 继续提供 `window.nekoHost.closeWindow()`。
- 在桌面端打开“记忆浏览”页面时承载同一套网页常驻入口和页面内 modal。

禁止：

- NEKO-PC 直接调用 `/api/storage/location/restart` 绕过网页端二次确认。
- NEKO-PC 自己判断目标路径是否安全。
- NEKO-PC 额外维护一套独立于网页端的原生迁移状态机或原生迁移确认弹窗逻辑。
- NEKO-PC 写策略文件或删除旧目录。

## 9. 实施阶段建议

实施总原则：

- 先前端、再后端，但第一步只能做“只读常驻入口”，不能为了提前跑通按钮而复用 `/select` 发起带副作用的探测。
- 每个阶段都要保持 `storage-location-migration-design.md` 的安全边界：NEKO 后端拥有迁移决策，网页端拥有展示与确认，NEKO-PC 只提供宿主能力。
- 阶段 1 必须能独立合并；它不依赖新增后端 API，只依赖当前已有的 `bootstrap/status/openPath` 能力。
- 阶段 2 必须先落地 `/preflight`，再允许阶段 3 的“确认关闭并迁移”按钮进入可用状态。
- 所有实现都应保留可回退路径：如果常驻入口异常，只影响“存储位置”卡片，不影响记忆列表在 ready 状态下的原有使用。

推荐 PR 切分：

- PR 1：前端只读入口。修改 `memory_browser.html/css/js`、i18n 和前端测试，只展示状态、打开当前目录、limited-mode 占位，不发起迁移。
- PR 2：后端只读预检。新增 `/api/storage/location/preflight` 和单元测试，证明不写策略、不写迁移检查点、不释放 startup barrier。
- PR 3：前端管理 modal 与 `/restart` 接线。启用选择目录、预检结果、二次确认、确认关闭并迁移、维护态轮询。
- PR 4：迁移完成提示与旧目录清理入口。接入 `completion_notice.cleanup_available` 和 cleanup API，完善桌面端契约测试。

### 阶段 0：实施前检查清单

正式改代码前建议先完成一次轻量检查，避免边做边发现接口语义不一致：

- 确认 `main_server.py` 和 `memory_server.py` 在 limited-mode 下仍允许 `/memory_browser`、`/static/*`、`/api/storage/location/*`。
- 确认 `bootstrap` 当前返回 `current_root`、`recommended_root`、`legacy_sources`、`blocking_reason`。
- 确认 `status` 当前不依赖为左侧卡片提供 `current_root/recommended_root`，阶段 1 不能只用它渲染。
- 确认 `static/app-storage-location.js` 仍有自动 startup barrier 行为；如果还未拆模块，`memory_browser.html` 不能直接引入它。
- 确认 NEKO-PC 的 `pickDirectory/openPath/closeWindow` 仍只是通用 host API，没有新增迁移决策逻辑。

### 阶段 1：只读入口

- 在 `memory_browser.html` 增加“存储位置”卡片。
- 在 `memory_browser.css` 增加卡片与按钮样式。
- 在 `memory_browser.js` 先加载 `/api/storage/location/bootstrap` 并展示当前目录和阻断状态；阶段 1 不在左侧卡片展示推荐目录。
- `bootstrap.blocking_reason` 非空时，跳过普通 `/api/memory/*` 初始化，显示记忆浏览受限启动占位。
- 如需迁移完成提示或后续维护态接续，再辅以 `/api/storage/location/status`。
- 支持“打开当前目录”。
- “更改存储位置”按钮在阶段 1 不真正发起迁移；推荐保留为禁用占位并设置 `memory.storageManagementComingSoon` 提示，不调用 `/select`、`/restart` 或 `/preflight`。
- 把初始化接入现有 `DOMContentLoaded`，并在 `languageChanged` 时刷新动态文案。
- 同步扩充 `tests/frontend/test_memory_browser.py`，把 `bootstrap-first` 作为阶段 1 的硬性回归用例，而不是留到后端阶段再补。

验收：

- 页面打开后不触发迁移。
- ready 状态展示正确。
- `selection_required/recovery_required/migration_pending` 下不会调用 `/api/memory/recent_files` 和 `/api/memory/review_config`。
- 长路径不撑破左侧栏。
- 不引入 `app-storage-location.js` 自动阻断流程。
- 不改变记忆列表、自动记忆整理、新手引导重置的现有行为。
- 移除或隐藏存储卡片后，ready 状态下的记忆浏览仍按旧逻辑可用，方便快速回滚。

### 阶段 2：side-effect-free 预检

- 后端新增 `/api/storage/location/preflight`。当前已实现。
- ready 状态下启用“更改存储位置”按钮，前端可选择或输入目录并展示预检结果。当前已实现。
- 非 ready 状态下管理按钮保持禁用或只展示状态说明。
- 前端在本阶段仍不调用 `/restart`；只展示预检结果，避免预检和关闭迁移风险耦合。当前代码已继续完成阶段 3，因此实现上会在成功预检后显示“确认关闭并迁移”按钮。

验收：

- 预检不会写任何状态文件。
- 目标等于当前根时返回 `restart_not_required`，不会调用 `/select` 或 `/restart`。
- 目标已有运行时内容时返回二次确认要求。
- 选择普通目录会显示归一化后的 `<目录>/N.E.K.O`。
- blocking 状态下 `/preflight` 明确拒绝，不释放 startup barrier。

### 阶段 3：确认迁移

- 前端接入 `/api/storage/location/restart`。当前已实现。
- 维护态优先复用 v0.8 的交互语义与文案；若要直接复用现有 UI 代码，必须先把 `app-storage-location.js` 拆成 `autoStart=false` 的可复用控制器。
- 桌面端仍只观察后端状态。
- 当前选择在 `memory_browser.js` 内先实现轻量 modal，并把与后端交互的函数集中在 storage management section 中，避免散落到记忆文件列表逻辑里。
- 成功调用 `/restart` 后，前端隐藏“确认关闭并迁移”按钮并锁定目标输入、选择目录和预检按钮，避免应用关闭前重复提交。

验收：

- 点击确认后调用 `/restart`，由后端写迁移检查点、进入维护态并请求受控关闭。
- 目标已有运行时内容时必须先完成二次确认，`/restart` 才携带 `confirm_existing_target_content=true`。当前已有前端回归测试覆盖。
- 服务关闭后迁移并重启。
- 迁移完成后当前目录变为目标目录。
- 旧目录默认保留。

### 阶段 4：旧目录清理入口

- 常驻入口展示 `cleanup_available`。
- 允许打开迁移完成提示或清理确认。
- 清理走现有 cleanup API。
- 清理按钮只在完成迁移且后端明确返回可清理时显示；不要根据路径字符串在前端自行推断。

验收：

- 不会误删当前运行根。
- 不会误删固定锚点状态目录。
- 清理完成后入口状态刷新。

### 回滚策略

- PR 1 回滚：隐藏或删除“存储位置”卡片，并让 `DOMContentLoaded` 回到原有记忆列表初始化顺序；后端不受影响。
- PR 2 回滚：下线 `/preflight` 路由即可；阶段 1 的只读入口继续可用，但管理按钮保持禁用。
- PR 3 回滚：关闭管理 modal 的入口或 feature flag，保留只读展示；不要回滚 v0.8 的 `/restart` 主链路。
- PR 4 回滚：隐藏 cleanup 按钮即可；保留后端 cleanup API 和完成提示数据，不自动清理旧目录。

## 10. 测试建议

### 后端

- `tests/unit/test_storage_location_router.py`
  - `/preflight` 不写策略、迁移、root_state。
  - ready 状态下目标不同返回 `restart_required`。
  - ready 状态下目标相同返回 `restart_not_required`，且不释放 startup barrier。
  - 目标已有运行时内容返回确认要求。
  - blocking 状态下拒绝新迁移。
  - `/restart` 在已有 active migration 时拒绝新的常驻迁移。

- `tests/unit/test_storage_policy.py`
  - 普通自定义目录追加 `N.E.K.O`。
  - 已是 `N.E.K.O` 目录时不重复追加。

### 前端

- `node --check static/js/memory_browser.js`
- 扩充 `tests/frontend/test_memory_browser.py`，确保新增入口不破坏现有页面加载、选择记忆文件、自动记忆整理开关。
- Playwright：
  - “新手引导”下方出现“存储位置”。
  - 长路径换行正常。
  - 阶段 1 仅依赖 `bootstrap` 也能正确渲染当前目录。
  - limited-mode 下先请求 `bootstrap`，不请求 `/api/memory/recent_files` 和 `/api/memory/review_config`。
  - 阶段 1 中“更改存储位置”不会调用 `/select`、`/restart`。
  - ready 状态能打开管理弹窗。
  - ready 状态选择目标后调用 `/preflight`，展示后端归一化后的目标目录。
  - 目标已有内容时必须二次确认。
  - 确认迁移后进入维护态，并能轮询 `/status` 恢复。

### 桌面端契约

NEKO-PC 可补充契约测试：

- “记忆浏览”页面可使用 host `pickDirectory/openPath`。
- NEKO-PC 不出现 storage-location 专用状态机。
- 迁移决策仍由 NEKO 网页和后端完成。

## 11. 风险与约束

- 最大风险是把首启阻断脚本直接复用到常驻入口，导致打开“记忆浏览”时误触发阻断。实施前应先拆出可复用控制器或明确禁用 auto-start。
- 第二风险是误把当前 `status` 当成“已包含 current 路径的概览接口”使用，导致前端阶段 1 无法正确渲染左侧卡片。
- 第三风险是复用 `/select` 做常驻预检时误写策略。推荐新增 `/preflight` 降低副作用。
- 第四风险是用户误以为更改后立即生效。所有文案必须强调：确认后应用会关闭，迁移在关闭后执行，完成后重新启动。
- 第五风险是路径过长破坏左侧栏布局。CSS 必须对路径值做换行保护。
- 第六风险是 limited-mode 下 `memory_browser` 页面先调用普通 `/api/memory/*`，造成用户看到“记忆加载失败”而不是存储状态。必须在初始化顺序和测试中覆盖 `bootstrap-first`。

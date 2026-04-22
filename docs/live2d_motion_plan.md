# Live2D 动作选择与保存功能 - 实施文档

## 📊 概述

本文档记录 Live2D 模型"选择动作"功能的实现过程，包括动作保存、循环播放和主页自动恢复功能。

---

## ✅ 已完成功能

### 1. 动作保存功能
- 用户可在模型管理页面从下拉菜单选择 `.motion3.json` 动作文件
- 选择后点击"保存设置"，动作路径会持久化保存到 `characters.json`
- 保存格式：`_reserved.avatar.live2d.idle_animation`（后端通过 `set_reserved(..., 'avatar', 'live2d', 'idle_animation', ...)` 写入）
- 兼容层：顶层 `live2d_idle_animation` 字段仅用于旧版本展开视图和兼容读取，新配置应使用 `_reserved` 结构

### 2. 循环播放功能
- 选中的动作以循环模式播放
- 通过劫持 `motionManager.motionGroups[groupName][index]` 获取动作实例
- 调用 `setIsLoop(true)` 或设置 `_loop = true` 开启循环

### 3. 主页自动恢复
- 主页加载时自动读取保存的待机动作
- 模型完全就绪后（`onModelReady` 回调 + 500ms 延迟确保稳定）自动播放保存的动作
- 循环播放，无需用户干预

---

## 📁 修改的文件

| 文件 | 修改内容 |
|------|----------|
| `utils/config_manager.py` | 添加 `live2d.idle_animation` 字段迁移逻辑、legacy_keys 读取兼容 |
| `main_routers/characters_router.py` | 添加 `live2d_idle_animation` 保存处理和路径校验 |
| `static/js/model_manager.js` | 添加动作保存逻辑、循环播放、恢复函数；动作选择器状态管理；异步令牌竞态防护；保存快照纳入 Live2D 待机动作 |
| `static/app-interpage.js` | 添加 `restoreLive2DIdleAnimationOnMainPage()` 函数和 `_injectMotionGroupSafely()` 隔离 Helper |
| `static/live2d-init.js` | 添加 `onModelReady` 回调触发恢复函数、模型实例对比守卫 |
| `static/live2d-model.js` | 添加 `onModelReady` 回调选项支持、setMouth Index 缓存、loadToken 穿透保护、coreModel 快照校验、Idle 判定去 PreviewAll、视线跟踪状态冗余消除 |

---

## 🔑 关键技术点

### 数据结构

```javascript
// characters.json 中的保存格式（真实结构）
{
    "猫娘": {
        "小天": {
            "_reserved": {
                "avatar": {
                    "model_type": "live2d",
                    "live2d": {
                        "idle_animation": "motions/跳舞.motion3.json"
                    }
                }
            }
        }
    }
}
```

> **说明**：
> - `idle_animation` 通过 `set_reserved(characters['猫娘'][name], 'avatar', 'live2d', 'idle_animation', ...)` 写入
> - `model_type` 通过 `set_reserved(characters['猫娘'][name], 'avatar', 'model_type', ...)` 写入
> - 顶层 `live2d`（模型名称）、`live2d_idle_animation`、`model_type` 字段仅用于旧版本展开视图和兼容读取

### motionGroups vs definitions

这是一个关键的实现细节：

| 属性 | 用途 | 内容 |
|------|------|------|
| `definitions` | 存放配置字典（文件路径） | `{ File: "motions/xxx.motion3.json" }` |
| `motionGroups` | 存放解析后的动作实例（内存对象） | 必须是**空数组** `[]`，由 SDK 填充 |

**重要**：`motionGroups` 必须初始化为空数组 `[]`，不能放入配置对象！否则 SDK 会误认为动作已加载，跳过网络请求和解析，导致播放失败。

```javascript
// 正确做法
if (!motionManager.motionGroups) {
    motionManager.motionGroups = {};
}
if (!motionManager.motionGroups[groupName]) {
    motionManager.motionGroups[groupName] = [];  // 空数组！
}

// definitions 可以放入配置
motionManager.definitions[groupName] = motionsList;  // [{ File: "..." }]
```

### 恢复函数调用时机

主页的 `onModelReady` 回调在模型淡入完成后触发，此时物理预跑已经完成。为确保模型完全稳定，保留 500ms 延迟：

```javascript
// live2d-init.js
onModelReady: (model) => {
    setTimeout(() => {
        if (typeof window.restoreLive2DIdleAnimationOnMainPage === 'function') {
            window.restoreLive2DIdleAnimationOnMainPage();
        }
    }, 500);
}
```

### 动作加载流程

```text
1. 从 API 获取模型动作列表 → motionFiles
2. 构建 motionsList = [{ File: path }, ...]
3. 更新 definitions[groupName] = motionsList
4. 初始化 motionGroups[groupName] = []
5. 调用 loadMotion(groupName, index) 加载动作
6. 获取动作实例并设置循环
7. 停止当前动画
8. 调用 model.motion(groupName, index, priority) 播放
```

---

## 🐛 解决的问题

### 问题：t.setFinishedMotionHandler is not a function

**原因**：错误地将配置对象塞入了 `motionGroups`，导致 SDK 认为动作已加载但实际上是纯 JSON 对象。

**解决**：严格按照 `definitions` 存配置、`motionGroups` 存空数组的原则初始化。

### 问题：主页加载时 fileReferences 为空

**原因**：主页的 Live2D 模型加载方式与模型管理页面不同，没有初始化 PreviewAll 动作组。

**解决**：在恢复函数中通过 API `/api/live2d/model_files/{modelName}` 获取动作列表，然后手动构建 `definitions` 和 `fileReferences`。

---

## 📱 N.E.K.O.-PC 兼容性

`N.E.K.O.-PC` 是一个 Electron 桌面应用程序，它通过 `localhost` 加载主应用程序的网页内容。

**结论**：不需要额外同步。所有修改的静态文件（位于 `static/` 目录）会自动被 `N.E.K.O.-PC` 加载。

---

## 🧪 测试清单

- [ ] 模型管理页面：选择动作后立即播放
- [ ] 模型管理页面：保存设置后显示成功提示
- [ ] 主页加载：等待 500ms 后自动播放保存的待机动作
- [ ] 主页加载：待机动作循环播放
- [ ] 点击交互：点击模型触发其他动作
- [ ] 情绪切换：切换情绪时动作正常播放
- [ ] 数据持久化：刷新页面后保存的设置仍然有效

---

## 📝 相关代码位置

| 功能 | 文件 | 函数/行号 |
|------|------|-----------|
| 动作保存 | `model_manager.js` | `saveModelToCharacter()` |
| 动作选择播放 | `model_manager.js` | `motionSelect.change` 事件 |
| 循环播放设置 | `model_manager.js` | `motionSelect.change` 事件 |
| 主页恢复函数 | `app-interpage.js` | `restoreLive2DIdleAnimationOnMainPage()` |
| 恢复触发 | `live2d-init.js` | `initLive2DModel()` |
| 模型就绪回调 | `live2d-model.js` | `loadModel()` 完成处 |
| 后端保存校验 | `characters_router.py` | PUT `/api/characters/catgirl/l2d/{name}` |

---

## 🛡️ 代码质量与稳定性增强

### 1. setMouth 高频性能优化

**问题**：`setMouth` 被音频解析器以 60fps 调用，每次都通过字符串查找 `getParameterIndex()` 造成 CPU 瓶颈。

**解决**：引入 Index 缓存机制，首次调用时缓存口型参数的 Index，后续全部使用 `setParameterValueByIndex()` 直接写入。

```javascript
// 缓存逻辑
if (!this._cachedMouthIndices || this._cachedMouthIndicesModel !== coreModel) {
    this._cachedMouthIndices = [];
    this._cachedMouthIndicesModel = coreModel;
    for (const id of mouthIds) {
        const idx = coreModel.getParameterIndex(id);
        if (idx !== -1) this._cachedMouthIndices.push(idx);
    }
}
// 极速写入
for (const idx of this._cachedMouthIndices) {
    coreModel.setParameterValueByIndex(idx, this.mouthValue, 1);
}
```

---

### 2. Fetch 竞态条件防护（loadToken 穿透保护）

**问题**：在 `await fetch()` 让出主线程期间，用户切换模型会导致旧模型的请求返回时污染新模型。

**解决**：在每个 `await` 恢复后立即检查 `loadToken`，如果不匹配则直接中断。

```javascript
const response = await fetch(`/api/live2d/load_model_parameters/...`);
// 【重要修复】Fetch 回来后，必须检查 Token！
if (!this._isLoadTokenActive(loadToken)) return;

const data = await response.json();
if (!this._isLoadTokenActive(loadToken)) return;
// ... 继续处理
```

涉及两处 fetch：
- `/api/live2d/model_files/{modelName}` - 获取动作/表情文件列表
- `/api/live2d/load_model_parameters/{modelName}` - 加载保存的参数

---

### 3. 定时器溢出防护（coreModel 快照校验）

**问题**：`_scheduleReinstallOverride` 的 setTimeout 回调触发时，如果模型已被切换，会在错误的模型上执行 `installMouthOverride()`。

**解决**：记录 coreModel 快照，触发时校验是否为同一模型。

```javascript
const snapshotCoreModel = (this.currentModel && this.currentModel.internalModel) ?
                           this.currentModel.internalModel.coreModel : null;

this._reinstallTimer = setTimeout(() => {
    // ...
    if (this.currentModel.internalModel.coreModel !== snapshotCoreModel) {
        console.warn('[Live2D] 模型已切换，废弃旧的口型重装任务');
        return;
    }
    // ...
}, REINSTALL_OVERRIDE_DELAY_MS);
```

---

### 4. Idle 动作判定去黑魔法化

**问题**：`hasIdleInFileReferences` 错误地将 `PreviewAll` 组（有动作就认为有 Idle）作为判定依据，导致随机测试动作被当成待机动作播放。

**解决**：移除 `PreviewAll` 判定，只保留真正标有 `Idle` 的动作文件。

```javascript
// 修改前（错误）
const hasIdleInFileReferences = this.fileReferences &&
    (this.fileReferences.Motions?.['Idle'] ||
     (... PreviewAll 条件 ...));  // ❌ 会误判

// 修改后（正确）
const hasIdleInFileReferences = this.fileReferences &&
    (this.fileReferences.Motions?.['Idle'] ||
     (Array.isArray(this.fileReferences.Expressions) &&
      this.fileReferences.Expressions.some(e => (e.Name || '').startsWith('Idle'))));
```

---

### 5. 口型覆盖安装竞态防护

**问题**：`onModelReady` 回调的 500ms 延迟期间，用户可能已切换模型。

**解决**：在 setTimeout 执行时再次校验模型实例。

```javascript
onModelReady: (model) => {
    setTimeout(() => {
        // 【修复】防竞态：确保 500ms 后当前存活的模型仍然是触发这个回调的模型
        if (window.live2dManager && window.live2dManager.getCurrentModel() !== model) {
            console.log('[Live2D Init] 模型已在 500ms 延迟期间被切换或销毁，跳过待机动作恢复');
            return;
        }
        // ...
    }, 500);
}
```

---

### 6. SDK 黑魔法隔离（_injectMotionGroupSafely）

**问题**：直接修改 pixi-live2d-display SDK 的内部私有属性会导致未来 SDK 升级时代码崩溃。

**解决**：将所有内部结构修改隔离到独立 Helper 函数，并添加 JSDoc 废弃警告和 try-catch 防错。

```javascript
/**
 * [HACK/WORKAROUND] 动态向已加载的 Live2D 模型实例注入动作组。
 * @deprecated-if-sdk-upgraded 如果未来升级了 live2d SDK，此函数极易崩溃，请优先寻找官方 API 替代。
 */
function _injectMotionGroupSafely(live2dModel, groupName, motionFiles) {
    // ... 详细的参数校验和 try-catch
}
```

---

### 7. 配置迁移增强（legacy_keys 读取兼容）

**问题**：旧配置文件中 `live2d_idle_animation` 平铺在顶层，迁移逻辑可能遗漏。

**解决**：在 `get_reserved` 调用时添加 `legacy_keys` 参数，并在清理列表中加入该字段。

```python
# 读取时兼容
live2d_idle_animation = get_reserved(
    catgirl_data, "avatar", "live2d", "idle_animation",
    default=None,
    legacy_keys=("live2d_idle_animation",),  # 新增
)

# 清理时删除
for legacy_key in (... "live2d_idle_animation", ...):
    changed |= delete_reserved(catgirl_data, "avatar", legacy_key)
```

---

### 8. 动作选择器状态管理与竞态防护

**问题 1**：`motionSelect` 选择动作后，`isMotionPlaying` 被无条件设为 `false`，导致下一次点击播放按钮时的状态判断错乱。

**问题 2**：只修改 Live2D 动作时，`hasUnsavedChanges` 被误判为未更改，退出页面不提示。

**问题 3**：用户快速切换不同动作时，因网络/加载速度不同导致预览播放错位。

**解决**：

```javascript
// 1. 添加局部变量标记播放状态
let playedSelectedMotion = false;

// 2. 播放前清理预览定时器
if (window._motionPreviewRestoreTimer) {
    clearTimeout(window._motionPreviewRestoreTimer);
    window._motionPreviewRestoreTimer = null;
}
// ...

// 3. 条件重置播放状态
if (!playedSelectedMotion) {
    isMotionPlaying = false;
}
window.hasUnsavedChanges = true;

// 4. 生成异步令牌，防止快速切换导致加载顺序错乱
window._currentLive2DMotionToken = (window._currentLive2DMotionToken || 0) + 1;
const currentToken = window._currentLive2DMotionToken;

await motionManager.loadMotion(groupName, motionIndex);

// 加载完成后检查令牌是否过期
if (window._currentLive2DMotionToken !== currentToken) {
    console.log('[Live2D] 动作加载完成，但已过期被丢弃:', selectedValue);
    return;
}
```

---

### 9. Live2D 待机动作纳入保存快照

**问题**：保存快照函数 `getModelSnapshot()` 未包含 Live2D 待机动作的当前值。

**解决**：在快照中添加 `live2dIdleAnimation` 字段。

```javascript
live2dIdleAnimation: document.getElementById('motion-select')?.value ?? '',
```

---

### 10. 视线跟踪状态冗余消除（_isMouseTrackingActive）

**问题**：`_isMouseTrackingActive` 仅在模型加载时被赋值一次，与运行时动态开关 `_mouseTrackingEnabled` 产生错配，导致死鱼眼/抖动冲突。

**解决**：完全移除 `_isMouseTrackingActive`，直接在视线微动逻辑中读取动态的 `this._mouseTrackingEnabled`。

```javascript
// 修改前
if (this._isMouseTrackingActive) return;

// 修改后
if (this._mouseTrackingEnabled) return;
```

涉及 4 处修改：
- 元数据重置时移除赋值
- `_updateRandomLookAt` 函数读取真实开关
- 模型配置期间移除赋值
- 帧更新注入点读取真实开关

---

### 11. 口型覆盖安装竞态防护增强（destroyed guard）

**问题**：在 `onModelReady` 回调的 500ms 延迟期间，`currentModel` 可能仍短暂指向已销毁的旧模型，导致继续触发待机动作恢复。

**解决**：在模型引用比较之外，增加 `model.destroyed` 检查。

```javascript
// 修改前
if (window.live2dManager && window.live2dManager.getCurrentModel() !== model) {

// 修改后
if (window.live2dManager && (window.live2dManager.getCurrentModel() !== model || model.destroyed)) {
```

---

### 12. 待机动作路径校验增强（绝对路径拦截）

**问题**：用户可能传入绝对路径（如 `/foo.motion3.json`、`C:\foo.motion3.json`），导致后续恢复时加载错误。

**解决**：在 `characters_router.py` 中增加相对路径校验。

```python
# 新增校验
if live2d_idle_str.startswith('/') or live2d_idle_str.startswith('\\') or re.match(r'^[A-Za-z]:', live2d_idle_str):
    return JSONResponse(content={'success': False, 'error': 'Live2D待机动作路径必须是相对路径，不能是绝对路径'}, status_code=400)
```

---

### 13. 待机动作恢复读取兼容增强（空值语义保留）

**问题 1**：使用 `||` 会把空字符串（用户清空后的有效值）当成缺失，继续回退到其他字段。

**问题 2**：漏读 `live2d_idle_animation` 平铺字段，旧配置恢复失败。

**解决**：使用 `??` 替代 `||`，并增加平铺字段读取。

```javascript
// 修改前
const live2dIdleAnimation = charData?._reserved?.avatar?.live2d?.idle_animation
                         || charData?.avatar?.live2d?.idle_animation;

// 修改后
const live2dIdleAnimation = charData?._reserved?.avatar?.live2d?.idle_animation
                         ?? charData?.avatar?.live2d?.idle_animation
                         ?? charData?.live2d_idle_animation;  // 兼容旧版本平铺字段
```

---

### 14. 动作选择异步令牌入口强化（竞态防护）

**问题**：如果用户快速选了动作 A，紧接着又点了空选项（"增加动作"），因为空选项会直接 return 导致 Token 没有增加，A 加载完后一看 Token 没变，就强行播放了。

**解决**：把 Token 的生成往上提到事件监听器的入口第一行，无论用户中间切了模型、切了无效项，甚至点开了"增加动作"文件选择框，旧的动画回调都抓不到控制权。

```javascript
// 修改前（Token 生成在 if 有效动作块内）
motionSelect.addEventListener('change', async (e) => {
    const selectedValue = e.target.value;
    if (selectedValue === '') {
        // ... return 直接退出，Token 未递增
    }
    if (motionIndex >= 0 && live2dModel) {
        // 生成异步令牌
        window._currentLive2DMotionToken = (window._currentLive2DMotionToken || 0) + 1;
    }

// 修改后（Token 生成在入口）
motionSelect.addEventListener('change', async (e) => {
    // 生成异步令牌（置于入口，确保任何 change 都会使旧的 await 失效）
    window._currentLive2DMotionToken = (window._currentLive2DMotionToken || 0) + 1;
    const currentToken = window._currentLive2DMotionToken;

    const selectedValue = e.target.value;
    // ... 空值 return 时 Token 已经递增，旧的 loadMotion 完成时检测到 token 变化会丢弃
```

---

### 15. 恢复流程模型身份守卫（竞态防护）

**问题**：在 `restoreLive2DIdleAnimation` 的 `await fetchJson()` / `await loadMotion()` 期间，如果用户切换了模型，旧恢复流程仍会改下拉框并播放动作，导致新模型被过期恢复覆盖。

**解决**：在恢复流程入口捕获初始模型身份，在每个异步操作前后验证模型是否已切换。

```javascript
// 捕获初始模型身份
const initialModel = window.live2dManager?.getCurrentModel() || live2dModel;

const data = await RequestHelper.fetchJson('/api/characters/');
// 模型可能已在 await 期间切换
if (window.live2dManager?.getCurrentModel() !== initialModel) {
    console.log('[Live2D Restore] 模型已在 fetchJson 期间切换，跳过恢复');
    return;
}

// ... 修改下拉框 ...

// 确保模型在 loadMotion 期间未被切换
if (window.live2dManager?.getCurrentModel() !== initialModel) {
    console.log('[Live2D Restore] 模型已在 loadMotion 前切换，跳过恢复');
    return;
}

await motionManager.loadMotion(groupName, motionIndex);
```

---

### 16. 动作选择器四重守卫机制

**问题**：用户选 A 后立刻选空值/无效值/切模型，A 的 `loadMotion()` 完成后仍可能继续播放。

**解决**：在选择变化事件中增加四重复核：

```javascript
// 入口递增 Token（任何 change 触发即失效）
window._currentLive2DMotionToken = (window._currentLive2DMotionToken || 0) + 1;
const currentToken = window._currentLive2DMotionToken;
const selectedMotionId = selectedValue;

// loadMotion 前检查
if (window._currentLive2DMotionToken !== currentToken
    || motionSelect.value !== selectedMotionId
    || live2dModel !== window.live2dManager?.getCurrentModel()) {
    return; // 丢弃过期请求
}

await motionManager.loadMotion(groupName, motionIndex);

// loadMotion 后检查
if (window._currentLive2DMotionToken !== currentToken
    || motionSelect.value !== selectedMotionId
    || live2dModel !== window.live2dManager?.getCurrentModel()) {
    return; // 丢弃过期请求
}
```

| 场景 | 旧行为 | 新行为 |
|------|--------|--------|
| 选 A → 选空值 | A 的 `loadMotion` 完成后强行播放 | 空值分支先递增 token，A 完成后检测到 token 变化，丢弃 |
| 选 A → 快速选 B | A 的 `loadMotion` 完成后强行播放 | B 先递增 token，A 完成后检测到 token 变化，丢弃 |
| 选 A → 切换模型 | A 的 `loadMotion` 完成后强行播放 | 切换模型时 token 已递增，A 完成后检测到 token/model 变化，丢弃 |

---

## 🧪 待优化项

1. ~~**缩短恢复延迟**：目前固定 2 秒，可考虑监听物理预跑完成事件~~ ✅ 已优化：移除了固定延迟，改为使用 `onModelReady` 回调 + 500ms 延迟触发恢复函数
2. **错误处理**：网络请求失败时的用户体验优化
3. **多动作支持**：同时保存多个待机动作，随机播放

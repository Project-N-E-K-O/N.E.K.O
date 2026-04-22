# Live2D 动作选择与保存功能 - 实施文档

## 📊 概述

本文档记录 Live2D 模型"选择动作"功能的实现过程，包括动作保存、循环播放和主页自动恢复功能。

---

## ✅ 已完成功能

### 1. 动作保存功能
- 用户可在模型管理页面从下拉菜单选择 `.motion3.json` 动作文件
- 选择后点击"保存设置"，动作路径会持久化保存到 `characters.json`
- 保存格式：`live2d_idle_animation: "motions/动态.motion3.json"`（顶层字段）

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
| `utils/config_manager.py` | 添加 `live2d.idle_animation` 字段迁移逻辑 |
| `main_routers/characters_router.py` | 添加 `live2d_idle_animation` 保存处理和路径校验 |
| `static/js/model_manager.js` | 添加动作保存逻辑、循环播放、恢复函数 |
| `static/app-interpage.js` | 添加 `restoreLive2DIdleAnimationOnMainPage()` 函数 |
| `static/live2d-init.js` | 添加 `onModelReady` 回调触发恢复函数 |
| `static/live2d-model.js` | 添加 `onModelReady` 回调选项支持 |

---

## 🔑 关键技术点

### 数据结构

```javascript
// characters.json 中的保存格式
{
    "猫娘": {
        "帕朵": {
            "live2d": "星海伊束小天",
            "live2d_idle_animation": "motions/动态.motion3.json",
            "model_type": "live2d"
        }
    }
}
```

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

```
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
- [ ] 主页加载：等待 2 秒后自动播放保存的待机动作
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
| 后端保存校验 | `characters_router.py` | PUT `/api/characters/{name}` |

---

## 🔄 待优化项

1. ~~**缩短恢复延迟**：目前固定 2 秒，可考虑监听物理预跑完成事件~~ ✅ 已优化：移除了固定延迟，改为使用 `onModelReady` 回调触发恢复函数
2. **错误处理**：网络请求失败时的用户体验优化
3. **多动作支持**：同时保存多个待机动作，随机播放

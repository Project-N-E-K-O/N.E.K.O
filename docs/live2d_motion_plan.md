# Live2D动作选择与保存功能 - 实施计划

## 📊 当前状态分析

### 现有实现参考 (MMD/VRM)

| 参考位置 | 说明 |
|----------|------|
| [characters_router.py#L580-679](file:///c:\Users\xzq\Documents\GitHub\N.E.K.O-Himifox\main_routers\characters_router.py#L580-679) | MMD/VRM动画保存逻辑 |
| [model_manager.js#L1765](file:///c:\Users\xzq\Documents\GitHub\N.E.K.O-Himifox\static\js\model_manager.js#L1765) | `saveModelToCharacter()` 函数 |
| [model_manager.js#L1179-1198](file:///c:\Users\xzq\Documents\GitHub\N.E.K.O-Himifox\static\js\model_manager.js#L1179-1198) | motion-select DropdownManager配置 |

### Live2D现状

| 项目 | 状态 | 位置 |
|------|------|------|
| 动作选择器 | ✅ 已存在 | model_manager.html#L196-214 |
| 动作播放 | ✅ 已实现 | `live2dModel.motion('PreviewAll', motionIndex, 3)` |
| 保存功能 | ❌ 缺失 | 需要添加 |
| 循环播放 | ❌ 缺失 | 需要添加 |
| 加载时恢复 | ❌ 缺失 | 需要添加 |

---

## 🎯 实施计划

### 阶段一：数据结构设计

> **重要更新**: 根据现有代码分析，系统已存在 `live2d_idle_animation` 字段，建议复用该字段而非新建。

| 字段 | 用途 | 存储位置 |
|------|------|----------|
| `avatar.live2d.idle_animation` | 保存的动作文件相对路径 | characters.json |

**设计原则**:
- 统一字段名：直接复用已有的 `idle_animation` 字段
- 默认循环：保存的待机动作默认就是循环的，前端无脑执行 `setIsLoop(true)`
- 简化后端：省略 `live2d.loop` 字段，数据结构更简洁

### 阶段二：后端实现

#### 任务 2.1: 修改 `config_manager.py`

**文件**: `utils/config_manager.py`

- 确认 `Live2D` 配置结构中 `idle_animation` 字段已正确定义
- 添加迁移逻辑处理旧版本配置（如果不存在该字段则使用默认值 `null`）
- 参考现有的 VRM/MMD `idle_animation` 迁移逻辑

#### 任务 2.2: 修改 `characters_router.py`

**文件**: `main_routers/characters_router.py`

- 在 `PUT /api/characters/{name}` 处理函数中添加 `live2d.idle_animation` 字段处理
- 添加路径安全校验：
  - 不允许 `..`（路径遍历）
  - 必须为相对路径格式（如 `motions/动态.motion3.json`）
- 参考现有的 [MMD动画校验逻辑](file:///c:\Users\xzq\Documents\GitHub\N.E.K.O-Himifox\main_routers\characters_router.py#L598-612)

### 阶段三：前端保存功能

#### 任务 3.1: 修改 `saveModelToCharacter()` 函数

**文件**: `static/js/model_manager.js`

```javascript
// 位置: saveModelToCharacter() 函数内，currentModelType === 'live2d' 分支
// 需要添加:
if (motionSelect && motionSelect.value) {
    modelData.live2d = {
        idle_animation: motionSelect.value  // 相对路径，如 "motions/动态.motion3.json"
    };
}
```

**关键点**:
- 使用 `motionSelect.value` 获取当前选中的 `.motion3.json` 文件路径
- Live2D 动作文件是强绑定于具体模型的，保存相对路径即可
- 相对路径格式有利于模型文件夹重命名后依然有效

### 阶段四：循环播放功能

#### 任务 4.1: 分析 Cubism SDK 循环播放 API

**结论**: 无需修改底层核心库 `live2d-core.js`，直接劫持动作实例即可开启循环。

#### 任务 4.2: 实现循环播放功能

**文件**: `static/live2d-interaction.js` 或 `static/live2d-emotion.js`

```javascript
// 1. 确保动作已经加载到内存
await motionManager.loadMotion(groupName, index);

// 2. 获取真实的动作实例
const motionInstance = motionManager.motionGroups[groupName][index];

if (motionInstance) {
    // 3. 开启循环 (兼容不同版本的 SDK)
    if (typeof motionInstance.setIsLoop === 'function') {
        motionInstance.setIsLoop(true); // Cubism 4 原生方法
    } else {
        motionInstance._loop = true;    // Cubism 2 或某些魔改版的回退属性
    }
    console.log(`[Live2D] 已将动作 ${groupName}[${index}] 设置为循环播放`);
}

// 4. 强制播放
motionManager.stopAllMotions();
await this.currentModel.motion(groupName, index, 3);
```

**注意**: 使用此方法后，只要不调用 `stopAllMotions()` 或播放新动作，底层会无限循环，不需要自己写 `while` 循环。

### 阶段五：加载时恢复保存的动作

#### 任务 5.1: 修改模型加载逻辑

**文件**: `static/js/model_manager.js`

- 在加载Live2D模型后，检查 `avatar.live2d.idle_animation` 字段
- 如果存在保存的动作，自动执行循环播放
- 参考现有的 [VRM动画恢复逻辑](file:///c:\Users\xzq\Documents\GitHub\N.E.K.O-Himifox\static\js\model_manager.js#L5845-5850)

#### 任务 5.2: 动作路径拼接

**拼接公式**: `完整URL = this.modelRootPath + '/' + savedPath`

```javascript
// 示例
const savedMotionPath = charData.live2d_idle_animation; // 如 "motions/动态.motion3.json"
const fullMotionUrl = this.modelRootPath + '/' + savedMotionPath;
// 结果: "/user_live2d/模型名/motions/动态.motion3.json"
```

---

## 📝 详细任务清单

| # | 任务 | 文件 | 优先级 | 状态 |
|---|------|------|--------|------|
| 1 | 在 `config_manager.py` 确认 `live2d.idle_animation` 字段定义 | `utils/config_manager.py` | High | ⬜ |
| 2 | 在 `characters_router.py` 添加 `live2d.idle_animation` 保存处理和校验 | `main_routers/characters_router.py` | High | ⬜ |
| 3 | 在 `saveModelToCharacter()` 中添加 Live2D 动画保存逻辑 | `static/js/model_manager.js` | High | ⬜ |
| 4 | 实现 Live2D 动作循环播放功能（劫持动作实例） | `static/live2d-*.js` | High | ⬜ |
| 5 | 实现加载时自动恢复保存的动作 | `static/js/model_manager.js` | Medium | ⬜ |
| 6 | 添加国际化文本 (i18n) | `static/locales/*.json` | Low | ⬜ |

---

## ⚠️ 潜在风险与注意事项

### 1. 路径格式问题 ✅ 已解决
- Live2D 动作文件存储在模型文件夹的 `motions/` 子目录下
- 保存为相对路径（如 `motions/动态.motion3.json`）
- 播放时用 `this.modelRootPath + '/' + savedPath` 拼接完整URL

### 2. 循环播放实现 ✅ 已解决
- 直接劫持 `motionManager.motionGroups[groupName][index]` 获取动作实例
- 调用 `setIsLoop(true)` 或设置 `_loop = true` 开启循环
- 无需修改底层核心库

### 3. 向后兼容 ✅ 已解决
- 使用已有字段 `idle_animation`，旧版本配置自然兼容
- 加载旧配置时 `idle_animation` 为 `null` 或空，不影响正常加载

### 4. 测试要点
- 保存动作后刷新页面，确认动作被正确恢复
- 循环播放是否正常工作
- 切换模型后保存的动作不应影响新模型

---

## 🔧 技术参考

### 路径策略

```
模型根目录: /user_live2d/模型文件夹名/ 或 /static/模型文件夹名/
动作文件:   模型根目录/motions/子文件夹下

示例结构:
/user_live2d/Nekosang/
├── NekoChan.model3.json
└── motions/
    ├── 动态.motion3.json
    └── 静态.motion3.json
```

**保存格式**: 相对路径 `motions/动态.motion3.json`

**拼接公式**:
```javascript
const fullPath = modelRootPath + '/' + savedRelativePath;
// 例如: "/user_live2d/Nekosang/" + "motions/动态.motion3.json"
// 结果: "/user_live2d/Nekosang/motions/动态.motion3.json"
```

### 循环播放 API

```javascript
// 关键代码片段
const motionInstance = motionManager.motionGroups[groupName][index];

if (motionInstance) {
    if (typeof motionInstance.setIsLoop === 'function') {
        motionInstance.setIsLoop(true); // Cubism 4
    } else {
        motionInstance._loop = true;    // Cubism 2 回退
    }
}

motionManager.stopAllMotions();
this.currentModel.motion(groupName, index, 3);
```

### 现有动作保存格式 (MMD)

```python
# characters_router.py 中 MMD 动画保存格式
{
    "avatar": {
        "mmd": {
            "model_path": "/user_mmd/xxx.pmx",
            "animation": "/user_mmd/animation/xxx.vmd",
            "idle_animation": ["/user_mmd/animation/idle.vmd"]
        }
    }
}
```

### 现有动作保存格式 (VRM)

```python
# characters_router.py 中 VRM 动画保存格式
{
    "avatar": {
        "vrm": {
            "model_path": "/user_vrm/xxx.vrm",
            "animation": "/user_vrm/animation/xxx.vrma",
            "idle_animation": ["/user_vrm/animation/idle.vrma"]
        }
    }
}
```

### 最终 Live2D 动作保存格式

```python
# 最终 Live2D 动画保存格式（统一字段名，复用 idle_animation）
{
    "avatar": {
        "live2d": {
            "model_path": "Nekosang/NekoChan.model3.json",
            "idle_animation": "motions/动态.motion3.json"
        }
    }
}
```

---

## 📍 下一步行动

### 1. 确认 Live2D 动作文件的存储路径格式 ✅ 已解决
- 路径格式: 相对路径（如 `motions/动态.motion3.json`）
- 存储位置: 模型文件夹内的 `motions/` 子目录
- 拼接方式: `this.modelRootPath + '/' + savedPath`

### 2. 分析 Cubism SDK 的循环播放 API ✅ 已解决
- 无需修改 `live2d-core.js`
- 直接劫持 `motionManager.motionGroups[groupName][index]` 动作实例
- 调用 `setIsLoop(true)` 开启循环

### 3. 开始实施
- 从任务 1 开始：确认 `config_manager.py` 中的字段定义
- 然后依次推进到后端校验 → 前端保存 → 循环播放 → 加载恢复

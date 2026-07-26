# Achievement System Skill

专门用于管理和操作 N.E.K.O 项目的 Steam 成就系统。

## 功能概述

这个 skill 提供了完整的成就系统管理能力，包括：

- 📋 查看成就系统架构和流程
- ➕ 添加新的成就定义
- 🔧 修改现有成就配置
- 🎯 在代码中集成成就触发点
- 🧪 测试成就解锁功能
- 📊 查看成就统计和状态
- ⏱️ 基于 Steam Progress Stat（`PLAY_TIME_SECONDS`）自动解锁计时成就

## 已实现的成就

### 时长相关成就（Progress Stat）✅
1. **ACH_TIME_5MIN** - 茶歇时刻（5分钟 / 300 秒）
2. **ACH_TIME_1HR** - 渐入佳境（1小时 / 3600 秒）
3. **ACH_TIME_100HR** - 朝夕相伴（100小时 / 360000 秒）

> 官方推荐：在 Steamworks 后台创建 Stat `PLAY_TIME_SECONDS`，并将上述成就绑定为 Progress Stat（阈值分别为 300 / 3600 / 360000）。
> 游戏只负责本地累加并定期 `SetStat` + `StoreStats`；Steam 服务器在同步后自动解锁成就。

### 一次性成就
4. **ACH_FIRST_DIALOGUE** - 初次邂逅 ✅
5. **ACH_CHANGE_SKIN** - 焕然一新 ⏳
6. **ACH_WORKSHOP_USE** - 来自异世界的礼物 ⏳
7. **ACH_SEND_IMAGE** - 与你分享的世界 ⏳

### 计数型成就
8. **ACH_MEOW_100** - 喵语十级（50次）⏳

> 详细状态请查看 `ACHIEVEMENT_STATUS.md`

## 成就系统架构

### 核心文件

1. **前端成就管理器**
   - 文件：`static/achievement_manager.js`
   - 功能：统一管理所有成就的定义、解锁逻辑、计数器追踪；计时成就由主窗口/Pet 本地累加并每 60s / 页面隐藏时上报 Progress Stat（Chat 窗口不重复上报，避免双倍计时）

2. **后端 API**
   - 文件：`main_routers/system_router.py`
   - 端点：
     - `/api/steam/set-achievement-status/{name}` — 显式解锁（一次性/计数型）
     - `/api/steam/update-playtime` — 累加 `PLAY_TIME_SECONDS` 并 `StoreStats`（**不**调用 `SetAchievement`）

3. **Steam SDK**
   - 文件：`steamworks/interfaces/userstats.py`
   - 功能：与 Steam 客户端通信，触发成就弹窗

### 成就流程

```text
触发点 (app.js 等)
    ↓
window.unlockAchievement('ACH_NAME')
    ↓
achievement_manager.js 检查是否已解锁
    ↓
调用 /api/steam/set-achievement-status/ACH_NAME
    ↓
system_router.py 调用 Steamworks API
    ↓
steamworks.UserStats.SetAchievement()
    ↓
steamworks.UserStats.StoreStats()
    ↓
Steam 客户端弹出成就通知 🎉
```

### 计时成就流程（Progress Stat）

```text
achievement_manager.js
    ├─ 本地累加会话秒数
    ├─ 每 60s / pagehide / visibility hidden
    └─ POST /api/steam/update-playtime {seconds}
         ↓
system_router.py
    ├─ GetStatInt(PLAY_TIME_SECONDS)
    ├─ SetStat(PLAY_TIME_SECONDS, current + seconds)
    ├─ StoreStats() + run_callbacks()
    └─ 读取 progressUnlocked（Steam 已自动解锁的 ACH_TIME_*）
         ↓
Steam 服务器：Stat 达绑定阈值 → 自动解锁成就并弹窗
前端：仅同步本地缓存 / toast，不主动 SetAchievement
```

## 成就类型

### 1. 一次性成就 (checkOnce)
只需要触发一次即可解锁，不需要计数器。

```javascript
ACH_FIRST_DIALOGUE: {
    name: 'ACH_FIRST_DIALOGUE',
    description: '首次对话',
    checkOnce: true
}
```

**触发方式：**
```javascript
await window.unlockAchievement('ACH_FIRST_DIALOGUE');
```

### 2. 计数型成就 (counter + threshold)
需要达到一定次数才能解锁，使用计数器自动追踪。

```javascript
ACH_CHAT_100: {
    name: 'ACH_CHAT_100',
    description: '对话100次',
    counter: 'chatCount',
    threshold: 100
}
```

**触发方式：**
```javascript
// 每次对话时增加计数，达到阈值自动解锁
window.incrementAchievementCounter('chatCount');
```

## 常用操作

### 添加新成就

1. 在 `static/achievement_manager.js` 的 `ACHIEVEMENTS` 对象中添加定义
2. 在 Steam 后台配置相同的成就
3. 在代码中适当位置添加触发逻辑

### 查看成就状态

```javascript
// 浏览器控制台
window.getAchievementStats();
```

### 测试成就解锁

```javascript
// 手动解锁（测试用）
await window.unlockAchievement('ACH_NAME');

// 手动增加计数（测试用）
window.incrementAchievementCounter('chatCount', 10);
```

### 重置成就数据

```javascript
// 清除本地存储
localStorage.removeItem('neko_achievement_counters');
localStorage.removeItem('neko_unlocked_achievements');
location.reload();
```

## 主要集成位置

### app.js 中的触发点

1. **第 1453 行** - `checkAndUnlockFirstDialogueAchievement()` - 首次对话成就
2. **第 2224 行** - 麦克风按钮点击 - 语音相关成就
3. **第 2449 行** - 屏幕分享按钮点击 - 屏幕分享成就
4. **第 2751 行** - 文本发送按钮点击 - 对话计数成就
5. **模型切换处** - 搜索 "switchModel" - 模型切换成就

### 其他页面

- `chara_manager.js` - 角色创建成就
- `voice_clone.js` - 声音克隆成就
- `model_manager.js` - 自定义模型成就

## API 参考

### 全局函数

```javascript
// 解锁成就
await window.unlockAchievement(achievementName)

// 增加计数器
window.incrementAchievementCounter(counterName, amount)

// 获取统计信息
window.getAchievementStats()

// 检查是否已解锁
+window.achievementManager.isUnlocked(achievementName)
```

### 事件监听

```javascript
// 监听成就解锁事件
window.addEventListener('achievement-unlocked', (e) => {
    console.log('成就解锁:', e.detail.achievement);
});
```

## 本地存储

- `neko_achievement_counters` - 存储所有计数器的值
- `neko_unlocked_achievements` - 存储已解锁的成就列表

## Steam 后台配置

在 Steamworks 后台需要配置：
1. 成就 API 名称（与代码中的 `name` 字段一致）
2. 成就显示名称
3. 成就描述
4. 成就图标（已解锁/未解锁）

## 注意事项

1. **成就只能解锁，不能撤销** - 一旦解锁，无法通过代码撤销
2. **Steam 客户端必须运行** - 否则成就解锁会失败
3. **计时成就依赖 Steamworks Progress Stat 绑定** - 后台需创建 `PLAY_TIME_SECONDS` 并将 `ACH_TIME_*` 绑定阈值；游戏侧只上报 Stat，不主动 `SetAchievement`
4. **本地存储同步** - 计数器存储在 localStorage，清除浏览器数据会重置
5. **防重复解锁** - 成就管理器会自动检查，避免重复调用 API
6. **跨窗口通信** - 子窗口需要通过 `window.parent` 或 `window.opener` 访问主窗口的成就管理器

## 示例：添加一个新成就

### 1. 定义成就

在 `static/achievement_manager.js` 中：

```javascript
const ACHIEVEMENTS = {
    ACH_FIRST_DIALOGUE: {
        name: 'ACH_FIRST_DIALOGUE',
        description: '首次对话',
        checkOnce: true
    },
    // 添加新成就
    ACH_SCREENSHOT_10: {
        name: 'ACH_SCREENSHOT_10',
        description: '截图10次',
        counter: 'screenshotCount',
        threshold: 10
    }
};
```

### 2. 在代码中触发

在 `app.js` 的截图功能中（约第 2896 行）：

```javascript
screenshotButton.addEventListener('click', async () => {
    // ... 原有截图代码 ...

    // 添加成就触发
    if (window.incrementAchievementCounter) {
        window.incrementAchievementCounter('screenshotCount');
    }
});
```

### 3. Steam 后台配置

在 Steamworks 后台添加：
- API 名称：`ACH_SCREENSHOT_10`
- 显示名称：截图达人
- 描述：累计截图10次

### 4. 测试

```javascript
// 浏览器控制台
window.incrementAchievementCounter('screenshotCount', 10);
// 应该会自动解锁成就
```

## 故障排查

### 成就没有解锁？

1. 检查 Steam 客户端是否运行
2. 查看浏览器控制台是否有错误
3. 检查 Steam 后台是否配置了该成就
4. 确认成就 API 名称是否一致

### 计数器没有增加？

1. 检查 localStorage 中的数据：`localStorage.getItem('neko_achievement_counters')`
2. 确认触发代码是否执行
3. 查看控制台是否有错误

### 成就重复解锁？

成就管理器会自动防止重复解锁，如果出现重复，检查：
1. 是否有多个地方调用了 `unlockAchievement`
2. 本地存储是否被清除

## 相关文件

- `static/achievement_manager.js` - 成就管理核心
- `ACHIEVEMENT_INTEGRATION_GUIDE.md` - 详细集成指南
- `achievement_integration_example.js` - 集成示例代码
- `main_routers/system_router.py` - 后端 API
- `steamworks/interfaces/userstats.py` - Steam SDK 接口

# VRM 模块文件说明

## 文件结构

VRM 管理器已拆分为多个模块文件，便于维护和管理：

### 核心模块文件

1. **`vrm-core.js`** - 核心模块
   - 场景初始化
   - 模型加载
   - 性能管理
   - VRM 版本检测

2. **`vrm-expression.js`** - 表情模块
   - 表情设置和管理
   - 获取可用表情列表
   - 重置所有表情

3. **`vrm-animation.js`** - 动画模块
   - VRMA 动画播放
   - 口型同步功能
   - 动画状态管理

4. **`vrm-interaction.js`** - 交互模块
   - 拖拽和缩放功能
   - 鼠标跟踪
   - 锁定状态同步

5. **`vrm-manager.js`** - 主管理器
   - 整合所有模块
   - 提供统一的 API 接口
   - 向后兼容

### 加载顺序

这些文件必须按以下顺序加载：

```html
<script src="/static/vrm-core.js"></script>
<script src="/static/vrm-expression.js"></script>
<script src="/static/vrm-animation.js"></script>
<script src="/static/vrm-interaction.js"></script>
<script src="/static/vrm-manager.js"></script>
```

## 关于 ES6 模块

### 什么是 ES6 模块？

ES6 模块是 JavaScript 的官方模块系统，使用 `import` 和 `export` 语法：

**ES6 模块方式（参考文件使用的方式）：**
```javascript
// 文件 A: math.js
export function add(a, b) { return a + b; }

// 文件 B: main.js
import { add } from './math.js';
console.log(add(1, 2));
```

**当前项目使用的方式（全局变量）：**
```javascript
// 文件 A: math.js
function add(a, b) { return a + b; }
window.add = add;  // 导出到全局

// 文件 B: main.js
console.log(window.add(1, 2));  // 从全局使用
```

### 为什么当前项目不使用 ES6 模块？

1. **兼容性**：项目使用传统的 `<script>` 标签加载方式
2. **简单性**：全局变量方式更简单，不需要构建工具
3. **现有代码**：项目中的其他代码都使用全局变量方式

### 当前实现方式

虽然拆分成多个文件，但每个文件都导出到全局变量（`window.XXX`），这样：
- ✅ 保持与现有代码的兼容性
- ✅ 不需要修改构建配置
- ✅ 可以直接在浏览器中使用
- ✅ 代码结构更清晰，便于维护

## 使用方式

使用方式保持不变：

```javascript
// 创建管理器实例
const vrmManager = new VRMManager();

// 初始化场景
await vrmManager.initThreeJS('vrm-canvas', 'vrm-container');

// 加载模型
await vrmManager.loadModel('/user_vrm/model.vrm');

// 播放动画
await vrmManager.loadAndPlayAnimation('/user_vrm/animation.vrma');
```

## 模块访问

如果需要直接访问某个模块：

```javascript
// 访问核心模块
vrmManager.core.detectVRMVersion(vrm);

// 访问表情模块
vrmManager.expression.setExpression('happy', 0.8);

// 访问动画模块
vrmManager.animation.playVRMAAnimation(url, options);

// 访问交互模块
vrmManager.interaction.enableMouseTracking(true);
```


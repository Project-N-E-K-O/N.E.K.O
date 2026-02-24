# 开发者笔记

每位 N.E.K.O. 贡献者必须了解的关键规则和注意事项。这些都是从项目实践中总结出的宝贵经验。

## 核心规则

::: danger 必须遵守
以下规则在整个代码库中强制执行。
:::

### 1. 始终使用 `uv` 运行任何命令

所有 Python 命令必须通过 `uv` 执行：

```bash
# ✅ Correct
uv run python main_server.py
uv run pytest tests/

# ❌ Wrong
python main_server.py
pytest tests/
```

### 2. 所有用户可见文本必须国际化

项目支持 5 种语言（`en`、`zh-CN`、`zh-TW`、`ja`、`ko`）。所有用户可见的字符串都必须经过国际化系统处理。

- **HTML**：使用 `data-i18n` 属性
- **JS**：使用 `window.t('key')` 并提供中文回退
- 语言文件位于 `static/locales/`

完整指南请参阅[国际化](/frontend/i18n)。

### 3. 隐私敏感日志：仅使用 `print()`

任何可能包含**原始用户对话数据**的日志必须使用 `print()`，绝不使用 `logger`。这确保敏感数据不会进入持久化日志文件。

```python
# ✅ User conversation data
print(f"User said: {user_message}")

# ✅ System events use logger
logger.info("Session started for character: %s", lanlan_name)

# ❌ Never log user conversations with logger
logger.info(f"User said: {user_message}")  # BAD!
```

### 4. 翻译时保留系统提示词水印

翻译系统提示词时（无论出于何种原因），必须保留标记 `======以上为`。这是用于提示词边界检测的内部水印。

### 5. Steam 成就不可撤销

Steam 成就一旦解锁，就**无法通过代码撤销**。在部署前务必使用控制台命令充分测试成就逻辑：

```javascript
// Test in browser console
await window.unlockAchievement('ACH_NAME');
window.getAchievementStats();
```

## 前端注意事项

### 国际化会破坏 HTML 图标

当 i18next 通过 `textContent` 更新元素文本时，会销毁元素内部的任何 `<img>` 或 `<span>` 标签。如果你的翻译字符串包含 HTML，国际化系统会检测到并改用 `innerHTML`。如果你要在可翻译元素中添加图标，请将 HTML 写入语言 JSON 文件中：

```json
{
  "button.save": "<img src='icon.svg'> Save"
}
```

### `overflow: hidden` 会破坏 `<select>` 下拉菜单

胶囊 UI 系统使用大圆角，这常常导致开发者给容器添加 `overflow: hidden`。这会裁剪原生 `<select>` 下拉菜单。修复方法：

```css
/* Any container with a <select> inside */
.field-row-with-select {
  overflow: visible !important;
}
```

### 按钮交互公式

所有按钮必须遵循以下交互模式以保持一致的手感：

```css
.button:hover {
  transform: translateY(-1px);
  /* enhanced shadow */
}
.button:active {
  transform: translateY(1px) scale(0.98);
}
```

### 原生 JS 竞态条件（DOM 懒加载）

由于 N.E.K.O. 使用原生 JavaScript 而非响应式框架，DOM 元素在代码运行时可能尚不存在——特别是在首次点击时才延迟创建的弹窗和 HUD 组件。

::: warning 永远不要使用固定的 `setTimeout` 进行 DOM 绑定
硬编码的 `setTimeout(..., 100)` 会错过尚未创建的元素。请改用自终止的递归轮询：
:::

```javascript
const bindEvents = () => {
    const getEl = (ids) => {
        for (let id of ids) {
            const el = document.getElementById(id);
            if (el) return el;
        }
        return null;
    };

    const targetEl = getEl(['live2d-agent-keyboard', 'vrm-agent-keyboard']);

    if (!targetEl) {
        setTimeout(bindEvents, 500); // Retry until DOM exists
        return;
    }

    // Found — bind and stop polling
    targetEl.addEventListener('change', myLogic);
    myLogic(); // Trigger first check
};

setTimeout(bindEvents, 100); // Start polling
```

**乐观 UI 冲突**：当切换按钮被点击时，UI 会在后端请求进行中时乐观地翻转到"开启"状态。如果另一个组件（例如轮询循环）在此窗口期读取 DOM，可能会看到过时的状态。防范方法是在信任元素值之前检查元素是否处于加载/禁用状态。

### UI 设计系统：胶囊 UI + Neko Blue

项目有严格的视觉系统：

| 令牌 | 值 | 用途 |
|------|-----|------|
| `--color-n-main` | `#40C5F1` | 品牌蓝：标题、主按钮、激活状态 |
| `--color-n-deep` | `#22b3ff` | 描边/深蓝：文字轮廓、聚焦发光 |
| `--color-n-light` | `#e3f4ff` | 浅蓝背景 |
| `--color-n-border` | `#b3e5fc` | 边框蓝：胶囊边框、分隔线 |
| `--radius-capsule` | `50px` | 所有交互元素 |
| `--radius-card` | `20px` | 卡片和容器 |

字体：
- **拉丁文**：`'Comic Neue'`、`'Segoe UI'`、`Arial`
- **CJK**：`'Source Han Sans CN'`、`'Noto Sans SC'`
- **等宽字体**（API 密钥、ID）：`'Courier New', monospace`

完整设计系统请参阅 `.agent/skills/ui-system-refactor/references/design-system.md`。

## 后端注意事项

### Gemini API 响应格式

Gemini 可能会将 JSON 响应包裹在 markdown 代码块中：

````
```json
{"emotion": "happy"}
```
````

在解析前务必去除 markdown 包裹：

```python
if result_text.startswith("```"):
    lines = result_text.split("\n")
    if lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    result_text = "\n".join(lines).strip()
```

### Gemini `extra_body` 需要双层嵌套

通过 OpenAI 兼容 API 控制 Gemini 的思考模式时，`extra_body` 必须双层嵌套：

```python
# ✅ Correct: double nesting
extra_body = {
    "extra_body": {
        "google": {
            "thinking_config": {
                "thinking_budget": 0  # Disable thinking for 2.5
            }
        }
    }
}

# ❌ Wrong: single nesting (causes "Unknown name 'google'" error)
extra_body = {
    "google": {
        "thinking_config": {"thinking_budget": 0}
    }
}
```

### 思考模式因服务商而异

每个 LLM 服务商禁用扩展推理的格式各不相同：

| 服务商 | 格式 |
|--------|------|
| Qwen, Step, DeepSeek | `{"enable_thinking": false}` |
| GLM | `{"thinking": {"type": "disabled"}}` |
| Gemini 2.x | `{"thinking_config": {"thinking_budget": 0}}` |
| Gemini 3.x | `{"thinking_config": {"thinking_level": "low"}}` |

`config/__init__.py` 模块会自动处理此映射——请查看 `MODELS_EXTRA_BODY_MAP`。

## VRM 模型注意事项

### SpringBone 物理爆炸

VRM 物理使用 `vrm.update(delta)`，其中 `delta` 必须以**秒**为单位，而非毫秒。如果头发/衣物在加载时向上飞起：

```javascript
let delta = clock.getDelta();
delta = Math.min(delta, 0.05); // Clamp to prevent explosion on tab switch
vrm.update(delta);
```

### 碰撞体过大（影响约 100% 的 VRM 模型）

从 VRoid Studio/UniVRM 导出的 VRM 模型存在一个已知 bug，碰撞体半径约为正常值的 2 倍（[UniVRM #673](https://github.com/vrm-c/UniVRM/issues/673)）。这会导致头发呈水平固定状态。

**修复方法**：加载后将所有碰撞体半径缩小 50%：

```javascript
const COLLIDER_REDUCTION = 0.5;
springBoneManager.colliders.forEach(collider => {
    if (collider.shape?.radius > 0) {
        collider._originalRadius = collider.shape.radius;
        collider.shape.radius *= COLLIDER_REDUCTION;
    }
});
```

### MToon 轮廓线粗细

当 VRM 模型被缩放时，MToon 轮廓线会变得不成比例地粗。切换到屏幕空间轮廓线：

```javascript
material.outlineWidthMode = 'screenCoordinates';
material.outlineWidthFactor = 0.005; // Thin, consistent outline
material.needsUpdate = true;
```

### 3D 摄像机：像素到世界坐标映射

在为 VRM 模型实现拖拽/缩放时，**永远不要使用固定的平移速度**。根据摄像机距离动态计算像素到世界坐标的映射：

```javascript
const worldHeight = 2 * Math.tan(fov / 2) * cameraDistance;
const pixelToWorld = worldHeight / screenHeight;
// Mouse delta * pixelToWorld = world space movement
```

## 测试

### 测试结构

```
tests/
├── unit/          # OmniOffline/Realtime 客户端、服务商连接测试
├── frontend/      # 每个 Web UI 页面的 Playwright 测试
├── e2e/           # 完整用户旅程（8 个阶段，需要 --run-e2e 标志）
└── utils/         # 基于 LLM 的响应质量评估器
```

### 运行测试

```bash
# All tests (excluding e2e)
uv run pytest tests/ -s

# Unit tests only
uv run pytest tests/unit -s

# Frontend tests (requires Playwright browsers)
uv run playwright install
uv run pytest tests/frontend -s

# E2E tests (requires explicit flag)
uv run pytest tests/e2e --run-e2e -s
```

### 测试用 API 密钥

将 `tests/api_keys.json.template` 复制为 `tests/api_keys.json` 并填入你的密钥。此文件已被 gitignore 忽略。

## Issue 模板

提交 Bug 或请求功能时，请使用 GitHub issue 模板：

- **Bug 报告**：包含复现步骤、预期与实际行为、以及环境信息
- **功能请求**：描述功能、使用场景及任何相关背景

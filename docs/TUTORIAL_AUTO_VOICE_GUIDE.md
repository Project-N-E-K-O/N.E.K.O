# N.E.K.O 新手引导语音系统文档

## 概述

N.E.K.O 新手引导语音系统为用户提供高质量的语音引导体验。当用户首次访问页面时，系统自动启动新手引导，并在每个步骤显示时朗读步骤标题和描述。

系统使用 **Edge TTS**（Microsoft Neural Voices）作为主要语音引擎，提供自然、清晰的语音效果，并在网络不可用时自动回退到浏览器内置的 `speechSynthesis` API。

---

## 系统架构

```
┌─────────────────────────────────────────────────┐
│                   前端浏览器                      │
│                                                   │
│  UniversalTutorialManager                         │
│    │                                              │
│    ├── driver.js (高亮 + 弹窗导航)                 │
│    │                                              │
│    └── TutorialAutoVoice                          │
│          │                                        │
│          ├── 内存缓存 (Map, LRU, 最多50条)         │
│          │     ↓ 命中 → 直接播放 blob URL           │
│          │                                        │
│          ├── fetch POST /api/tutorial-tts/synthesize│
│          │     ↓ 成功 → 缓存 + HTMLAudioElement 播放│
│          │                                        │
│          └── 回退 → 浏览器 speechSynthesis API      │
│                (网络错误或后端不可用时自动触发)       │
└────────────────────┬────────────────────────────┘
                     │ HTTP POST
                     ↓
┌─────────────────────────────────────────────────┐
│                  后端 FastAPI                     │
│                                                   │
│  tutorial_tts_router.py                           │
│    │                                              │
│    ├── 磁盘缓存检查                                │
│    │     {config_dir}/cache/tutorial_tts/{hash}.mp3│
│    │     ↓ 命中 → 直接返回 FileResponse             │
│    │                                              │
│    └── edge-tts 合成                               │
│          edge_tts.Communicate(text, voice).save()  │
│          → 原子写入缓存 → 返回 MP3                   │
└─────────────────────────────────────────────────┘
```

---

## 核心文件

| 文件 | 作用 |
|------|------|
| `static/tutorial_auto_voice.js` | 前端语音模块，负责请求、缓存和播放音频 |
| `static/universal-tutorial-manager.js` | 教程管理器，在步骤切换时调用语音模块 |
| `main_routers/tutorial_tts_router.py` | 后端 Edge TTS 合成接口 |
| `static/css/tutorial-styles.css` | 教程 UI 样式 |
| `static/libs/driver.min.js` | 步骤高亮和弹窗导航库 |

---

## 语音选择

系统根据当前 i18n 语言自动选择对应的 Microsoft Neural 语音：

| 语言代码 | 语音名称 | 描述 |
|----------|----------|------|
| `zh-CN` | `zh-CN-XiaoxiaoNeural` | 中文女声，年轻甜美 |
| `zh-TW` | `zh-TW-HsiaoChenNeural` | 台湾中文女声 |
| `en` | `en-US-JennyNeural` | 英文女声，自然清晰 |
| `ja` | `ja-JP-NanamiNeural` | 日文女声，自然柔和 |
| `ko` | `ko-KR-SunHiNeural` | 韩文女声 |
| `ru` | `ru-RU-SvetlanaNeural` | 俄文女声 |

所有语音均为 Microsoft Neural 语音，完全免费，无需 API Key。

---

## API 接口

### POST `/api/tutorial-tts/synthesize`

合成语音并返回 MP3 音频文件。

**请求体 (JSON):**
```json
{
    "text": "欢迎使用 N.E.K.O 新手引导",
    "lang": "zh-CN"
}
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `text` | string | 是 | 要合成的文本，最大 500 字符 |
| `lang` | string | 否 | 语言代码，默认 `zh-CN` |

**响应:**
- 成功: `200 OK`，返回 `audio/mpeg` 格式的 MP3 文件
- 文本为空或过长: `400 Bad Request`
- edge-tts 未安装: `503 Service Unavailable`
- 合成失败: `502 Bad Gateway`

### GET `/api/tutorial-tts/voices`

列出所有可用的教程语音。

**响应:**
```json
{
    "voices": {
        "zh-CN": "zh-CN-XiaoxiaoNeural",
        "en": "en-US-JennyNeural",
        "ja": "ja-JP-NanamiNeural"
    },
    "default": "en-US-JennyNeural"
}
```

### POST `/api/tutorial-tts/cleanup-cache`

手动触发缓存清理，删除超过 7 天的缓存文件。

---

## 前端 TutorialAutoVoice 类

### 公共方法

| 方法 | 说明 |
|------|------|
| `speak(text, options?)` | 播放文本（停止当前播放后立即播放） |
| `enqueue(text, options?)` | 添加到播放队列 |
| `stop()` | 停止当前播放 |
| `clearQueue()` | 清空播放队列 |
| `pause()` | 暂停播放 |
| `resume()` | 恢复播放 |
| `setEnabled(bool)` | 启用/禁用语音 |
| `setRate(number)` | 设置语速 (0.5 - 2.0) |
| `setPitch(number)` | 设置音调 (0 - 2，仅回退模式) |
| `setVolume(number)` | 设置音量 (0 - 1) |
| `isAvailable()` | 检查是否可用 |
| `checkSpeaking()` | 检查是否正在播放 |
| `getStatus()` | 获取当前状态 |
| `destroy()` | 销毁模块，释放资源 |

### speak() 参数

```javascript
tutorialVoice.speak('要朗读的文本', {
    lang: 'zh-CN',  // 可选，覆盖当前语言
    rate: 1.0,       // 可选，覆盖语速
    volume: 1.0      // 可选，覆盖音量
});
```

### 状态对象 (getStatus)

```javascript
{
    isAvailable: true,
    isEnabled: true,
    isSpeaking: false,
    isPaused: false,
    queueLength: 0,
    voiceMode: 'Edge TTS',
    language: 'zh-CN',
    cacheSize: 5,
    rate: 1.0,
    pitch: 1.0,
    volume: 1.0
}
```

---

## 缓存机制

### 两级缓存

1. **前端内存缓存** (`Map<cacheKey, blobURL>`)
   - 最多 50 条，LRU（最近最少使用）淘汰策略
   - 页面导航后清空
   - 缓存键基于 `语言:文本` 的哈希值

2. **后端磁盘缓存** (`{config_dir}/cache/tutorial_tts/`)
   - 文件名: `{SHA-256(voice:text)}.mp3`
   - 永久缓存，自动清理超过 7 天的文件
   - 原子写入（临时文件 + 重命名），避免并发读取不完整文件

### 缓存命中流程

```
speak("你好") → 检查前端缓存
    ├── 命中 → 直接播放 blob URL（瞬时）
    └── 未命中 → 请求后端
                    ├── 后端磁盘缓存命中 → 返回 MP3（快速）
                    └── 后端缓存未命中 → edge-tts 合成 → 缓存 → 返回 MP3
```

---

## 回退机制

当 Edge TTS 后端不可用时（网络错误、服务器未启动、edge-tts 未安装等），系统自动回退到浏览器内置的 `speechSynthesis` API。

回退触发场景：
- `fetch` 请求失败（网络断开）
- 后端返回非 200 状态码
- `Audio.play()` 播放失败

回退语音选择优先级（中文）：
1. Microsoft Huihui Desktop
2. Microsoft Xiaoxiao Desktop
3. Google 普通话
4. 当前语言匹配的系统语音
5. 第一个可用语音

---

## 与教程管理器的集成

`UniversalTutorialManager` 在以下时机调用语音模块：

| 时机 | 调用 | 说明 |
|------|------|------|
| 构造函数 | `new TutorialAutoVoice()` | 创建语音模块实例 |
| driver 步骤事件 | `_speakCurrentStep()` → `speak(voiceText, { lang })` | 在 `driver.on('next')` 回调中同步调用，朗读步骤标题 + 描述 |
| 教程结束时 | `stop()` + `clearQueue()` | 清理所有语音状态 |

**重要**：语音播放在 `driver.on('next')` 事件回调中触发（而非 `onHighlighted` 配置回调），因为自定义 `driver.min.js` 不调用 `config.onHighlighted`。`driver.on('next')` 事件在步骤切换时同步触发，保持用户手势链以满足浏览器自动播放策略。

语音文本格式：`步骤标题 + 分隔符 + 步骤描述`
- 中文: `步骤标题。步骤描述`
- 其他语言: `Step Title. Step Description`

---

## 支持的教程页面

| 页面 | URL | 步骤数 |
|------|-----|--------|
| 首页 | `/` | 20+ |
| 模型管理 | `/model_manager` | 3-4 |
| 参数编辑器 | `/parameter_editor` | 2 |
| 表情管理 | `/emotion_manager` | 3 |
| 角色管理 | `/chara_manager` | 3 |
| 设置 | `/api_key` | 2 |
| 声音克隆 | `/voice_clone` | 5 |
| 记忆浏览器 | `/memory_browser` | 2 |

---

## 依赖

### 后端
- `edge-tts >= 6.1.0` — Microsoft Edge TTS Python 客户端（免费，无需 API Key）
- `fastapi` — Web 框架（已有）

### 前端
- `driver.js` — 步骤高亮和导航（已有，本地 `static/libs/driver.min.js`）
- `Web Speech API` — 浏览器内置，作为回退方案
- `HTMLAudioElement` — 播放 Edge TTS 生成的 MP3 音频

---

## 配置与自定义

### 修改默认语速/音量

在 `TutorialAutoVoice` 构造函数中修改默认值：

```javascript
this.rate = 1.0;    // 语速 0.5 - 2.0
this.volume = 1.0;  // 音量 0 - 1
```

### 添加新语言语音

1. 在 `main_routers/tutorial_tts_router.py` 的 `EDGE_TTS_VOICE_MAP` 中添加：
   ```python
   'fr': 'fr-FR-DeniseNeural',  # 法语女声
   ```

2. 在 `_normalize_lang()` 函数中添加对应的语言规范化逻辑。

3. 可选：在 `tutorial_auto_voice.js` 的 `_cleanText()` 方法中添加 N.E.K.O 的本地化发音替换。

### 查看可用的 Edge TTS 语音

```bash
edge-tts --list-voices
```

筛选特定语言的语音：
```bash
edge-tts --list-voices | grep zh-CN
```

---

## 故障排除

| 问题 | 可能原因 | 解决方案 |
|------|----------|----------|
| 没有声音播放 | 浏览器自动播放策略 | 确保用户先点击了页面（教程由用户操作触发，通常不会触发此问题） |
| 语音质量差（机器人声） | 使用了浏览器回退模式 | 检查后端 edge-tts 是否安装：`pip install edge-tts` |
| 后端返回 503 | edge-tts 未安装 | 运行 `pip install edge-tts` |
| 后端返回 502 | Edge TTS 服务不可达 | 检查网络连接（edge-tts 需要访问 Microsoft 服务器） |
| 首次播放延迟 | 首次合成需要网络请求 | 正常现象，后续播放会从缓存加载（瞬时） |
| 切换语言后语音不变 | 缓存了旧语言的音频 | 缓存键包含语言，切换语言后会自动使用新语音 |

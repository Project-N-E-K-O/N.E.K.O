# dump_llm_input.py

离线还原 N.E.K.O 对话系统发送给 LLM 的完整输入（`messages` 数组），用于调试、分析和测试。

## 功能

脚本复用项目现有模块，从本地存储文件中读取角色配置与记忆数据，按照运行时相同的逻辑拼装出完整的 system prompt，输出为 OpenAI `chat.completions` 格式的 `messages` JSON。

**无需启动任何服务**——脚本直接读取磁盘文件，绕过 `memory_server` HTTP 层。

## System Prompt 结构

输出的 system message 由以下区块按顺序拼接而成：

| # | 区块 | 数据来源 |
|---|------|----------|
| 1 | 会话开场指令 | `config/prompts_sys.py` → `SESSION_INIT_PROMPT` |
| 2 | 角色核心人设 | `characters.json` 的自定义 `system_prompt`，或 `config/prompts_chara.py` 的默认模板 |
| 3 | 长期记忆 (Persona) | `Documents/N.E.K.O/memory/{角色名}/persona.json` |
| 4 | 反思印象 | `ReflectionEngine` 提供的 pending / confirmed reflections |
| 5 | 近期对话回顾 | `Documents/N.E.K.O/memory/{角色名}/recent.json` |
| 6 | 时间上下文 | 聊天间隔、当前时间、节假日 |
| 7 | 结尾标记 | `CONTEXT_SUMMARY_READY`——通知角色即将开始对话 |

## 用法

使用项目虚拟环境运行：

```bash
# 默认：输出当前活跃角色的完整 messages JSON
python tests/dump_llm_input.py

# 指定角色
python tests/dump_llm_input.py -c 天凌

# 附带模拟用户消息
python tests/dump_llm_input.py -m "你好呀"

# 输出纯文本（不含 JSON 包装）
python tests/dump_llm_input.py --raw

# 写入文件
python tests/dump_llm_input.py -o prompt_dump.json

# 指定语言（zh/en/ja/ko/ru）
python tests/dump_llm_input.py -l en

# 组合
python tests/dump_llm_input.py -c 天凌 -m "今天过得怎么样" -o dump.json
```

### 参数

| 参数 | 缩写 | 说明 |
|------|------|------|
| `--character` | `-c` | 角色名称，省略时使用当前活跃角色 |
| `--user-message` | `-m` | 模拟用户发送的第一条消息 |
| `--lang` | `-l` | 语言代码，省略时自动检测系统语言 |
| `--output` | `-o` | 输出文件路径，默认为 `tests/prompt_dump.json` |
| `--raw` | — | 仅输出原始 system prompt 文本 |
| `--flat` | — | 输出旧版扁平 OpenAI messages 数组格式 |

## 输出格式

### 默认：结构化 JSON（推荐）

默认输出将 system prompt 拆分为**背景信息**（`background`）和**对话内容**（`conversation`）两大块，便于分析各部分的构成：

```json
{
  "metadata": {
    "character": "天凌",
    "master": "碳基生物",
    "language": "zh",
    "dump_time": "2026-04-13T21:14:00",
    "system_prompt_chars": 3170,
    "approx_tokens": 1585
  },
  "background": {
    "session_init": "你是一个角色扮演大师。请按要求扮演以下角色（天凌）。\n",
    "character_prompt": "A fictional character named 天凌 ...",
    "persona_header": "======天凌的长期记忆======\n",
    "persona_content": "### 关于天凌\n- 昵称: 天凌喵\n..."
  },
  "conversation": {
    "context_header": "======以下是天凌的内心活动======\n",
    "context_timestamp": "现在时间是Monday, April 13, 2026 at 09:14 PM。...",
    "recent_history": [
      { "speaker": "SYSTEM_MESSAGE", "content": "先前对话的备忘录: ..." },
      { "speaker": "天凌", "content": "废话。\n..." },
      { "speaker": "碳基生物", "content": "没有哦" }
    ],
    "time_context": "距离上次与碳基生物聊天已经过去了1小时7分钟。\n",
    "holiday_context": null
  },
  "closing": "======以上为前情概要。...======\n",
  "user_message": "你好呀"
}
```

| 字段 | 说明 |
|------|------|
| `metadata` | 导出元信息（角色、语言、字符数等） |
| `background.session_init` | 会话开场指令 |
| `background.character_prompt` | 角色核心人设 |
| `background.persona_header` | 长期记忆区块标题 |
| `background.persona_content` | 长期记忆内容（Persona / 反思） |
| `conversation.context_header` | 对话回顾区块标题 |
| `conversation.context_timestamp` | 当前时间戳说明 |
| `conversation.recent_history` | 近期对话条目数组，每条含 `speaker` + `content` |
| `conversation.time_context` | 聊天间隔提示（可能为 null） |
| `conversation.holiday_context` | 节假日上下文（可能为 null） |
| `closing` | 结尾标记 |
| `user_message` | 模拟用户消息（未指定时为 null） |

### `--flat`：旧版扁平格式

使用 `--flat` 恢复旧版 OpenAI messages 数组格式：

```json
[
  {
    "role": "system",
    "content": "你是一个角色扮演大师。请按要求扮演以下角色（天凌）。\n..."
  },
  {
    "role": "user",
    "content": "你好呀"
  }
]
```

### 控制台摘要

运行结束后 stderr 会打印摘要统计：

```
============================================================
角色          : 天凌
主人          : 碳基生物
语言          : zh
System prompt : 3170 字符 (~1585 tokens)
输出格式      : structured
============================================================
```

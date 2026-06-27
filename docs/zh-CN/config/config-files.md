# 配置文件

配置文件存储在当前平台标准应用数据目录下的 `N.E.K.O/` 子目录中：Windows 为 `%LOCALAPPDATA%\N.E.K.O\`，macOS 为 `~/Library/Application Support/N.E.K.O/`，Linux 为 `$XDG_DATA_HOME/N.E.K.O/`（未设置时回退到 `~/.local/share/N.E.K.O/`）。

## 文件位置

| 文件 | 用途 |
|------|------|
| `core_config.json` | API 密钥、提供商选择、自定义端点 |
| `characters.json` | 角色定义和人设数据 |
| `user_preferences.json` | UI 偏好设置、模型选择 |
| `voice_storage.json` | 自定义语音配置 |
| `workshop_config.json` | Steam 创意工坊设置 |
| `tutorial_prompt_config.json` | 新手引导提示阈值与流程状态 |

## `core_config.json`

主运行时配置文件。

```json
{
  "coreApiKey": "",
  "coreApi": "qwen",
  "assistApi": "qwen",
  "assistApiKeyQwen": "",
  "assistApiKeyOpenai": "",
  "assistApiKeyGlm": "",
  "assistApiKeyStep": "",
  "assistApiKeySilicon": "",
  "assistApiKeyGemini": "",
  "mcpToken": "",
  "agentModelUrl": "",
  "agentModelId": "",
  "agentModelApiKey": ""
}
```

## `characters.json`

定义所有角色及拥有者档案信息。

```json
{
  "主人": {
    "档案名": "哥哥",
    "性别": "男",
    "昵称": "哥哥"
  },
  "猫娘": {
    "小天": {
      "性别": "女",
      "年龄": 15,
      "昵称": "T酱, 小T",
      "live2d": "mao_pro",
      "voice_id": "",
      "system_prompt": "..."
    }
  },
  "当前猫娘": "小天"
}
```

顶层键名为中文，且代码严格依赖这些字面键名：`主人`（拥有者档案）、`猫娘`（以名字为键的角色映射表）、`当前猫娘`（当前激活角色的名字）。`master`、`catgirl` 等英文键名不会被识别，会被忽略。

角色字段是灵活的——可以添加任意键值对，这些键值对都会被包含在角色的上下文中。

## 文件发现

`ConfigManager` 类（`utils/config_manager.py`）负责文件发现：

1. 优先使用当前平台的标准应用数据目录（Windows `%LOCALAPPDATA%`、macOS `~/Library/Application Support`、Linux `$XDG_DATA_HOME` 或 `~/.local/share`），并在其下创建/读取 `N.E.K.O/`。
2. 若标准目录不可用，回退到历史遗留位置（如 `~/Documents/N.E.K.O/`、可执行文件所在目录、当前工作目录）——这些仅用于读取/导入旧数据。
3. 回退到项目自带的 `config/` 目录。
4. 如果不存在任何文件，则创建默认文件。

旧版的 `~/Documents/N.E.K.O/` 路径（在 Windows 上通过 Windows API `SHGetFolderPathW` 解析）现在只作为遗留数据导入的候选项，不再是主存储位置。

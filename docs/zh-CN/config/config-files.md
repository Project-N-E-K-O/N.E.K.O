# 配置文件

配置文件存储在用户文档目录下的 `N.E.K.O/` 文件夹中。

## 文件位置

| 文件 | 用途 |
|------|------|
| `core_config.json` | API 密钥、提供商选择、自定义端点 |
| `characters.json` | 角色定义和人设数据 |
| `user_preferences.json` | UI 偏好设置、模型选择 |
| `voice_storage.json` | 自定义语音配置 |
| `workshop_config.json` | Steam 创意工坊设置 |

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

定义所有角色和主人（拥有者）的信息。

```json
{
  "master": {
    "档案名": "哥哥",
    "性别": "男",
    "昵称": "哥哥"
  },
  "catgirl": {
    "小天": {
      "性别": "女",
      "年龄": 15,
      "昵称": "T酱, 小T",
      "live2d": "mao_pro",
      "voice_id": "",
      "system_prompt": "..."
    }
  }
}
```

角色字段是灵活的——可以添加任意键值对，这些键值对都会被包含在角色的上下文中。

## 文件发现

`ConfigManager` 类（`utils/config_manager.py`）负责文件发现：

1. 检查用户文档目录（`~/Documents/N.E.K.O/`）
2. 回退到项目的 `config/` 目录
3. 如果不存在任何文件，则创建默认文件

在 Windows 上，文档目录通过 Windows API 解析。在 macOS/Linux 上，使用 `~/Documents/`。

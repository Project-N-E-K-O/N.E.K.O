# 配置文件

N.E.K.O. 将**可写用户数据**与**仓库内置默认值**分开保存。

## 可写数据根目录

默认数据目录为：

- Windows：`%LOCALAPPDATA%\N.E.K.O`
- macOS：`~/Library/Application Support/N.E.K.O`
- Linux：`$XDG_DATA_HOME/N.E.K.O`，未设置时为 `~/.local/share/N.E.K.O`

用户可选择其他存储位置。排查其他机器时应查看当前存储位置，不要假设仍是默认路径。

选定根目录的 `config/` 可包含：

| 文件 | 用途 |
| --- | --- |
| `core_config.json` | Provider、凭据、模型角色覆盖及功能设置 |
| `characters.json` | 角色定义与保留头像数据 |
| `user_preferences.json` | 用户/UI 偏好 |
| `voice_storage.json` | 语音元数据 |
| `workshop_config.json` | 创意工坊设置 |

功能还可能创建其他文件。优先使用 Web UI，因为它按当前 schema 写入。

仓库的 `config/` 不是普通用户数据目录：`api_providers.json` 定义 Provider 与相关目录；`characters/*.json` 提供 8 个 locale 默认值；Python 模块提供常量与回退。`utils/config_manager/` 首次运行时迁移支持的默认配置，之后写入可写根目录。

角色标识和显示名来自当前角色数据。多形象保留结构位于 `_reserved.avatar`；旧顶层 `live2d` 可能为兼容继续存在。代码紧急回退与 locale JSON 默认值并不相同，不能把翻译显示名写死成 ID。

手工编辑前停止程序、备份数据根并保留 JSON 类型与保留字段。个人配置和密钥不得提交。

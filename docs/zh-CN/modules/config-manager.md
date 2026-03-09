# 配置管理器

**文件：** `utils/config_manager.py`（约 1490 行）

`ConfigManager` 是一个单例类，集中处理所有配置的加载、验证和持久化。

## 访问方式

```python
from utils.config_manager import get_config_manager

config = get_config_manager()
```

## 关键方法

### 角色数据

```python
config.get_character_data()      # 获取所有角色（返回 master_name、her_name、stores 等）
config.load_characters()          # 从磁盘重新加载
config.save_characters()          # 持久化到磁盘
```

### API 配置

```python
config.get_core_config()                     # API 密钥、提供商、端点
config.get_model_api_config(model_type)      # 特定角色的配置（realtime/tts_custom/agent 等）
config.is_agent_api_ready()                  # 检查 Agent API 是否已配置
```

### 语音管理

```python
config.load_voice_storage()                  # 加载所有语音数据
config.save_voice_storage()                  # 持久化语音数据
config.get_voices_for_current_api()          # 获取当前 API 提供商的语音
config.save_voice_for_current_api(voice)     # 为当前提供商保存语音
config.delete_voice_for_current_api(voice_id) # 删除语音
```

### 文件系统

```python
config.get_workshop_path()        # Steam 创意工坊目录
config.ensure_live2d_directory()  # 确保 Live2D 模型目录存在
config.ensure_vrm_directory()     # 确保 VRM 模型目录存在
```

### 迁移

```python
config.migrate_config_files()     # 从旧路径迁移配置文件
config.migrate_memory_files()     # 从旧路径迁移记忆文件
```

## 目录结构

配置管理器解析并管理以下目录：

| 属性 | 用途 |
|------|------|
| `docs_dir` | 用户文档目录 |
| `app_docs_dir` | `~/Documents/N.E.K.O/` |
| `config_dir` | 配置文件 |
| `memory_dir` | 记忆存储 |
| `live2d_dir` | Live2D 模型 |
| `vrm_dir` | VRM 模型 |
| `vrm_animation_dir` | VRM 动画文件 |
| `workshop_dir` | Steam 创意工坊内容 |
| `chara_dir` | 角色数据 |
| `project_config_dir` | 项目级配置 |
| `project_memory_dir` | 项目级记忆 |

## 配置解析

配置管理器实现了[优先级链](/config/config-priority)：

1. 检查环境变量（`NEKO_*`）
2. 检查用户配置文件（`core_config.json`）
3. 检查 API 提供商定义（`api_providers.json`）
4. 回退到代码默认值（`config/__init__.py`）

## 文件发现

管理器按以下方式搜索用户文档目录：

- **Windows**：`SHGetFolderPathW`（CSIDL_PERSONAL）→ 注册表回退 → `USERPROFILE` 回退。同时搜索同驱动器的"文档"/"Documents"/"My Documents"候选路径。
- **macOS/Linux**：`~/Documents/`

`_get_project_root()` 方法检测项目根目录以访问捆绑资源。

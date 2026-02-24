# 配置管理器

**文件：** `utils/config_manager.py`（约 1500 行）

`ConfigManager` 是一个单例类，集中处理所有配置的加载、验证和持久化。

## 访问方式

```python
from utils.config_manager import get_config_manager

config = get_config_manager()
```

## 关键方法

### 角色数据

```python
config.get_character_data()      # 获取所有角色
config.load_characters()          # 从磁盘重新加载
config.save_character(name, data) # 持久化更改
```

### API 配置

```python
config.get_core_config()              # API 密钥、提供商、端点
config.get_model_api_config(model_type)  # 特定模型角色的配置
```

### 文件系统

```python
config.get_workshop_path()        # Steam 创意工坊目录
config.ensure_live2d_directory()  # 创建 Live2D 模型目录
config.ensure_vrm_directory()     # 创建 VRM 模型目录
```

## 配置解析

配置管理器实现了[优先级链](/config/config-priority)：

1. 检查环境变量（`NEKO_*`）
2. 检查用户配置文件（`core_config.json`）
3. 检查 API 提供商定义（`api_providers.json`）
4. 回退到代码默认值（`config/__init__.py`）

## 文件发现

管理器按以下顺序搜索配置文件：

1. 用户文档目录（`~/Documents/N.E.K.O/`）
2. 项目 `config/` 目录
3. 如果未找到任何文件，则创建默认值

在 Windows 上，文档路径通过 Windows API（`SHGetFolderPath`）解析。在 macOS/Linux 上，使用 `~/Documents/`。

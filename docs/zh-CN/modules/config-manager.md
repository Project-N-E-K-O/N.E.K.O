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
config.save_characters(data)      # 持久化全部角色（同步，会阻塞事件循环）
config.asave_characters(data)     # 异步版本（async 路径用此方法）
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

管理器按以下顺序搜索运行时数据根目录（其中存放 `config/`、`memory/` 等）：

1. 当前平台的标准应用数据目录：
   - Windows：`%LOCALAPPDATA%\N.E.K.O\`（例如 `C:\Users\<你>\AppData\Local\N.E.K.O\`）
   - macOS：`~/Library/Application Support/N.E.K.O/`
   - Linux：`$XDG_DATA_HOME/N.E.K.O/`（未设置时为 `~/.local/share/N.E.K.O/`）
2. 历史遗留位置（仅用于一次性数据导入 / 最后兜底）：用户文档目录（`~/Documents/N.E.K.O/`，在 Windows 上通过 Windows API `SHGetFolderPath` 解析）、可执行文件所在目录（冻结打包构建）以及当前工作目录。
3. 若均无可用项，则在应用数据目录下创建默认文件。

源码模式下随源码树附带的项目 `config/` 文件仍相对仓库根目录解析。

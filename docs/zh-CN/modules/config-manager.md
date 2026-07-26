# 配置管理器

**Package：** `utils/config_manager/`

`ConfigManager` 是运行时存储与持久化配置的进程级 facade。它由存储根、迁移、角色、核心 API 配置、音色存储、配额和 Workshop 等 mixin 组成；package facade 保留稳定的 `utils.config_manager` 导入路径。

## 访问与迁移

```python
from utils.config_manager import get_config_manager

config = get_config_manager()
```

`get_config_manager()` 在进程内缓存一个实例。首次正常访问时会运行配置、卡面和记忆迁移。遗留 Documents 记忆导入是 best-effort；即使失败也不会阻止启动。

启动 phase 0 可调用 `get_config_manager(migrate=False)`，只创建管理器而不执行迁移。`reset_config_manager_cache()` 用于测试和受控重新初始化；应用代码不应创建相互竞争的多个实例。

## 运行时存储根与文件解析

选定的运行时根目录包含 `config/`、`memory/`、`plugins/`、`live2d/`、`vrm/`、`character_cards/` 等目录。根目录由 storage policy 解析；启动/存储流程可通过 `NEKO_STORAGE_SELECTED_ROOT` 和 `NEKO_STORAGE_ANCHOR_ROOT` 注入路径。这两个变量并不是针对每个配置项的通用环境变量覆盖机制。

如果先前选定的根目录不可用，管理器会在 anchor root 进入恢复状态，而不会把恢复位置静默当成新的已确认选择。存储状态不安全时，cloud-save write fence 可以拒绝普通写入。本地状态目录故障以结构化的 `LocalStateDirectoryError` 暴露。

对于 JSON 配置：

- `get_config_path(name)` 先读取运行时 `config/` 副本，再回退到项目 `config/` 默认文件；
- `get_runtime_config_path(name)` 始终指向可写的运行时副本；
- 文件不存在或不可读时，`load_json_config()` 会返回调用方所给默认值的深拷贝；
- `save_json_config()` 原子写入运行时副本，并执行 cloud-save write fence。

Provider profile 与代码默认值由对应的 core/voice 方法解析。项目不存在适用于所有设置的统一“环境变量 → 用户文件 → provider 文件 → 默认值”链。

## 主要契约

### 角色数据

```python
characters = config.load_characters()
config.save_characters(characters)          # 同步文件写入
await config.asave_characters(characters)   # 卸载到线程的异步包装

current = config.get_character_data()
current_async = await config.aget_character_data()
```

`get_character_data()` 返回的不是原始角色字典。它会解析当前主人和猫娘、有效人设 payload 与 prompt、名称映射和逐角色记忆路径，并以九项 tuple 返回。需要原始持久化映射时应使用 `load_characters()`。

角色缓存由锁保护，并使用文件修改时间检查更新。当前角色选择损坏时，管理器可能切换到第一个有效角色并尝试写回修复。

### API 与音色配置

```python
core = config.get_core_config()
core_async = await config.aget_core_config()
conversation = config.get_model_api_config("conversation")
agent_ready = config.is_agent_api_ready()

voices = config.load_voice_storage()
config.save_voice_storage(voices)
```

这些方法会规范化持久化配置，并解析角色/用途与 provider 专用设置。音色 helper 还会根据当前 provider 验证预置和克隆音色 ID。

### 目录与集成

管理器为模型、插件、记忆、cloud-save 状态和 Workshop 数据提供路径及目录创建 helper。Package 还导出了便利函数 `get_plugins_directory()`。

## 线程与错误行为

`load_json_config()`、`save_json_config()`、`load_characters()`、`save_characters()` 等文件系统方法均为同步调用，不能直接放在延迟敏感的事件循环上。角色和核心配置读取提供了基于 `asyncio.to_thread()` 的异步包装。

缺失文件可以使用调用方提供的默认值；但无效 JSON、原子写失败、write fence 拒绝以及本地状态目录不可用仍会暴露给调用方，不会被伪装成成功写入。

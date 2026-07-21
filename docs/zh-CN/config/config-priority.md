# 配置优先级

项目不存在统一的“环境变量 > 用户文件 > Provider 文件 > 代码”规则，必须按配置面判断。

## 用户与模型

`utils/config_manager/` 从代码默认值、选中的 Provider profile 和可写 `core_config.json` 组装运行时配置。Provider、密钥、已验证端点和支持的角色覆盖由该加载器处理。

Provider profile 来自 `config/api_providers.json`。文件缺失或损坏时，core/assist 回退到代码默认值；assist JSON 覆盖同名代码默认值，并保留只存在于代码中的回退 profile。

## 端口

`config/network.py` 依次读取：

1. `NEKO_<PORT_NAME>`
2. 兼容用的裸 `<PORT_NAME>`
3. Electron 的 `port_config.json`
4. 代码默认端口

首选端口被占用时，launcher 可能选择回退端口并传给子服务。

## Docker

`docker/entrypoint.sh` 只在 `/app/config/core_config.json` 不存在，或设置 `NEKO_FORCE_ENV_UPDATE` 时，用 API 相关 `NEKO_*` 生成它。这是初始化，不是通用实时覆盖。持久化用户数据可能已有更新配置，启动后应在 Web UI 确认实际值。

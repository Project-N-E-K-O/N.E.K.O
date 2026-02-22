# 配置概览

N.E.K.O. 使用分层配置系统，支持多种配置来源。配置值按优先级从高到低的顺序解析。

## 优先级链

1. **环境变量**（最高）— `NEKO_*` 前缀
2. **用户配置文件** — `core_config.json`、`user_preferences.json`
3. **API 提供商配置** — `api_providers.json`
4. **代码默认值**（最低）— `config/__init__.py`

## 快速参考

| 配置内容 | 位置 |
|----------|------|
| API 密钥和提供商 | [环境变量](./environment-vars) 或 Web UI `/api_key` 页面 |
| 配置文件位置 | [配置文件](./config-files) |
| 可用 AI 提供商 | [API 提供商](./api-providers) |
| 按任务选择模型 | [模型配置](./model-config) |
| 覆盖机制说明 | [配置优先级](./config-priority) |

## Web UI 配置

通过 Web UI 配置 N.E.K.O. 是最简便的方式：

- **API 密钥：** `http://localhost:48911/api_key`
- **角色设置：** `http://localhost:48911/chara_manager`
- **模型管理：** `http://localhost:48911/model_manager`

通过 Web UI 所做的更改会自动保存到相应的配置文件中。

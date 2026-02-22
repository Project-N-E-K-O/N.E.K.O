# 配置优先级

N.E.K.O. 通过分层优先级系统解析配置值。高优先级的来源会覆盖低优先级的来源。

## 优先级顺序

```
┌─────────────────────────────────┐  最高优先级
│  1. 环境变量                     │  NEKO_* 前缀
│     （在 shell 或 .env 中设置）  │
├─────────────────────────────────┤
│  2. 用户配置文件                 │  core_config.json
│     （~/Documents/N.E.K.O/）    │  user_preferences.json
├─────────────────────────────────┤
│  3. API 提供商配置               │  config/api_providers.json
│     （项目目录）                 │
├─────────────────────────────────┤
│  4. 代码默认值                   │  config/__init__.py
│     （硬编码的回退值）           │
└─────────────────────────────────┘  最低优先级
```

## 解析示例

以摘要模型为例：

1. 检查 `NEKO_SUMMARY_MODEL` 环境变量
2. 检查 `core_config.json` 中是否有自定义摘要模型 URL/名称
3. 检查 `api_providers.json` 中所选 Assist 提供商的 `summary_model`
4. 回退到 `config/__init__.py` 中的 `DEFAULT_SUMMARY_MODEL = "qwen-plus"`

## 何时使用各层级

| 层级 | 最适用于 |
|------|----------|
| 环境变量 | Docker 部署、CI/CD、密钥管理 |
| 用户配置文件 | Web UI 配置（自动管理） |
| API 提供商配置 | 每个提供商的默认模型分配 |
| 代码默认值 | 未配置任何内容时的回退值 |

## Docker 特别说明

在 Docker 部署中，环境变量是主要的配置机制。`entrypoint.sh` 脚本会在启动时自动从 `NEKO_*` 环境变量生成 `core_config.json`。

详见 [Docker 部署](/deployment/docker)。

# 環境変数

Current code が明示的に読む変数だけがサポート対象です。`NEKO_` prefix を優先し、一部 network helper は bare name も互換用に受け付けます。

| 変数 | 既定値 | Service |
| --- | ---: | --- |
| `NEKO_MAIN_SERVER_PORT` | 48911 | Main Web/API |
| `NEKO_MEMORY_SERVER_PORT` | 48912 | Memory |
| `NEKO_MONITOR_SERVER_PORT` | 48913 | Monitor |
| `NEKO_COMMENTER_SERVER_PORT` | 48914 | Commenter |
| `NEKO_TOOL_SERVER_PORT` | 48915 | Agent/Tool |
| `NEKO_USER_PLUGIN_SERVER_PORT` | 48916 | User-plugin host |
| `NEKO_AGENT_MQ_PORT` | 48917 | Agent transport |
| `NEKO_MAIN_AGENT_EVENT_PORT` | 48918 | Main/Agent events |
| `NEKO_OPENFANG_PORT` | 50051 | OpenFang A2A |

Runtime では `NEKO_INSTANCE_ID`、`NEKO_AUTOSTART_CSRF_TOKEN`、`NEKO_AUTOSTART_ALLOWED_ORIGINS`、`NEKO_BEHIND_PROXY`、`NEKO_LOG_LEVEL`、`NEKO_MERGED` を使います。Storage root は `NEKO_STORAGE_SELECTED_ROOT` と `NEKO_STORAGE_ANCHOR_ROOT` です。

Local vectors は `NEKO_VECTORS_ENABLED`、`NEKO_VECTORS_QUANTIZATION`（`auto/int8/fp32`）、`NEKO_VECTORS_MIN_AVAILABLE_RAM_GB`（既定 4）。Boolean は `1/true/yes/on` と `0/false/no/off` です。

Docker entrypoint は initial `/app/config/core_config.json` の生成時だけ `NEKO_CORE_API_KEY`、`NEKO_CORE_API`、`NEKO_ASSIST_API`、一部 `NEKO_ASSIST_API_KEY_*`、`NEKO_MCP_TOKEN` を読みます。`NEKO_FORCE_ENV_UPDATE` は再生成要求です。旧 `docker/env.template` の未接続 model 変数には依存しないでください。

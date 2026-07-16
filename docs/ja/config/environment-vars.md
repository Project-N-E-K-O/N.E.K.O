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

Local vectors は `NEKO_VECTORS_ENABLED` と `NEKO_VECTORS_QUANTIZATION`（`auto/int8/fp32`）を受け付けます。Boolean は `1/true/yes/on` と `0/false/no/off` です。利用可能 RAM の下限は現在、固定の実行時定数 `VECTORS_MIN_RAM_GB = 4.0` であり、環境変数による上書きはありません。

## ランタイム構成

| 変数 | デフォルト | 説明 |
|------|------------|------|
| `NEKO_MERGED` | ソース環境: `0`、凍結パッケージ: `1` | `1` は main、memory、agent の HTTP サービスを同一プロセスで実行しつつ各契約を維持します。`0` は 3 サービスを別プロセスで実行します。既存バックエンドが不完全または混在している場合は再利用せず、merged が選択されていても分離したフォールバックポートで 3 プロセスを起動します。 |

開発、サービスごとの監視、agent 障害の分離が必要な場合はマルチプロセスを使用してください。
パッケージ版は `NEKO_MERGED=0` ですぐにロールバックできます。
`NEKO_MERGED` 自体が受け付ける値は `1/true/yes` と `0/false/no` です。

Docker entrypoint は initial `/app/config/core_config.json` の生成時だけ `NEKO_CORE_API_KEY`、`NEKO_CORE_API`、`NEKO_ASSIST_API`、一部 `NEKO_ASSIST_API_KEY_*`、`NEKO_MCP_TOKEN` を読みます。`NEKO_FORCE_ENV_UPDATE` は再生成要求です。旧 `docker/env.template` の未接続 model 変数には依存しないでください。

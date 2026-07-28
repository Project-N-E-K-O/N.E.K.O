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

## プロセスモデルと単一インスタンス

launcher は foreground プロセスです。daemon 化して親から離脱することはなく、所有者
プロセスが消えた時点でサービス構成全体を片付けます。また OS のファイルロックで唯一性
を自己証明し、その隣に権威ある runtime record（pid、instance id、確定したポート）を
書き出します。

| 変数 | デフォルト | 説明 |
|------|------------|------|
| `NEKO_OWNER_PID` | 本プロセスの親 | 親プロセス死亡ガードが監視する pid。所有者が直接の親で**ない**場合に設定します（例：storage 移行の世代交代で生成された次の launcher。その spawn 元は意図的に終了します）。 |
| `NEKO_OWNER_RELAUNCH` | 未設定 | `1` は「所有者が自分で runtime を再起動する」という宣言です。storage 移行の再起動は自分で後継を spawn せず、クリーンに終了して再起動を待ちます。 |
| `NEKO_PARENT_DEATH_GUARD` | `1` | `0` でガードを完全に無効化します。対象を再 parent 化する debugger / profiler 用のみ。無効化した runtime は所有者より長生きし得ます。 |
| `NEKO_LAUNCHER_RESTART_HANDOFF` | 未設定 | 前世代の launcher が後継に設定します。後継は「別インスタンスが動作中」と結論せず、単一インスタンスロックの解放を待ちます。手動設定は想定していません。 |
| `NEKO_RUNTIME_STATE_DIR` | ユーザーごとの runtime ディレクトリ | `launcher.lock` と `launcher.json` の場所を上書きします。既定は Windows `%LOCALAPPDATA%\N.E.K.O\runtime`、macOS `~/Library/Application Support/N.E.K.O/runtime`、Linux `$XDG_RUNTIME_DIR/neko`。上書き値はそのまま使われ、ユーザーごとの接尾辞は付かず権限も検証されないため、必ず単一ユーザー専用のディレクトリを指す必要があります。共有ディレクトリを指すと単一インスタンスの証明が壊れます: Windows では 2 ユーザーが同一ロックを奪い合い、POSIX では 2 人目が 1 人目のロックファイルを開けず、一意性の証明なしで起動します。 |

## ランタイム構成

| 変数 | デフォルト | 説明 |
|------|------------|------|
| `NEKO_MERGED` | ソース環境: `0`、凍結パッケージ: `1` | `1` は main、memory、agent の HTTP サービスを同一プロセスで実行しつつ各契約を維持します。`0` は 3 サービスを別プロセスで実行します。既存バックエンドが不完全または混在している場合は再利用せず、merged が選択されていても分離したフォールバックポートで 3 プロセスを起動します。 |

開発、サービスごとの監視、agent 障害の分離が必要な場合はマルチプロセスを使用してください。
パッケージ版は `NEKO_MERGED=0` ですぐにロールバックできます。
`NEKO_MERGED` 自体が受け付ける値は `1/true/yes` と `0/false/no` です。

Docker entrypoint は initial `/app/config/core_config.json` の生成時だけ `NEKO_CORE_API_KEY`、`NEKO_CORE_API`、`NEKO_ASSIST_API`、一部 `NEKO_ASSIST_API_KEY_*`、`NEKO_MCP_TOKEN` を読みます。`NEKO_FORCE_ENV_UPDATE` は再生成要求です。旧 `docker/env.template` の未接続 model 変数には依存しないでください。

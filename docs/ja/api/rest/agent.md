# Agent API

**プレフィックス:** `/api/agent`

メインサーバーがブラウザー向けに提供する、ループバック Agent Server のプロキシです。キャラクター/セッションの同期とリモート配備の安全チェックを担当した後、`TOOL_SERVER_PORT` へランタイム操作を転送します。プロキシ障害は通常 `502`、リモート配備での変更ルートはサーバーマシンの操作を防ぐため `501` です。

## 状態とコマンド

### `GET /api/agent/flags`

マスタースイッチと Computer Use、Browser Use、ユーザープラグイン、OpenClaw、OpenFang のサブ機能を含む Agent Server の flags スナップショットを返します。プロキシ障害は `502` と `success: false` です。

### `POST /api/agent/flags`

旧式の部分更新です。ボディは `{"lanlan_name":"...","flags":{...}}`。キャラクターセッションを更新し、認識したサブフラグを転送します。キャラクター不在は `404`。転送失敗時はローカル flags を安全な無効状態へ戻し、`502` を返します。

### `GET /api/agent/state`

revision、flags、capabilities、notification 状態、タスク要約を含む正式な Agent Server 状態を返します。

### `POST /api/agent/command`

推奨される変更入口です。現在のコマンドは次のとおりです。

| コマンド | 追加フィールド | 用途 |
|---|---|---|
| `set_agent_enabled` | `enabled`、任意の `profile` | マスターランタイム gate を切り替え |
| `set_flag` | `key`、`value` | `computer_use_enabled`、`browser_use_enabled`、`user_plugin_enabled`、`openclaw_enabled`、`openfang_enabled` のいずれかを切り替え |
| `refresh_state` | なし | 新しい状態を返し、ブロードキャスト |

`request_id` と `lanlan_name` は任意です。不明なコマンド/flag key は Agent Server が拒否し、上流/プロキシ障害は `502` です。

## ヘルスと capability

| メソッド | パス | 応答境界 |
|---|---|---|
| `GET` | `/api/agent/health` | `{"status":"ok","tool":{...}}`。Agent Server 不可用は `502` と `status: "down"` |
| `GET` | `/api/agent/computer_use/availability` | Agent Server の準備状態と理由 |
| `GET` | `/api/agent/browser_use/availability` | ブラウザー依存関係/モデルの準備状態 |
| `GET` | `/api/agent/user_plugin/availability` | プラグインサービス到達性。不可用は `502` |
| `GET` | `/api/agent/openclaw/availability` | OpenClaw/QwenPaw 準備状態。不可用は `502` |
| `GET` | `/api/agent/mcp/availability` | 互換応答。MCP は `brain/` から移動済みで常に利用不可 |

## タスクと管理

| メソッド | パス | 用途 |
|---|---|---|
| `GET` | `/api/agent/tasks` | Agent Server のタスクスナップショット一覧 |
| `GET` | `/api/agent/tasks/{task_id}` | タスクを 1 件取得 |
| `POST` | `/api/agent/tasks/{task_id}/cancel` | タスクを 1 件キャンセル。上流 `404` を保持し、リクエスト到達後の応答タイムアウトは `504` |
| `POST` | `/api/agent/admin/control` | `{"action":"end_all"}` などの管理操作を転送。ローカル限定で活動中の作業を終了 |

## 内部/UI ヘルパールート

| メソッド | パス | 境界 |
|---|---|---|
| `POST` | `/api/agent/internal/analyze_request` | `analyze_request` をメインイベントバスへ発行する内部フォールバックブリッジ |
| `GET` | `/api/agent/user_plugin/dashboard` | ローカルプラグイン dashboard へリダイレクト。検証済みの `v` とループバック `yui_opener_origin` を受理 |
| `GET` | `/api/agent/openclaw/guide` | ローカル OpenClaw ガイドページを表示 |
| `GET` | `/api/agent/openclaw/guide/content` | ローカライズ済みガイド Markdown を返す。`lang` は任意 |
| `GET` | `/api/agent/openclaw/guide/assets/{asset_path:path}` | 固定ガイドアセットディレクトリ配下だけを配信。越境/欠落は `404` |

analyze bridge は公開タスク送信 API ではありません。Dashboard と guide はブラウザーヘルパーであり、Agent Server のタスク API ではありません。

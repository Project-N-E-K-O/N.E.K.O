# Agent API

**プレフィックス:** `/api/agent`

バックグラウンドエージェントシステムを管理します — 機能フラグ、タスク状態、ヘルス監視。すべてのリクエストは Agent Server（ポート 48915）にプロキシされます。

## フラグ

### `GET /api/agent/flags`

現在のエージェント機能フラグを取得します。

**レスポンス:**

```json
{
  "agent_enabled": false,
  "computer_use_enabled": false,
  "mcp_enabled": false,
  "browser_use_enabled": false,
  "user_plugin_enabled": false
}
```

### `POST /api/agent/flags`

エージェントフラグを更新します。変更はセッションマネージャーに反映され、ツールサーバーに転送されます。

**ボディ:**

```json
{
  "lanlan_name": "character_name",
  "flags": {
    "agent_enabled": true,
    "mcp_enabled": true,
    "user_plugin_enabled": false
  }
}
```

## 状態とヘルス

### `GET /api/agent/state`

エージェントのリビジョン番号、フラグ、機能を含む権威ある状態スナップショットを取得します。

### `GET /api/agent/health`

エージェントのヘルスチェックエンドポイント（ツールサーバーにプロキシ）。

## 機能チェック

### `GET /api/agent/computer_use/availability`

Computer Use が利用可能かどうかを確認します（ビジョンモデルの設定が必要です）。

### `GET /api/agent/mcp/availability`

MCP（Model Context Protocol）が利用可能かどうかを確認します。

### `GET /api/agent/user_plugin/availability`

ユーザープラグインが利用可能かどうかを確認します。

### `GET /api/agent/browser_use/availability`

Browser Use が利用可能かどうかを確認します。

## タスク

### `GET /api/agent/tasks`

すべてのエージェントタスク（アクティブおよび完了済み）を一覧表示します。

### `GET /api/agent/tasks/{task_id}`

特定のタスクの詳細を取得します。

## コマンド

### `POST /api/agent/command`

フロントエンドからエージェントを制御するための統一コマンドエントリポイント。

**ボディ:**

```json
{
  "lanlan_name": "character_name",
  "command": "pause",
  "request_id": "optional_request_id",
  "enabled": true,
  "key": "optional_key",
  "value": "optional_value"
}
```

## 内部エンドポイント

### `POST /api/agent/internal/analyze_request`

内部ブリッジエンドポイント：サブプロセスからの分析リクエストを受信し、EventBus を通じてパブリッシュします。

**ボディ:**

```json
{
  "trigger": "conversation",
  "lanlan_name": "character_name",
  "messages": [ ... ]
}
```

### `POST /api/agent/admin/control`

ツールサーバーにプロキシされる管理者制御コマンド。使用時は注意してください。

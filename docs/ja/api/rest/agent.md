# Agent API

**プレフィックス:** `/api/agent`

バックグラウンドエージェントシステムを管理します — 機能フラグ、タスク状態、ヘルス監視。

## フラグ

### `GET /api/agent/flags`

現在のエージェント機能フラグを取得します。

**レスポンス:**

```json
{
  "agent_enabled": false,
  "computer_use_enabled": false,
  "mcp_enabled": false,
  "browser_use_enabled": false
}
```

### `POST /api/agent/flags`

エージェントフラグを更新します。変更はツールサーバーに転送されます。

**ボディ:**

```json
{
  "lanlan_name": "character_name",
  "flags": {
    "agent_enabled": true,
    "mcp_enabled": true
  }
}
```

## 状態とヘルス

### `GET /api/agent/state`

エージェントの現在の状態（実行中のタスク、保留中のリクエスト）のスナップショットを取得します。

### `GET /api/agent/health`

エージェントのヘルスチェックエンドポイント。

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

エージェントに制御コマンドを送信します。

**ボディ:**

```json
{
  "lanlan_name": "character_name",
  "command": "pause",
  "task_id": "optional_task_id"
}
```

**利用可能なコマンド:** `pause`、`resume`、`cancel`

## 内部エンドポイント

### `POST /api/agent/internal/analyze_request`

分析リクエストを送信するための内部エンドポイント。メインサーバーのセッションマネージャーによって使用されます。

### `POST /api/agent/admin/control`

管理者制御コマンド（例：プロセスの強制終了）。使用時は注意してください。

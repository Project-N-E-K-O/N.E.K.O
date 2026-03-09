# Agent Server API

**ポート:** 48915（内部）

Agent Server はバックグラウンドタスクの実行を処理します。ZeroMQ ソケット（リアルタイムイベントストリーミング用）と HTTP REST エンドポイント（管理および制御用）の両方を介してメインサーバーと通信します。

## REST エンドポイント

| エンドポイント | メソッド | 用途 |
|----------|--------|---------|
| `/health` | GET | N.E.K.O. シグネチャ付きヘルスチェック |
| `/status` | GET | エージェント状態スナップショット（タスク、フラグ、機能） |
| `/flag_set/{flag_name}/{value}` | POST | エージェント機能フラグの設定 |
| `/capabilities` | GET | 利用可能なエージェント機能の一覧 |
| `/task/spawn_computer_use` | POST | Computer Use タスクのキューイング |
| `/task/{task_id}` | GET | タスク状態と結果の取得 |
| `/task/{task_id}` | DELETE | タスクのキャンセル/削除 |
| `/reload_config` | POST | API 設定のリロード |

## ZeroMQ インターフェース

| ソケット | アドレス | タイプ | 方向 |
|--------|---------|------|-----------|
| Session events | `tcp://127.0.0.1:48961` | PUB/SUB | Main → Agent |
| Task results | `tcp://127.0.0.1:48962` | PUSH/PULL | Agent → Main |
| Analyze queue | `tcp://127.0.0.1:48963` | PUSH/PULL | Main → Agent |

## メッセージタイプ

### Main → Agent

**分析リクエスト:**

メインサーバーがアクション可能な会話コンテキストを検出したときにパブリッシュされます。

```json
{
  "type": "analyze_request",
  "lanlan_name": "character_name",
  "messages": [ ... ],
  "agent_flags": { ... }
}
```

### Agent → Main

**タスク結果:**

```json
{
  "type": "task_result",
  "task_id": "uuid",
  "lanlan_name": "character_name",
  "result": { ... },
  "status": "completed"
}
```

**プロアクティブメッセージ:**

```json
{
  "type": "proactive_message",
  "lanlan_name": "character_name",
  "text": "I found something interesting...",
  "source": "web_search"
}
```

**エージェント状態更新:**

```json
{
  "type": "agent_status_update",
  "capabilities": { ... },
  "flags": { ... }
}
```

**分析 ACK:**

```json
{
  "type": "analyze_ack",
  "lanlan_name": "character_name"
}
```

## 実行エンジン

Agent Server はタスク実行に `DirectTaskExecutor` を使用し、並列機能評価と優先度ベースの実行を行います：

| アダプター | モジュール | 機能 |
|---------|--------|-------------|
| MCP Client | `brain/mcp_client.py` | Model Context Protocol を介した外部ツール呼び出し（JSON-RPC 2.0 over HTTP+SSE） |
| Computer Use | `brain/computer_use.py` | ビジョンベースのデスクトップ自動化（Thought→Action→Code ループ） |
| Browser Use | `brain/browser_use_adapter.py` | Web ブラウジング、フォーム入力、コンテンツ抽出 |
| User Plugin | Plugin system | `POST /plugin/trigger` を介したローカルプラグイン実行 |

実行優先度：**MCP → Browser Use → Computer Use → User Plugin**

詳細なアーキテクチャについては[エージェントシステム](/ja/architecture/agent-system)を参照してください。

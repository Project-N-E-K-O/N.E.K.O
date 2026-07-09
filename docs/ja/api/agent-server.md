# Agent Server API

**ポート:** 48915（内部）

Agent Server はバックグラウンドタスクの実行を処理します。HTTP ではなく ZeroMQ ソケットを介してメインサーバーと通信します。

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

## 実行アダプター

Agent Server はタスク実行に以下の実行アダプターを使用します：

| アダプター | モジュール | 機能 |
|---------|--------|-------------|
| Computer Use | `brain/computer_use.py` | スクリーンショット分析、マウス/キーボード自動化 |
| Browser Use | `brain/browser_use_adapter.py` | Web ブラウジング、フォーム入力、コンテンツ抽出 |
| OpenClaw | `brain/openclaw_adapter.py` | タスクを OpenClaw スタンドアロンエージェントチャネルに委譲 |
| OpenFang | `brain/openfang_adapter.py` | タスクを OpenFang スタンドアロンエージェントチャネルに委譲 |

MCP（Model Context Protocol）ツール呼び出しは `brain/` レイヤーから削除され、現在はユーザーがインストール可能なプラグイン `plugin/plugins/mcp_adapter/`（`MCPClient` / `MCPPluginInvoker`）によって提供されます。

詳細なアーキテクチャについては[エージェントシステム](/ja/architecture/agent-system)を参照してください。

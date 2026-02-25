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

Agent Server はタスク実行に3つのアダプターを使用します：

| アダプター | モジュール | 機能 |
|---------|--------|-------------|
| MCP Client | `brain/mcp_client.py` | Model Context Protocol を介した外部ツール呼び出し |
| Computer Use | `brain/computer_use.py` | スクリーンショット分析、マウス/キーボード自動化 |
| Browser Use | `brain/browser_use_adapter.py` | Web ブラウジング、フォーム入力、コンテンツ抽出 |

詳細なアーキテクチャについては[エージェントシステム](/ja/architecture/agent-system)を参照してください。

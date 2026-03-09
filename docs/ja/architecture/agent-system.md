# エージェントシステム

エージェントシステムにより、N.E.K.O. のキャラクターはバックグラウンドタスク — Webブラウジング、コンピューター操作、外部ツールの呼び出し、ローカルプラグインのトリガー — を会話コンテキストに基づいて実行できます。

## アーキテクチャ

```
Main Server                          Agent Server
┌────────────────┐                  ┌─────────────────────────────┐
│ LLMSession     │                  │ DirectTaskExecutor           │
│ Manager        │  ZeroMQ          │   ├── _assess_mcp()         │
│   │            │ ──────────────>  │   ├── _assess_browser_use() │
│   │ agent_flags│  PUB/SUB         │   ├── _assess_computer_use()│
│   │            │                  │   └── _assess_user_plugin() │
│   │ callbacks  │ <──────────────  │                              │
│   │            │  PUSH/PULL       │ Priority: MCP > BU > CU > UP │
└────────────────┘                  │                              │
                                    │ Adapters:                    │
                                    │   ├── McpRouterClient        │
                                    │   ├── BrowserUseAdapter      │
                                    │   ├── ComputerUseAdapter     │
                                    │   └── POST /plugin/trigger   │
                                    │                              │
                                    │ Support:                     │
                                    │   ├── Analyzer               │
                                    │   └── Deduper                │
                                    └─────────────────────────────┘
```

## 機能フラグ

エージェント機能は、`/api/agent/flags` エンドポイントを通じて管理されるフラグで切り替えできます：

| フラグ | デフォルト | 説明 |
|--------|----------|------|
| `agent_enabled` | false | エージェントシステムのマスタースイッチ |
| `mcp_enabled` | false | Model Context Protocolツール呼び出し |
| `computer_use_enabled` | false | スクリーンショット分析、マウス/キーボード |
| `browser_use_enabled` | false | Webブラウジング自動化 |
| `user_plugin_enabled` | false | プラグインシステム経由のローカルプラグイン実行 |

## タスク実行パイプライン

`DirectTaskExecutor`（`brain/task_executor.py`）がコアエンジンです。旧来のPlanner→Executorの2ステップフローを、統一された**並列評価 + 優先度実行**モデルに置き換えています：

1. **トリガー**: メインサーバーが会話内の実行可能なリクエストを検出し、ZeroMQ経由で分析リクエストをパブリッシュ。

2. **並列評価**: エクゼキューターが `asyncio.gather` を使用して**4つの評価を同時実行**：
   - `_assess_mcp()` — MCPツールがリクエストを処理可能か評価；`tool_name` + `tool_args` を返す
   - `_assess_browser_use()` — ブラウザ自動化が適切か評価
   - `_assess_computer_use()` — デスクトップGUI自動化が適切か評価
   - `_assess_user_plugin()` — ローカルプラグインが処理可能か評価；`plugin_id` + `entry_id` を返す

3. **優先度選択**: 最初に成功した評価が選ばれ、順序は：**MCP → Browser Use → Computer Use → User Plugin**

4. **実行**: 選択されたアダプターがタスクを実行：
   - `_execute_mcp()` — `McpRouterClient` 経由でMCPツールを呼び出し
   - `BrowserUseAdapter.run_instruction()` — ブラウザ自動化を実行
   - `ComputerUseAdapter.run_instruction()` — デスクトップ自動化を実行
   - `_execute_user_plugin()` — plugin_id と entry_id で `POST /plugin/trigger`

5. **分析**: `Analyzer` がマルチターン会話を評価し、完了したタスクを特定し応答方法を決定。

6. **重複排除**: `Deduper` がLLMを使用して新しい結果が既存結果と意味的に重複していないか判定。

7. **返却**: 結果がZeroMQ PUSH/PULL経由で `task_result` または `proactive_message` としてメインサーバーにストリーミング返却。

## ZeroMQソケットマップ

| アドレス | タイプ | 方向 | 用途 |
|---------|--------|------|------|
| `tcp://127.0.0.1:48961` | PUB/SUB | Main → Agent | セッションイベント、タスクリクエスト |
| `tcp://127.0.0.1:48962` | PUSH/PULL | Agent → Main | タスク結果、ステータス更新 |
| `tcp://127.0.0.1:48963` | PUSH/PULL | Main → Agent | 分析リクエストキュー |

イベントブリッジはフォールバック付きNOBLOCK送信、ACK/リトライメカニズム（0.5sタイムアウト、1リトライ）、asyncioイベントループ経由のバックグラウンド配信を使用します。

## MCP Client

`McpRouterClient`（`brain/mcp_client.py`）はMCP（Model Context Protocol）2024-11-05標準を実装：

- **トランスポート**: HTTP + SSE（Server-Sent Events）、JSON-RPC 2.0
- **エンドポイント**: 設定可能な `MCP_ROUTER_URL` 環境変数、デフォルトパス `/mcp`
- **キャッシング**: ツール一覧用 `TTLCache(ttl=3s)`、失敗クールダウン（1s）付き
- **ツールカタログ**: `McpToolCatalog` が評価用の `get_capabilities()` を提供

## Computer Use

Computer Useアダプター（`brain/computer_use.py`）はKimiパラダイムに着想を得た **Thought → Action → Code** ループを実装：

1. デスクトップのスクリーンショットをキャプチャ
2. ビジョン言語モデル（VLM）に送信して分析
3. VLMが構造化出力を返す：`## Thought` / `## Action` / `## Code`
4. 生成された `pyautogui` コードを実行
5. `computer.terminate()` が呼ばれるか最大ステップ数（50）に達するまでループ

主な特徴：
- **正規化座標**: `[0-999]` 座標系を実際の画面解像度にマッピング（`_ScaledPyAutoGUI`）
- **CJKテキスト入力**: CJK文字サポートにクリップボードペーストを使用
- **思考モード**: Claude拡張思考（reasoningフィールド）をサポート
- **画像履歴**: コンテキスト用に直近3枚のスクリーンショットを保持
- **実行可能アクション**: `click`、`doubleClick`、`rightClick`、`moveTo`、`dragTo`、`scroll`、`write`、`press`、`hotkey`、`computer.wait(seconds)`、`computer.terminate(status, answer)`

## Browser Use

Browser Useアダプター（`brain/browser_use_adapter.py`）は、Web自動化のための `browser-use` ライブラリをラップしています：

- URLへのナビゲーション
- ページコンテンツの抽出
- フォームの入力
- 要素のクリック
- ページスクリーンショットの撮影

## APIエンドポイント

完全なエンドポイントリファレンスについては、[エージェントREST API](/api/rest/agent)を参照してください。

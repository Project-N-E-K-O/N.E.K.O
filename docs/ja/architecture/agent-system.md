# エージェントシステム

エージェントシステムにより、N.E.K.O. のキャラクターはバックグラウンドタスク — Webブラウジング、コンピューター操作、外部ツールの呼び出し — を会話コンテキストに基づいて実行できます。

## アーキテクチャ

```
Main Server                          Agent Server
┌────────────────┐                  ┌────────────────────┐
│ LLMSession     │                  │ TaskExecutor        │
│ Manager        │  ZeroMQ          │   ├── Planner       │
│   │            │ ──────────────>  │   ├── Processor     │
│   │ agent_flags│  PUB/SUB         │   ├── Analyzer      │
│   │            │                  │   └── Deduper        │
│   │ callbacks  │ <──────────────  │                      │
│   │            │  PUSH/PULL       │ Adapters:            │
└────────────────┘                  │   ├── MCP Client     │
                                    │   ├── Computer Use   │
                                    │   └── Browser Use    │
                                    └────────────────────┘
```

## 機能フラグ

エージェント機能は、`/api/agent/flags` エンドポイントを通じて管理されるフラグで切り替えできます：

| フラグ | デフォルト | 説明 |
|--------|----------|------|
| `agent_enabled` | false | エージェントシステムのマスタースイッチ |
| `computer_use_enabled` | false | スクリーンショット分析、マウス/キーボード |
| `mcp_enabled` | false | Model Context Protocolツール呼び出し |
| `browser_use_enabled` | false | Webブラウジング自動化 |

## タスク実行パイプライン

1. **トリガー**: メインサーバーが会話内の実行可能なリクエストを検出し、ZeroMQ経由で分析リクエストをパブリッシュします。

2. **計画**: `Planner` がリクエストを順序付きのステップを持つタスクプランに分解します。

3. **実行**: `Processor` が適切なアダプターを通じて各ステップを実行します：
   - **MCP Client** — Model Context Protocol経由で外部ツールを呼び出し
   - **Computer Use** — スクリーンショットを撮影し、ビジョンモデルで分析し、マウス/キーボード操作を実行
   - **Browser Use** — Webページのナビゲーション、コンテンツの抽出、フォームの入力

4. **分析**: `Analyzer` がタスクの目標が達成されたかどうかを評価します。

5. **重複排除**: `Deduper` が冗長な結果の送信を防止します。

6. **返却**: 結果がZeroMQ PUSH/PULL経由でメインサーバーにストリーミングで返されます。

## ZeroMQソケットマップ

| アドレス | タイプ | 方向 | 用途 |
|---------|--------|------|------|
| `tcp://127.0.0.1:48961` | PUB/SUB | Main → Agent | セッションイベント、タスクリクエスト |
| `tcp://127.0.0.1:48962` | PUSH/PULL | Agent → Main | タスク結果、ステータス更新 |
| `tcp://127.0.0.1:48963` | PUSH/PULL | Main → Agent | 分析リクエストキュー |

## Computer Use

Computer Useアダプター（`brain/computer_use.py`）はビジョンベースのコンピューターインタラクションを実現します：

1. デスクトップのスクリーンショットをキャプチャ
2. ビジョンモデル（例：`qwen3-vl-plus`）に送信して分析
3. 視覚的理解に基づいてマウス/キーボード操作を計画
4. `pyautogui` 経由でアクションを実行

Computer Useモデルの設定については、[モデル設定](/config/model-config)リファレンスを参照してください。

## Browser Use

Browser Useアダプター（`brain/browser_use_adapter.py`）は、Web自動化のための `browser-use` ライブラリをラップしています：

- URLへのナビゲーション
- ページコンテンツの抽出
- フォームの入力
- 要素のクリック
- ページスクリーンショットの撮影

## APIエンドポイント

完全なエンドポイントリファレンスについては、[エージェントREST API](/api/rest/agent)を参照してください。

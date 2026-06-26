# エージェントシステム

エージェントシステムにより、N.E.K.O. のキャラクターはバックグラウンドタスク — Webブラウジング、コンピューター操作、独立したエージェントチャネルへの委譲、外部ツールの呼び出し — を会話コンテキストに基づいて実行できます。

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
└────────────────┘                  │   ├── Computer Use   │
                                    │   ├── Browser Use    │
                                    │   ├── OpenClaw       │
                                    │   ├── OpenFang       │
                                    │   └── User Plugin    │
                                    └────────────────────┘
```

## 機能フラグ

エージェント機能は、`/api/agent/flags` エンドポイントを通じて管理されるフラグで切り替えできます：

| フラグ | デフォルト | 説明 |
|--------|----------|------|
| `agent_enabled` | false | エージェントシステムのマスタースイッチ |
| `computer_use_enabled` | false | スクリーンショット分析、マウス/キーボード |
| `browser_use_enabled` | false | Webブラウジング自動化 |
| `user_plugin_enabled` | false | プラグイン / Model Context Protocol ツール呼び出し |
| `openclaw_enabled` | false | OpenClaw 独立エージェントチャネル |
| `openfang_enabled` | false | OpenFang 独立エージェントチャネル |

## タスク実行パイプライン

1. **トリガー**: メインサーバーが会話内の実行可能なリクエストを検出し、ZeroMQ経由で分析リクエストをパブリッシュします。

2. **計画**: `Planner` がリクエストを順序付きのステップを持つタスクプランに分解します。

3. **実行**: `Processor` が適切なアダプターを通じて各ステップを実行します：
   - **Computer Use** — スクリーンショットを撮影し、ビジョンモデルで分析し、マウス/キーボード操作を実行
   - **Browser Use** — Webページのナビゲーション、コンテンツの抽出、フォームの入力
   - **OpenClaw / OpenFang** — タスクを独立したエージェントチャネルに委譲
   - **User Plugin** — ユーザーがインストールしたプラグイン（Model Context Protocol）経由で外部ツールを呼び出し

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

## OpenClaw

OpenClaw アダプター（`brain/openclaw_adapter.py`）は、実行可能なタスクを OpenClaw 独立エージェントチャネル（内部では `qwenpaw` として参照）に委譲します。

## OpenFang

OpenFang アダプター（`brain/openfang_adapter.py`）は、実行可能なタスクを OpenFang 独立エージェントチャネルに委譲します。

チャネル選択の優先順位は `brain/task_executor.py` で `_CHANNEL_PRIORITY = ["qwenpaw", "openfang", "browser_use", "computer_use"]` として定義されています。プラグイン / MCP ツール呼び出し（`user_plugin_enabled`）は別の経路でディスパッチされ、`_CHANNEL_PRIORITY` には**含まれません**。

## APIエンドポイント

完全なエンドポイントリファレンスについては、[エージェントREST API](/api/rest/agent)を参照してください。

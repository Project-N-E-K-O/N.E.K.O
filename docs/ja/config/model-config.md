# モデル設定

N.E.K.O. はタスクごとに異なる AI モデルを使用します。それぞれ個別に設定できます。

## モデルの役割

| 役割 | デフォルト | 環境変数 | 用途 |
|------|------------|----------|------|
| 会話 | `qwen-max` | - | キャラクターチャット（オフラインモード） |
| 要約 | `qwen-plus` | `NEKO_SUMMARY_MODEL` | 会話の要約 |
| 校正 | `qwen-max` | `NEKO_CORRECTION_MODEL` | テキスト校正 |
| 感情 | `qwen-flash` | `NEKO_EMOTION_MODEL` | 表情用の感情分析 |
| ビジョン | `qwen3-vl-plus-2025-09-23` | `NEKO_VISION_MODEL` | 画像/スクリーンショットの理解 |
| エージェント | `qwen3.5-plus` | `NEKO_AGENT_MODEL` | エージェントタスクの実行 |
| ルーター | `qwen-plus` | `NEKO_ROUTER_MODEL` | メモリルーティングの判断 |
| セマンティック | `text-embedding-v4` | `NEKO_SEMANTIC_MODEL` | メモリ用テキストエンベディング |
| リランカー | `qwen-plus` | `NEKO_RERANKER_MODEL` | 検索結果のリランキング |
| 設定提案 | `qwen-max` | `NEKO_SETTING_PROPOSER_MODEL` | 設定更新の提案 |
| 設定検証 | `qwen-max` | `NEKO_SETTING_VERIFIER_MODEL` | 設定更新の検証 |

## カスタムモデルエンドポイント

各モデルの役割にはカスタム API エンドポイントを使用できます。`core_config.json` または Web UI で設定します：

```json
{
  "conversationModel": "custom-model-name",
  "conversationModelUrl": "https://custom-api.example.com/v1",
  "conversationModelApiKey": "sk-xxxxx"
}
```

カスタム URL/キーが設定されている場合、その特定の役割についてグローバルな Assist API プロバイダーをオーバーライドします。

## Computer Use モデル

Computer Use には2つのビジョンモデルが必要です：

| 役割 | デフォルト | 用途 |
|------|------------|------|
| プランニングモデル | `qwen3-vl-plus-2025-09-23` | スクリーンショットを分析しアクションを計画 |
| グラウンディングモデル | `qwen3-vl-plus-2025-09-23` | クリック対象の UI 要素を特定 |

`core_config.json` で設定します：

```json
{
  "computerUseModel": "qwen3-vl-plus-2025-09-23",
  "computerUseModelUrl": "https://dashscope.aliyuncs.com/compatible-mode/v1",
  "computerUseModelApiKey": "sk-xxxxx",
  "computerUseGroundModel": "qwen3-vl-plus-2025-09-23",
  "computerUseGroundUrl": "https://dashscope.aliyuncs.com/compatible-mode/v1",
  "computerUseGroundApiKey": "sk-xxxxx"
}
```

## 思考モードの設定

一部のモデルは「思考」または「拡張推論」モードをサポートしています。N.E.K.O. はより高速なレスポンスのためにデフォルトでこれらを無効にしています。無効化のフォーマットはプロバイダーによって異なります：

| プロバイダー | 無効化フォーマット |
|-------------|-------------------|
| Qwen, Step, DeepSeek | `{"enable_thinking": false}` |
| GLM | `{"thinking": {"type": "disabled"}}` |
| Gemini 2.x | `{"thinking_config": {"thinking_budget": 0}}` |
| Gemini 3.x | `{"thinking_config": {"thinking_level": "low"}}` |

これは `config/__init__.py` でモデル名に基づいて自動的に処理されます。

## 画像レート制限

| 設定 | デフォルト | 説明 |
|------|------------|------|
| `NATIVE_IMAGE_MIN_INTERVAL` | 1.5 秒 | 画面キャプチャの最小間隔 |
| `IMAGE_IDLE_RATE_MULTIPLIER` | 5 倍 | 音声アクティビティがない場合の倍率 |

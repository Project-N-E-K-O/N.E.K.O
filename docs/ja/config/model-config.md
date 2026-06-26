# モデル設定

N.E.K.O. はタスクごとに異なる AI モデルを使用します。それぞれ個別に設定できます。

## モデルの役割

各役割で実際に使われるモデルは、**選択中のアシスト API プロバイダー**（`config/api_providers.json` → `assist_api_providers`）から解決されます。そのため単一のグローバルなデフォルトは存在せず、各プロバイダーが役割ごとのモデルセットを同梱しています。下表は OpenAI・Claude（Anthropic）・Qwen 各プロバイダーの出荷時デフォルトです。（`config/__init__.py` の `DEFAULT_*_MODEL` 定数は最終フォールバックのみ。）

| 役割 | 設定フィールド | OpenAI | Claude | Qwen |
|------|----------------|--------|--------|------|
| 会話 | `conversation_model` | `gpt-5-chat-latest` | `claude-sonnet-4-6` | `qwen3.7-plus-2026-05-26` |
| 要約 | `summary_model` | `gpt-5-chat-latest` | `claude-sonnet-4-6` | `qwen3.7-plus-2026-05-26` |
| 校正 | `correction_model` | `gpt-5-chat-latest` | `claude-sonnet-4-6` | `qwen3.7-plus` |
| 感情 | `emotion_model` | `gpt-4.1-nano` | `claude-haiku-4-5-20251001` | `qwen3.6-flash` |
| ビジョン | `vision_model` | `gpt-5-chat-latest` | `claude-sonnet-4-6` | `qwen3.7-plus-2026-05-26` |
| エージェント | `agent_model` | `glm-5v-turbo` | `claude-opus-4-6` | `qwen3.7-plus` |

> **注:** `agent_model` はエージェント（Computer Use）タスクのグラウンディングに使うモデルです。複数のプロバイダーは、自社ファミリーに関係なく強力なビジョン/グラウンディングモデルを意図的に指定します——例えば OpenAI プロバイダーの `agent_model` は `glm-5v-turbo`（GLM のビジョンモデル）です。これらの値は `config/api_providers.json` をそのまま反映したもので、誤記ではなく仕様です。

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

# API プロバイダー

N.E.K.O. は2つのカテゴリの API プロバイダーをサポートしています：**Core**（リアルタイム音声/マルチモーダル用）と **Assist**（要約、感情分析、ビジョンなどのテキストベースタスク用）。

## Core API プロバイダー

Core プロバイダーは **Realtime API**（WebSocket ベースのストリーミング）をサポートしている必要があります。

| プロバイダー | WebSocket URL | デフォルトモデル |
|-------------|---------------|-----------------|
| `free` | 内蔵サーバー | `free-model` |
| `qwen` | `wss://dashscope.aliyuncs.com/api-ws/v1/realtime` | `qwen3-omni-flash-realtime` |
| `openai` | `wss://api.openai.com/v1/realtime` | `gpt-realtime-mini` |
| `step` | `wss://api.stepfun.com/v1/realtime` | `step-audio-2` |
| `gemini` | Google GenAI SDK | `gemini-2.5-flash-native-audio-preview-12-2025` |

::: tip
**free** ティアはコミュニティサーバーを使用し、API キーは不要です。テスト用途には適していますが、容量は限られています。
:::

## Assist API プロバイダー

Assist プロバイダーはテキストベースタスク用に OpenAI 互換の HTTP API を使用します。

| プロバイダー | ベース URL |
|-------------|-----------|
| `qwen` | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| `openai` | `https://api.openai.com/v1` |
| `glm` | `https://open.bigmodel.cn/api/paas/v4` |
| `step` | `https://api.stepfun.com/v1` |
| `silicon` | `https://api.siliconflow.cn/v1` |
| `gemini` | `https://generativelanguage.googleapis.com/v1beta/openai/` |
| `kimi` | `https://api.moonshot.cn/v1` |

各 Assist プロバイダーは以下のタスク用のモデルを定義します：

| タスク | 設定フィールド | 用途 |
|--------|---------------|------|
| 会話 | `conversation_model` | キャラクターチャット（オフラインモード） |
| 要約 | `summary_model` | 会話の要約 |
| 校正 | `correction_model` | テキスト校正 |
| 感情 | `emotion_model` | 感情分析 |
| ビジョン | `vision_model` | 画像理解 |
| エージェント | `agent_model` | エージェントタスクの実行 |

## プロバイダーの設定

プロバイダーは `config/api_providers.json` で定義されています。プロバイダーは以下の方法で選択できます：

1. **Web UI**（`http://localhost:48911/api_key`）
2. **環境変数** `NEKO_CORE_API` と `NEKO_ASSIST_API`
3. **設定ファイル** `core_config.json` のフィールド `coreApi` と `assistApi`

## API キーのマッピング

各 Assist プロバイダーは特定の環境変数にマッピングされます：

| プロバイダー | 環境変数 |
|-------------|----------|
| `qwen` | `NEKO_ASSIST_API_KEY_QWEN` |
| `openai` | `NEKO_ASSIST_API_KEY_OPENAI` |
| `glm` | `NEKO_ASSIST_API_KEY_GLM` |
| `step` | `NEKO_ASSIST_API_KEY_STEP` |
| `silicon` | `NEKO_ASSIST_API_KEY_SILICON` |
| `gemini` | `NEKO_ASSIST_API_KEY_GEMINI` |
| `kimi` | `NEKO_ASSIST_API_KEY_KIMI` |

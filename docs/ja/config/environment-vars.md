# 環境変数

すべての環境変数は `NEKO_` プレフィックスを使用します。

## API キー

| 変数 | 必須 | 説明 |
|------|------|------|
| `NEKO_CORE_API_KEY` | はい（free 使用時を除く） | Core Realtime API キー |
| `NEKO_ASSIST_API_KEY_QWEN` | いいえ | Alibaba Cloud (Qwen) Assist API キー |
| `NEKO_ASSIST_API_KEY_OPENAI` | いいえ | OpenAI Assist API キー |
| `NEKO_ASSIST_API_KEY_GLM` | いいえ | Zhipu (GLM) Assist API キー |
| `NEKO_ASSIST_API_KEY_STEP` | いいえ | StepFun Assist API キー |
| `NEKO_ASSIST_API_KEY_SILICON` | いいえ | SiliconFlow Assist API キー |
| `NEKO_ASSIST_API_KEY_GEMINI` | いいえ | Google Gemini Assist API キー |
| `NEKO_MCP_TOKEN` | いいえ | MCP Router 認証トークン |
| `NEKO_OPENROUTER_API_KEY` | いいえ | OpenRouter API キー |

## プロバイダー選択

| 変数 | デフォルト | オプション |
|------|------------|------------|
| `NEKO_CORE_API` | `qwen` | `free`, `qwen`, `openai`, `glm`, `step`, `gemini` |
| `NEKO_ASSIST_API` | `qwen` | `qwen`, `openai`, `glm`, `step`, `silicon`, `gemini` |

## サーバーポート

| 変数 | デフォルト | 説明 |
|------|------------|------|
| `NEKO_MAIN_SERVER_PORT` | `48911` | メインサーバー（Web UI、API） |
| `NEKO_MEMORY_SERVER_PORT` | `48912` | メモリサーバー |
| `NEKO_MONITOR_SERVER_PORT` | `48913` | モニターサーバー |
| `NEKO_COMMENTER_SERVER_PORT` | `48914` | コメンターサーバー |
| `NEKO_TOOL_SERVER_PORT` | `48915` | エージェント/ツールサーバー |
| `NEKO_USER_PLUGIN_SERVER_PORT` | `48916` | ユーザープラグインサーバー |
| `NEKO_AGENT_MQ_PORT` | `48917` | エージェントメッセージキュー |
| `NEKO_MAIN_AGENT_EVENT_PORT` | `48918` | エージェントイベントポート |

## モデルのオーバーライド

| 変数 | デフォルト | 説明 |
|------|------------|------|
| `NEKO_SUMMARY_MODEL` | `qwen-plus` | 要約モデル |
| `NEKO_CORRECTION_MODEL` | `qwen-max` | テキスト校正モデル |
| `NEKO_EMOTION_MODEL` | `qwen-turbo` / `qwen-flash` | 感情分析モデル |
| `NEKO_VISION_MODEL` | `qwen3-vl-plus-2025-09-23` | ビジョン/画像理解モデル |
| `NEKO_ROUTER_MODEL` | `qwen-plus` | メモリルーティングモデル |
| `NEKO_SEMANTIC_MODEL` | `text-embedding-v4` | エンベディングモデル |
| `NEKO_RERANKER_MODEL` | `qwen-plus` | 検索リランキングモデル |
| `NEKO_SETTING_PROPOSER_MODEL` | `qwen-max` | 設定更新提案モデル |
| `NEKO_SETTING_VERIFIER_MODEL` | `qwen-max` | 設定更新検証モデル |

## サービス URL

| 変数 | デフォルト | 説明 |
|------|------------|------|
| `NEKO_MCP_ROUTER_URL` | `http://localhost:3282` | MCP Router エンドポイント |

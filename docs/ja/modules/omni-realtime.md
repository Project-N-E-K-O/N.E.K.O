# Realtime Client

**ファイル:** `main_logic/omni_realtime_client.py`

`OmniRealtimeClient` は Realtime API プロバイダー（Qwen、OpenAI、Gemini、Step、GLM）への WebSocket 接続を管理します。

## サポートされるプロバイダー

| プロバイダー | プロトコル | 備考 |
|-------------|----------|------|
| Qwen (DashScope) | WebSocket | プライマリ、最もテスト済み |
| OpenAI | WebSocket | GPT Realtime API |
| Step | WebSocket | Step Audio |
| GLM | WebSocket | Zhipu Realtime |
| Gemini | Google GenAI SDK | SDK ラッパーを使用、生の WebSocket ではない |

## 主要メソッド

### `connect()`

プロバイダーの Realtime API エンドポイントへの WebSocket 接続を確立します。

### `prime_context(text, skipped=False)`

ユーザーのテキストをコンテキストとして会話に注入します。`skipped=True`（または Qwen）の場合、テキストはモデルのレスポンスをトリガーせずにセッション指示へ追記されます。`skipped=False`（GPT/GLM/Step）の場合は、ワンショットのユーザーメッセージを注入してレスポンスをトリガーします。

### `create_response(instructions, skipped=False)`

user ロールの会話メッセージを作成し、LLM のレスポンスをトリガーします。会話の途中でモデルの即時返信が必要な場面で使用します。

### `inject_text_and_request_response(text, *, on_rejected=None)`

user ロールのテキスト項目を注入し、1 回の呼び出しでレスポンスを明示的にトリガーします。音声モードの能動的な発話パス（agent タスクのコールバック / プラグインの `push_message` `ai_behavior="respond"`）で使用され、次のユーザーターンを待たずに結果を即座に話させます。

### `stream_audio(audio_chunk)`

生の PCM オーディオチャンクを LLM にストリーミングします。入力サンプルレートはチャンクサイズから自動検出されるため（480 サンプル = PC からの 48 kHz で、RNNoise でノイズ除去して 16 kHz にダウンサンプリング。512 サンプル = モバイルからの 16 kHz でそのまま透過）、サンプルレート引数は不要です。

### `stream_image(image_b64, *, bypass_rate_limit=False)`

マルチモーダル理解のためにスクリーンショット / カメラフレームをストリーミングします。`NATIVE_IMAGE_MIN_INTERVAL`（デフォルト 1.5 秒）によりレート制限されます。`bypass_rate_limit=True` を渡すと、意図的に送信する単一の手がかり画像（例: 能動的コールバックのスクリーンショット）に対してスロットルをスキップできます。

## イベントハンドラー

| イベント | 用途 |
|---------|------|
| `on_text_delta()` | LLM からのストリーミングテキストレスポンス |
| `on_audio_delta()` | ストリーミングオーディオレスポンス |
| `on_input_transcript()` | ユーザーの音声をテキストに変換（STT） |
| `on_output_transcript()` | LLM の出力をテキストとして取得 |
| `on_interrupt()` | ユーザーが LLM の出力を中断 |

## ターン検出

クライアントはデフォルトで**サーバーサイド VAD**（音声アクティビティ検出）を使用します。LLM プロバイダーがユーザーの発話終了を判断し、自然な会話のターンテイキングを実現します。

## 画像スロットリング

API への過負荷を防ぐため、画面キャプチャはレート制限されます：

- **発話中**: `NATIVE_IMAGE_MIN_INTERVAL` 秒ごとに画像を送信（1.5 秒）
- **アイドル（音声なし）**: 間隔に `IMAGE_IDLE_RATE_MULTIPLIER` を乗算（5 倍 = 7.5 秒）

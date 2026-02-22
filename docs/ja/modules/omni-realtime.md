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

### `send_text(text)`

ユーザーのテキスト入力を LLM に送信します。

### `send_audio(audio_bytes, sample_rate)`

ユーザーのオーディオチャンクを LLM にストリーミングします。オーディオは生の PCM データとして送信されます。

### `send_screenshot(base64_data)`

マルチモーダル理解のためにスクリーンショットを送信します。`NATIVE_IMAGE_MIN_INTERVAL`（デフォルト 1.5 秒）によりレート制限されます。

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

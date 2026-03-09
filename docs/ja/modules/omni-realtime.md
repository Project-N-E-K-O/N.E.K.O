# Realtime Client

**ファイル:** `main_logic/omni_realtime_client.py`

`OmniRealtimeClient` は Realtime API プロバイダー（Qwen、OpenAI、Gemini、Step、GLM）への WebSocket 接続を管理します。

## サポートされるプロバイダー

| プロバイダー | プロトコル | 備考 |
|-------------|----------|------|
| Qwen (DashScope) | WebSocket | プライマリ、Momo ボイス、gummy-realtime-v1 トランスクリプション |
| OpenAI | WebSocket | GPT Realtime API（gpt-realtime-mini、semantic VAD、marin ボイス） |
| Step | WebSocket | Step Audio（qingchunshaonv ボイス、web_search ツール） |
| GLM | WebSocket | Zhipu Realtime（video_passive、tongtong ボイス） |
| Gemini | Google GenAI SDK | SDK ラッパー（`_connect_gemini`）を使用、生の WebSocket ではない |
| Free | WebSocket | ツールなしの Step 設定と同じ |

## 主要メソッド

### `connect()`

プロバイダーの Realtime API エンドポイントへの WebSocket 接続を確立します。Gemini は Google GenAI SDK 経由の専用 `_connect_gemini()` パスを使用します。

### `send_text(text)`

ユーザーのテキスト入力を LLM に送信します。

### `send_audio(audio_bytes, sample_rate)`

ユーザーのオーディオチャンクを LLM にストリーミングします。オーディオは生の PCM データとして送信されます。

### `send_screenshot(base64_data)`

マルチモーダル理解のためにスクリーンショットを送信します。`NATIVE_IMAGE_MIN_INTERVAL`（デフォルト 1.5 秒）によりレート制限されます。

### `stream_proactive(instruction)`

指定された指示でプロアクティブ（キャラクター主導）メッセージストリームを開始します。

## イベントハンドラー

| イベント | 用途 |
|---------|------|
| `on_text_delta()` | LLM からのストリーミングテキストレスポンス |
| `on_audio_delta()` | ストリーミングオーディオレスポンス |
| `on_input_transcript()` | ユーザーの音声をテキストに変換（STT） |
| `on_output_transcript()` | LLM の出力をテキストとして取得 |
| `on_interrupt()` | ユーザーが LLM の出力を中断 |
| `on_response_done()` | 完全なレスポンスが完了 |
| `on_repetition_detected()` | 出力の重複を検出 |
| `on_response_discarded()` | レスポンスが破棄された（重複、エラー） |

## ターン検出

クライアントはデフォルトで**サーバーサイド VAD**（音声アクティビティ検出）を使用します。LLM プロバイダーがユーザーの発話終了を判断し、自然な会話のターンテイキングを実現します。

## 画像スロットリング

API への過負荷を防ぐため、画面キャプチャはレート制限されます：

- **発話中**: `NATIVE_IMAGE_MIN_INTERVAL` 秒ごとに画像を送信（1.5 秒）
- **アイドル（音声なし）**: 間隔に `IMAGE_IDLE_RATE_MULTIPLIER` を乗算（5 倍 = 7.5 秒）

## ビジョン分析

`_analyze_image_with_vision_model(image_b64)` メソッドは、メインの LLM 会話と併用されるスクリーンショットのスタンドアロンビジョンモデル分析を提供します。

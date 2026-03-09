# LLMSessionManager

**ファイル:** `main_logic/core.py`（約2460行）

`LLMSessionManager` は N.E.K.O. の中核であり、キャラクターごとに1つのインスタンスが会話のライフサイクル全体を管理します。

## 責務

- WebSocket 接続管理
- LLM セッションの作成とホットスワップ
- TTS パイプラインの調整
- オーディオリサンプリング（24kHz → 48kHz、soxr 経由）
- エージェントコールバックの注入
- プロアクティブメッセージの配信
- スクリーンショットのリクエスト/レスポンス
- 翻訳サポート

## 主要メソッド

### セッションライフサイクル

### `start_session(websocket, new, input_mode)`

新しい LLM セッションを初期化します：

1. キャラクターの設定で `OmniRealtimeClient`（または `OmniOfflineClient`）を作成
2. WebSocket 経由で Realtime API に接続
3. TTS ワーカースレッドを開始（音声出力が有効な場合）
4. ホットスワップ用に次のセッションのバックグラウンド準備を開始

### `stream_data(message)`

受信したユーザー入力を処理します：

- **音声**: PCM オーディオチャンクを Realtime API クライアントに送信
- **テキスト**: テキストメッセージを LLM に送信
- **スクリーン/カメラ**: マルチモーダル理解のためにスクリーンショットを送信

### `end_session(by_server)`

現在のセッションを終了し、ホットスワップをトリガーします：

1. Realtime API の WebSocket を閉じる
2. シームレスな遷移のために `_perform_final_swap_sequence()` を呼び出す
3. スワップ期間中のキャッシュされたオーディオをフラッシュ

### `cleanup(expected_websocket)`

WebSocket が切断されたときにすべてのリソースを解放します。

### コールバックハンドラー

| ハンドラー | 用途 |
|-----------|------|
| `handle_new_message()` | LLM 出力を TTS または WebSocket にルーティング |
| `handle_text_data(text, is_first_chunk)` | ストリーミングテキストチャンクの処理 |
| `handle_audio_data(audio_data)` | ストリーミングオーディオ出力の処理 |
| `handle_response_complete()` | 完全な LLM レスポンスが完了した時に呼び出される |
| `handle_response_discarded(reason, ...)` | レスポンスが破棄された時に呼び出される（重複など） |
| `handle_input_transcript(transcript)` | ユーザーの音声テキスト変換結果 |
| `handle_output_transcript(text, is_first_chunk)` | LLM 出力のテキスト版（オーディオと並行） |
| `handle_silence_timeout()` | ユーザーが無音になった時にトリガー |
| `handle_connection_error(message)` | LLM 接続失敗のハンドラー |
| `handle_repetition_detected()` | 重複が検出された時にトリガー |

### エージェント統合

| メソッド | 用途 |
|---------|------|
| `trigger_agent_callbacks()` | 保留中のエージェント結果を LLM ターンに配信 |
| `enqueue_agent_callback(callback)` | エージェント結果を注入キューに追加 |
| `drain_agent_callbacks_for_llm()` | キューされたコールバックを LLM コンテキストとして収集 |
| `update_agent_flags(flags)` | エージェント機能フラグを更新 |

### プロアクティブメッセージング

| メソッド | 用途 |
|---------|------|
| `deliver_text_proactively(...)` | キャラクター主導のメッセージをプッシュ |
| `prepare_proactive_delivery(min_idle_secs)` | アイドル時間をチェックして配信を準備 |
| `feed_tts_chunk(text)` | プロアクティブ配信中に TTS にテキストチャンクを供給 |
| `finish_proactive_delivery(full_text)` | 完全テキストでプロアクティブメッセージを完了 |

### スクリーンショットメカニズム

| メソッド | 用途 |
|---------|------|
| `request_fresh_screenshot(timeout)` | フロントエンドにスクリーンショットをリクエスト |
| `resolve_screenshot_request(b64)` | フロントエンドのスクリーンショットレスポンスを処理 |

### ホットスワップ内部

| メソッド | 用途 |
|---------|------|
| `_perform_final_swap_sequence()` | ホットスワップ遷移を実行 |
| `_background_prepare_pending_session()` | バックグラウンドで次のセッションをプレウォーム |
| `_flush_hot_swap_audio_cache()` | スワップ期間中のキャッシュされたオーディオをフラッシュ |

### `translate_if_needed(text)`

ユーザーの言語がキャラクターの言語と異なる場合にテキストを翻訳します。

## スレッドモデル

```
メイン非同期ループ (FastAPI)
  ├── WebSocket 受信ループ
  ├── LLM イベントハンドラー (on_text_delta, on_audio_delta, on_interrupt, ...)
  │
  ├── TTS ワーカースレッド（キューコンシューマー）
  │
  └── バックグラウンドセッション準備（ホットスワップ）
```

## 統合ポイント

- **WebSocket Router** → `start_session`、`stream_data`、`end_session` を呼び出す
- **Agent Event Bus** → `enqueue_agent_callback` 経由で結果を配信
- **Config Manager** → キャラクターデータと API 設定を提供
- **TTS Client** → `get_tts_worker()` ファクトリが TTS ワーカーを作成
- **Cross Server** → Monitor と Memory サーバーにメッセージを転送

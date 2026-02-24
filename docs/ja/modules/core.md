# LLMSessionManager

**ファイル:** `main_logic/core.py`（約2300行）

`LLMSessionManager` は N.E.K.O. の中核であり、キャラクターごとに1つのインスタンスが会話のライフサイクル全体を管理します。

## 責務

- WebSocket 接続管理
- LLM セッションの作成とホットスワップ
- TTS パイプラインの調整
- オーディオリサンプリング（24kHz → 48kHz）
- エージェントコールバックの注入
- 翻訳サポート

## 主要メソッド

### `start_session(websocket, new, input_mode)`

新しい LLM セッションを初期化します：

1. キャラクターの設定で `OmniRealtimeClient` を作成
2. WebSocket 経由で Realtime API に接続
3. TTS ワーカースレッドを開始（音声出力が有効な場合）
4. ホットスワップ用に次のセッションのバックグラウンド準備を開始

### `stream_data(message)`

受信したユーザー入力を処理します：

- **音声**: PCM オーディオチャンクを Realtime API クライアントに送信
- **テキスト**: テキストメッセージを LLM に送信
- **スクリーン**: マルチモーダル理解のためにスクリーンショットを送信

### `handle_new_message()`

LLM が出力を生成したときに呼び出されます：

- テキスト出力を TTS キューに送信（または WebSocket に直接送信）
- 表情マッピング用の感情ラベルを送信
- エージェント通知を処理

### `end_session(by_server)`

現在のセッションを終了し、ホットスワップをトリガーします：

1. Realtime API の WebSocket を閉じる
2. シームレスな遷移のために `_perform_final_swap_sequence()` を呼び出す
3. スワップ期間中のキャッシュされたオーディオをフラッシュ

### `cleanup(expected_websocket)`

WebSocket が切断されたときにすべてのリソースを解放します。

### `trigger_agent_callbacks()`

保留中のエージェント結果を次の LLM 会話ターンに注入し、キャラクターがエージェントの調査結果を参照できるようにします。

### `translate_if_needed(text)`

ユーザーの言語がキャラクターの言語と異なる場合にテキストを翻訳します。

## スレッドモデル

```
メイン非同期ループ (FastAPI)
  ├── WebSocket 受信ループ
  ├── LLM イベントハンドラー (on_text_delta, on_audio_delta)
  │
  ├── TTS ワーカースレッド（キューコンシューマー）
  │
  └── バックグラウンドセッション準備（ホットスワップ）
```

## 統合ポイント

- **WebSocket Router** → `start_session`、`stream_data`、`end_session` を呼び出す
- **Agent Event Bridge** → `pending_agent_callbacks` 経由で結果を配信
- **Config Manager** → キャラクターデータと API 設定を提供
- **TTS Client** → `get_tts_worker()` ファクトリが TTS ワーカーを作成

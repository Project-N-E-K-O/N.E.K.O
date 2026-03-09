# Offline Client

**ファイル:** `main_logic/omni_offline_client.py`

`OmniOfflineClient` は Realtime API が利用できない場合のフォールバックとして、テキストベースの LLM 会話を提供します。

## 使用される場面

- 選択されたプロバイダーが Realtime API をサポートしていない場合
- ローカル LLM デプロイメント（Ollama など）を使用する場合
- 音声入力が無効でテキストのみモードが好まれる場合

## 機能

- テキスト入力、テキスト出力の会話
- OpenAI 互換の任意の API エンドポイントと互換
- LLM 統合に LangChain（`langchain_openai.ChatOpenAI`）を使用
- 会話履歴とシステムプロンプトをサポート
- 独立したビジョンモデル設定（`vision_model`、`vision_base_url`、`vision_api_key`）
- `stream_proactive(instruction)` によるプロアクティブメッセージ生成
- 重複検出とレスポンス破棄処理

## 主要メソッド

| メソッド | 用途 |
|---------|------|
| `connect()` | チャットモデルの初期化 |
| `stream_audio(data)` | 音声入力の受信（別途 STT が必要） |
| `stream_image(b64)` | ビジョンモデル用の画像入力受信 |
| `create_response()` | LLM レスポンスの生成 |
| `stream_proactive(instruction)` | プロアクティブ（キャラクター主導）メッセージの生成 |
| `switch_model(new_model, use_vision_config)` | 別のモデルにホットスイッチ |
| `has_pending_images()` | 未処理の画像があるか確認 |

## Realtime Client との違い

| 機能 | Realtime Client | Offline Client |
|------|----------------|----------------|
| 音声 I/O | ネイティブ | 別途 STT/TTS が必要 |
| ストリーミング | WebSocket 双方向 | HTTP ストリーミング |
| マルチモーダル | ネイティブ（音声 + 画像） | ビジョンモデル（別設定） |
| レイテンシ | 低い（永続接続） | 高い（リクエストごと） |
| プロバイダーサポート | 限定的（Realtime API 必須） | OpenAI 互換なら任意 |
| プロアクティブメッセージ | `stream_proactive()` | `stream_proactive()` |

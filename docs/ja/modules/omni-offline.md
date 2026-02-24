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
- LLM 統合に LangChain を使用
- 会話履歴とシステムプロンプトをサポート

## Realtime Client との違い

| 機能 | Realtime Client | Offline Client |
|------|----------------|----------------|
| 音声 I/O | ネイティブ | 別途 STT/TTS が必要 |
| ストリーミング | WebSocket 双方向 | HTTP ストリーミング |
| マルチモーダル | ネイティブ（音声 + 画像） | テキストのみ |
| レイテンシ | 低い（永続接続） | 高い（リクエストごと） |
| プロバイダーサポート | 限定的（Realtime API 必須） | OpenAI 互換なら任意 |

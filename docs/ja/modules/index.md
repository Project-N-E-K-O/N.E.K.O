# コアモジュール

このセクションでは、内部ロジックの理解や変更が必要な開発者向けに、N.E.K.O. のコア Python モジュールを詳しく解説します。

## モジュールマップ

| モジュール | ファイル | 用途 |
|-----------|---------|------|
| [LLMSessionManager](./core) | `main_logic/core.py` | 中央セッションコーディネーター |
| [Realtime Client](./omni-realtime) | `main_logic/omni_realtime_client.py` | Realtime API 用 WebSocket クライアント |
| [Offline Client](./omni-offline) | `main_logic/omni_offline_client.py` | テキストベース LLM クライアント（フォールバック） |
| [TTS Client](./tts-client) | `main_logic/tts_client.py` | テキスト音声合成 |
| [Config Manager](./config-manager) | `utils/config_manager.py` | 設定の読み込みと永続化 |

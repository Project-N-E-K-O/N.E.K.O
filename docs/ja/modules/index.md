# コアモジュール

このセクションでは、会話セッションを構成する Python ランタイムモジュールを説明します。現在のソースは package 構成です。公開 import は通常各 package の `__init__.py` から提供され、実装は役割別の mixin と provider worker に分割されています。

## ランタイムマップ

| モジュール | 現在のソース | 責務 |
|---|---|---|
| [LLMSessionManager](/ja/modules/core) | `main_logic/core/` | セッションのライフサイクルを所有し、入力、モデル、メモリ、ツール、音声出力を調整 |
| [Realtime Client](/ja/modules/omni-realtime) | `main_logic/omni_realtime_client/` | provider 固有のトランスポートでネイティブ音声/realtime セッションを実行 |
| [Offline Client](/ja/modules/omni-offline) | `main_logic/omni_offline_client/` | chat-completion API に対するストリーミングテキスト・画像ターンを実行 |
| [TTS Client](/ja/modules/tts-client) | `main_logic/tts_client/` | 外部 TTS worker を解決し、worker のキュー契約を提供 |
| [Config Manager](/ja/modules/config-manager) | `utils/config_manager/` | ランタイムストレージ、移行、キャラクターデータ、API profile、永続設定を解決 |

## 組み合わせ方

`LLMSessionManager` は `ConfigManager` から正規化済み設定を読み、入力モードに応じて会話クライアントを一つ選択します。

- テキスト入力では `OmniOfflineClient` を作成します。
- 音声入力では `OmniRealtimeClient` を作成します。
- 外部音声出力では、さらに `get_tts_worker()` が返す worker を起動します。
- ネイティブ音声 realtime provider は自身で音声を返すため、外部 TTS 経路を使いません。

したがって Offline Client は、Realtime Client 障害時の自動フォールバックではありません。両者の切り替えは、セッションモードに基づく manager の判断です。

## 実行境界

- 設定と永続化の大部分は同期ファイルシステム操作です。async 呼び出し側は、用意されている `a*` wrapper を使うか、明示的にスレッドへオフロードします。
- どちらの会話クライアントも async メソッドを公開します。Realtime は永続トランスポートとバックグラウンド受信タスクを持ち、Offline はターンごとにストリーミング要求を実行します。
- 外部 TTS は専用 worker スレッドで動作します。セッション manager が要求/応答キューで async モデル出力とそのスレッドを接続します。

まず [LLMSessionManager](/ja/modules/core) で呼び出し経路を確認し、その後クライアントと TTS のページで provider 固有の動作を参照してください。

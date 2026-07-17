# Offline Client

**Package:** `main_logic/omni_offline_client/`

`OmniOfflineClient` はテキスト入力セッションで使うストリーミング chat-completion クライアントです。`LLMSessionManager` は `input_mode == "text"` のとき明示的にこれを選択します。realtime 接続失敗後の自動フォールバックではありません。

公開クラスは lifecycle、streaming、media、tool、Gemini-support の各 mixin から構成され、`main_logic.omni_offline_client` が安定した import パスです。

## リクエストモデル

`connect(instructions)` はメモリ上の system message とクライアント状態を初期化しますが、永続的なモデル socket は開きません。各 `stream_text()` は async ストリーミング要求を実行して callback を通知し、その後に会話履歴を更新します。

主な lifecycle メソッドは次のとおりです。

| メソッド | 契約 |
|---|---|
| `connect(instructions, native_audio=False)` | system prompt と履歴を初期化 |
| `stream_text(text)` | ユーザーターンを送り、可視テキストを stream して完了ターンを保存 |
| `stream_image(image_b64)` | 次のテキストターン用に画像を stage |
| `switch_model(model, use_vision_config=False)` | async モデル切替 lock の下でチャットクライアントを置換 |
| `prime_context(text, skipped=False)` | 起動/hot-swap context を system message に追加 |
| `create_response(instructions, skipped=False)` | 永続ユーザーメッセージを追加する realtime 互換 interface |
| `prompt_ephemeral(...)` | instruction 自体を永続化せず、一時 instruction を実行 |
| `cancel_response()` / `handle_interruption()` | active response の後続 chunk 受け入れを停止 |
| `close()` | HTTP/SDK クライアントを閉じ、履歴と staged media を消去 |

`stream_audio()` と `send_event()` は互換 no-op です。テキストモードの STT はこのクライアント内では行いません。

## Provider とマルチモーダル入力

通常経路はプロジェクトの chat-LLM adapter を使って、設定済みの OpenAI/Anthropic 互換 provider に接続します。条件を満たすネイティブ Gemini 設定は Google GenAI SDK 経路を使います。互換性は adapter と endpoint に依存し、任意の OpenAI 風サーバーを保証するものではありません。

このクライアントはテキスト専用ではありません。`stream_image()` は base64 画像を queue に入れ、次の `stream_text()` がマルチモーダルなユーザーメッセージを構築します。画像がある場合は、別途設定された vision model と endpoint へ切り替えられます。履歴肥大化を抑えるため、古い画像 payload は保持履歴から削除されます。

外部音声合成は `LLMSessionManager` と [TTS Client](/ja/modules/tts-client) の責務であり、`OmniOfflineClient` には属しません。

## ツール呼び出し

`set_tools()`、`set_tool_call_handler()`、`has_tools()` が現在の tool 契約を管理します。Tool-aware streaming は互換チャット形式とネイティブ Gemini SDK 形式を処理し、tool call を検証して manager callback を呼び出し、結果をモデルへ戻します。`max_tool_iterations` はモデル/tool の反復を制限し、既定値は 3 です。

## Async とキャンセルの境界

- Streaming と lifecycle メソッドは async で、モデル置換は `_model_switch_lock` で直列化されます。
- 会話履歴は一つのクライアントインスタンスに属します。並行ユーザーターンで同時に変更する使い方はサポートされません。
- `cancel_response()` は response state を変更し、後続 chunk を破棄します。すでに送信済みの全 provider request を transport level でキャンセルできる保証はありません。
- `close()` は同期的な Gemini SDK close もスレッドへオフロードし、event loop をブロックしません。

## リトライと障害動作

通常のテキスト streaming は、リトライ可能なモデル/ネットワークエラーを短い backoff 付きで最大 3 回試行します。close または interruption により以降の試行は止まります。アカウント、API key、quota、safety、empty completion、repetition、response length は別々の status/discard callback で manager に通知されます。

構築、モデル切替、または全リトライが失敗すると、エラーは callback か例外でセッション層へ伝わります。クライアント自身が `OmniRealtimeClient` や別 provider へ切り替えることはありません。

# セッション管理

`main_logic/core.py` の `LLMSessionManager` クラスは、各キャラクターの会話セッションの中央コーディネーターです。各キャラクターは独自のマネージャーインスタンスを持ちます。

## セッションライフサイクル

```
new connection ──> start_session() ──> stream_data() ──> end_session()
                        │                                      │
                        │                               ホットスワップで
                        │                               事前ウォームアップ済みセッションへ
                        │
                   OmniRealtimeClientを作成
                   TTSワーカースレッドを開始
                   次のセッションを準備（バックグラウンド）
```

## 主要な属性

| 属性 | 型 | 用途 |
|------|-----|------|
| `websocket` | WebSocket | 現在のクライアント接続 |
| `lanlan_name` | str | キャラクター識別子 |
| `session` | OmniRealtimeClient | 現在のLLMセッション |
| `is_active` | bool | セッションが実行中かどうか |
| `input_mode` | str | `"audio"` または `"text"` |
| `voice_id` | str | キャラクターのTTSボイスID |
| `tts_request_queue` | Queue | 送信TTS要求 |
| `tts_response_queue` | Queue | 受信TTS音声 |
| `agent_flags` | dict | エージェント機能フラグ |
| `hot_swap_audio_cache` | list | スワップ中にバッファリングされた音声 |

## ホットスワップメカニズム

ホットスワップシステムは、ダウンタイムゼロのセッション移行を実現します：

1. **準備**: 現在のセッションがユーザー入力を処理している間に、最新のキャラクター設定で新しい `OmniRealtimeClient` セッションがバックグラウンドで作成されます。

2. **キャッシュ**: `end_session()` が呼ばれると、処理中の音声出力は `hot_swap_audio_cache` に保存されます。

3. **スワップ**: `_perform_final_swap_sequence()` が古いセッションを新しいセッションにアトミックに置き換えます。

4. **フラッシュ**: キャッシュされた音声がクライアントに送信され、シームレスな移行が実現されます。

これにより、キャラクターは会話ターンの間に、ユーザーに遅延を感じさせることなく、個性、声、またはモデル設定を更新できます。

## 音声処理

音声はリサンプリングパイプラインを通過します：

```
LLM output (24kHz PCM) ──> soxr resampler ──> 48kHz PCM ──> base64 ──> WebSocket
```

リサンプラーは `soxr`（高品質サンプルレート変換）を使用して、LLMのネイティブ24kHz出力をブラウザが期待する48kHz再生レートに変換します。

## エージェント連携

セッションマネージャーはコールバックを通じてエージェントシステムと連携します：

1. エージェントの結果が `MainServerAgentBridge` のZeroMQ経由で到着
2. 結果は `pending_agent_callbacks` を通じて該当する `LLMSessionManager` にディスパッチ
3. `trigger_agent_callbacks()` がエージェントの結果を次のLLM会話ターンに注入
4. LLMはその後、ユーザーへの応答でエージェントの結果を参照可能

## 翻訳サポート

`translate_if_needed()` は、ユーザーの言語がキャラクターの設定言語と異なる場合に自動翻訳を提供します。これは `TranslationService` を使用し、googletrans → translatepy → LLMベース翻訳の順にフォールバックします。

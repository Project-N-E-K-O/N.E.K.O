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
| `is_hot_swap_imminent` | bool | 最終スワップ進行中 |
| `is_preparing_new_session` | bool | バックグラウンド準備中 |
| `message_cache_for_new_session` | list | 準備中の会話を蓄積 |
| `pending_session_warmed_up_event` | Event | バックグラウンド準備完了を通知 |
| `background_preparation_task` | Task | バックグラウンド準備用asyncioタスク |
| `final_swap_task` | Task | アトミックスワップ用asyncioタスク |

## ホットスワップメカニズム

ホットスワップシステムは、約40秒サイクルでゼロダウンタイムのセッション移行を実現します：

```
0-40s:  アクティブセッションがターンを処理
  40s:  稼働時間閾値到達 → メモリアーカイブトリガー
  50s:  サマリー完了 + 10秒遅延 → バックグラウンド準備開始
        • 新しいクライアントを準備（OmniRealtimeClient or OmniOfflineClient）
        • 必要に応じてTTSワーカーをウォームアップ
        • pending_session_warmed_up_event をセット
  50s+: 現在のターン終了 + 新セッション準備完了 → 最終スワップ
        • 旧セッションを閉じる
        • 新セッションをアクティブ化
        • レート制限付きでホットスワップ音声キャッシュをフラッシュ（5×チャンク倍率）
        • 会話をシームレスに再開
```

1. **準備**: 現在のセッションがユーザー入力を処理している間に、最新のキャラクター設定で新しいセッションがバックグラウンドで作成されます。

2. **キャッシュ**: `end_session()` が呼ばれると、処理中の音声出力は `hot_swap_audio_cache` に保存されます。

3. **スワップ**: `_perform_final_swap_sequence()` が古いセッションを新しいセッションにアトミックに置き換えます。

4. **フラッシュ**: キャッシュされた音声がレート制限付きでクライアントに送信され、シームレスな移行が実現されます。

これにより、キャラクターは会話ターンの間に、ユーザーに遅延を感じさせることなく、個性、声、またはモデル設定を更新できます。

## コールバックハンドラー

`LLMSessionManager` はセッションイベント用の豊富なコールバックハンドラーを提供します：

| ハンドラー | 用途 |
|-----------|------|
| `handle_new_message()` | TTSキューのクリア、リサンプラー状態のリセット、新しい `speech_id` の生成 |
| `handle_text_data(text, is_first_chunk)` | フロントエンドへのテキスト送信 + TTS要求のエンキュー |
| `handle_audio_data(audio_bytes)` | リサンプリング（24→48kHz）してフロントエンドへストリーミング |
| `handle_input_transcript(transcript)` | ユーザーアクティビティ追跡 + メッセージキャッシュ更新 |
| `handle_output_transcript(text, is_first_chunk)` | テキスト表示 + TTSエンキュー |
| `handle_response_complete()` | TTSシグナル、ターン終了、ホットスワップトリガー評価 |
| `handle_response_discarded()` | TTSパイプラインのクリア、フロントエンドへの通知 |
| `handle_silence_timeout()` | 90秒の沈黙で自動クローズ（GLM/Free APIのみ） |

## 音声処理

音声はステートフルなリサンプリングパイプラインを通過します：

```
LLM output (24kHz PCM) ──> soxr ResampleStream ──> 48kHz PCM ──> base64 ──> WebSocket
```

リサンプラーは `soxr` `ResampleStream` を使用し、チャンク境界での不連続を防ぐためチャンク間で内部状態を保持します。リサンプラーの状態はクロスメッセージのアーティファクトを避けるため、各新メッセージ（`handle_new_message()`）でリセットされます。

## エージェント連携

セッションマネージャーはコールバックを通じてエージェントシステムと連携します：

1. エージェントの結果が `MainServerAgentBridge` のZeroMQ経由で到着
2. 結果は `pending_agent_callbacks` を通じて該当する `LLMSessionManager` にディスパッチ
3. `trigger_agent_callbacks()` がエージェントの結果を次のLLM会話ターンに注入
4. LLMはその後、ユーザーへの応答でエージェントの結果を参照可能

## 主動メッセージバッチング

連続する未同期のアシスタントメッセージはメモリ同期前にマージされます。システムはユーザー駆動と主動（エージェント発起）レスポンスを区別し、メモリストレージでの適切な帰属を保証します。

## 翻訳サポート

`translate_if_needed()` は、ユーザーの言語がキャラクターの設定言語と異なる場合に自動翻訳を提供します。これは `TranslationService` を使用し、googletrans → translatepy → LLMベース翻訳の順にフォールバックします。

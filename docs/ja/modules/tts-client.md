# TTS Client

**ファイル:** `main_logic/tts_client.py`

TTS クライアントは、統一されたキューベースのインターフェースで複数のプロバイダーにわたるテキスト音声合成を処理します。

## ファクトリ関数

```python
from main_logic.tts_client import get_tts_worker

worker = get_tts_worker(core_api_type='qwen', has_custom_voice=False)
```

アクティブなプロバイダーと音声設定に応じた TTS ワーカーを作成します。

## プロバイダー解決

ファクトリは以下の優先順位に従います：

1. `tts_custom` が設定されている場合: `gptsovits_tts_worker`（HTTP）または `local_cosyvoice_worker`（WebSocket）
2. `has_custom_voice=True` の場合: `cosyvoice_vc_tts_worker`（DashScope ボイスクローニング）
3. `core_api_type` に応じて: 対応するワーカーにルーティング
4. フォールバック: `dummy_tts_worker`

## サポートされるプロバイダー

| プロバイダー | ワーカー関数 | プロトコル | 特徴 |
|-------------|------------|----------|------|
| Qwen CosyVoice | `qwen_realtime_tts_worker` | WebSocket | DashScope Realtime TTS |
| CosyVoice VC | `cosyvoice_vc_tts_worker` | DashScope SDK | カスタムボイス（クローン） |
| StepFun | `step_realtime_tts_worker` | WebSocket | step-tts-2、`wss://lanlan.tech/tts` 経由フリーモード |
| GLM CogTTS | `cogtts_tts_worker` | WebSocket | Zhipu CogTTS |
| Gemini | `gemini_tts_worker` | Google GenAI SDK | Google Gemini TTS |
| OpenAI | `openai_tts_worker` | HTTP | OpenAI TTS API |
| GPT-SoVITS v3 | `gptsovits_tts_worker` | HTTP→WebSocket | ローカルカスタム TTS |
| Local CosyVoice | `local_cosyvoice_worker` | WebSocket | ローカル CosyVoice サーバー直接接続 |
| Free | `step_realtime_tts_worker(free_mode=True)` | WebSocket | lanlan.tech 経由フリーティア |
| (none) | `dummy_tts_worker` | — | No-op フォールバック |

## キューアーキテクチャ

TTS クライアントはプロデューサー・コンシューマーパターンを使用します：

1. **リクエストキュー**: セッションマネージャーによりテキスト文がエンキュー
2. **ワーカースレッド**: テキストをデキューし、TTS API を呼び出し、オーディオチャンクを生成
3. **レスポンスキュー**: リサンプリングと WebSocket 配信の準備が整ったオーディオチャンク

## 音声クローニングフロー

1. ユーザーがキャラクター API 経由で音声サンプルをアップロード
2. 音声が DashScope の音声登録 API に送信される
3. `voice_id` が返され、キャラクター設定に保存される
4. 以降の TTS 呼び出しに `cosyvoice_vc_tts_worker` が `voice_id` 付きで使用される
5. システムはクラウドにフォールバックする前にまずローカル TTS を試行

## 中断処理

ユーザーが中断した場合：

1. 両方のキューがフラッシュされる
2. 進行中の TTS API 呼び出しがキャンセルされる
3. ワーカーは即座に新しい入力を受け付ける準備が整う

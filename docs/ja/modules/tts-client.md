# TTS Client

**Package:** `main_logic/tts_client/`

TTS package は現在の core・voice 設定から外部音声 provider を解決し、統一されたキュー契約を持つ worker 関数を提供します。`LLMSessionManager` がキューを所有し、daemon thread を起動して、生成 PCM をクライアントへ転送します。

ネイティブ音声 realtime model はこの外部 TTS 経路を使いません。

## Factory 契約

```python
from main_logic.tts_client import get_tts_worker

worker_fn, api_key_override, provider_key = get_tts_worker(
    core_api_type="qwen",
    has_custom_voice=False,
    voice_id="",
)
```

`get_tts_worker()` は worker object を作成・起動しません。次の三つを返します。

1. session manager の thread で実行する worker 関数
2. その route が必要とする任意の API key override
3. key 解決と診断に使う canonical `provider_key`

非対応または利用不能な選択は dummy worker に解決され、合成成功を装わず、キューへ制御されたエラーを報告します。

## Provider 選択

登録済みの特殊 route は、native/core-provider fallback より先に priority 順で評価されます。

1. GPT-SoVITS
2. vLLM-Omni
3. MiniMax clone voice
4. ElevenLabs clone voice
5. CosyVoice clone voice
6. MiMo
7. Doubao TTS

残りの選択は active core route に従い、Qwen/Qwen International、free route の Step または Gemini、Step、GLM/CogTTS、Gemini、OpenAI、Grok を含みます。有効な clone route は voice metadata と選択 provider で決まり、voice cloning は DashScope 専用ではありません。

Provider 実装は `main_logic/tts_client/workers/` にあります。provider 固有の decode と、必要に応じた 48 kHz resampling を含め、出力をアプリケーションが期待する PCM format に正規化します。

## キュープロトコル

Manager は worker thread へ次の request item を送ります。

| Request | 意味 |
|---|---|
| `(speech_id, text)` | 一つの text segment を合成 |
| `(None, None)` | 現在の utterance を終了 |
| `("__interrupt__", None)` | 現在の utterance を破棄/mute し、worker state を reset |
| `("__shutdown__", None)` | worker を停止 |

Worker は raw PCM chunk、またはタグ付き `("__audio__", speech_id, payload)` message と、ready/error などの control message を返します。`speech_id` により manager は interruption 済み、または置換済み response の遅延音声を拒否できます。

## Thread と async の境界

Model text は asyncio 側で生成されます。Session manager が text を分割して TTS request を enqueue し、選択された同期 worker が専用 thread で消費します。Manager の response task は event loop を block せず response queue を読み、受理した音声を WebSocket client へ転送します。

したがって TTS package 自体は、session lifecycle、WebSocket delivery、worker thread を所有しません。

## Interruption とエラー

Interruption 時は manager が queued output を drain し、interrupt sentinel を送り、対応 provider では worker acknowledgement を待ち、遅延 chunk を排除して pending speech ID を消去します。Provider worker の能力は異なり、upstream request を cancel できるものもあれば、出力停止または結果の mute しかできないものもあります。すべての remote API call を物理的に cancel できる保証はありません。

起動・合成失敗は control/error message で返されます。Manager が retry、status 表示、worker 再起動、speech path 無効化を判断します。Voice enrollment の HTTP route と cloned `voice_id` の永続化は、runtime TTS queue とは別の責務です。

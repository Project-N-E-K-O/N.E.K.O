# セッション管理

`LLMSessionManager` はキャラクターごとのチャット転送、LLM client、TTS、hot-swap、tool callback、proactive delivery を統括します。クラスは `main_logic/core/manager.py` で組み立てられ、domain method は `lifecycle.py`、`streaming.py`、`turn.py`、`tts_runtime.py`、`proactive.py` などの mixin にあります。

## 所有モデル

Main Server はロード済みキャラクターごとに1つの manager を保持します。Manager はブラウザ接続より長く生存できますが、現在の WebSocket のみを保持し、最新の接続世代だけが操作できます。切断 cleanup は期待する WebSocket を検証し、古い socket が新しい接続を破棄するのを防ぎます。

Manager は2種類の LLM client を所有します。

| 入力モード | Client | 入出力 |
|---|---|---|
| `text` | `OmniOfflineClient` | text/image 入力、stream text 出力、project TTS による発話 |
| `audio` | `OmniRealtimeClient` | realtime PCM/transcript、voice routing に応じた native audio または project TTS |

text/audio の切り替えは in-place toggle ではなく再構築です。

## 起動ステートマシン

```text
start_session requested
        │
        ├─ concurrent start の直列化 / cross-mode start 待機
        ├─ model と voice 設定の再読込
        ├─ session_ready = false
        ├─ memory context 取得と initial prompt 構築
        ├─ mode 別 client をローカル変数に構築
        ├─ connect、guarded callback bind、tool sync
        ├─ compare-and-set で self.session に昇格
        ├─ 必要な external TTS の開始または再利用
        └─ pending input flush、session_started
```

フロントエンドはまず `session_preparing` を受け、成功時は `session_started`、失敗時は `session_failed` を受けます。半端に作成された resource は閉じられます。

Memory context は起動依存であり、失敗時に空 context へ落とす optional fallback ではありません。Memory Server request が失敗すると connection error となり、通常の failure cleanup と retry/circuit-breaker count に入ります。

`_starting_session_count` と `_starting_input_mode` が非同期起動区間を保護します。別モードの要求は進行中の起動完了を待ってから直列 teardown/restart を行います。Compare-and-set 昇格により、遅れて完了した起動が新しい session を上書きしません。

起動が3回連続で失敗すると circuit が開き、内部 recovery は停止します。ユーザー起点の `start_session` が circuit を解除し、短い cooldown も高速再試行を防ぎます。

## 入力順序とバックプレッシャー

Upstream client の準備前でも入力を受けられます。

- 順序付き text/image は `input_cache_lock` 下の `pending_input_data` に保持され、activation 後に flush されます。
- 音声は最大300件の `asyncio.Queue` を通ります。満杯時は最古の項目を捨て、memory の無制限増加を防ぎます。
- Audio processor 有効時は 48 kHz microphone chunk を noise reduction し、realtime upstream 用の 16 kHz に変換します。
- Stream operation は session と audio epoch を snapshot し、前処理中に teardown/replacement が起きたデータを破棄します。

そのため caller は `self.session` に直接書かず `stream_data()` を使用します。

## セッション更新と hot-swap

Hot-swap は context と microphone input を失わずに長時間会話を更新します。

1. 同一モードの pending client を次の context snapshot でバックグラウンド prewarm します。
2. 更新境界で final context と budget 内の Agent/event callback を pending client に prime します。
3. 昇格直前に到着した音声は前処理後 `hot_swap_audio_cache` に保存します。
4. 旧 listener を cancel/await し、pending client を原子的に `self.session` へ昇格します。
5. キャッシュした 16 kHz input audio を新 realtime client へ有界 chunk で flush します。

この cache は**swap 中のユーザー入力音声**であり assistant output ではありません。`end_session()` は teardown で、prewarm session への swap を意味しません。昇格失敗・取消時は pending resource を閉じ、安全に消費されなかった callback を保持します。

## TTS の所有

Project TTS 使用時、manager は request `Queue`、response `Queue`、daemon provider thread、async response handler を所有します。Text session はこの経路を使い、audio session は realtime provider-native audio を使う場合があります。

TTS readiness と pending text は session readiness とは別管理です。互換性のある live worker は session start をまたいで再利用でき、provider、voice、endpoint、model、credential が変わると新 runtime identity/worker になります。

Project TTS は 48 kHz mono PCM を配信します。Resampling は provider ごとの source rate に応じて worker 内で行われ、`LLMSessionManager` の共通最終工程ではありません。

## Agent と proactive delivery

Agent result は Main Server の ZeroMQ bridge から到着し、該当キャラクターの `pending_agent_callbacks` に入ります。

- Text mode は state machine が idle のとき controlled proactive text turn を開始できます。
- Voice mode は対応する manual injection を試し、未対応なら次の hot-swap prime まで queue に残します。
- 実際の frontend `voice_play_start` / `voice_play_end` で proactive speech を gate し、生成完了を再生完了と誤認しません。
- Goodbye mode や takeover controller は queue を失わずに通常の local delivery を延期・抑制できます。

Delivery は lock と token budget で保護され、収まらない項目は次の turn に残ります。WebSocket reconnect 時にも再配信を試みます。

## Teardown

`end_session()` と `cleanup()` は listener、startup/swap task、TTS handler、pending resource を cancel/await してから参照を消します。Resource identity を snapshot するため、並行 start が作った新しい worker/client を誤って閉じません。

WebSocket 切断は `cleanup(expected_websocket=...)` を呼びます。Server-driven close は必要に応じて `session_ended_by_server` を送ります。

## 翻訳は別サービス

字幕・プロフィール翻訳は `utils/language_utils.py` にあり、`/translate` などの route から公開されます。`LLMSessionManager` の stage ではありません。

Fallback は地域依存です。

- 中国地域: 中国本土から利用可能な `translatepy`、その後 LLM 翻訳。
- その他: Google Translate、その後 LLM 翻訳。Google unavailable が記録されると次回以降は skip。
- すべて失敗した場合は原文を返します。

## 実装マップ

| 関心事 | File |
|---|---|
| 属性所有と mixin assembly | `main_logic/core/manager.py` |
| start、hot-swap、end、cleanup | `main_logic/core/lifecycle.py` |
| input cache と audio streaming | `main_logic/core/streaming.py` |
| turn completion と interruption | `main_logic/core/turn.py` |
| TTS worker lifecycle | `main_logic/core/tts_runtime.py` |
| Agent/proactive callback delivery | `main_logic/core/proactive.py` |
| Browser WebSocket dispatch | `main_routers/websocket_router.py` |

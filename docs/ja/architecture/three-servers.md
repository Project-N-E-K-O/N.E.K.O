# 3サーバー設計

Standalone development command は `uv run python -m app.main_server`、`uv run python -m app.memory_server`、`uv run python -m app.agent_server` です。各 command は package の `__main__.py` を実行し、FastAPI app と実装は package 内にあります。

## Main Server（`app/main_server/`、ポート 48911）

`app/main_server/__main__.py` が Uvicorn を設定し、`app/main_server/__init__.py` と `web_app.py` が組み立てる FastAPI `app` を起動します。`character_runtime.py` が character 別 runtime slot を所有します。

### Startup ownership

Main Server は次を行います。

1. storage startup state を解決し、`ConfigManager` を初期化します。
2. 必要に応じて cloud-save state を import し、runtime data が変わった場合は Memory Server に reload を要求します。
3. character data を読み、character ごとの `LLMSessionManager`、WebSocket lock、queue、sync connector task を作成または保持します。
4. Steam/Workshop integration と dynamic static mount を初期化します。
5. Main 側 Agent ZeroMQ bridge、background warmup、game cleanup、token tracking を開始します。
6. 組み立て済み router と WebSocket endpoint を `127.0.0.1:48911` で公開します。

Storage selection が block されている場合、browser が startup barrier を解放するまで limited mode に留まれます。したがって startup は固定 import sequence ではありません。

### 所有する責務

- browser page と mount された frontend/model/Workshop asset
- REST router と `/ws/{lanlan_name}` browser session
- character role ごとの `LLMSessionManager`
- 明示的な text `OmniOfflineClient` または audio `OmniRealtimeClient` model session
- 外部 TTS worker thread と frontend 48 kHz PCM delivery
- Memory/Agent control 用 HTTP client と Main-owned ZeroMQ socket
- memory persistence と任意 Monitor mirror 用の character 別 cross-server stream

該当 native realtime provider path の音声は 24 kHz で届き、session manager が 48 kHz へ resample します。外部 TTS worker はすでにアプリの output PCM contract で返します。

## Memory Server（`app/memory_server/`、ポート 48912）

Memory Server はキャラクター別の永続メモリを管理します。live working context は Main Server の LLM session に残り、Memory Server は完了 turn の受信、durable view の更新、startup context の rendering、明示的 recall request への応答を担います。

### 永続データと派生インデックス

| データ | 用途 | Backend |
|---|---|---|
| 直近履歴 | sliding conversation window と LLM 圧縮 memo | character 別 `recent.json` |
| 時間インデックス付き原文 | 時系列 source conversation history | SQLite `time_indexed_original` table |
| Fact | source と processing metadata を持つ抽出 statement | `facts.json` と flat `facts_archive.json` |
| Reflection | evidence score と pending、confirmed、promoted/merged、denied、archived state を持つ observation | `reflections.json` と archive shard |
| Persona | new-dialog context に render される durable character/user profile | `persona.json` と archive shard |
| Retrieval index | recall candidate 用 BM25 と任意の local-ONNX vector | derived cache。source of truth ではない |

旧 SQLite `time_indexed_compressed` table は互換性のため保持されますが、新規 write は行われません。Recent summary は `recent.json` に残り、durable abstraction は fact、reflection、persona が担います。

### 主な操作

- 完了 turn を**取り込み・settle**し、timestamp 付き original history を保持
- chronological source record を置換せず、recent sliding window を**圧縮**
- fact の抽出・整理、evidence 検出、reflection 合成、stable observation の persona への promote/merge
- `/new_dialog` 用に persona、利用可能な reflection、recent context を**render**
- time-only lookup または BM25/任意 vector retrieval と Reciprocal Rank Fusion（RRF）で**オンデマンド recall**。この latency-sensitive tool path は追加 LLM rerank を行わない
- cursor、outbox、event log、reconciliation、decay、archive sweep による**復旧と監査**
- `/memory_browser` で**recent history を review**。この UI は fact、reflection、persona を直接編集しない

`app/memory_server/__main__.py` が standalone Uvicorn entry です。launcher は package を import/mount することもできます。Storage startup barrier により、Main Server が active storage root を確認するまで mutation-heavy runtime work を limited に保てます。

完全な lifecycle と automatic/on-demand context の境界は[メモリシステム](/ja/architecture/memory-system)を参照してください。

## Agent Server（`app/agent_server/`、ポート 48915 / 48916）

`app/agent_server/__main__.py` は package の Tool Server FastAPI app を `127.0.0.1:48915` で起動します。実装は `api_runtime.py`、`api_routes.py`、`capabilities.py`、`registry.py`、`tracker.py`、`results.py`、`plugin_host.py`、`channels/` に分割されています。

Agent startup では capability state、`DirectTaskExecutor`、Agent 側 ZeroMQ bridge、channel probe、background scheduler を初期化します。`plugin_host.py` は isolated thread で embedded user-plugin FastAPI listener を `127.0.0.1:48916` に起動します。Listener が同一 process にあっても、user-plugin execution は feature flag と plugin lifecycle gate の対象です。

### HTTP ownership

- **`:48915` Tool Server** — Agent flag/capability、task submit/inspect、cancel、health、proactive trigger、internal channel control
- **`:48916` embedded user-plugin service** — installed plugin discovery、run lifecycle、market bridge target、deferred plugin completion support

Main Server は公開 `/api/agent/*` surface をこれら内部 service へ proxy します。Browser code は process-local object を呼ばず、Main Server response を authoritative state として扱います。

### ZeroMQ ownership

| 既定アドレス | Pattern | Bind owner | 方向 | 用途 |
|---|---|---|---|---|
| `tcp://127.0.0.1:48961` | PUB / SUB | Main | Main → Agent | Session/lifecycle event |
| `tcp://127.0.0.1:48963` | PUSH / PULL | Main | Main → Agent | Reliable `analyze_request` queue |
| `tcp://127.0.0.1:48962` | PUSH / PULL | Main（`PULL`） | Agent → Main | ACK、status、task update、result |

Agent は対応 SUB/PULL/PUSH mirror socket に connect します。Bridge は synchronous ZeroMQ socket を background receive thread で扱います。Agent → Main delivery に HTTP fallback はありません。

### Task execution path

1. `main_logic/cross_server.py` が bounded conversation view を `analyze_request` として publish し、Main bridge は ACK を待ち、timeout 時に一度 retry します。
2. Agent は master/capability gate、cancelled content redaction、deduplication を適用します。
3. `brain/task_executor.py` の `DirectTaskExecutor` が enabled channel を評価します。廃止済み Analyzer/Planner/Processor class は live pipeline にありません。
4. Non-plugin work は priority 順で最初の executable channel を選びます。user plugin は deterministic candidate filter の後、検証付き LLM entry selection を行います。
5. `app/agent_server/channels/` の選択 adapter が task を register し、update を emit し、work を実行/委譲して terminal result を記録します。
6. ACK、`task_update`、`task_result`、proactive event が `:48962` で戻り、Main bridge が browser を更新し、model-visible result を対応 `LLMSessionManager` へ queue します。

Cancellation は provider-specific teardown より先に registry state を terminal にし、late provider output による上書きを防ぎます。Deferred user-plugin task は completion endpoint または timeout まで running のままです。

Channel routing、flag、retention、delivery semantics は [Agent システム](/ja/architecture/agent-system)を参照してください。

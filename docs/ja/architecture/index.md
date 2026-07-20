# アーキテクチャ概要

Project N.E.K.O. は三つの主要 Python service process で構成されます。Main Server は UI と live session、Memory Server は durable memory、Agent Server は任意の background work の評価・実行を所有します。HTTP/WebSocket が service/browser traffic を、ZeroMQ が Agent event bridge を運びます。

## システム図

![アーキテクチャ](/framework.svg)

## 主要サービス

| Service | 既定ポート | Standalone entry | 役割 |
|---|---:|---|---|
| **Main Server** | 48911 | `app/main_server/__main__.py` | Web UI/static asset、REST API、browser WebSocket、character 別 session、外部 TTS |
| **Memory Server** | 48912 | `app/memory_server/__main__.py` | 会話 ingest、recent context、fact/reflection/persona、startup rendering、recall |
| **Agent / Tool Server** | 48915 | `app/agent_server/__main__.py` | capability state、task assessment、channel dispatch、cancel、task result |

各 service の FastAPI app と実装は対応 package 内にあります。特に Agent 実装は `app/agent_server/` にあります。

Agent process はさらに isolated thread で、`127.0.0.1:48916` に embedded user-plugin FastAPI service をホストします。これは第二の HTTP listener であり、第四の主要 process ではありません。任意の Monitor Server（`:48913`）は character 別 mirror stream を受信しますが、core 3-service control path の外側です。

## 通信マップ

```text
Browser
  │ HTTP + WebSocket
  ▼
Main Server :48911
  ├── HTTP ───────────────> Memory Server :48912
  ├── HTTP control ───────> Agent Tool Server :48915
  ├── HTTP proxy/call ────> Embedded User-Plugin :48916
  ├── WebSocket mirror ───> Monitor Server :48913（任意）
  └── ZeroMQ bridge
       PUB  :48961 ───────> Agent SUB       session/lifecycle event
       PUSH :48963 ───────> Agent PULL      reliable analyze request
       PULL :48962 <─────── Agent PUSH      ACK、task update、result
```

三つの ZeroMQ socket はすべて Main process が bind し、Agent process が対応する mirror socket へ connect します。Agent → Main result に HTTP fallback はありません。

## 主要な runtime pattern

### Character 別 ownership

`app/main_server/character_runtime.py` は `lanlan_name` ごとに一つの role-state slot を所有します。その内容は `LLMSessionManager`、async WebSocket lock、sync-message queue、cross-server connector asyncio task です。Inactive manager は長寿命 task の停止後に置換でき、active/starting manager は保持されます。

### 明示的 session mode と条件付き hot swap

Text input は `OmniOfflineClient`、audio input は `OmniRealtimeClient` を使い、manager が暗黙に failover することはありません。Pending-session preparation は turn/token threshold、renew state、queued context により開始され、すべての session start で無条件には実行されません。

### Async、thread、process 境界

- FastAPI lifecycle、WebSocket I/O、model callback、cross-server connector、Main 側 Agent bridge coordination は asyncio 上で動作します。
- 外部 TTS provider worker は character 別 thread で request/response queue を使います。
- Agent ZeroMQ bridge は synchronous socket を background receive thread で包み、Windows Proactor loop に対応します。
- Embedded user-plugin HTTP server は独自 thread で動きますが、Agent と process を共有します。
- Memory/Agent state は process-local です。他 process は相手の runtime object を import せず、公開 HTTP/ZeroMQ contract を使います。

## 次へ

- [3サーバー設計](/ja/architecture/three-servers) — service ownership と startup boundary
- [データフロー](/ja/architecture/data-flow) — browser input から model output、persistence まで
- [セッション管理](/ja/architecture/session-management) — mode selection と hot-swap lifecycle
- [メモリシステム](/ja/architecture/memory-system) — persistence、automatic rendering、on-demand recall
- [Agent システム](/ja/architecture/agent-system) — assessment、channel、event delivery、task state

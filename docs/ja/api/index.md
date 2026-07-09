# API リファレンス

N.E.K.O. は FastAPI を通じて包括的な API を公開しています。すべてのエンドポイントはメインサーバー（デフォルト `http://localhost:48911`）から提供されます。

## ベース URL

```
http://localhost:48911
```

## 認証

ローカルアクセスでは認証は不要です。LLM プロバイダーの API キーは[設定](/ja/config/)システムで別途管理されます。

## REST エンドポイント

| ルーター | プレフィックス | 説明 |
|--------|--------|-------------|
| [Config](/ja/api/rest/config) | `/api/config` | API キー、ユーザー設定、プロバイダー設定 |
| [Characters](/ja/api/rest/characters) | `/api/characters` | キャラクターの CRUD、音声設定、マイク |
| [Live2D](/ja/api/rest/live2d) | `/api/live2d` | Live2D モデル管理、感情マッピング |
| [VRM](/ja/api/rest/vrm) | `/api/model/vrm` | VRM モデル管理、アニメーション |
| [Memory](/ja/api/rest/memory) | `/api/memory` | メモリファイル、レビュー設定 |
| [Agent](/ja/api/rest/agent) | `/api/agent` | エージェントフラグ、タスク、ヘルスチェック |
| [Workshop](/ja/api/rest/workshop) | `/api/steam/workshop` | Steam Workshop アイテム、パブリッシュ |
| [MMD](/ja/api/rest/mmd) | `/api/model/mmd` | MMD モデル管理 |
| [PNGTuber](/ja/api/rest/pngtuber) | `/api/model/pngtuber` | PNGTuber モデル管理 |
| [Music](/ja/api/rest/music) | `/api/music` | 音楽検索と再生プロキシ |
| [Jukebox](/ja/api/rest/jukebox) | `/api/jukebox` | 楽曲とアクションのライブラリ |
| [Game](/ja/api/rest/game) | `/api/game` | ミニゲームバックエンド |
| [Galgame](/ja/api/rest/galgame) | `/api/galgame` | ギャルゲーの返信オプション |
| [Icebreaker](/ja/api/rest/icebreaker) | `/api/icebreaker` | 新規ユーザーのオンボーディング |
| [Proactive](/ja/api/rest/proactive) | `/api/proactive` | プロアクティブチャットのモードと設定 |
| [System](/ja/api/rest/system) | `/api` | 感情分析、スクリーンショット、ユーティリティ |

### その他 / 内部ルーター

以下のルーターは稼働していますが、ここでは個別にドキュメント化されていません：`capture`（`/api/capture`）、`cloudsave`（`/api/cloudsave`）、`storage-location`（`/api/storage/location`）、`avatar-drop`（`/api/avatar-drop`）、`card-assist`（`/api/card-assist`）、`auth`/cookies（`/api/auth`）、`tool`（`/api/tools`）、`debug`（`/api/debug`）。

## WebSocket

| エンドポイント | 説明 |
|----------|-------------|
| [プロトコル](/ja/api/websocket/protocol) | 接続ライフサイクルとセッション管理 |
| [メッセージタイプ](/ja/api/websocket/message-types) | すべてのクライアント→サーバーおよびサーバー→クライアントのメッセージフォーマット |
| [オーディオストリーミング](/ja/api/websocket/audio-streaming) | バイナリオーディオフォーマット、割り込み、リサンプリング |

## 内部 API

これらはサービス間 API であり、外部からの使用を意図していません：

| サーバー | 説明 |
|--------|-------------|
| [Memory Server](/ja/api/memory-server) | メモリの保存と取得（ポート 48912） |
| [Agent Server](/ja/api/agent-server) | エージェントタスクの実行（ポート 48915） |

## レスポンスフォーマット

すべての REST エンドポイントは JSON を返します。成功レスポンスは通常、データを直接含みます。エラーレスポンスは FastAPI のデフォルトフォーマットに従います：

```json
{
  "detail": "Error message describing what went wrong"
}
```

## コンテンツタイプ

- `application/json` — ほとんどのエンドポイント
- `multipart/form-data` — ファイルアップロード（モデル、音声サンプル）
- `audio/*` — 音声プレビューレスポンス

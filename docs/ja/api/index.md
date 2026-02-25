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
| [System](/ja/api/rest/system) | `/api` | 感情分析、スクリーンショット、ユーティリティ |

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

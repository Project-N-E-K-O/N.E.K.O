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
| [Config](/ja/api/rest/config) | `/api/config` | API キー、ユーザー設定、プロバイダー設定、言語 |
| [Characters](/ja/api/rest/characters) | `/api/characters` | キャラクターの CRUD、音声設定、音声クローン、キャラクターカード |
| [Live2D](/ja/api/rest/live2d) | `/api/live2d` | Live2D モデル管理、感情マッピング |
| [VRM](/ja/api/rest/vrm) | `/api/model/vrm` | VRM モデル管理、アニメーション、ライティング |
| [Memory](/ja/api/rest/memory) | `/api/memory` | メモリファイル、レビュー設定 |
| [Agent](/ja/api/rest/agent) | `/api/agent` | エージェントフラグ、機能チェック、タスク、ヘルスチェック |
| [Workshop](/ja/api/rest/workshop) | `/api/steam/workshop` | Steam Workshop サブスクリプション、パブリッシュ、メタデータ |
| [System](/ja/api/rest/system) | `/api` | 感情分析、Steam 連携、翻訳、ユーティリティ |
| [Pages](/ja/api/rest/pages) | — | Jinja2 テンプレートによる HTML ページ配信 |

## WebSocket

| エンドポイント | 説明 |
|----------|-------------|
| [プロトコル](/ja/api/websocket/protocol) | 接続ライフサイクルとセッション管理 |
| [メッセージタイプ](/ja/api/websocket/message-types) | すべてのクライアント→サーバーおよびサーバー→クライアントのメッセージフォーマット |
| [オーディオストリーミング](/ja/api/websocket/audio-streaming) | バイナリオーディオフォーマット、割り込み、リサンプリング |

## 内部 API

これらはサービス間 API であり、外部からの使用を意図していません：

| サーバー | ポート | 説明 |
|--------|------|-------------|
| [Memory Server](/ja/api/memory-server) | 48912 | メモリの保存、取得、設定抽出、レビュー |
| [Agent Server](/ja/api/agent-server) | 48915 | エージェントタスクの実行、機能フラグ |
| Monitor Server | 48913 | ヘルスチェック、ランチャーへの設定リレー |

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

# キャプチャブリッジ API

キャプチャルーターは、N.E.K.O. Electron レンダラーのキャプチャブリッジに対する HTTP 入口です。Linux の pure Wayland など、ネイティブキャプチャバックエンドが別アプリのウィンドウを読み取れない場合に GalGame プラグインが使用します。

> [!WARNING]
> これはファーストパーティー用のローカルブリッジ契約であり、汎用スクリーンショット API ではありません。両エンドポイントはループバッククライアントだけを受け入れ、接続済みの N.E.K.O. Electron レンダラーがメイン WebSocket 経由で処理する必要があります。非ループバックの呼び出し元には HTTP `403` が返ります。

## ルート一覧

| メソッド | パス | 用途 |
|---|---|---|
| `GET` | `/api/capture/health` | キャプチャ可能なレンダラーが接続中か確認する |
| `POST` | `/api/capture/screenshot` | 指定したデスクトップソースのキャプチャをレンダラーへ要求する |

## ヘルス

レンダラーが登録済みの場合：

```http
GET /api/capture/health
```

```json
{"success": true, "available": true}
```

レンダラーがない場合は HTTP `503`：

```json
{"success": false, "available": false, "error": "no_renderer"}
```

## ソースをキャプチャ

```http
POST /api/capture/screenshot
Content-Type: application/json

{
  "target_id": "window:123456:0",
  "pid": 4242,
  "title": "Example Game"
}
```

| フィールド | 型 | 要件 |
|---|---|---|
| `target_id` | integer または string | 必須。文字列へ正規化され、長さ 1 以上かつブリッジ上限以内 |
| `pid` | integer | 必須、0 より大きい値 |
| `title` | string | 省略可能、最大 512 文字、既定値 `""` |

未知のフィールドは拒否されます。`target_id` はプラグインバックエンドが取得したネイティブウィンドウハンドル、または Electron `desktopCapturer` の source ID です。ソース解決はレンダラーが担当し、HTTP ルーター自体はデスクトップをキャプチャしません。

成功時は画像 data URL と任意のレンダラーメタデータを返します：

```json
{
  "success": true,
  "image": "data:image/jpeg;base64,...",
  "width": 1920,
  "height": 1080,
  "source_id": "window:123456:0"
}
```

`width`、`height`、`source_id` は、レンダラーが有効な値を返した場合だけ含まれます。

## エラーレスポンス

| ステータス | `error` | 意味 |
|---:|---|---|
| `400` | `invalid_json` | リクエスト本文が有効な JSON ではない |
| `403` | `loopback_only` | 呼び出し元がループバッククライアントではない |
| `422` | `validation_error` | 本文フィールドまたは `target_id` の長さが不正 |
| `502` | `source_not_found` | レンダラーが指定デスクトップソースを見つけられない |
| `502` | `bridge_error` | キャプチャブリッジのその他の上流エラー |
| `502` | `empty_image` | レンダラーが使用可能な画像を返さなかった |
| `503` | `no_renderer` | キャプチャ可能なレンダラーが接続されていない |
| `504` | `renderer_timeout` | ブリッジのタイムアウトまでにレンダラーが応答しなかった |
| `500` | `internal_error` | 予期しないサーバー側エラー |

ブリッジは[メッセージタイプ](/ja/api/websocket/message-types)に記載された WebSocket の `capture_bridge_status`、`capture_bridge_response`、サーバー側キャプチャ要求メッセージを使います。返される画像はユーザーの機密画面内容として扱い、ログに記録しないでください。

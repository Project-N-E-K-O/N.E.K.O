# Config API

**プレフィックス:** `/api/config`

この router は同梱 frontend が使う provider 設定、接続 probe、モデル表示設定、locale hint、会話設定、GPT-SoVITS 検出、実行時 proxy 切替を提供します。

::: warning 内部設定 surface
同梱 UI 向けで、versioned public SDK ではありません。JSON field は provider と frontend 機能に伴って増えます。独自認証を追加しない限り loopback のみで公開してください。
:::

## Provider 設定と probe

### `POST /api/config/test_connectivity`

設定画面と同じ種類の provider probe を実行します。Pydantic body は 2 モードです。

```json
{
  "provider_key": "openai",
  "provider_scope": "core",
  "api_key": "..."
}
```

または custom endpoint:

```json
{
  "url": "https://example.test/v1",
  "api_key": "...",
  "model": "model-name",
  "provider_type": "openai_compatible",
  "sub_type": "",
  "voice_id": "",
  "is_free": false
}
```

応答は `success`、必要に応じて `error`、`error_code`、`resolved_url`。Pydantic 型エラーは `422`。network/auth/model 失敗は設定画面で分類表示するため、通常 HTTP `200` と `success: false` です。

### Core provider

| メソッドとパス | 用途 |
|---|---|
| `GET /api/config/core_api` | core/assist/audio provider の実効設定を取得。保存済み secret は mask されます。 |
| `POST /api/config/core_api` | 検証済み設定を merge し、影響する session を通知/再起動。UI が返した mask は新しい key として扱いません。 |
| `GET /api/config/api_providers` | runtime provider catalog と frontend metadata を返します。 |

POST body は `coreApi`、`coreApiKey`、`assistApi` など同梱設定 UI の field と provider 固有 field を使う拡張可能 JSON です。

### GPT-SoVITS

| メソッドとパス | 用途 |
|---|---|
| `POST /api/config/gptsovits/list_voices` | HTTP base URL を検証し、音声一覧を proxy。 |
| `POST /api/config/gptsovits/test_connectivity` | WebSocket init/ready/synthesis を再生なしで検証。 |

いずれも設定画面の接続値を受け `success` envelope を返します。検証・接続失敗は段階により `400`、`502`、`504` です。

## Preference と会話設定

| メソッドとパス | 用途 |
|---|---|
| `GET /api/config/preferences` | モデルごとの表示 preference を取得。 |
| `POST /api/config/preferences` | 必須 `model_path`、`position`、`scale` と、任意 `parameters`、`display`、`rotation`、`viewport`、`camera_position` を保存。 |
| `POST /api/config/preferences/set-preferred` | 必須 `model_path` を preference 順序の先頭へ移動。 |
| `GET /api/config/conversation-settings` | global 会話設定と初回既定値用 telemetry branch を取得。 |
| `POST /api/config/conversation-settings` | global 会話設定を保存。`noiseReductionEnabled` は互換 active session に即時適用。 |

検証失敗は通常 `{ "success": false, "error": "..." }`、storage maintenance 中は HTTP service unavailable になる場合があります。

## ページ・言語データ

| メソッドとパス | 用途 |
|---|---|
| `GET /api/config/page_config` | 指定/現在キャラクターと Live2D、VRM、MMD、PNGTuber path を解決。任意 query `lanlan_name`。応答は `no-store`。 |
| `GET /api/config/character_reserved_fields` | frontend/backend 共通の予約 profile field 設定を返します。 |
| `GET /api/config/steam_language` | Steam locale と利用可能なら GeoIP hint を返します。 |
| `GET /api/config/user_language` | frontend/subtitle 用 user language を返します。 |

## Proxy mode

### `POST /api/config/set_proxy_mode`

現在 process の proxy 環境変数を hot switch します。

```json
{ "direct": true }
```

`true` は proxy 変数を snapshot・削除し `NO_PROXY=*` を設定、`false` は snapshot を復元します。`proxies_after` の資格情報は除去済みです。実行中 process のみ変更します。

## 実装で確認した route 一覧

```text
POST /api/config/test_connectivity
GET  /api/config/core_api
POST /api/config/core_api
GET  /api/config/api_providers
POST /api/config/gptsovits/list_voices
POST /api/config/gptsovits/test_connectivity
GET  /api/config/steam_language
GET  /api/config/user_language
GET  /api/config/character_reserved_fields
GET  /api/config/page_config
GET  /api/config/preferences
POST /api/config/preferences
POST /api/config/preferences/set-preferred
GET  /api/config/conversation-settings
POST /api/config/conversation-settings
POST /api/config/set_proxy_mode
```

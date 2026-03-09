# Config API

**プレフィックス:** `/api/config`

API プロバイダー設定、ユーザー設定、ページ設定を管理します。

## エンドポイント

### `GET /api/config/page_config`

ページ設定（モデルパス、モデルタイプ）を取得します。

**クエリパラメータ:**

| 名前 | 型 | 必須 | 説明 |
|------|------|----------|-------------|
| `lanlan_name` | string | いいえ | キャラクター名 |

**レスポンス:** Live2D/VRM モデルパスとタイプを含むページ設定。

---

### `GET /api/config/preferences`

ユーザー設定（モデル選択、表示設定）を取得します。

---

### `POST /api/config/preferences`

ユーザー設定を更新します。

**ボディ:** 設定のキーと値のペアを含む JSON オブジェクト。

```json
{
  "model_path": "/path/to/model",
  "position": { ... },
  "scale": 1.0,
  "parameters": { ... },
  "display": { ... },
  "rotation": { ... },
  "viewport": { ... },
  "camera_position": { ... }
}
```

---

### `POST /api/config/preferences/set-preferred`

キャラクターの優先モデルを設定します。

**ボディ:**

```json
{
  "model_path": "/path/to/model"
}
```

---

### `GET /api/config/steam_language`

Steam クライアントの言語設定と GeoIP 情報を取得します。

**レスポンス:**

```json
{
  "i18n_language": "zh",
  "ip_country": "CN",
  "is_mainland_china": true
}
```

---

### `GET /api/config/user_language`

ユーザーが設定した言語設定を取得します。

---

### `GET /api/config/core_api`

現在のコア API 設定（プロバイダー、モデル、エンドポイント）を取得します。

::: warning
このエンドポイントは生の API キーを公開しません。キーはマスクされた形式で返されます。
:::

---

### `POST /api/config/core_api`

コア API 設定を更新します。

**ボディ:**

```json
{
  "coreApiKey": "sk-xxxxx",
  "coreApi": "qwen",
  "assistApi": "qwen",
  "enableCustomApi": false,
  "conversationModel": "model-name",
  "summaryModel": "model-name"
}
```

利用可能なプロバイダー値については [API プロバイダー](/ja/config/api-providers) を参照してください。

---

### `GET /api/config/api_providers`

利用可能なすべての API プロバイダーとその設定の一覧を取得します（フロントエンドドロップダウン用のコア＋アシストプロバイダー）。

---

### `POST /api/config/gptsovits/list_voices`

ローカルサービスから利用可能な GPT-SoVITS v3 音声の一覧を取得します。

**ボディ:**

```json
{
  "api_url": "http://localhost:9880"
}
```

::: warning
セキュリティのため、localhost URL のみが受け付けられます。
:::

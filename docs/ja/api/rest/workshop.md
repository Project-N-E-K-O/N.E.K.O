# Steam Workshop API

**プレフィックス:** `/api/steam/workshop`

Steam Workshop アイテムを管理します — サブスクライブ済みアイテムの閲覧、パブリッシュ、ローカル Mod 管理。

::: info
Steam Workshop 機能を使用するには、Steam クライアントが起動中で、Steamworks SDK が初期化されている必要があります。
:::

## アイテム

### `GET /api/steam/workshop/subscribed-items`

サブスクライブ済みのすべての Steam Workshop アイテムを取得します。

### `GET /api/steam/workshop/item/{item_id}`

特定の Workshop アイテムの詳細を取得します。

### `POST /api/steam/workshop/publish`

新しいアイテムを Steam Workshop にパブリッシュします。

**ボディ:** アイテムメタデータ。必須フィールド: `title`、`content_folder`、`visibility`（および description、tags などの任意メタデータ）。

::: warning
パブリッシュはシリアライズされたロックを使用して、同時パブリッシュ操作を防止します。
:::

## 設定

### `GET /api/steam/workshop/config`

Workshop 設定（Workshop ルートパス、メタデータ）を取得します。

## Workshop メタデータ

Workshop アイテムは、そのディレクトリ内の `.workshop_meta.json` ファイルにキャラクターカードメタデータを保存します。これには以下が含まれます：

- キャラクターのパーソナリティデータ
- モデルバインディング
- 音声設定
- パブリケーションメタデータ

すべてのファイル操作にパストラバーサル保護が適用されます。

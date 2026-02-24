# Steam Workshop API

**プレフィックス:** `/api/steam/workshop`

Steam Workshop アイテムを管理します — サブスクライブ済みアイテムの閲覧、パブリッシュ、ローカル Mod 管理。

::: info
Steam Workshop 機能を使用するには、Steam クライアントが起動中で、Steamworks SDK が初期化されている必要があります。
:::

## アイテム

### `GET /api/steam/workshop/items`

サブスクライブ済みのすべての Steam Workshop アイテムを取得します。

### `GET /api/steam/workshop/items/{item_id}`

特定の Workshop アイテムの詳細を取得します。

### `POST /api/steam/workshop/items/publish`

新しいアイテムを Steam Workshop にパブリッシュします。

**ボディ:** タイトル、説明、タグ、コンテンツパスを含むアイテムメタデータ。

::: warning
パブリッシュはシリアライズされたロックを使用して、同時パブリッシュ操作を防止します。
:::

### `POST /api/steam/workshop/items/{item_id}/update`

既存の Workshop アイテムを更新します。

## 設定

### `GET /api/steam/workshop/config`

Workshop 設定（Workshop ルートパス、メタデータ）を取得します。

### `GET /api/steam/workshop/local_items`

Workshop にまだパブリッシュされていないローカル Mod/アイテムを一覧表示します。

## Workshop メタデータ

Workshop アイテムは、そのディレクトリ内の `.workshop_meta.json` ファイルにキャラクターカードメタデータを保存します。これには以下が含まれます：

- キャラクターのパーソナリティデータ
- モデルバインディング
- 音声設定
- パブリケーションメタデータ

すべてのファイル操作にパストラバーサル保護が適用されます。

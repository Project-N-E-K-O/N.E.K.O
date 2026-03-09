# Steam Workshop API

**プレフィックス:** `/api/steam/workshop`

Steam Workshop アイテムを管理します — サブスクライブ済みアイテムの閲覧、パブリッシュ、ローカル Mod 管理、設定。

::: info
Steam Workshop 機能を使用するには、Steam クライアントが起動中で、Steamworks SDK が初期化されている必要があります。
:::

## サブスクライブ済みアイテム

### `GET /api/steam/workshop/subscribed-items`

サブスクライブ済みのすべての Steam Workshop アイテムを取得します。

### `GET /api/steam/workshop/item/{item_id}`

特定の Workshop アイテムの詳細情報を取得します。

### `GET /api/steam/workshop/item/{item_id}/path`

Workshop アイテムのローカルインストールパスを取得します。

### `POST /api/steam/workshop/unsubscribe`

Workshop アイテムのサブスクリプションを解除します。

**ボディ:**

```json
{ "item_id": 12345678 }
```

## パブリッシュ

### `POST /api/steam/workshop/prepare-upload`

パブリッシュ用のコンテンツを準備します（一時ディレクトリの作成、ファイルのコピー）。

**ボディ:**

```json
{
  "charaData": { ... },
  "modelName": "model_name",
  "fileName": "optional_filename",
  "character_card_name": "optional_card_name"
}
```

### `POST /api/steam/workshop/publish`

Steam Workshop にアイテムをパブリッシュします。

**ボディ:**

```json
{
  "title": "Item Title",
  "content_folder": "/path/to/content",
  "visibility": 0,
  "preview_image": "/path/to/preview.jpg",
  "description": "Item description",
  "tags": ["tag1", "tag2"],
  "change_note": "Initial release",
  "character_card_name": "card_name"
}
```

### `POST /api/steam/workshop/upload-preview-image`

プレビュー画像をアップロードします（JPEG/PNG、`preview.*` として統一）。

**ボディ:** `file` フィールドとオプションの `content_folder` を含む `multipart/form-data`。

### `GET /api/steam/workshop/check-upload-status`

現在のアップロード状態を確認します。

**クエリ:** `item_path` — 確認するコンテンツパス。

### `POST /api/steam/workshop/cleanup-temp-folder`

一時アップロードディレクトリをクリーンアップします。

**ボディ:**

```json
{ "temp_folder": "/path/to/temp" }
```

## 設定

### `GET /api/steam/workshop/config`

Workshop 設定（パス、設定値）を取得します。

### `POST /api/steam/workshop/config`

Workshop 設定を更新します。

**ボディ:**

```json
{
  "default_workshop_folder": "/path",
  "auto_create_folder": true,
  "user_mod_folder": "/path"
}
```

## メタデータ

### `GET /api/steam/workshop/meta/{character_name}`

キャラクターカードの Workshop メタデータを取得します。

## ローカルアイテム

### `POST /api/steam/workshop/local-items/scan`

ローカルディレクトリをスキャンして Workshop 互換アイテムを検索します。

**ボディ:**

```json
{ "folder_path": "/path/to/scan" }
```

### `GET /api/steam/workshop/local-items/{item_id}`

ローカル Workshop アイテムを取得します。

**クエリ:** `folder_path`（オプション）

## ファイルユーティリティ

### `GET /api/steam/workshop/read-file`

Workshop ファイルからコンテンツを読み取ります。

**クエリ:** `path` — Workshop コンテンツ内のファイルパス。

### `GET /api/steam/workshop/list-chara-files`

ディレクトリ内のすべての `.chara.json` ファイルを一覧表示します。

**クエリ:** `directory`

### `GET /api/steam/workshop/list-audio-files`

ディレクトリ内のすべてのオーディオファイルを一覧表示します。

**クエリ:** `directory`

::: warning
すべてのファイル操作にパストラバーサル保護が適用されます。
:::

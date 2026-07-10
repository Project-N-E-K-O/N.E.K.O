# MMD API

**Prefix:** `/api/model/mmd`

MMD（MikuMikuDance）アバターモデルを管理します — PMX/PMD モデル、VMD アニメーション、モデルごとの感情マッピング。

::: info
MMD の表情はモーフターゲット / ブレンドシェイプベースです。各感情はモデル内の名前付きモーフを駆動して表現されます。下記の感情マッピングのエンドポイントは、感情ラベルをモデルのモーフターゲットに関連付けます。
:::

## アップロード

### `POST /api/model/mmd/upload`

単一の MMD モデルファイル（`.pmx` / `.pmd`）をアップロードします。リクエストは `multipart/form-data` で `file` フィールドを持ち、チャンク単位でディスクにストリーミング書き込みされます。最大サイズは 500 MB です。

**Response:**

```json
{
  "success": true,
  "message": "...",
  "model_name": "<filename stem>",
  "model_url": "/user_mmd/<filename>",
  "file_size": 0
}
```

エラー時（ファイルなし、拡張子不一致、ファイルが既に存在、サイズ超過）は `{ "success": false, "error": "..." }` を 4xx/5xx ステータスで返します。

### `POST /api/model/mmd/upload_animation`

単一の VMD アニメーションファイル（`.vmd`）をアップロードします。モデルアップロードと同じく `multipart/form-data` の `file` フィールドを使い、500 MB の上限が適用されます。ユーザー MMD ディレクトリの `animation/` サブディレクトリに保存されます。

**Response:**

```json
{
  "success": true,
  "message": "...",
  "filename": "<filename>",
  "file_path": "/user_mmd/animation/<filename>"
}
```

### `POST /api/model/mmd/upload_zip`

MMD モデルの **ZIP パッケージ**（`.pmx`/`.pmd` モデルとそのテクスチャ）をアップロードします。アーカイブは一時ファイルに書き込まれ、検証された後、モデル名にちなんだサブディレクトリ（またはアーカイブ既存のトップレベルフォルダ）へ展開されます。

::: info
多くの MMD アーカイブは日本由来で、ファイル名を UTF-8 フラグなしの Shift-JIS / CP932（中国語/韓国語パッケージでは GBK、Big5、EUC-KR）で格納しています。サーバーは実際のファイル名エンコーディングを検出し、展開時に文字化けさせずに元の CJK 名を復元します。
:::

ZIP には少なくとも 1 つの `.pmx`/`.pmd` ファイルが含まれている必要があります。zip bomb 対策が適用されます。エントリ数は最大 10000、展開後の合計サイズは最大 2 GB で、絶対パスや `..` を含むエントリは拒否されます。

**Response:**

```json
{
  "success": true,
  "message": "...",
  "model_name": "<model stem>",
  "model_url": "/user_mmd/<relative path to model>",
  "file_count": 0,
  "file_size": 0
}
```

## 一覧

### `GET /api/model/mmd/models`

利用可能な MMD モデル（`.pmx` / `.pmd`）を一覧表示します。プロジェクトの `static/mmd/` ディレクトリ、ユーザー MMD ディレクトリ、購読中の Steam ワークショップアイテムを再帰的に検索します。

**Response:** `{ "success": true, "models": [ ... ] }`。各エントリには `name`、`filename`、`url`、`rel_path`、`type`、`size`、`location`（`"project"`、`"user"`、`"steam_workshop"`）が含まれます。ワークショップのエントリには `source` と `item_id` も付きます。モデルファイルのない残存モデルディレクトリは `"broken": true` として返されます。

### `GET /api/model/mmd/animations`

プロジェクトの `static/mmd/animation/` ディレクトリとユーザー MMD の `animation/` ディレクトリから VMD アニメーションファイルを一覧表示します。

**Response:** `{ "success": true, "animations": [ ... ] }`。各エントリには `name`、`filename`、`url`、`type`（`"vmd"`）、`size` が含まれます。

### `GET /api/model/mmd/animations/list`

削除可能なユーザーアップロードの VMD アニメーション（ユーザー MMD の `animation/` ディレクトリ配下のもの）を一覧表示します。

**Response:** `{ "success": true, "animations": [ ... ] }`。各エントリには `name`、`filename`、`url`、`path` が含まれます。

## 設定

### `GET /api/model/mmd/config`

MMD のパス設定を返します。

**Response:**

```json
{
  "success": true,
  "paths": {
    "user_mmd": "/user_mmd",
    "static_mmd": "/static/mmd"
  }
}
```

## 感情マッピング

### `GET /api/model/mmd/emotion_mapping`

モデルの感情マッピング設定を取得します。

**Query:** `model` — モデル名（パス区切り文字は不可）。省略時、または設定が存在しない場合は空のマッピングを返します。

**Response:** `{ "success": true, "mapping": { ... } }`

### `POST /api/model/mmd/emotion_mapping`

モデルの感情マッピングを作成または更新します。マッピングはモデルごとにユーザー MMD の `emotion_config/` ディレクトリへ永続化されます。

**Body:**

```json
{
  "model": "<model name>",
  "mapping": { }
}
```

`model` は必須で、パス区切り文字を含めることはできません。`mapping` はオブジェクトである必要があります。

**Response:** `{ "success": true, "message": "..." }`

## 削除

### `DELETE /api/model/mmd/model`

ユーザー MMD モデルを削除します。モデルがサブディレクトリ内にある場合は、そのディレクトリ全体（テクスチャやその他の関連リソース）が削除されます。トップレベルのモデルファイルは単体で削除されます。対応する感情マッピング設定も併せて削除されます。プロジェクト組み込みモデル（`/static/mmd/...`）は削除できません。

**Body:**

```json
{
  "url": "/user_mmd/<relative path>"
}
```

**Response:** `{ "success": true, "message": "...", "deleted_files": 0 }`

### `DELETE /api/model/mmd/animation`

ユーザーアップロードの VMD アニメーションを削除します。対象はユーザー MMD の `animation/` ディレクトリ配下の `.vmd` ファイルである必要があります。

**Body:**

```json
{
  "url": "/user_mmd/animation/<filename>"
}
```

**Response:** `{ "success": true, "message": "..." }`

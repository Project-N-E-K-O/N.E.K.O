# PNGTuber API

**Prefix:** `/api/model/pngtuber`

PNGTuber アバターを管理します。PNGTuber は 2D 画像ベースのアバターで、画像ステート（待機・発話・リアクション）を切り替えることで見た目を制御します。エンドポイントはパッケージのアップロード・一覧取得・削除をカバーします。

## モデルパッケージ

PNGTuber モデルは（複数ファイルのパッケージとしてアップロードされる）フォルダーで、`model_type` が `"pngtuber"` に設定された `model.json` を含みます。`pngtuber` 設定ブロックが各アバターステートを画像ファイルへマッピングします。`idle_image` は必須で、その他のステートはすべて任意です。

サポートする画像ステート:

- `idle_image`（**必須**）
- `talking_image`
- `drag_image`
- `click_image`
- `happy_image`
- `sad_image`
- `angry_image`
- `surprised_image`

サポートする画像拡張子: `.png`、`.gif`、`.jpg`、`.jpeg`、`.webp`。

::: info
サイズ制限: 1 ファイルあたり最大 **50 MB**、パッケージ全体で最大 **250 MB** です。
:::

## アップロード

### `POST /api/model/pngtuber/upload_model`

PNGTuber パッケージを複数ファイルの `multipart/form-data` リクエストとしてアップロードします。各パートは 1 つのファイルで、その `filename` がパッケージ内の相対パスを持ちます（共通の最上位フォルダーは自動的に取り除かれます）。ファイルはステージング用ディレクトリへストリーミングされ、その後パッケージの判定と正規化・検証が行われ、ユーザーモデルディレクトリへ確定されます。

**Body:** 1 つ以上の `files` パートを含む `multipart/form-data`。パッケージには、ルートの `model.json`（`model_type: "pngtuber"`）か、認識可能なサードパーティのプロジェクトファイル（下記「インポートアダプター」を参照）のいずれかが含まれている必要があります。

**Response（成功）:**

```json
{
  "success": true,
  "message": "...",
  "model_type": "pngtuber",
  "model_name": "...",
  "name": "...",
  "folder": "...",
  "url": "/user_pngtuber/<folder>/model.json",
  "pngtuber": { },
  "source_format": "simple_package",
  "warnings": [],
  "file_size": 0
}
```

`pngtuber` オブジェクトは正規化された設定です。画像ステートのパスは `/user_pngtuber/<folder>/...` 配下へ書き換えられ、レイアウト用フィールド（`scale`、`offset_x`、`offset_y`、`mobile_scale`、`mobile_offset_x`、`mobile_offset_y`、`mirror`）と `adapter`、`layered_metadata`、`source_type`、`source_format` が付加されます。

エラー時のレスポンスは `{ "success": false, "error": "..." }` です（認識できたがインポートに失敗した場合は `source_format` と `warnings` も含まれます）。

::: info
検証では `model_type` が `"pngtuber"` であること、`idle_image` が空でないことが必須です。各相対 `*_image` パスはサポートする拡張子を使用し、パッケージ内に実在するファイルを指している必要があります。
:::

#### インポートアダプター

パッケージがまだネイティブの `model.json` でない場合、アップローダーがソース形式を判定し、その場で変換します:

- **`simple_package`** —— ネイティブ N.E.K.O パッケージ: ルートの `model.json`（`model_type: "pngtuber"`）。そのまま使用します。
- **PNGTuber-Plus**（`.save`）→ `source_format: "pngtuber_plus_save"`、**`layered_canvas_v1`** アダプター経由で変換。発話レイヤーとまばたきレイヤーを優先的に有効化し、物理とマルチフレームアニメーションは後続のランタイム対応に備えてメタデータとして保持します。
- **PNGTube-Remix**（`.pngRemix`）→ `source_format: "pngtube_remix_pngremix"`、**`layered_canvas_v1`** アダプター経由で変換。発話レイヤーとまばたきレイヤーを優先的に有効化し、ホットキー・物理・メッシュはメタデータとして保持します。
- **veadotube**（`.veadomini` / `.veado`）→ 認識はされますが**未対応**です。アップロードは拒否され、`source_format: "veadotube"` を返すとともに、対応のためのサンプル提供を求めます。

## 一覧

### `GET /api/model/pngtuber/models`

インポート済みのすべての PNGTuber モデルを一覧表示します。各エントリはパッケージの `model.json` から読み込まれます（`model.json` の `model_type` が `"pngtuber"` のフォルダーのみが対象で、無効なパッケージはスキップされます）。

**Response:**

```json
{
  "success": true,
  "models": [
    {
      "name": "...",
      "folder": "...",
      "filename": "...",
      "location": "user",
      "type": "pngtuber",
      "model_type": "pngtuber",
      "url": "/user_pngtuber/<folder>/model.json",
      "pngtuber": { },
      "source_format": "simple_package"
    }
  ]
}
```

## 削除

### `DELETE /api/model/pngtuber/model`

PNGTuber モデルパッケージとそのすべてのファイルを削除します。

**Body:**

```json
{ "folder": "<folder>" }
```

対象は**フォルダー slug** で解決されます。ハンドラーは `folder` を読み取り、なければ `url`、さらに `name` の順でフォールバックします。どの値が渡されてもフォルダー slug として扱われ（`.../<folder>/model.json` を指す `url` はその `<folder>` へ解決されます）、人間が読める表示名と照合されることはありません。

削除には `GET /models` が返す `folder` slug、または model.json の `url` を使うことを推奨します。`name` への依存は避けてください。`GET /models` が返す `name` は表示名で、`folder` がディスク上の slug であり、両者は異なる場合があります。表示用の `name` を渡しても、それがたまたまフォルダー slug と一致するときしか機能しないため、曖昧になりうる最終手段としてのみ使用してください。解決後のパスは PNGTuber ディレクトリ内に収まっている必要があります。

**Response:** `{ "success": true, "message": "..." }`。識別子の欠落やパスの範囲外は `400`、存在しないモデルは `404` を返します。

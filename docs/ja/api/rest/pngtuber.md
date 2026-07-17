# PNGTuber API

**Prefix:** `/api/model/pngtuber`

PNGTuber アバターを管理します。PNGTuber は 2D 画像ベースのアバターで、画像ステート（待機・発話・リアクション）を切り替えることで見た目を制御します。エンドポイントはパッケージのアップロード・一覧取得・削除をカバーします。

ファーストパーティー用のローカルモデル管理ルートは 3 つです：`POST /api/model/pngtuber/upload_model`、`GET /api/model/pngtuber/models`、`DELETE /api/model/pngtuber/model`。アップロード／削除はユーザーモデルディレクトリを書き換え、失敗は `{ "success": false, "error": "..." }` 形式です。独立した認証レイヤーはなく、パス末尾に `/` はありません。

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

パッケージがまだネイティブの `model.json` でない場合、アップローダーがソース形式を判定し、その場で変換します。判定された形式は `source_format` として返されます:

- `source_format: "simple_package"` —— ネイティブ N.E.K.O パッケージ: ルートの `model.json`（`model_type: "pngtuber"`）。そのまま使用し、idle/talking/drag/click と軽量な感情画像を駆動します。
- `source_format: "pngtuber_plus_save"` —— PNGTuber-Plus（`.save`）、**`layered_canvas_v1`** アダプター経由で変換（`adapter_version: 2`）: コスチューム、トグル、発話/まばたき、スプライトシート多フレーム、Plus ノードツリー、矩形クリップ、近似物理をサポート。
- `source_format: "pngtube_remix_pngremix"` —— PNGTube-Remix（`.pngRemix`）、**`layered_canvas_v1`** アダプター経由で変換（`adapter_version: 2`）: ステート切り替え、`emotion_mappings`、スプライトシート、`effective_z_index` 順序、`physics_v2`、利用可能なメッシュ変形をサポート。
- `source_format: "veadotube"` —— veadotube（`.veadomini` / `.veado`）。認識はされますが**未対応**で、アップロードは拒否され、対応のためのサンプル提供を求めます。
- `source_format: "image_pair_candidate"` —— `model.json` やプロジェクトファイルのない画像のみ。拒否され、2 枚画像インポートを案内します。

#### 機能と失敗マトリクス

`window.pngtuberManager.getDebugState()` は `source_format` ごとに有効な機能を報告します。感情は `window.applyEmotion('happy')` で駆動され、`pngtuber` モデルでは `pngtuberManager.setEmotion` にルーティングされます。

| 機能 | `simple_package` | `pngtuber_plus_save` | `pngtube_remix_pngremix` |
|------|:----------------:|:--------------------:|:------------------------:|
| idle / talking 切り替え | ✅ | ✅ | ✅ |
| 感情 `window.applyEmotion('happy')` | ✅ 画像切替 | ✅ ステート切替 | ✅ ステート切替 |
| まばたき + 発話バウンス | —— | ✅ | ✅ |
| コスチュームホットキー / トグル | —— | ✅ | —— |
| スプライトシート多フレーム | —— | ✅ | ✅ |
| `physics_v2` | —— | 近似 | ✅ |
| メッシュ変形（`meshRuntime`） | —— | —— | ✅ 実ジオメトリがある場合 |

Remix プロジェクトが実際の vertices / triangles / UVs を含む場合にのみ、debug state の `meshRuntime` が `true` になります。そうでない場合は `meshMetadata` が `true`、`meshRuntime` が `false` のままとなり、理由が `unsupportedFeatures` に列挙されます。

失敗レスポンス:

- `source_format: "veadotube"` → 認識されるが拒否され、実サンプルを待ちます。
- `source_format: "image_pair_candidate"` → 拒否され、2 枚画像インポートまたは `model.json` の追加を案内します。
- 一意に決められない複数の `.save` → HTTP 400 を返し、`source_format: "pngtuber_plus_save"` と `warnings` の候補リストを含みます。
- 解析できない `.pngRemix` → PNGTube-Remix 変換失敗（`source_format: "pngtube_remix_pngremix"`）に分類され、`model.json` 欠落エラーには決してなりません。

#### 受け入れチェック

```powershell
node --check static\pngtuber-core.js
node --check static\app-buttons.js
uv run pytest tests\unit\test_pngtuber_static_contracts.py tests\unit\test_card_maker_static_contracts.py tests\unit\test_pngtuber_router_delete.py tests\unit\test_model_manager_window_features.py
```

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

インポート、一覧、ファイルシステムの予期しない失敗は HTTP `500` です。ファーストパーティーのファイル管理 API として扱い、信頼できないクライアントへ公開しないでください。

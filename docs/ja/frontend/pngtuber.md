# PNGTuber モデル

## 概要

N.E.K.O. は Live2D・MMD・VRM モデルの代替として、軽量な 2D 画像アバター（「PNGTuber」スタイル）をレンダリングできます。PNGTuber アバターは `static/pngtuber-core.js`（`PNGTuberManager` クラス）が駆動し、音声とポインター操作に応じて静止画を切り替えます（インポートしたレイヤー方式のプロジェクトの場合は、重ね合わせた canvas を描画します）。

3D/Live2D アバターと違い、PNGTuber パッケージは画像のフォルダと `model.json` 記述ファイルだけで構成されます——リギングも Cubism ランタイムも不要です。

## パッケージ形式

PNGTuber モデルは、`model_type` を `pngtuber` に設定した `model.json` ファイルを含むフォルダです。画像の参照は `pngtuber` オブジェクトの下に置きます。

```json
{
  "name": "My Avatar",
  "model_type": "pngtuber",
  "pngtuber": {
    "idle_image": "idle.png",
    "talking_image": "talking.png",
    "drag_image": "drag.png",
    "click_image": "click.png",
    "happy_image": "happy.png",
    "sad_image": "sad.png",
    "angry_image": "angry.png",
    "surprised_image": "surprised.png"
  }
}
```

### 画像ステートのキー

| キー | 用途 |
|-----|---------|
| `idle_image` | **必須。** 既定の待機フレーム。 |
| `talking_image` | アシスタントの発話中に表示。 |
| `drag_image` | アバターのドラッグ中に表示。 |
| `click_image` | アバターのクリック時に一瞬表示。 |
| `happy_image` / `sad_image` / `angry_image` / `surprised_image` | 感情フレーム（[感情ステート](#感情ステート)を参照）。 |

相対パスはパッケージフォルダ内で解決されます。絶対パス（`/…`）と `http(s)://` URL はそのまま保持されます。画像参照はサーバー側で `/user_pngtuber/<folder>/<file>` に正規化されます。

### 許可される拡張子とサイズ上限

| 制約 | 値 |
|------------|-------|
| 画像拡張子 | `.png`、`.gif`、`.jpg`、`.jpeg`、`.webp` |
| 単一ファイル上限 | 50 MB |
| パッケージ合計上限 | 250 MB |

サーバーはパッケージを受理する前に、`idle_image` が存在すること、およびすべての `*_image` 参照が許可された拡張子を持つ実在のファイルを指していることを検証します。

## 感情ステート

`window.applyEmotion('happy')` がランタイムで PNGTuber の感情を駆動します。`applyEmotion`（`static/app-buttons.js` 内）は、現在の `model_type` が `pngtuber` のとき、Live2D パスにフォールバックする前に `window.pngtuberManager.setEmotion(emotion)` へルーティングします。

- **simple package** —— `setEmotion('happy')` は `happy_image` / `sad_image` / `angry_image` / `surprised_image` に切り替え、既定で 5 秒保持したのち idle に戻ります。ニュートラルな値（`neutral`、`idle`、`default`、`none`、`clear`、空文字列）は感情を即座にクリアします。
- **レイヤーパッケージ** —— `setEmotion` は `setLayeredEmotion` を介して感情をレイヤーステートにマッピングします。Remix の `emotion_mappings` が優先され、それがない場合、モデルが少なくとも 5 つのステートを公開しているときに happy → 1、sad → 2、angry → 3、surprised → 4 のフォールバック順が適用されます。

`idle` ↔ `talking` は引き続きアシスタントの**音声**開始/終了イベントで、`drag` / `click` はポインターの**操作**で切り替わります。一致する感情画像やステートがない場合、`setEmotion` はフレームを変更せずに戻り、`[PNGTuber] emotion unavailable` をログ出力します。

## インポート形式

アップロードエンドポイントはパッケージの種類を検出し、その場で正規化します。検出された種類は `source_format` として返されます。

| 入力元 | 検出方法 | `source_format` |
|--------|-----------|-----------------|
| ネイティブ simple package | フォルダ直下に `model.json` | `source_format: "simple_package"` |
| PNGTuber-Plus | `.save` プロジェクトファイル | `source_format: "pngtuber_plus_save"` |
| PNGTube-Remix | `.pngRemix` プロジェクトファイル | `source_format: "pngtube_remix_pngremix"` |
| veadotube | `.veadomini` / `.veado` ファイル | `source_format: "veadotube"` |
| 画像のみ・プロジェクトファイルなし | `model.json` のない画像ファイル | `source_format: "image_pair_candidate"` |

### レイヤーアダプター（`layered_canvas_v1`）

PNGTuber-Plus または PNGTube-Remix のプロジェクトをインポートすると、コンバーターはレイヤーメタデータファイル（`adapter_version: 2`）を生成し、`adapter` を `layered_canvas_v1` に設定します。ランタイムでは `PNGTuberManager` が単一の `<img>` を切り替える代わりに各レイヤーを `<canvas>` に描画します。Plus と Remix は `source_format` で振り分けられ、互いのランタイムを汚染しません。どちらもランダムなまばたきタイマーと発話バウンスを追加します。メタデータの読み込みに失敗した場合、ランタイムは通常の単一画像モードにフォールバックします。

## 機能マトリクス

`window.pngtuberManager.getDebugState()` は、読み込まれたモデルでどの機能が有効かを報告します。

| 機能 | `simple_package` | `pngtuber_plus_save` | `pngtube_remix_pngremix` |
|------|:----------------:|:--------------------:|:------------------------:|
| idle / talking 切り替え | ✅ | ✅ | ✅ |
| 感情 `window.applyEmotion('happy')` | ✅ 画像切替 | ✅ ステート切替 | ✅ ステート切替 |
| まばたき + 発話バウンス | —— | ✅ | ✅ |
| コスチュームホットキー / トグル | —— | ✅ | —— |
| スプライトシート多フレーム | —— | ✅ | ✅ |
| `physics_v2` | —— | 近似 | ✅ |
| メッシュ変形 | —— | —— | ✅ 実ジオメトリがある場合 |

Remix プロジェクトが実際の vertices / triangles / UVs を含む場合にのみ、debug state の `meshRuntime` が `true` になります。そうでない場合は `meshMetadata` が `true`、`meshRuntime` が `false` のままとなり、理由が `unsupportedFeatures` に列挙されます。

### 失敗時の表示

- **veadotube**（`.veadomini` / `.veado`）→ `source_format: "veadotube"`。アップロードは拒否され、適配用の実サンプル提供を求めます。
- **画像のみ** → `source_format: "image_pair_candidate"`。アップロードは拒否され、2 枚画像インポートまたは `model.json` の追加を案内します。
- **一意に決められない複数の `.save`** → HTTP 400 を返し、`source_format: "pngtuber_plus_save"` と `warnings` の候補リストを含みます。
- **解析できない `.pngRemix`** → PNGTube-Remix 変換失敗（`source_format: "pngtube_remix_pngremix"`）に分類され、`model.json` 欠落エラーには決してなりません。

## 受け入れチェック

静的コントラクト（リポジトリのルートで実行）：

```powershell
node --check static\pngtuber-core.js
node --check static\app-buttons.js
uv run pytest tests\unit\test_pngtuber_static_contracts.py tests\unit\test_card_maker_static_contracts.py tests\unit\test_pngtuber_router_delete.py tests\unit\test_model_manager_window_features.py
```

手動チェック：

- PNGTuber-Plus `.save` をインポートし、コスチューム・トグル・発話/まばたき・スプライトシート多フレーム・親子 transform・矩形クリップが描画されることを確認します。
- 複数ステートの PNGTube-Remix `.pngRemix` をインポートし、`window.applyEmotion('happy')` が正しいステートにマッピングされることを確認します。
- `window.pngtuberManager.getDebugState()` の `sourceFormat`、`adapterVersion`、`runtimeFeatures`、`meshRuntime`、`physicsVersion` を確認します。

## 静的配信

ユーザーの PNGTuber パッケージは `/user_pngtuber` マウントから配信され、ディスク上の設定済み PNGTuber ディレクトリにマッピングされます。モデルファイルは `/user_pngtuber/<folder>/model.json` および `/user_pngtuber/<folder>/<image>` として参照されます。

## API エンドポイント

**プレフィックス:** `/api/model/pngtuber`

### `POST /upload_model`

PNGTuber パッケージを multipart のファイルリストとしてアップロードします。各ファイルの `filename` はパッケージ内の相対パスを保持します。共有された単一の最上位フォルダは自動的に取り除かれます。パッケージはまずステージングされ、検出・検証され、（サードパーティ製プロジェクトの場合は）変換されてから正式に確定されます。

**Body**——`multipart/form-data`、`files` フィールド（1 つ以上の `UploadFile` エントリ）を含みます。

**Response**（成功）

```json
{
  "success": true,
  "message": "...",
  "model_type": "pngtuber",
  "model_name": "My Avatar",
  "name": "My Avatar",
  "folder": "My_Avatar",
  "url": "/user_pngtuber/My_Avatar/model.json",
  "pngtuber": { "idle_image": "/user_pngtuber/My_Avatar/idle.png", "...": "..." },
  "source_format": "simple_package",
  "warnings": [],
  "file_size": 123456
}
```

失敗時は `{ "success": false, "error": "..." }` を該当する 4xx/5xx ステータスとともに返します。サードパーティのインポートエラーには `source_format` と `warnings` も含まれます。

### `GET /models`

インストール済みのユーザー PNGTuber パッケージをすべて一覧します。

**Response**

```json
{
  "success": true,
  "models": [
    {
      "name": "My Avatar",
      "folder": "My_Avatar",
      "filename": "My_Avatar",
      "location": "user",
      "type": "pngtuber",
      "model_type": "pngtuber",
      "url": "/user_pngtuber/My_Avatar/model.json",
      "pngtuber": { "idle_image": "/user_pngtuber/My_Avatar/idle.png", "...": "..." },
      "source_format": "simple_package"
    }
  ]
}
```

有効な `model.json` を持たない、または `model_type` が `pngtuber` でないフォルダはスキップされます。

### `DELETE /model`

インストール済みの PNGTuber パッケージを削除します。

**Body**

```json
{ "folder": "My_Avatar" }
```

識別子は**フォルダ slug** として解決されます（優先順位 `folder` → `url` → `name`）。`/user_pngtuber/My_Avatar/model.json` のような `model.json` の URL は、そのフォルダに解決されます。`GET /models` が返す `folder` slug（または `url`）の使用を推奨します——`name` は表示用の名前で slug と異なる場合があり、`name` での削除はそれがフォルダ名と一致するときのみ機能します。対象は PNGTuber ディレクトリ内に限定されます。

**Response**

```json
{ "success": true, "message": "PNGTuber model My_Avatar deleted" }
```

::: info
PNGTuber のモデル管理は共有の `/model_manager` ページにあります。専用の PNGTuber 感情マネージャーページはありません。アバターの設定メニューは、キャラクターカードマネージャー・モデルマネージャー・ボイスクローンの各ページへリンクします。
:::

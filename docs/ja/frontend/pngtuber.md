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
| `happy_image` / `sad_image` / `angry_image` / `surprised_image` | 感情フレーム（[感情ステート](#感情ステートまだ感情解析では駆動されない)を参照）。 |

相対パスはパッケージフォルダ内で解決されます。絶対パス（`/…`）と `http(s)://` URL はそのまま保持されます。画像参照はサーバー側で `/user_pngtuber/<folder>/<file>` に正規化されます。

### 許可される拡張子とサイズ上限

| 制約 | 値 |
|------------|-------|
| 画像拡張子 | `.png`、`.gif`、`.jpg`、`.jpeg`、`.webp` |
| 単一ファイル上限 | 50 MB |
| パッケージ合計上限 | 250 MB |

サーバーはパッケージを受理する前に、`idle_image` が存在すること、およびすべての `*_image` 参照が許可された拡張子を持つ実在のファイルを指していることを検証します。

## 感情ステート（まだ感情解析では駆動されない）

::: warning 正直な現状
`happy_image` / `sad_image` / `angry_image` / `surprised_image` の各キーはパッケージのスキーマの一部であり、**サーバー側で検証**されます（アップロード時にパスと拡張子をチェック）。しかし PNGTuber ランタイムは、感情解析に基づいてこれらに**まだ**切り替えません。

`PNGTuberManager` が現在駆動するのは以下のみです。

- `idle` ↔ `talking`。アシスタントの**音声**開始/終了イベントで切り替わります。
- `drag` と `click`。ポインターの**操作**で切り替わります。

Live2D / MMD / VRM に相当する `setEmotion` 系のフックはなく、専用の PNGTuber 感情マネージャーページもありません。4 つの感情画像キーは、将来の感情駆動パスに備えてパッケージとともに保存・配布されますが、現時点ではそれらを指定しても検証を通過する以外に目に見える効果はありません。
:::

## インポート形式

アップロードエンドポイントはパッケージの種類を検出し、その場で正規化します。検出された種類は `source_format` として返されます。

| 入力元 | 検出方法 | 結果 |
|--------|-----------|--------|
| ネイティブ simple package | フォルダ直下に `model.json` | `source_format: simple_package`——そのまま使用。 |
| **PNGTuber-Plus** | `.save` プロジェクトファイル | `layered_canvas_v1` アダプターに変換。 |
| **PNGTube-Remix** | `.pngremix` プロジェクトファイル | `layered_canvas_v1` アダプターに変換。 |
| veadotube | `.veadomini` / `.veado` ファイル | **認識されるが未対応**——アップロードは説明付きエラーで拒否されます。 |

### レイヤーアダプター（`layered_canvas_v1`）

PNGTuber-Plus または PNGTube-Remix のプロジェクトをインポートすると、コンバーターはレイヤーメタデータファイルを生成し、`adapter` を `layered_canvas_v1` に設定します。ランタイムでは `PNGTuberManager` が単一の `<img>` を切り替える代わりに各レイヤーを `<canvas>` に描画し、さらに次を追加します。

- **まばたき（blink）**——目のレイヤーがランダムなタイマーでまばたきします。
- **発話バウンス（speech bounce）**——発話中にアバターが上下に弾み/つぶれます。

元プロジェクトのホットキー・物理・マルチフレームアニメーションは、将来のランタイム対応に備えてメタデータに保持されますが、現時点ではすべてが駆動されるわけではありません。メタデータの読み込みに失敗した場合、ランタイムは通常の単一画像モードにフォールバックします。

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

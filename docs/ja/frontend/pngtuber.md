# PNGTuber モデル

## ランタイム

PNGTuber は `static/pngtuber-core.js` が描画する画像ステート型アバターです。`PNGTuberManager` は単純な画像切り替えと、`layered_canvas_v1` アダプターを介した正規化レイヤーパッケージに対応します。Live2D、VRM、MMD と同じメインページのアバター選択と `window.LanLan1.setEmotion()` 契約に接続されます。

## 正規化パッケージ

インポートされた各モデルは設定済みユーザー PNGTuber ディレクトリに保存され、`/user_pngtuber/{folder}/model.json` から配信されます。最小の単純パッケージは次のとおりです。

```json
{
  "model_type": "pngtuber",
  "name": "Example",
  "pngtuber": {
    "idle_image": "idle.png",
    "talking_image": "talking.png"
  }
}
```

`model_type` は `pngtuber`、`idle_image` は必須です。相対画像パスはパッケージ内に留めます。対応拡張子は `.png`、`.gif`、`.jpg`、`.jpeg`、`.webp` です。

任意の画像ステートキーは `talking_image`、`drag_image`、`click_image`、`happy_image`、`sad_image`、`angry_image`、`surprised_image` です。talking が idle を使うなど、欠落ステートにはランタイムフォールバックがあるため、必須なのは `idle_image` だけです。

レイアウトキーには `scale`、`offset_x`、`offset_y`、モバイル専用 scale/offset、`mirror` があります。レイヤーインポートでは `adapter: "layered_canvas_v1"` と `layered_metadata` も使います。

## インポート形式

インポーターは次の順で形式を検出します。

1. ルートに `model.json` があるネイティブ単純パッケージ
2. PNGTuber Plus `.save` プロジェクト
3. PNGTube Remix `.pngRemix` プロジェクト
4. veadotube `.veadomini` または `.veado` ファイル

PNGTuber Plus と PNGTube Remix は正規化パッケージに変換され、レイヤーメタデータや警告を生成することがあります。veadotube は現在、識別後に未対応形式として拒否されます。画像だけのフォルダーもパッケージ API では拒否されるため、モデルマネージャーの画像ペアフローか、有効な `model.json` を使ってください。

リクエストではフォルダーツリーをアップロードできます。サーバーは共通トップディレクトリを 1 つ取り除き、各相対パスを検証し、一時ディレクトリへ書き込み、インポート成功後だけ所定位置へリネームします。既存モデルフォルダーは上書きしません。

上限は 1 ファイル 50 MB、パッケージ全体 250 MB です。

## ステートと感情の動作

基本ステートは idle、talking、drag、click です。意味的な感情は任意の `happy`、`sad`、`angry`、`surprised` 画像または同等のレイヤーステートを使います。インポーターが対応する場合、レイヤーアダプターは第三者形式の表示状態、ホットキー、トグル、スプライトシート、まばたき、物理メタデータを維持できます。

正規化された `source_format` はパッケージの生成元を示します。クライアントは診断にのみ使い、描画方式の選択には使わないでください。描画方式は正規化済み `pngtuber` オブジェクトとアダプターメタデータで決まります。

## API 概要

すべてのエンドポイントは `/api/model/pngtuber` プレフィックスを使います。

| メソッド | エンドポイント | 用途 |
| --- | --- | --- |
| `POST` | `/api/model/pngtuber/upload_model` | フォルダー/パッケージをアップロードして正規化 |
| `GET` | `/api/model/pngtuber/models` | 有効なユーザーパッケージを一覧 |
| `DELETE` | `/api/model/pngtuber/model` | `folder`、`url`、`name` のいずれかでパッケージを削除 |

アップロード成功応答には正規化モデル、公開 URL、`source_format`、警告、アップロード合計サイズが含まれます。一覧 API は有効な PNGTuber `model.json` がないディレクトリを無視します。削除は直接のパッケージフォルダーか `/user_pngtuber/{folder}/model.json` だけを受け付け、ネストパスとパストラバーサルを拒否します。

## ホスト境界

PNGTuber はメインページと `index.html` を使う Electron ペットウィンドウに描画されます。`/chat` と `/subtitle` は別の PNGTuber マネージャーを初期化しません。ウィンドウ間でアバター状態を反映する場合はメインウィンドウと通信します。

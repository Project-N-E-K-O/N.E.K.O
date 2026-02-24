# System API

**プレフィックス:** `/api`

感情分析、ファイルユーティリティ、スクリーンショット、プロアクティブチャットのための各種システムエンドポイント。

## 感情分析

### `POST /api/analyze_emotion`

テキストの感情トーンを分析します。

**ボディ:**

```json
{
  "text": "I'm so happy to see you!",
  "lanlan_name": "character_name"
}
```

**レスポンス:** Live2D/VRM の表情マッピングに使用される感情ラベル。

## ファイルユーティリティ

### `GET /api/file-exists`

指定されたパスにファイルが存在するかどうかを確認します。

**クエリ:** `path` — 確認するファイルパス。

### `GET /api/find-first-image`

ディレクトリ内の最初の画像ファイルを検索します。

**クエリ:** `directory` — 検索するディレクトリパス。

### `GET /api/proxy-image`

CORS 制限を回避するための画像リクエストのプロキシ。

**クエリ:** `url` — プロキシする画像 URL。

## Steam 実績

### `POST /api/steam_achievement`

Steam 実績をアンロックします。

**ボディ:**

```json
{ "achievement_id": "ACHIEVEMENT_NAME" }
```

## プロアクティブチャット

### `POST /api/proactive_chat`

キャラクターからのプロアクティブメッセージを生成します（アイドル会話に使用されます）。

**ボディ:**

```json
{
  "lanlan_name": "character_name",
  "context": "optional context about what's happening"
}
```

::: info
プロアクティブメッセージにはレート制限があります：キャラクターごとに1時間あたり最大10件。
:::

## Web スクリーニング

### `POST /api/web_screening`

AI レビューによる Web コンテンツのスクリーニング（コンテンツフィルタリングと関連性ランキング用）。

**ボディ:** スクリーニングモード付きの Web コンテンツデータ。

## スクリーンショット分析

### `POST /api/screenshot_analysis`

ビジョンモデルを使用してスクリーンショットを分析します。

**ボディ:** オプションのコンテキスト付き Base64 エンコード画像データ。

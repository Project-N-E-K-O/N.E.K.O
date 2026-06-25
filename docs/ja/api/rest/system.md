# System API

**プレフィックス:** `/api`

感情分析、ファイルユーティリティ、スクリーンショット、プロアクティブチャットのための各種システムエンドポイント。

## 感情分析

### `POST /api/emotion/analysis`

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

### `GET /api/meme/proxy-image`

CORS 制限を回避するためにリモート画像（ミームなど）をプロキシします。SSRF 保護とキャッシュ付き。

**クエリ:** `url` — プロキシするリモート画像 URL（http/https である必要があります）。

### `GET /api/steam/proxy-image`

ローカル画像ファイル（特に Steam Workshop ディレクトリ）へのアクセスをプロキシします。絶対パスと相対パスに対応します。

**クエリ:** `image_path` — 画像のローカルファイルパス。

## Steam 実績

### `POST /api/steam/set-achievement-status/{name}`

Steam 実績をアンロックします。実績名はパスパラメータ `{name}` として渡します。

**パスパラメータ:** `name` — Steam 実績名（例: `ACH_FIRST_DIALOGUE`）。

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

::: info
内部的には、プロアクティブチャットは 2 段階のパイプラインで動作します。フェーズ 1 の LLM 呼び出しが候補となる Web コンテンツをスクリーニングし（あわせて音楽/ミームのキーワードを抽出）、その後フェーズ 2 でペルソナに沿った返信を生成します。単独で呼び出せる Web スクリーニング用エンドポイントは存在しません。
:::

## スクリーンショット

### `POST /api/screenshot`

バックエンドのスクリーンショットフォールバック：フロントエンドの画面キャプチャ API がすべて失敗した場合に、バックエンドが pyautogui でローカル画面をキャプチャします。ループバックのみ。バックエンドがリモートとして構成されている場合は無効です。

**レスポンス:** `{ "success": true, "data": "data:image/jpeg;base64,...", "size": <バイト数> }`

### `POST /api/screenshot/interactive`

システムネイティブの対話的（範囲選択）スクリーンショット。チャットのスクリーンショットボタンが優先的に使用します。macOS は `screencapture` の範囲選択を使用し、それ以外のプラットフォームではフロントエンドに委譲します。ループバックのみ。

**レスポンス:** JSON エンベロープ（生の DataURL ではありません）。

```json
{ "success": true, "data": "data:image/jpeg;base64,...", "size": <バイト数> }
```

ユーザーが選択をキャンセルした場合: `{ "success": false, "canceled": true }`。localhost 以外 / リモートとして構成されたバックエンドの場合: `{ "success": false, "error": "..." }`。

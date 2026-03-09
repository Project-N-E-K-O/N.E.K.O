# System API

**プレフィックス:** `/api`

感情分析、ファイルユーティリティ、Steam 連携、プロアクティブチャット、翻訳のための各種システムエンドポイント。

## 感情分析

### `POST /api/emotion/analysis`

テキストの感情トーンを分析します。

**ボディ:**

```json
{
  "text": "I'm so happy to see you!",
  "lanlan_name": "character_name",
  "api_key": "optional",
  "model": "optional"
}
```

**レスポンス:**

```json
{
  "emotion": "happy",
  "confidence": 0.95
}
```

## ファイルユーティリティ

### `GET /api/file-exists`

指定されたパスにファイルが存在するかどうかを確認します。

**クエリ:** `path` — 確認するファイルパス。

### `GET /api/find-first-image`

フォルダ内の最初のプレビュー画像ファイルを検索します。

**クエリ:** `folder` — 検索するフォルダパス。

### `GET /api/steam/proxy-image`

ローカルファイルアクセスのための画像リクエストのプロキシ。

**クエリ:** `image_path` — ローカル画像ファイルパス。

## Steam 連携

### `POST /api/steam/set-achievement-status/{name}`

Steam 実績のアンロック状態を設定します。

**パス:** `name` — 実績識別子。

### `GET /api/steam/list-achievements`

すべての Steam 実績とそのステータスを一覧表示します。

### `POST /api/steam/update-playtime`

トラッキングされたゲームプレイ時間を更新します。

**ボディ:**

```json
{ "seconds": 3600 }
```

## プロアクティブチャット

### `POST /api/proactive_chat`

キャラクターからのプロアクティブメッセージを生成します（二段階アーキテクチャ）。

**ボディ:**

```json
{
  "lanlan_name": "character_name",
  "enabled_modes": ["vision", "news", "video", "home", "window", "personal"],
  "screenshot_data": "optional base64",
  "language": "zh"
}
```

## 翻訳

### `POST /api/translate`

言語間のテキスト翻訳。

**ボディ:**

```json
{
  "text": "Hello",
  "target_lang": "zh",
  "source_lang": "en",
  "skip_google": false
}
```

## その他のエンドポイント

### `POST /api/personal_dynamics`

パーソナライズされたコンテンツデータを取得します。

**ボディ:**

```json
{ "limit": 10 }
```

### `GET /api/get_window_title`

現在アクティブなウィンドウタイトルを取得します（Windows のみ）。

# Live2D API

**プレフィックス:** `/api/live2d`

Live2D モデルを管理します — 一覧表示、設定、感情マッピング、パラメータ編集。

## モデル一覧

### `GET /api/live2d/models`

利用可能なすべての Live2D モデルを一覧表示します（Steam Workshop モデルを含む）。

**クエリ:** `simple`（オプション、boolean） — true の場合、完全な設定なしでモデル名のみを返します。

## モデル設定

### `GET /api/live2d/model_config/{model_name}`

モデルの完全な `model3.json` 設定を取得します。

### `POST /api/live2d/model_config/{model_name}`

モデル設定を更新します（モーション/エクスプレッション）。

## 感情マッピング

### `GET /api/live2d/emotion_mapping/{model_name}`

モデルの感情からアニメーションへのマッピングを取得します。

**レスポンス例:**

```json
{
  "happy": { "expression": "f01", "motion": "idle_01" },
  "sad": { "expression": "f03", "motion": "idle_02" }
}
```

### `POST /api/live2d/emotion_mapping/{model_name}`

感情マッピングを更新します。EmotionMapping と FileReferences の両方を同期します。

## パラメータ

### `GET /api/live2d/model_parameters/{model_name}`

`.cdi3.json` ファイルから利用可能なすべてのモデルパラメータを取得します（パラメータエディタ用）。

### `POST /api/live2d/save_model_parameters/{model_name}`

調整済みのモデルパラメータを `parameters.json` に保存します。

**ボディ:**

```json
{
  "parameters": { ... }
}
```

## ファイル管理

### `GET /api/live2d/model_files/{model_name}`

モデルに属するモーションファイル（`.motion3.json`）とエクスプレッションファイル（`.exp3.json`）を一覧表示します。

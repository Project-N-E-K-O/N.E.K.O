# Live2D API

**プレフィックス:** `/api/live2d`

Live2D モデルを管理します — 一覧表示、設定、感情マッピング、ファイルアップロード、パラメータ編集。

## モデル一覧

### `GET /api/live2d/models`

利用可能なすべての Live2D モデルを一覧表示します。

**クエリ:** `simple`（オプション、boolean） — true の場合、完全な設定なしでモデル名のみを返します。

### `GET /api/live2d/user_models`

ユーザーがインポートしたモデル（ビルトインや Workshop モデルとは異なる）を一覧表示します。

## モデル設定

### `GET /api/live2d/model_config/{model_name}`

モデルの完全な設定（位置、スケール、表情マッピング）を取得します。

### `POST /api/live2d/model_config/{model_name}`

モデル設定を保存します。

### `GET /api/live2d/model_config_by_id/{model_id}`

Steam Workshop アイテム ID で設定を取得します。

### `POST /api/live2d/model_config_by_id/{model_id}`

Workshop アイテム ID で設定を保存します。

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

感情マッピングを更新します。

## パラメータ

### `GET /api/live2d/model_parameters/{model_name}`

利用可能なすべてのモデルパラメータ（パラメータエディタ用）を取得します。

### `POST /api/live2d/save_model_parameters/{model_name}`

調整済みのモデルパラメータを保存します。

### `GET /api/live2d/load_model_parameters/{model_name}`

以前に保存したモデルパラメータを読み込みます。

## ファイル管理

### `GET /api/live2d/model_files/{model_name}`

モデルに属するすべてのファイルを一覧表示します。

### `GET /api/live2d/model_files_by_id/{model_id}`

Workshop アイテム ID でファイルを一覧表示します。

### `POST /api/live2d/upload_model`

新しい Live2D モデルをアップロードします（モデルアーカイブを含むマルチパートフォーム）。

### `POST /api/live2d/upload_file/{model_name}`

既存のモデルに追加ファイルをアップロードします。

### `DELETE /api/live2d/model/{model_name}`

モデルとそのすべてのファイルを削除します。

### `GET /api/live2d/open_model_directory/{model_name}`

システムのファイルエクスプローラーでモデルのディレクトリを開きます。

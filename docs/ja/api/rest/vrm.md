# VRM API

**プレフィックス:** `/api/model/vrm`

VRM（3D）モデルを管理します — 一覧表示、アップロード、アニメーション管理、感情マッピング。

## モデル

### `GET /api/model/vrm/models`

利用可能なすべての VRM モデルを一覧表示します。

### `GET /api/model/vrm/models/{model_name}`

特定の VRM モデルの詳細を取得します。

### `POST /api/model/vrm/upload`

新しい VRM モデルをアップロードします。

**ボディ:** `.vrm` ファイルを含む `multipart/form-data`。

::: info
最大ファイルサイズ: **200 MB**。ファイルは 1 MB チャンクでストリーミングされます。
:::

### `DELETE /api/model/vrm/delete/{model_name}`

VRM モデルを削除します。

::: warning
パストラバーサルは `safe_vrm_path()` バリデーションによって保護されています。
:::

## アニメーション

### `GET /api/model/vrm/animation/list`

利用可能なすべての VRM アニメーションを一覧表示します。

### `POST /api/model/vrm/animation/upload`

VRM アニメーションファイルをアップロードします。

**ボディ:** アニメーションファイルを含む `multipart/form-data`。

## 感情マッピング

### `GET /api/model/vrm/emotion_mapping`

VRM モデルの感情からアニメーションへのマッピングを取得します。

### `POST /api/model/vrm/emotion_mapping`

VRM の感情マッピングを更新します。

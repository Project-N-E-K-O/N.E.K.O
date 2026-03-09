# VRM API

**プレフィックス:** `/api/model/vrm`

VRM（3D）モデルを管理します — 一覧表示、アップロード、アニメーション管理、感情マッピング。

## モデル

### `GET /api/model/vrm/models`

利用可能なすべての VRM モデルを一覧表示します（プロジェクトディレクトリとユーザーディレクトリの両方から）。

### `POST /api/model/vrm/upload`

新しい VRM モデルをアップロードします。

**ボディ:** `.vrm` ファイルを含む `multipart/form-data`。

::: info
最大ファイルサイズ: **200 MB**。ファイルはパストラバーサル保護付きでチャンクストリーミングされます。
:::

## 設定

### `GET /api/model/vrm/config`

VRM モデルの統合パス設定を取得します（プロジェクトディレクトリ、ユーザーディレクトリ）。

## アニメーション

### `GET /api/model/vrm/animations`

利用可能なすべての VRM アニメーションファイル（`.vrma` と `.vrm`）を一覧表示します。

### `POST /api/model/vrm/upload_animation`

VRM アニメーションファイル（`.vrma`）をアップロードします。

**ボディ:** アニメーションファイルを含む `multipart/form-data`。

## 感情マッピング

### `GET /api/model/vrm/emotion_mapping/{model_name}`

VRM モデルの感情からエクスプレッションへのマッピングを取得します。

### `POST /api/model/vrm/emotion_mapping/{model_name}`

VRM の感情マッピングを更新します。

**ボディ:**

```json
{
  "happy": ["expressionName1"],
  "sad": ["expressionName2"]
}
```

## エクスプレッション

### `GET /api/model/vrm/expressions/{model_name}`

VRM モデルでサポートされているエクスプレッションの一覧を取得します（汎用リファレンスリスト）。

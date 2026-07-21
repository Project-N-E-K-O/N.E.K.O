# VRM API

**プレフィックス:** `/api/model/vrm`

VRM（3D）モデルを管理します — 一覧表示、アップロード、アニメーション管理、感情マッピング。

## モデル

### `GET /api/model/vrm/config`

user VRM、同梱 VRM、同梱 animation の runtime path 設定を返します。frontend 統合用の応答で、filesystem path は移植可能な public schema ではありません。

### `GET /api/model/vrm/models`

利用可能なすべての VRM モデルを一覧表示します。

### `POST /api/model/vrm/upload`

新しい VRM モデルをアップロードします。

**ボディ:** `.vrm` ファイルを含む `multipart/form-data`。

::: info
最大ファイルサイズ: **200 MB**。ファイルは 1 MB チャンクでストリーミングされます。
:::

### `DELETE /api/model/vrm/model/{model_name}`

ユーザーがインポートした VRM モデルを名前で削除します（同名の組み込みモデルが存在しない場合は、関連する感情マッピング設定も削除します）。組み込み/静的モデルは削除できません（404 を返します）。

::: warning
パストラバーサルは `safe_vrm_path()` バリデーションによって保護されています。
:::

### `DELETE /api/model/vrm/model`

ユーザーがインポートした VRM モデルを URL で削除します。JSON ボディ `{ "url": "/user_vrm/<file>.vrm" }` を送信します。削除できるのは `/user_vrm/` 直下のトップレベルの `.vrm` ファイルのみです。

## アニメーション

### `GET /api/model/vrm/animations`

利用可能なすべての VRM アニメーションを一覧表示します。

### `POST /api/model/vrm/upload_animation`

VRM アニメーションファイルをアップロードします。

**ボディ:** アニメーションファイルを含む `multipart/form-data`。

## 感情マッピング

### `GET /api/model/vrm/emotion_mapping/{model_name}`

特定の VRM モデルの感情からアニメーションへのマッピングを取得します。

### `POST /api/model/vrm/emotion_mapping/{model_name}`

特定の VRM モデルの感情マッピングを更新します。

### `GET /api/model/vrm/expressions/{model_name}`

一般的な参考 expression 一覧を返します。handler は指定 VRM file を解析しません。実際の expression は frontend が model load 後に検出するため、model 固有 capability probe として扱わないでください。

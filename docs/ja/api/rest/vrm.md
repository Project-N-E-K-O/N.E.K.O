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

ユーザーがインポートした VRM モデルを名前で削除します（同名の組み込みモデルが存在しない場合は、関連する感情マッピング設定と物理設定も削除します。両者は個別に処理され、一方の削除に失敗しても他方には影響しません）。組み込み/静的モデルは削除できません（404 を返します）。

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

## 物理設定

### `GET /api/model/vrm/physics_profile/{model_name}`

読み取り専用のエンドポイントで、`static/vrm/configs/{model_name}_physics.json` を読みます。ディレクトリは作成しません。ファイル形式、マッチング規則、既知の制限は [VRM モデル · モデル別物理設定](/ja/frontend/vrm#モデル別物理設定) を参照してください。

| 状況 | ステータス | レスポンス |
| --- | --- | --- |
| JSON オブジェクトを読めた | 200 | `{ "success": true, "config": {...}, "source": "file" }`。`NaN` / `Infinity` は `null` に置き換え済み |
| ファイルがない | 200 | `{ "success": true, "config": null, "source": "absent" }` |
| JSON が壊れている、オブジェクトではない、またはデコードできない | 200 | `{ "success": true, "config": null, "source": "corrupt" }` |
| モデル名が不正（アップロードと同じ検証。パストラバーサル検査を含む） | 400 | `{ "success": false, "error": "..." }` |
| その他のエラー | 500 | `{ "success": false, "error": "..." }` |

ファイルは `utf-8-sig` で読むため、Unity / C# が書き出す BOM は破損扱いになりません。

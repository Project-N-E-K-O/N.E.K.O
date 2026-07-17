# Live2D API

**プレフィックス：** `/api/live2d`

この 17 ルートは N.E.K.O. のファーストパーティー Live2D モデル管理とパラメーターエディター用です。ローカルのモデルファイルを読み書きするもので、リモートモデルホスティング API ではありません。

## ルート一覧

| メソッド | パス | 用途 |
|---|---|---|
| `GET` | `/api/live2d/models` | 組み込み、ユーザー、インストール済み Workshop モデルを一覧する |
| `GET` | `/api/live2d/user_models` | ユーザーがインポートしたモデルを一覧する |
| `GET` / `POST` | `/api/live2d/model_config/{model_name}` | Cubism `.model3.json` を読み、モーション／表情参照を更新する |
| `GET` / `POST` | `/api/live2d/model_config_by_id/{model_id}` | Steam Workshop アイテム ID で同じ操作を行う |
| `GET` / `POST` | `/api/live2d/emotion_mapping/{model_name}` | 感情からモーション／表情へのマッピングを読み書きする |
| `GET` | `/api/live2d/model_files/{model_name}` | `.motion3.json` と `.exp3.json` を一覧する |
| `GET` | `/api/live2d/model_files_by_id/{model_id}` | Workshop ID で一覧し、モデル名へフォールバックする |
| `GET` | `/api/live2d/model_parameters/{model_name}` | `.cdi3.json` からパラメーターメタデータを読む |
| `POST` | `/api/live2d/save_model_parameters/{model_name}` | エディター値を `parameters.json` に保存する |
| `GET` | `/api/live2d/load_model_parameters/{model_name}` | 保存済みエディター値を読み込む |
| `POST` | `/api/live2d/upload_model` | 複数ファイルのモデルパッケージをインポートする |
| `POST` | `/api/live2d/upload_file/{model_name}` | モーションまたは表情 JSON を 1 ファイル追加する |
| `GET` | `/api/live2d/open_model_directory/{model_name}` | OS ファイルマネージャーでモデルディレクトリを開く |
| `DELETE` | `/api/live2d/model/{model_name}` | ユーザーインポートモデルを削除する |

## 一覧

`GET /api/live2d/models?simple=false` は互換性のため完全なモデル配列を直接返します。`simple=true` では `{ "success": true, "models": ["..."] }` です。Steam がインストール済みと報告し、`.model3.json` がある Workshop モデルだけが追加されます。

`GET /api/live2d/user_models` は、N.E.K.O. が読めるユーザーモデルを `{ "success": true, "models": [...] }` で返します。

## 設定と感情マッピング

設定 `GET` は `{ "success": true, "config": {...} }` を返します。読み取れる `.model3.json` に `FileReferences.Motions` または `FileReferences.Expressions` がない場合、ハンドラーはコンテナを追加して書き戻しを試みます。

設定 `POST` は Cubism 設定形式の JSON を受け取りますが、永続化するのは次だけです：

```json
{
  "FileReferences": {
    "Motions": {},
    "Expressions": []
  }
}
```

その他の `.model3.json` フィールドは無視されます。モデル設定全体の置換 API ではありません。

`GET /api/live2d/emotion_mapping/{model_name}` は保存済み `EmotionMapping` を返し、なければ `FileReferences` から `{ "motions": {...}, "expressions": {...} }` を導出します。`POST` は後者の形式を受け取り、安全な相対パスへ正規化し、標準 Cubism 参照と互換用 `EmotionMapping` の両方を書き込み、予約グループ `常驻` のモーションを無視します。

## ファイルとパラメーター

- `model_files` と `model_files_by_id` は `motion_files` と `expression_files` を再帰的に返し、ID 版は `model_config_url` も返します。
- `model_parameters` は `.cdi3.json` のパラメーター／グループ情報を読みます。実行中の値ではありません。
- `save_model_parameters` は `{ "parameters": { ... } }` を必要とし、`parameters` はオブジェクトでなければなりません。
- `parameters.json` がない、またはオブジェクトでない場合、`load_model_parameters` は空オブジェクトを返します。

## インポートと変更

`POST /api/live2d/upload_model` は `multipart/form-data` で、相対パス付きファイル名を持つ 1 個以上の `files` を受け取ります。単一アーカイブでは**ありません**。`.model3.json` は正確に 1 個必要です。危険なパス、設定ファイル 0／複数、既存の有効な保存先は HTTP `400` です。インポート後、ランタイムのリップシンクが駆動できるよう、アップロード済みモーションの口／リップシンク曲線を消去します。

`POST /api/live2d/upload_file/{model_name}?file_type=motion` は `file` を 1 つ受け取ります。`file_type` は `motion` または `expression` で、拡張子は `.motion3.json` または `.exp3.json`、内容は UTF-8 JSON、上限 50 MB です。既存ファイルは上書きしません。

`DELETE /api/live2d/model/{model_name}` は書き込み可能なユーザーインポートディレクトリ内だけを削除します。組み込み／Workshop モデルや Windows 保護で読み取り専用になったユーザーディレクトリは HTTP `403` です。

`GET /api/live2d/open_model_directory/{model_name}` は Explorer、Finder、または `xdg-open` を起動するローカルデスクトップ副作用があり、ファーストパーティー設定 UI 用です。

## エラー

多くの変更エラーは `{ "success": false, "error": "..." }` と HTTP `400`、`403`、`404`、`500` を返します。一部の旧式読み取りヘルパーは HTTP `200` で同じ失敗形式を返すため、ステータスだけでなく `success` を確認してください。パス末尾に `/` はありません。

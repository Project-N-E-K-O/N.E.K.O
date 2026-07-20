# Live2D モデル

## ランタイム

Live2D はメインページの `#live2d-canvas` に描画されます。現在の実装は次のファイルに分割されています。

- `static/live2d/live2d-core.js`
- `static/live2d/live2d-model.js`
- `static/live2d/live2d-emotion.js`
- `static/live2d/live2d-interaction.js`
- `static/live2d/live2d-init.js`
- `static/live2d/live2d-ui-buttons.js`

`live2d-init.js` が `window.live2dManager` を作成し、共通の `window.LanLan1` 互換メソッドを公開します。モデルマネージャーのプレビューも同じレンダラーモジュールを使い、別の旧 Live2D ページ実装はありません。

## モデルと配信元

モデルは Cubism `.model3.json` と、それが参照する `.moc3`、テクスチャ、モーション、表情、任意の物理ファイルから検出されます。モデル内の相対ディレクトリ構造を維持してください。

`GET /api/live2d/models` は次を統合します。

- プロジェクトの静的モデルディレクトリにある同梱モデル
- `/user_live2d` から配信されるユーザーモデル（設定によっては書き込み可能な `/user_live2d_local` シャドウも使用）
- `/workshop/{item_id}/...` から配信されるインストール済み Steam Workshop モデル

API が返す URL を使い、絶対ファイルシステムパスから URL を作らないでください。

## 感情マッピング

エディターとランタイムは次の論理構造を使います。

```json
{
  "motions": { "happy": ["motions/happy.motion3.json"] },
  "expressions": { "happy": ["expressions/happy.exp3.json"] }
}
```

`EmotionMapping` があればサーバーはそれを優先し、なければ `FileReferences.Motions` と表情名のプレフィックスからグループを導出します。保存時は標準 Cubism の `FileReferences.Motions` と `FileReferences.Expressions` に書き込みます。モーションと表情のパスは相対パスで、モデルディレクトリ外へ移動できません。

`window.LanLan1.setEmotion(name)` は現在のレンダラーへ委譲します。Live2D では設定済みの表情とモーションを適用し、一方がない場合は安全にフォールバックします。特殊な `常驻` グループは表情専用です。

## 管理ページ

- `/model_manager` でモデルの選択、インポート、プレビュー、削除を行います。
- `/live2d_emotion_manager` で感情グループをモーションと表情へ割り当てます。
- `/live2d_parameter_editor` で保存済みレイアウト/パラメーター設定を編集します。

## API 概要

| メソッド | エンドポイント | 用途 |
| --- | --- | --- |
| `GET` | `/api/live2d/models` | ローカルと Workshop モデルを一覧。`?simple=true` は名前だけを返す |
| `GET`、`POST` | `/api/live2d/model_config/{model_name}` | Cubism 設定を読み取り、モーションと表情だけを更新 |
| `GET`、`POST` | `/api/live2d/emotion_mapping/{model_name}` | 感情グループを読み取りまたは保存 |
| `GET` | `/api/live2d/model_files/{model_name}` | 検証済みモデルリソースを一覧 |
| `GET` | `/api/live2d/model_parameters/{model_name}` | Cubism パラメーターのメタデータを確認 |
| `GET`、`POST` | `/api/live2d/load_model_parameters/{model_name}`、`/api/live2d/save_model_parameters/{model_name}` | パラメーター設定の読み込みまたは保存 |
| `POST` | `/api/live2d/upload_model` | 複数ファイルのモデルパッケージをインポート |
| `POST` | `/api/live2d/upload_file/{model_name}` | モーションまたは表情ファイルを追加。上限 50 MB |
| `DELETE` | `/api/live2d/model/{model_name}` | ユーザーモデルを削除 |

ID ベース版（`model_config_by_id` と `model_files_by_id`）は、公開アイテム ID を安定した識別子にする Workshop モデルをサポートします。

## ホスト境界

Live2D のアセットと初期化は `index.html` で動作し、そのテンプレートを読み込む Electron ペットウィンドウも含みます。独立した `/chat` と `/subtitle` は第二のアバターを描画しません。クロスウィンドウ命令は別の Live2D マネージャーを初期化せず、メインページへ転送してください。

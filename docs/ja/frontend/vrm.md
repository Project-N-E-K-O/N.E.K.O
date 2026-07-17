# VRM モデル

## ランタイムと形式

VRM レンダラーは Three.js と `@pixiv/three-vrm` を使います。モデルは `.vrm`、アニメーションは通常 `@pixiv/three-vrm-animation` で読み込む `.vrma` です。

現在の実装は `static/vrm/` の core、manager、init、animation、expression、interaction、cursor-follow、orientation、UI モジュールにあります。VRM キャラクターが有効な場合だけ、`vrm-init.js` が `window.vrmManager` を作成し `#vrm-canvas` を初期化します。

## モデルとアニメーション

`GET /api/model/vrm/models` は `static/vrm/` 直下の同梱ファイル、`/user_vrm` のユーザーファイル、`/workshop/{item_id}/...` のインストール済み Workshop ファイルを統合します。API は公開 URL を返し、絶対ファイルシステムパスを公開しません。

アニメーションは `static/vrm/animation/` と `/user_vrm/animation/` から列挙されます。アップロードはモデル `.vrm` とアニメーション `.vrma` を受け付け、1 ファイルの上限は 200 MB です。ユーザーモデル削除は設定済み VRM ディレクトリ直下の `.vrm` だけに制限されます。

## ライティング

`config/character_defaults.py` のバックエンド既定値は、レンダラースクリプトより先に `window.VRM_DEFAULT_LIGHTING` としてテンプレートへ注入されます。現在のキーは次のとおりです。

```json
{
  "ambient": 0.83,
  "main": 1.91,
  "fill": 0.0,
  "rim": 0.0,
  "top": 0.0,
  "bottom": 0.0,
  "exposure": 1.1,
  "toneMapping": 7,
  "outlineWidthScale": 1.0
}
```

キャラクター別ライティングはこれを上書きできます。バックエンド既定値、テンプレートコンテキスト、`vrm-core.js` の防御的フォールバックを一致させてください。

## 感情マッピング

VRM の感情は意味名を順序付きの表情名候補へ割り当てます。

```json
{
  "neutral": ["neutral"],
  "happy": ["happy", "joy", "fun", "smile"],
  "surprised": ["surprised", "surprise", "shock", "e", "o"]
}
```

サーバーはモデル別マップを `static/vrm/configs/` に保存します。`vrm-expression.js` は保存値を既定値へ上書きマージし、表情名を大文字小文字を無視した完全一致で検索します。管理ページは保存前に `/api/model/vrm/expressions/{model_name}` から実際の表情名を取得できます。

VRM が有効なとき、`window.LanLan1.setEmotion(name)` は `window.vrmManager.expression.setMood(name)` に委譲されます。non-neutral の mood は実行時遅延後に neutral へ戻ります。

## ランタイム保護

現在のレンダラーは長い停止後のフレーム delta をクランプし、読み込んだ spring-bone collider 半径を縮小し、ライティング設定から MToon アウトライン幅を調整します。これらは内部互換対策で、モデル形式の要件ではありません。再現のためにアップロード VRM を事前編集しないでください。

## API 概要

| メソッド | エンドポイント | 用途 |
| --- | --- | --- |
| `POST` | `/api/model/vrm/upload` | `.vrm` モデルを 1 ファイルアップロード |
| `POST` | `/api/model/vrm/upload_animation` | `.vrma` アニメーションを 1 ファイルアップロード |
| `GET` | `/api/model/vrm/models` | 同梱、ユーザー、Workshop モデルを一覧 |
| `GET` | `/api/model/vrm/animations` | 同梱とユーザーアニメーションを一覧 |
| `GET` | `/api/model/vrm/config` | 公開 VRM URL プレフィックスを返す |
| `GET`、`POST` | `/api/model/vrm/emotion_mapping/{model_name}` | 表情マップを読み取りまたは保存 |
| `GET` | `/api/model/vrm/expressions/{model_name}` | モデル内の表情名を確認 |
| `DELETE` | `/api/model/vrm/model` | 公開 URL でユーザーモデルを削除 |

## ホスト境界

VRM は Electron ペットウィンドウを含む `index.html` に描画されます。独立したチャットと字幕テンプレートは第二の VRM シーンを作らず、ネイティブウィンドウは共通のクロスウィンドウブリッジでメインページと連携します。

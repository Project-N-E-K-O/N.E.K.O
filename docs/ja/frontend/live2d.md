# Live2D 統合

## 概要

N.E.K.O. は Pixi.js 経由の Cubism SDK を使用して Live2D モデルをレンダリングします。モデルはメインのチャットインターフェースに表示され、会話で検出された感情に反応します。

## モデルソース

| ソース | 場所 |
|--------|----------|
| 組み込み | `static/` ディレクトリ |
| ユーザーインポート | `user_live2d/` ディレクトリ |
| Steam Workshop | `workshop/` ディレクトリ（自動マウント） |

## 感情マッピング

各 Live2D モデルは、感情ラベルから表情やモーションへのマッピングを定義できます：

```json
{
  "happy": { "expression": "f01", "motion": "idle_01" },
  "sad": { "expression": "f03", "motion": "idle_02" },
  "angry": { "expression": "f05", "motion": "idle_03" }
}
```

感情はバックエンド（`/api/analyze_emotion`）で検出され、WebSocket 経由でフロントエンドに送信されます。

## UI コンポーネント

| モジュール | 用途 |
|--------|---------|
| `live2d-ui-buttons.js` | コントロールボタン（モデル切り替え、設定） |
| `live2d-ui-drag.js` | モデル配置のためのドラッグとズーム |
| `live2d-ui-hud.js` | ヘッドアップディスプレイオーバーレイ |
| `live2d-ui-popup.js` | ポップアップダイアログとメニュー |

## モデル管理ページ

- `/model_manager` -- モデルの閲覧、アップロード、削除
- `/live2d_parameter_editor` -- モデルパラメータの微調整
- `/live2d_emotion_manager` -- 感情とアニメーションのマッピング設定

## API エンドポイント

完全な REST エンドポイントリファレンスは [Live2D API](/ja/api/rest/live2d) を参照してください。

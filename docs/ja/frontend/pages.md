# ページとテンプレート

## テンプレートレンダリング

ページはサーバーサイドで Jinja2 を使用してレンダリングされます。テンプレートは `templates/` ディレクトリに配置されています。

## ページ一覧

| パス | テンプレート | 説明 |
|------|----------|-------------|
| `/` | `index.html` | Live2D/VRM レンダリング付きメインチャットインターフェース |
| `/chara_manager` | `chara_manager.html` | キャラクターの性格と設定エディター |
| `/api_key` | `api_key_settings.html` | API キー設定パネル |
| `/model_manager` | `model_manager.html` | モデルの閲覧と管理 |
| `/live2d_parameter_editor` | `live2d_parameter_editor.html` | Live2D モデルパラメータの微調整 |
| `/live2d_emotion_manager` | `live2d_emotion_manager.html` | Live2D 感情マッピング |
| `/vrm_emotion_manager` | `vrm_emotion_manager.html` | VRM 感情マッピング |
| `/voice_clone` | `voice_clone.html` | ボイスクローニングインターフェース |
| `/memory_browser` | `memory_browser.html` | メモリの閲覧と編集 |
| `/steam_workshop_manager` | `steam_workshop_manager.html` | Steam Workshop 管理 |

## ダークモード

ダークモードは `static/theme-manager.js` によって管理されます：

- UI ボタンで切り替え
- `localStorage` に保存
- CSS 変数は `static/css/dark-mode.css` で定義
- システム設定を尊重（`prefers-color-scheme`）

## 静的ファイル配信

| マウントポイント | ディレクトリ | コンテンツ |
|-------------|-----------|---------|
| `/static` | `static/` | JS、CSS、画像、ロケール |
| `/user_live2d` | ユーザードキュメント | ユーザーがインポートした Live2D モデル |
| `/user_vrm` | ユーザードキュメント | ユーザーがインポートした VRM モデル |
| `/workshop` | Steam Workshop | Workshop でサブスクライブしたモデル |

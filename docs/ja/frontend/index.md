# フロントエンド概要

N.E.K.O. のフロントエンドは、従来のサーバーレンダリングページ、React チャットウィンドウコンポーネント、Vue プラグイン管理ダッシュボードの3層で構成されています。

## アーキテクチャ

| レイヤー | 技術 | 場所 |
|---------|------|------|
| メインUIページ | Vanilla JS + Jinja2 テンプレート | `static/` + `templates/` |
| チャットウィンドウ | React 18 + TypeScript | `frontend/react-neko-chat/` |
| プラグインマネージャー | Vue 3 + Element Plus | `frontend/plugin-manager/` |
| Live2D レンダリング | Pixi.js + Live2D Cubism SDK | `static/` |
| VRM レンダリング | Three.js + @pixiv/three-vrm | `static/` |

## 従来のフロントエンド（static/ + templates/）

メインUIは **vanilla JavaScript** と Jinja2 HTML テンプレートで構築されています。

```
static/
├── app.js                    # メインアプリケーションロジック
├── theme-manager.js          # ダーク/ライトモード切り替え
├── css/                      # スタイルシート
├── js/                       # 機能別 JS モジュール
├── locales/                  # i18n JSON ファイル（en, zh-CN, zh-TW, ja, ko）
├── live2d-ui-*.js            # Live2D UI コンポーネント
├── vrm-ui-*.js               # VRM UI コンポーネント
└── react/neko-chat/          # React チャットウィンドウのビルド出力
```

## チャットウィンドウ（React）

チャットウィンドウは IIFE ライブラリとしてビルドされ、メインページに埋め込まれます。

- **ソース**: `frontend/react-neko-chat/`
- **ビルド出力**: `static/react/neko-chat/neko-chat-window.iife.js`
- **グローバル変数**: `window.NekoChatWindow`
- **開発サーバー**: `npm run dev`（ポート 5174）

グルーレイヤー `static/app-react-chat-window.js` が React コンポーネントを DOM に読み込んでマウントします。

## プラグインマネージャー（Vue）

プラグインの管理、ログの表示、メトリクスの監視を行うスタンドアロンのダッシュボードです。

- **ソース**: `frontend/plugin-manager/`
- **ビルド出力**: `frontend/plugin-manager/dist/`
- **配信パス**: プラグインサーバー（ポート 48916）の `/ui/`
- **開発サーバー**: `npm run dev`（ポート 5173、プラグインサーバーへの API プロキシ）

## 主要な概念

- **ページ** はサーバーサイドでレンダリングされる HTML テンプレートで、JavaScript モジュールを読み込みます
- **WebSocket** はリアルタイムの音声/テキストチャットに使用されます（[WebSocket プロトコル](/ja/api/websocket/protocol) を参照）
- **REST API** はすべての CRUD 操作に使用されます（[API リファレンス](/ja/api/) を参照）
- **テーママネージャー** は CSS 変数のオーバーライドによりダーク/ライトモードを管理します
- **i18n** はクライアントサイドで適切なロケール JSON ファイルを読み込むことで処理されます

# フロントエンド概要

N.E.K.O. のフロントエンドは **vanilla JavaScript**（フレームワーク不使用）と Jinja2 HTML テンプレートで構築されています。Live2D モデルは Pixi.js、VRM モデルは Three.js を使用してレンダリングされます。

## 技術スタック

| コンポーネント | 技術 |
|-----------|-----------|
| テンプレート | Jinja2（サーバーサイドレンダリング） |
| JavaScript | Vanilla ES6+ |
| Live2D レンダリング | Pixi.js + Live2D Cubism SDK |
| VRM レンダリング | Three.js + @pixiv/three-vrm |
| スタイリング | カスタム CSS + ダークモード対応 |
| i18n | JSON ロケールファイル + JS ランタイム |

## ファイル構成

```
static/
├── app.js                    # メインアプリケーションロジック
├── theme-manager.js          # ダーク/ライトモード切り替え
├── css/
│   ├── index.css             # メインスタイルシート
│   ├── dark-mode.css         # ダークモードのオーバーライド
│   ├── chara_manager.css     # キャラクターマネージャーのスタイル
│   └── ...
├── js/
│   ├── api_key_settings.js   # API キー設定ページ
│   ├── agent_ui_v2.js        # エージェントインターフェース
│   ├── steam_workshop_manager.js
│   └── ...
├── locales/
│   ├── en.json               # 英語
│   ├── zh-CN.json            # 簡体字中国語
│   ├── zh-TW.json            # 繁体字中国語
│   ├── ja.json               # 日本語
│   └── ko.json               # 韓国語
├── live2d-ui-*.js            # Live2D UI コンポーネント
└── vrm-ui-*.js               # VRM UI コンポーネント
```

## 主要な概念

- **ページ** はサーバーサイドでレンダリングされる HTML テンプレートで、JavaScript モジュールを読み込みます
- **WebSocket** はリアルタイムの音声/テキストチャットに使用されます（[WebSocket プロトコル](/ja/api/websocket/protocol) を参照）
- **REST API** はすべての CRUD 操作に使用されます（[API リファレンス](/ja/api/) を参照）
- **テーママネージャー** は CSS 変数のオーバーライドによりダーク/ライトモードを管理します
- **i18n** はクライアントサイドで適切なロケール JSON ファイルを読み込むことで処理されます

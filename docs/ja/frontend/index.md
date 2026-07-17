# フロントエンド概要

N.E.K.O. のメイン UI は FastAPI メインサーバーから配信されます。リポジトリには、ビルドと実行時の境界が異なる 3 つのフロントエンドコードベースがあります。

## コードベース

| サーフェス | 技術 | ソース | 実行時成果物 |
| --- | --- | --- | --- |
| メイン UI と補助ページ | Jinja2、Vanilla JavaScript、CSS | `templates/`、`static/app/`、`static/live2d/`、`static/vrm/`、`static/mmd/` | メインサーバーが描画し、通常はポート `48911` を使用 |
| チャット UI | React 18、TypeScript | `frontend/react-neko-chat/` | `static/react/neko-chat/neko-chat-window.iife.js` と `.css` |
| プラグインマネージャー | Vue 3、TypeScript | `frontend/plugin-manager/` | `frontend/plugin-manager/dist/`、プラグインサーバーから配信 |

アバターレンダラーはメイン UI の一部です。Live2D は Pixi/Cubism、VRM と MMD は Three.js、PNGTuber は `static/pngtuber-core.js` を使用します。Electron デスクトップペットはホストモードであり、別のアバター形式ではありません。

## 唯一のチャット実装

`frontend/react-neko-chat/` がチャット UI の唯一の実装です。IIFE は `window.NekoChatWindow` を公開し、`static/app/app-react-chat-window/` のスクリプトが `#react-chat-window-root` にマウントします。

`templates/index.html` と `templates/chat.html` はどちらもこのマウント先を持ちます。前者はメインページ内の折りたたみ可能なフローティング UI、後者は compact または full の独立チャット UI を提供します。

古い `#chat-container` DOM は旧スクリプト向けの互換シェルとしてのみ残っています。両テンプレートで非表示にされ、`static/app/app-chat-adapter.js` が従来の `appendMessage()` 呼び出しを `window.reactChatWindowHost` への呼び出しに置き換えます。旧コンテナに新しい UI やロジックを追加しないでください。

## Web と Electron ホスト

ブラウザーでは `/` が単一のメインページです。開発とテストでは `/chat`、`/chat_full`、`/subtitle` も直接開けます。

Electron ディストリビューションは別のホストアプリです。複数のルートを独立したウィンドウに読み込み、ペットはメインページテンプレート、チャットは `/chat` または `/chat_full`、字幕は `/subtitle` を使います。レンダラーは `window.nekoChatWindow` や `window.nekoSubtitle` など preload のグローバルを検出し、ネイティブウィンドウ生成と IPC はホストが所有します。

クロスウィンドウの Web フォールバックは `static/app/app-interpage/` にあり、`neko_page_channel` `BroadcastChannel` と同一オリジンの `postMessage` を使います。ルート、アセット URL、初期化順序、ウィンドウ通信を変更するときは、ブラウザーと Electron の両方を確認してください。

## 読み込みとアセットの規則

- サーバー描画ページでは `/static/...` のルート相対 URL を使い、現在のルートからアセットパスを組み立てません。
- ユーザーモデルと Workshop モデルは専用マウントから配信されます。ファイルシステムパスをブラウザー URL に変換しないでください。
- `static/` のクラシックスクリプトは規定のグローバルと DOM イベントで通信するため、テンプレートの読み込み順序も実行時契約です。
- React チャットの変更は `frontend/react-neko-chat/` で行い、生成ファイルを編集せず IIFE を再ビルドします。
- プラグインマネージャーの変更は `frontend/plugin-manager/` で行います。そのビルドとローカライズはメインページから独立しています。

現在のエントリーポイントは[ページとテンプレート](/ja/frontend/pages)、[国際化](/ja/frontend/i18n)、各レンダラーのページを参照してください。

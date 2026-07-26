# ページとテンプレート

## レンダリングモデル

`main_routers/pages_router.py` は `templates/` の Jinja2 テンプレートを描画します。共通コンテキストにはアセットバージョンが含まれ、VRM を初期化できるページにはバックエンド管理のライティング既定値も渡されます。

アセット URL はルート相対（`/static/...`）にしてください。Electron ウィンドウやネストしたルートでは URL の深さが異なるため、相対アセットパスは別の場所に解決される可能性があります。

## 公開ページルート

| ルート | テンプレート | 用途 |
| --- | --- | --- |
| `/`、`/{lanlan_name}` | `index.html` | メイン UI とアバターレンダラー |
| `/model_manager`、旧 `/l2d` | `model_manager.html` | Live2D、VRM、MMD、PNGTuber のモデル管理 |
| `/live2d_parameter_editor` | `live2d_parameter_editor.html` | Live2D パラメーター編集 |
| `/live2d_emotion_manager` | `live2d_emotion_manager.html` | Live2D モーション/表情マッピング |
| `/vrm_emotion_manager` | `vrm_emotion_manager.html` | VRM 表情マッピング |
| `/mmd_emotion_manager` | `mmd_emotion_manager.html` | MMD モーフマッピング |
| `/character_card_manager`、旧 `/chara_manager` | `character_card_manager.html` | キャラクターカードと設定 |
| `/api_key` | `api_key_settings.html` | Provider と API キー設定 |
| `/voice_clone` | `voice_clone.html` | ボイスクローンフロー |
| `/cloudsave_manager` | `cloudsave_manager.html` | クラウドセーブ管理 |
| `/memory_browser` | `memory_browser.html` | 最近の会話メモリの確認と処理設定 |
| `/cookies_login` | `cookies_login.html` | Cookie ログインフロー |
| `/chat` | `chat.html` | compact の独立 React チャット UI |
| `/chat_full` | `chat.html` | full の独立 React チャット UI |
| `/web_chat_compact` | `index.html` | compact チャットモードを強制したメインページ |
| `/subtitle` | `subtitle.html` | 独立字幕ウィンドウ |
| `/agenthud` | `agenthud.html` | Agent タスク HUD |
| `/card_maker` | `card_maker.html` | キャラクターカード作成 |
| `/jukebox`、`/jukebox/manager` | `jukebox.html`、`jukebox_manager.html` | ジュークボックスと管理画面 |
| `/toast` | `toast.html` | 独立 toast UI |
| `/soccer_demo`、`/badminton_demo` | 対応する demo テンプレート | ミニゲーム開発ページ |

`/chara_manager` は `/character_card_manager` にリダイレクトします。`/l2d` は互換ルートにすぎず、別の Live2D 実装ではありません。

メモリブラウザーが編集するのは、メインサーバーが公開する最近の会話ファイルだけです。メモリシステムが管理する facts、reflections、persona、archive shard、検索インデックスは直接編集しません。

## チャットと字幕ウィンドウ

`index.html` と `chat.html` は同じ React チャットバンドルを `#react-chat-window-root` にマウントします。残りの非表示 DOM は共通の音声、セッション、スクリーンショット用スクリプトの互換性のためであり、第二のチャット UI ではありません。

Electron は preload 提供 API でネイティブウィンドウを制御します。`chat.html` は `window.nekoChatWindow` を確認し、`subtitle-window.js` は存在する場合に `window.nekoSubtitle` を使い、なければ Web ページとして動作します。ページ間状態には `BroadcastChannel('neko_page_channel')` と同一オリジンの `postMessage` フォールバックを使います。Electron のメインプロセスと preload 実装はこのリポジトリの外にあります。

## テーマ

`static/theme-manager.js` は表示のちらつきを避けるため、ほとんどのページ内容より先にテーマを初期化します。テーマスタイルは `static/css/dark-mode.css` にあり、ダークモード対応ページは両方を読み込み、共通の data 属性/CSS 変数を使います。

## 静的マウント

| URL プレフィックス | 内容 |
| --- | --- |
| `/static` | バージョン付き JS、CSS、画像、同梱ライブラリ、locale JSON |
| `/user_live2d` | 現在のユーザー Live2D ディレクトリ |
| `/user_live2d_local` | Live2D の配信元が異なる場合の書き込み可能なローカルシャドウ |
| `/user_vrm`、`/user_vrm/animation` | ユーザー VRM モデルと VRMA アニメーション |
| `/user_mmd`、`/user_mmd/animation` | ユーザー MMD モデルと VMD アニメーション |
| `/user_pngtuber` | 正規化された PNGTuber パッケージ |
| `/user_mods` | 設定されたローカル mod ディレクトリ |
| `/workshop` | Steam Workshop コンテンツ。利用可能な場合に起動時マウント |

マウントはバックエンドディレクトリの存在に依存します。API 応答はすでに公開 URL を返すため、ローカルパスから再構築しないでください。

# クイックスタート

このページでは、[開発環境セットアップ](./dev-setup)の完了後に N.E.K.O. を初めて実行する手順を説明します。

## 1. サーバーの起動

```bash
# 別々のターミナルで：
uv run python memory_server.py
uv run python main_server.py
```

## 2. APIプロバイダーの設定

`http://localhost:48911/api_key` にアクセスし、少なくとも**Core API**プロバイダーを設定します。

APIキーなしでクイックテストを行う場合は、Core APIプロバイダーとして **Free** を選択してください。

## 3. デフォルトキャラクターとの対話

ブラウザで `http://localhost:48911` を開きます。デフォルトキャラクター（「小天」）がLive2Dモデルとともに読み込まれます。

**テキストモード：** チャット入力欄にメッセージを入力し、Enterキーを押します。

**音声モード：** マイクボタンをクリックして音声セッションを開始します。自然に話してください — システムはサーバーサイドVAD（Voice Activity Detection）を使用して、発話の終了を検出します。

## 4. キャラクターのカスタマイズ

`http://localhost:48911/chara_manager` にアクセスして、以下の操作ができます：

- キャラクターの名前、性別、年齢、性格特性の変更
- カスタムLive2DまたはVRMモデルの設定
- カスタムボイスのクローン（約15秒のクリーンな音声サンプルをアップロード）
- 動作を完全に制御するためのシステムプロンプトの編集

## 5. Web UIページの探索

| URL | 用途 |
|-----|------|
| `/` | メインチャットインターフェース |
| `/chara_manager` | キャラクター設定 |
| `/api_key` | APIキーの設定 |
| `/model_manager` | Live2D/VRMモデル管理 |
| `/live2d_emotion_manager` | 感情からアニメーションへのマッピング |
| `/vrm_emotion_manager` | VRM感情マッピング |
| `/voice_clone` | 音声クローン |
| `/memory_browser` | メモリの閲覧と編集 |
| `/steam_workshop_manager` | Steam Workshopアイテム |

## 次のステップ

- [プロジェクト構成](./project-structure) — コードベースのレイアウトを理解する
- [アーキテクチャ概要](/ja/architecture/) — 3つのサーバーがどのように連携するか
- [APIリファレンス](/api/) — すべてのRESTおよびWebSocketエンドポイント

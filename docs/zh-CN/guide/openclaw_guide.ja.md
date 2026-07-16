
# N.E.K.O. を QwenPaw に接続する

N.E.K.O. は既存設定との互換性のため、QwenPaw 連携を引き続き **OpenClaw** と呼びます。このガイドの OpenClaw スイッチは、別プロセスで動作する QwenPaw サービスへ接続します。

## 1. 配布元を確認してインストール

現在の手順は [QwenPaw 公式リポジトリ](https://github.com/agentscope-ai/QwenPaw)で確認してください。以下のコマンドはリモートのインストールスクリプトをダウンロードして直接実行します。セキュリティポリシーで必要な場合は、先にスクリプトを確認してください。制限されたネットワークや管理対象端末では失敗することがあります。

macOS / Linux：

```bash
curl -fsSL https://qwenpaw.agentscope.io/install.sh | bash
```

Windows PowerShell：

```powershell
irm https://qwenpaw.agentscope.io/install.ps1 | iex
```

インストーラーは `uv`、隔離環境、QwenPaw と依存関係を準備します。完了後は新しい terminal を開いてください。

## 2. 初期化

```bash
qwenpaw init --defaults
```

承認前に QwenPaw のセキュリティ警告を読んでください。一つのローカル instance は、実行 account が利用できる file、command、credential にアクセスできます。信頼できないユーザー間で共有しないでください。

![QwenPaw の初期化時セキュリティ通知](assets/openclaw_guide/image1.png)

## 3. 起動と確認

```bash
qwenpaw app
```

既定の console は `http://127.0.0.1:8088/` です。terminal を動かしたまま、browser で開きます。表示できなければ、N.E.K.O. を有効にする前に QwenPaw の起動エラーを解消してください。

認証とネットワーク境界を理解して設定するまでは、localhost の外へ公開しないでください。

## 4. QwenPaw でモデルを設定

QwenPaw console の model page を開き、provider と必要な credential を設定して保存します。その後 chat page で設定済み model を選びます。利用可能な provider／model 名はインストール中の QwenPaw version に依存するため、コピーされた一覧ではなく現在の UI を確認してください。

![QwenPaw のモデル設定](assets/openclaw_guide/image2.png)

## 5. 任意：executor persona

同梱の[置換用 archive](/downloads/qwenpaw-executor-profile.zip)には、executor 向けの `SOUL.md`、`AGENTS.md`、`PROFILE.md` が含まれます。接続に必須ではなく、QwenPaw の挙動を変更します。

置換前に：

1. QwenPaw を停止し、`.qwenpaw/workspaces/default` を backup します。
2. archive の内容を確認し、現在の workspace と比較します。
3. 置換する意図のある file だけをコピーします。

設定 directory は通常、Windows では `%USERPROFILE%\.qwenpaw`、macOS/Linux では `~/.qwenpaw` です。`BOOTSTRAP.md` の削除は、この任意 executor-profile 手順だけの一部であり、N.E.K.O. 接続には不要です。変更後に `qwenpaw app` を再起動します。

## 6. N.E.K.O. で有効化

1. QwenPaw を起動したままにします。
2. N.E.K.O. の paw／Agent panel を開きます。
3. Agent master switch を有効にします。
4. **OpenClaw** child switch を有効にします。
5. availability check を待ちます。

N.E.K.O. の既定接続先は `http://127.0.0.1:8088` です。QwenPaw が別 address を使う場合、N.E.K.O. の core 設定で `openclawUrl`（adapter は `qwenpawUrl` も受理）を更新してから再試行します。

現在の adapter は QwenPaw v2 console API と旧 agent-compatible API の両方を認識します。Availability check は version に応じて `/api/version` または `/api/agent/health` を確認し、一致する console／agent endpoint を使います。既定 console 構成では別の channel file は不要です。

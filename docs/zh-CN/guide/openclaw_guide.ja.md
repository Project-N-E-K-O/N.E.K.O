# NEKO から QwenPaw に接続する方法

## QwenPaw インストールガイド

### ステップ 1：インストール

Python を手動で設定する必要はありません。1 行のコマンドで `uv` の導入、仮想環境の作成、QwenPaw 本体と依存関係のインストールまで自動で行われます。なお、ネットワーク環境や企業の権限制限によっては利用できない場合があります。

macOS / Linux:

```bash
curl -fsSL https://qwenpaw.agentscope.io/install.sh | bash
```

Windows（PowerShell）:

```powershell
irm https://qwenpaw.agentscope.io/install.ps1 | iex
```

### ステップ 2：初期化

インストール後、新しいターミナルを開いて次を実行します。

```bash
qwenpaw init --defaults
```

初期化時には安全警告が表示されます。QwenPaw はローカル環境で動作し、同じインスタンスを複数人で共有すると、ファイル、コマンド、シークレットへのアクセス権も共有されると説明します。内容を確認し、`yes` を入力して続行してください。

![QwenPaw 初期化時のセキュリティ警告](assets/openclaw_guide/image1.png)

### ステップ 3：起動

```bash
qwenpaw app
```

正常に起動すると、通常はターミナルの最後に次が表示されます。

```text
INFO:     Uvicorn running on http://127.0.0.1:8088 (Press CTRL+C to quit)
```

起動後、`http://127.0.0.1:8088` にアクセスすると QwenPaw コンソールを開けます。

### ステップ 4：人格ファイルの置き換え（任意）

初期化後、QwenPaw は自動的に設定ディレクトリを作成します。

- Windows の既定パス: `C:\Users\ユーザー名\.qwenpaw`
- macOS の既定パス: `~/.qwenpaw`

`.qwenpaw` は隠しフォルダなので、必要に応じて表示してください。

- Windows: エクスプローラーで隠し項目を表示
- macOS: Finder で `Command + Shift + .`

QwenPaw を N.E.K.O 用の純粋なバックエンド実行役として使いたい場合は、次の置き換えファイルをダウンロードします。

- [置き換えファイル.zip](assets/openclaw_guide/替换文件.zip)

圧縮ファイル内の `SOUL.md`、`AGENTS.md`、`PROFILE.md` を `.qwenpaw/workspaces/default` にコピーして上書きし、そのディレクトリの `BOOTSTRAP.md` は削除してください。

その後、`CTRL+C` で QwenPaw を停止し、次で再起動します。

```bash
qwenpaw app
```

## 基本設定：モデル設定

QwenPaw コンソールを開き、「モデル」ページに移動して使用したいプロバイダを選びます。初心者には `DashScope` が分かりやすいですが、API Key に応じて別のプロバイダでも構いません。

設定を開き、API Key を入力して保存します。

![QwenPaw のモデル設定画面](assets/openclaw_guide/image2.png)

保存後、チャット画面に戻ると設定したモデルを選択できます。

## N.E.K.O で OpenClaw を有効化する

N.E.K.O の内部名は引き続き `openclaw` のままなので、UI に表示される `OpenClaw` トグルは QwenPaw を意味します。

次の順番で操作してください。

1. N.E.K.O の Agent パネルを開く
2. まず `Agent` のメインスイッチを ON にする
3. `openclawUrl` が `http://127.0.0.1:8088` を指していることを確認する
4. 次に `OpenClaw` のサブスイッチを ON にする
5. 利用可否チェックが通るのを待つ

N.E.K.O はまず QwenPaw の互換エンドポイントを試し、必要に応じて自動で `process` エンドポイントへフォールバックします。メインの接続経路ではカスタムチャンネル設定は不要です。

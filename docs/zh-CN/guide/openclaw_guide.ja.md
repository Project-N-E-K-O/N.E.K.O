# NEKO から QwenPaw に接続する方法

## QwenPaw インストールガイド

### ステップ 1：インストール

Python を手動で設定する必要はありません。1 行のコマンドで自動的にインストールが完了します。スクリプトは `uv`（Python パッケージマネージャー）をダウンロードし、仮想環境を作成し、QwenPaw 本体と依存関係をインストールします。Node.js とフロントエンド資産も含まれます。なお、一部のネットワーク環境や企業の権限制限下では利用できない場合があります。

macOS / Linux:

```bash
curl -fsSL https://qwenpaw.agentscope.io/install.sh | bash
```

Windows（PowerShell）:

```powershell
irm https://qwenpaw.agentscope.io/install.ps1 | iex
```

### ステップ 2：初期化

インストールが完了したら、新しいターミナルを開いて次を実行してください。

```bash
qwenpaw init --defaults
```

ここには親切な安全警告があります。QwenPaw は次のように明示します。

> これはローカル環境で動作する個人アシスタントです。さまざまなチャンネルへの接続、コマンド実行、API 呼び出しが可能です。同じ QwenPaw インスタンスを複数人で共有すると、ファイル、コマンド、シークレットを含む同じ権限を共有することになります。

![Neko チャンネル有効化手順画像 1](assets/openclaw_guide/image1.png)

続行するには、内容を理解したうえで `yes` を選択する必要があります。

### ステップ 3：起動

```bash
qwenpaw app
```

起動に成功すると、ターミナルの最後の行に次が表示されます。

```text
INFO:     Uvicorn running on http://127.0.0.1:8088 (Press CTRL+C to quit)
```

サービス起動後、`http://127.0.0.1:8088` にアクセスすると QwenPaw のコンソール画面が開きます。

## NEKO チャンネル設定：NEKO を QwenPaw に接続する

初期化が完了すると、QwenPaw は自動的に設定ディレクトリを作成します。Windows の既定パスは `C:\Users\あなたのユーザー名\.qwenpaw`、macOS の既定パスは `~/.qwenpaw` です。すべての内蔵スキルも自動的に有効になります。

そのパスを見つけてください。`.qwenpaw` は隠しフォルダなので、次の操作が必要です。

- Windows ユーザーは、タスクバーからエクスプローラーを開き、`表示 > 表示` を選んで「隠しファイル」を表示してください。
- macOS ユーザーは Finder を開き、ホームフォルダに移動してから `Command + Shift + .` を同時に押してください。

用意してあるチャンネル設定ファイル `custom_channels` を `.qwenpaw` フォルダにコピーします。

[キャラクターフォルダ内のファイル](assets/openclaw_guide/%E6%9B%BF%E6%8D%A2%E5%86%85%E5%AE%B9.zip)を `.qwenpaw/workspaces/default` にコピーし、`BOOTSTRAP.md` を削除します。

その後、ターミナルで `CTRL+C` を押して qwenpaw を終了し、再度 `qwenpaw app` を実行して再起動します。

次に、画像の手順に従って Neko チャンネルを有効化してください。

![Neko チャンネル有効化手順画像 2](assets/openclaw_guide/image2.png)

## 基本設定：モデル設定

モデルをクリックして DashScope を選択します。API Key に応じて別のモデルを選ぶこともできます。設定を開き、Alibaba Cloud Bailian API Key を入力して保存してください。

![Neko チャンネル有効化手順画像 3](assets/openclaw_guide/image3.png)

保存後、チャット画面に戻ると設定済みのモデルを選択できるようになります。

N.E.K.O に戻れば openclaw を使えるようになります。

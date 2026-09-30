# N.E.K.O Plugin Market にプラグインを公開する

このチュートリアルは[クイックスタート](/ja/plugins/quick-start)の続きです。N.E.K.O のソース版ですでに動作しているプラグインを GitHub にアップロードし、初回審査を申請し、最初のインストール可能なバージョンを公開して、その後のバージョンをリリースするところまで説明します。

例には、クイックスタートで作成した `hello_world` を使います。ソースは引き続き次の場所に置きます：

```text
N.E.K.O/plugin/plugins/hello_world/
```

このディレクトリはプラグイン自身の Git リポジトリであり、開発中に N.E.K.O が実行するソースでもあります。プラグインのコピー、シンボリックリンクの作成、開発環境へのインストールパッケージの import は必要ありません。

全体の流れは次のとおりです：

```text
ソースを開発
  → GitHub に push
  → Market に初回審査を申請
  → 審査に合格
  → GitHub Release を公開
  → Market にインストール可能なバージョンが表示される
  → ソースを更新して次のバージョンを公開
```

::: info 始める前に
手順に `cd` と明記されている場合を除き、以下の `uv run neko-plugin ...` コマンドは N.E.K.O ソースのルートディレクトリで実行してください。
:::

## 1. 公開できる状態か確認する

プラグインのディレクトリに、少なくとも次のファイルがあることを確認します：

```text
plugin/plugins/hello_world/
├── .git/
├── .github/workflows/
│   ├── verify.yml
│   └── release.yml
├── plugin.toml
├── config.example.toml
├── __init__.py
└── tests/
```

`.git/` があるということは、このプラグインディレクトリ自体が Git リポジトリだということです。クイックスタートの `neko-plugin init` がすでに作成しています。プラグインは N.E.K.O のソースディレクトリ内にありますが、プラグインのコミット、リモートリポジトリ、バージョンタグは、この内側のリポジトリに属します。

チェックを実行します：

```bash
uv run neko-plugin check hello_world
```

プラグインがサードパーティー製の Python ライブラリを使う場合は、先にプラグイン自身の `vendor/` を更新します：

```bash
uv run --with pip neko-plugin sync hello_world --clean
uv run neko-plugin check hello_world
```

error または `[FAIL]` があれば、先にすべて修正してください。サードパーティーライブラリを使わない場合は `sync` を実行する必要はありません。

最後に `plugin/plugins/hello_world/plugin.toml` を開き、プラグイン ID と最初のバージョン番号を確認します：

```toml
[plugin]
id = "hello_world"
version = "0.1.0"
```

公開後はプラグイン ID を変更しないでください。一度公開したバージョン番号も再利用できません。

## 2. GitHub リポジトリを作成してソースを push する

GitHub で、次の名前の空の公開リポジトリを作成します：

```text
n.e.k.o_plugin_hello_world
```

命名規則は `n.e.k.o_plugin_<プラグイン ID>` です。プラグインディレクトリにはすでに必要なファイルがあるため、リポジトリ作成時に GitHub で README、`.gitignore`、License を追加しないでください。

次にプラグインディレクトリに移動し、ソースを commit して push します：

```bash
cd plugin/plugins/hello_world
git add .
git commit -m "feat: first release"
git remote add origin https://github.com/your-name/n.e.k.o_plugin_hello_world.git
git push -u origin main
cd ../../..
```

`origin` がすでに存在する場合は、`git remote add` をもう一度実行しないでください。プラグインディレクトリ内で次のコマンドを実行し、正しい GitHub リポジトリを参照しているか確認します：

```bash
git remote -v
```

push が終わったら、GitHub リポジトリの **Actions** ページを開き、**Verify N.E.K.O Plugin** が成功するまで待ちます。このワークフローはコード、依存関係、プラグイン設定、テスト、ビルド結果を確認します。

::: warning commit するのはプラグインのリポジトリです
Git コマンドは `plugin/plugins/hello_world/` 内で実行してください。N.E.K.O リポジトリ全体をプラグインのリポジトリとして push しないでください。
:::

## 3. 初回審査を申請する

[N.E.K.O Plugin Market の投稿ページ](https://market.project-neko.cn/#/upload)を開いてログインし、次の順に操作します：

1. `https://github.com/your-name/n.e.k.o_plugin_hello_world` のような GitHub リポジトリ URL を入力します。
2. リポジトリ情報を読み込みます。
3. プラグイン名を確認し、カテゴリと 1〜5 個のタグを選び、必要に応じて説明を入力します。
4. 審査申請を送信します。
5. 自分のプラグイン一覧から申請を開き、チェック結果と審査コメントを確認します。

ここで提出するのは、GitHub リポジトリ内の特定のリビジョンです。Market は対象ブランチを完全な commit SHA に変換し、固定されたコードスナップショットとして審査します。この時点では `.neko-plugin` パッケージを提出しておらず、ダウンロード可能なバージョンも公開していません。

プラグインの初回申請は一度だけ行います。その申請が終了していない間は、同じリポジトリで別の申請を作らないでください。

### 審査担当者から修正を求められた場合

修正を求められた場合は：

1. `plugin/plugins/hello_world/` のソースを修正します。
2. `neko-plugin check hello_world` をもう一度実行します。
3. プラグイン自身のリポジトリで commit して push します。
4. `git rev-parse HEAD` を実行し、新しい完全な commit SHA をコピーします。
5. 元の申請に戻り、リビジョン更新欄に修正内容と新しい commit を入力します。
6. 新しい Revision を送信します。

新しい Revision は、審査待ちのコードスナップショットを置き換えます。これはプラグインの新しいバージョンではなく、Market Version も作成しません。

申請が閉じられた後は、作者自身で Revision を追加できません。却下された申請に修正を提出するには、先に審査担当者に申請を再開してもらう必要があります。

## 4. 審査の合格を待つ

審査に合格するとプラグインは Market の一覧に表示されますが、この時点ではダウンロードボタンがまだない場合があります。

次の二つは別の操作です：

| 操作 | 結果 |
| --- | --- |
| 初回審査に合格 | Market にプラグインの項目が作られる |
| 最初のバージョンを公開 | ユーザーがプラグインをインストールできる |

審査に合格したら次の手順に進み、最初の GitHub Release と Market Version を公開します。

## 5. 最初のインストール可能なバージョンを公開する

まず、公開する変更がすべて commit、push 済みであることを確認します：

```bash
cd plugin/plugins/hello_world
git status --short
git push
cd ../../..
```

`git status --short` は何も表示しない状態でなければなりません。次に、N.E.K.O ソースのルートディレクトリで実行します：

```bash
uv run neko-plugin publish hello_world
```

このコマンドは次の処理を順番に行います：

1. プラグインディレクトリに自身の `.git/` があり、未 commit の変更がないことを確認します。
2. 標準の `release.yml` が現行版であることを確認します。
3. 公開前チェック、テスト、パッケージのビルドと検証を実行します。
4. 現在の commit が `origin` に push 済みであることを確認します。
5. `plugin.toml` のバージョン `0.1.0` からタグ `v0.1.0` を作成して push します。
6. GitHub Actions が GitHub Release を作成するまで待ち、`hello_world.neko-plugin`、`hello_world.market-release-check.txt`、`market-evidence.json` の三つがすべてアップロード済みで、ダウンロードできることを確認します。
7. Market にその Release を読み込ませ、`stable` チャンネルに公開します。

正常に完了すると、ターミナルには最初に GitHub Release の準備完了が表示され、その後に対応する Market バージョンの公開完了が表示されます。

`publish` がバージョンタグを push するときは、あなたの GitHub 認証情報を使います。プラグインのコードは push しません。Market への通知に Market のパスワードやトークンは必要ありません。Market が受け付けるのは、審査に合格し、登録されたリポジトリと一致し、標準のリリース検証に成功したプラグインだけです。

::: warning タグを push しただけでは公開は完了しません
`release.yml` は GitHub Release を作成しますが、それだけでは Market にバージョンを作成しません。通常は `neko-plugin publish` を Market への公開成功が表示されるまで実行してください。
:::

## 6. ユーザーがインストールできることを確認する

公開が成功したら、次の二か所を確認します：

1. GitHub リポジトリの **Releases** ページを開き、`v0.1.0` に `hello_world.neko-plugin`、チェックレポート、公開証明があることを確認します。
2. Market でプラグインの詳細を開き、バージョン一覧に `0.1.0` が表示され、stable の最新バージョンが空ではなくなっていることを確認します。

Market が保存するのは、GitHub Release にあるパッケージの URL とチェックサムです。開発者が `.neko-plugin` ファイルを Market に別途アップロードする必要はありません。

ここまで終わって、最初のバージョンの公開が完了します。

## 7. 次のバージョンを公開する

初回審査に合格した後は、通常の機能更新で審査を再申請する必要はありません。以後の各バージョンは次の順で公開します：

1. `plugin/plugins/hello_world/` のソースを修正します。
2. `plugin.toml` のバージョンを、まだ公開したことのない番号に変更します。
3. 依存関係が変わった場合は `uv run --with pip neko-plugin sync hello_world --clean` を実行します。
4. `neko-plugin check hello_world` を実行します。
5. プラグインのリポジトリで commit して push します。
6. `neko-plugin publish hello_world` を実行します。

たとえば、バージョンを `0.1.0` から `0.1.1` に変更した後は：

```bash
uv run neko-plugin check hello_world

cd plugin/plugins/hello_world
git add .
git commit -m "fix: improve greeting"
git push
cd ../../..

uv run neko-plugin publish hello_world
```

Market は新しく公開された stable バージョンを最新バージョンにします。`latest` はバージョン番号の大小ではなく、Market での公開時刻によって決まります。新しいバージョンを公開した後に、古いバージョンを追加で公開しないでください。

## 8. beta を公開する、または更新内容を書く

通常の `neko-plugin publish hello_world` は `stable` に公開し、Changelog は入力しません。

beta チャンネルを選ぶ場合や更新内容を書く場合は、まず GitHub Release だけを作成します：

```bash
uv run neko-plugin publish github hello_world
```

GitHub Release の準備ができたら：

1. Market にログインし、プラグインのバージョンページを開きます。
2. 新しいバージョンを公開する操作を選びます。
3. 作成した GitHub Release を選びます。
4. `stable` または `beta` を選びます。
5. 必要に応じて Changelog を入力し、公開を確定します。

同じプラグインのバージョン番号は、stable と beta で分かれず、全体で一意です。beta として公開済みのバージョン番号を、そのまま stable の公開に再利用することはできません。stable 版には新しいバージョン番号と新しい Release を使ってください。

## 9. 公開が中断した後に再開する

まず、コマンドがどの手順で止まったか確認します。

### GitHub Release は成功している

完全なコマンドをもう一度実行します：

```bash
uv run neko-plugin publish hello_world
```

リモートタグが現在の commit を指したままであれば、CLI は別のバージョンを作らず、Release の待機または Market への通知を続けます。

GitHub Release ページの URL をコピーし、Market への通知だけを再試行することもできます：

```bash
uv run neko-plugin publish market \
  https://github.com/your-name/n.e.k.o_plugin_hello_world/releases/tag/v0.1.0
```

このモードでも、標準の検証に成功した stable バージョンだけを公開します。

### GitHub Actions が失敗した

プラグインリポジトリの **Actions** ページを開き、失敗した手順を確認します。

- 一時的なネットワーク障害だけでコードを変更していない場合は、GitHub で失敗したワークフローを再実行し、その後 `publish` をもう一度実行します。
- コードまたはリリース設定の修正が必要な場合は、修正して新しいバージョン番号に変更し、commit、push してから新しいタグを公開します。同じバージョン番号を別のコードに向けないでください。

### 標準のリリース設定が現行版ではない

CLI が変更する内容を先に確認します：

```bash
uv run neko-plugin setup-repo hello_world \
  --upgrade-github-actions \
  --dry-run
```

競合がないことを確認してから更新を適用します：

```bash
uv run neko-plugin setup-repo hello_world \
  --upgrade-github-actions
```

その後、プラグインリポジトリで `.github/workflows/` と `ruff.toml` の変更を commit、push してから、もう一度公開します。CLI が認識できないカスタムワークフローの内容を見つけた場合は、上書きせずに停止します。

### Market が公開可能なプラグインを見つけられない

次の三点を確認します：

- 初回審査に合格していること。申請を送信しただけでは不十分です。
- Market に登録されたリポジトリが、現在 `origin` が参照しているリポジトリと同じであること。
- GitHub Release が標準の `release.yml` で正常に作成され、検証に成功していること。

`publish` は初回投稿の代わりにはなりません。審査に合格していないリポジトリを直接 Market に公開することもできません。

## 10. 問題のあるバージョンを取り下げる

公開済みのバージョンに重大な問題がある場合は、Market のバージョン管理ページで理由を入力して取り下げます。

取り下げは元に戻せません。同じバージョン番号や同じ GitHub Release を再び公開することもできません。ソースを修正し、新しいバージョン番号で新しい Release を作成して公開してください。

## 一ページで分かる流れ

### 初回公開

```text
check
  → Git commit
  → GitHub の main に push
  → Verify の成功を待つ
  → Market に初回審査を申請
  → 審査コメントに従って Revision を送信
  → 審査に合格
  → neko-plugin publish
  → GitHub Release
  → Market stable Version
```

### 二回目以降の公開

```text
ソースを修正
  → plugin.toml のバージョンを更新
  → check
  → Git commit / push
  → neko-plugin publish
```

## 既存プラグインに必要なもの

このチュートリアルでは、プラグインディレクトリ自体に `.git/` があり、標準の `verify.yml` と `release.yml` があることを前提にしています。現在のクイックスタートにある `neko-plugin init` で作成したプラグインは、すでにこの条件を満たしています。

既存のプラグインが外側の N.E.K.O リポジトリだけで管理され、プラグインディレクトリ自身に `.git/` がない場合、現在の `publish` は停止します。このようなディレクトリを公開可能なプラグインリポジトリへ自動変換するコマンドは、現在の CLI にはありません。ディレクトリのコピー、パッケージの import、シンボリックリンクで公開フローを代用しないでください。

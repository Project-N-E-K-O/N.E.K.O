# コマンドでプラグインを作成・公開する

N.E.K.O プラグインを作り始めるときに、ディレクトリ、テスト、GitHub 設定をすべて手作業で用意する必要はありません。Plugin CLI は完全なプラグインプロジェクトを作成し、開発中のチェックと新しいバージョンの公開を支援します。

このガイドでは、新規プロジェクトの作成から N.E.K.O Plugin Market への初回公開までを説明します。

::: info 現在の実行方法
Plugin CLI は現在 N.E.K.O ソースツリーに含まれており、まだ単独ではインストールできません。以下のコマンドは N.E.K.O のソースチェックアウトから実行してください。
:::

## プラグインプロジェクトを作成する

```bash
uv run neko-plugin init weather_helper \
  --type plugin \
  --name "Weather Helper" \
  --output ../n.e.k.o_plugin_weather_helper
```

`--output` は最終的なプロジェクトディレクトリです。CLI はプラグインコード、設定、テスト、エディタタスク、Ruff 設定、Verify と Release の GitHub 設定を作成し、`main` ブランチの Git リポジトリを初期化します。

外部プロトコルを N.E.K.O に接続するプロジェクトでは `--type adapter` を使用します。

主に編集するファイルは次の三つです：

| ファイル | 用途 |
| --- | --- |
| `__init__.py` | プラグインの Python コード |
| `plugin.toml` | 名前、バージョン、エントリ、実行設定 |
| `pyproject.toml` | プラグインが利用する外部 Python ライブラリ |

詳しい設定は[プラグイン設定](./plugin-toml)を参照してください。

## 外部ライブラリを準備する

`pyproject.toml` の依存関係を変更したら、プラグインの `vendor/` を更新します：

```bash
uv run neko-plugin sync ../n.e.k.o_plugin_weather_helper --clean
```

外部ライブラリがない場合は、余分なファイルを作らずに成功します。プラグインパッケージでは `requirements.txt` を使用しません。

## プラグインをチェックする

開発中の通常チェック：

```bash
uv run neko-plugin check ../n.e.k.o_plugin_weather_helper
```

公開前の完全チェック：

```bash
uv run neko-plugin check -r ../n.e.k.o_plugin_weather_helper
```

完全チェックはテストを実行し、インストールパッケージを作成して、そのパッケージが壊れていないことを確認します。

公開せずローカルパッケージだけを作る場合：

```bash
uv run neko-plugin build ../n.e.k.o_plugin_weather_helper \
  --target-dir ../plugin-builds
```

## GitHub にプッシュする

CLI は GitHub リポジトリの作成、コミット、現在のブランチのプッシュを行いません。次の名前で GitHub リポジトリを作成してください：

```text
n.e.k.o_plugin_weather_helper
```

コードをコミットしてプッシュします：

```bash
cd ../n.e.k.o_plugin_weather_helper
git add .
git commit -m "feat: first release"
git remote add origin \
  https://github.com/your-name/n.e.k.o_plugin_weather_helper
git push -u origin main
```

## プラグインを審査に提出する

初回公開の前に [N.E.K.O Plugin Market](https://market.project-neko.cn) を開いてログインし、GitHub リポジトリを提出して、プラグインの承認を待ちます。

この手順はプラグインごとに一度だけ必要です。CLI はプラグインの登録や審査提出を代行しません。承認後、Market はこのリポジトリをプラグインに関連付けられるため、以後のリリースは `publish` で自動的に追加できます。

## バージョンを公開する

`plugin.toml` のバージョンを確認し、変更をコミットしてプッシュします。その後、N.E.K.O のソースディレクトリから実行します：

```bash
uv run neko-plugin publish ../n.e.k.o_plugin_weather_helper
```

バージョンが `0.1.0` の場合、CLI はプロジェクトをチェックし、`v0.1.0` タグをプッシュし、GitHub が Release とパッケージを作成するまで待ってから、Plugin Market にその Release を読み取るよう通知します。

Market への通知に Market の認証情報は必要ありません。Git の操作には自分の GitHub 認証情報を使用します。

同じタグが同じコミットを指している場合、このコマンドは安全に再実行できます。別のコードを指すタグは上書きせず停止します。

## 既存のプラグインプロジェクト

標準 GitHub 設定の変更内容を先に確認します：

```bash
uv run neko-plugin setup-repo /path/to/existing-plugin \
  --upgrade-github-actions \
  --dry-run
```

確認後に適用します：

```bash
uv run neko-plugin setup-repo /path/to/existing-plugin \
  --upgrade-github-actions
```

この操作が管理するのは `ruff.toml`、`.github/workflows/verify.yml`、`.github/workflows/release.yml` だけです。認識できない独自の内容がある場合は上書きせず停止します。

## 公開の途中から再開する

通常は `publish` をもう一度実行してください。次のモードは、中断した公開の再開や一つの段階の調査に使用します：

```bash
uv run neko-plugin publish github /path/to/plugin

uv run neko-plugin publish market \
  https://github.com/owner/repository/releases/tag/v0.1.0
```

## コマンド一覧

| コマンド | 用途 |
| --- | --- |
| `init` | 完全な新規プラグインプロジェクトを作成する |
| `setup-repo` | 既存プロジェクトの標準 GitHub 設定を更新する |
| `sync` | `vendor/` の外部ライブラリを更新する |
| `check` | 開発中のプラグインをチェックする |
| `check -r` | 公開前にテスト、ビルド、検証を行う |
| `build` | 公開せずにローカルパッケージを作る |
| `publish` | GitHub Release を作成して Market に通知する |
| `install` | デバッグ用にローカルパッケージを指定ディレクトリへインストールする |
| `analyze` | bundle 候補の SDK と依存関係を比較する |

全オプションは `uv run neko-plugin <command> --help` で確認できます。

## よくある失敗

- **未コミットの変更がある：** 公開前にコミットまたは stash します。
- **HEAD がまだプッシュされていない：** 現在のブランチを先にプッシュします。
- **標準 Release 設定が古い：** `setup-repo --upgrade-github-actions` を実行します。
- **依存関係が `vendor/` にない：** `sync --clean` を実行します。
- **GitHub Release が作成されない：** リポジトリの Actions ページで Release ワークフローを確認します。

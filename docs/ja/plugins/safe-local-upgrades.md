# ロールバック可能なローカルプラグイン更新

N.E.K.O. は、同じ実行可能プラグインの再インポートを更新として扱い、`my_plugin_1` のような接尾辞付きコピーを作成しません。実行可能プラグインのディレクトリは `[plugin].entry` が参照する Python package と一致する必要があります。ディレクトリ名だけを変更すると、registry はそのプラグインを読み込めません。

このページはローカル package 置換の正規メンテナ向け資料です。個別プラグインの移行資料は、トランザクション規則を複製せず、このページを参照してください。

## ユーザーフロー

Plugin Manager はファイルを変更する前に install plan を取得します。

| Action | 意味 | 表示される結果 |
| --- | --- | --- |
| `install` | 対象 identity とディレクトリが未使用です。 | そのままインストールします。 |
| `upgrade` | package identity と対象ディレクトリに一致する既存プラグインが 1 つあります。 | 現在と更新後の version を表示し、明示確認を求めます。 |
| `blocked` | 曖昧または危険な置換になります。 | インストール先を変更する前に停止します。 |

確認 token は package bytes、対象 path、現在の `plugin.toml` から生成されます。サーバーはインストール直前に plan を再構築し、確認後に package または既存対象が変化していれば更新を拒否します。

## 更新トランザクション

確認済み更新は次の順序で実行されます。

1. プラグインが実行中か確認します。
2. 必要な場合は停止します。
3. 既存のプラグインディレクトリと package profile を timestamp 付き backup に移動します。
4. 元の実行可能ディレクトリへ新しい package をインストールします。
5. インストール済み ID とディレクトリが確認済み plan と一致することを検証します。
6. 保持対象の profile 内容を新しい profile に統合します。
7. 更新前に実行中だった場合は再起動します。
8. 新しいインストールの検証と起動後に backup を削除します。

正常な更新後の backup cleanup 失敗は warning であり、有効な新バージョンをロールバックしません。

## 失敗とロールバック

backup、install、validate、profile preserve、restart の失敗時は、全対象を逆順で復元します。更新前に実行中だったプラグインは、可能であれば復元済みの旧バージョンから再起動します。

| `rollback_status` | 意味 |
| --- | --- |
| `not_needed` | 更新成功。ロールバックは不要でした。 |
| `completed` | 更新失敗。旧プラグインと profile を復元しました。 |
| `incomplete` | 更新と復元の両方に失敗した箇所があります。手動確認が必要です。 |

Plugin Manager は `incomplete` を復旧成功として表示してはいけません。

## Block されるケース

- bundle 内に既存プラグインまたは実行可能ディレクトリとの衝突がある;
- `[plugin].previous_ids` の旧プラグインがまだインストールされている;
- 対象ディレクトリの plugin ID が package の ID と異なる;
- 同じ plugin ID が複数ディレクトリに存在する;
- single-plugin package のプラグイン数が 1 ではない;
- ディレクトリ名、package ID、または指定 root が許可範囲外を指す。

衝突のある bundle は一括更新せず、single-plugin package で個別に更新します。

## 安定 identity と改名

```toml
[plugin]
id = "my_plugin"
entry = "plugin.plugins.my_plugin:MyPlugin"
previous_ids = ["old_plugin_id"] # 任意の旧 identity 衝突 guard
```

インストールディレクトリは `my_plugin` でなければなりません。`previous_ids` は新旧 identity の同時インストールを防ぐ install-time guard です。runtime alias ではなく、旧データの自動移行や削除も行いません。

## API 契約

- `POST /plugin-cli/install-plan` は action、identity、version、block reason、legacy IDs、confirmation token を返します。
- `POST /plugin-cli/install` は初回インストールを直接実行します。更新には `confirm_upgrade=true` と現在の `confirmation_token` が必要です。
- block は `PLUGIN_INSTALL_BLOCKED`、確認不足は `PLUGIN_UPGRADE_CONFIRMATION_REQUIRED`、古い token は `PLUGIN_UPGRADE_PLAN_CHANGED` を返します。
- transaction 失敗は `PLUGIN_UPGRADE_ROLLED_BACK` と `stage`、`rollback_status` を返します。

両 endpoint は administrator authorization が必要です。ユーザー向け error に package 内容、設定値、credential、confirmation token、無制限の絶対 path を含めてはいけません。

## メンテナ向け実装マップ

| 責務 | 正規実装 |
| --- | --- |
| Plan 分類と confirmation token | `plugin/server/application/plugin_cli/install_plan.py` |
| Transaction、backup、restore、restart | `plugin/server/application/plugins/upgrade_support.py` |
| Install orchestration と path policy | `plugin/server/application/plugin_cli/service.py` |
| HTTP request/response model | `plugin/server/routes/plugin_cli.py` |
| Plugin Manager の確認と結果表示 | `frontend/plugin-manager/src/composables/usePackageManager.ts` |

## 検証

初回インストール、確認済み更新、確認キャンセル、token 失効、各種衝突、各 transaction stage の失敗、完全・不完全ロールバック、plugin/profile 復元、Plugin Manager 動作、locale key 一致を確認してください。

`plugin/tests/unit/server/` の関連 backend tests、CLI workflow integration tests、Plugin Manager Vitest、TypeScript type check、frontend i18n check を使用します。

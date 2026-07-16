
# テスト

## 準備

```bash
uv sync
uv run playwright install chromium
```

ルートの `tests/conftest.py` も Chromium がない場合にインストールを試みますが、明示的な準備のほうが予測可能です。`tests/api_keys.json` は commit しません。外部サービスを実際に呼ぶテストでのみ template から作成します。

## よく使うコマンド

```bash
# ルート全体
uv run pytest -q

# 対象 path／marker
uv run pytest tests/unit -q
uv run pytest tests/frontend -m frontend -q
uv run pytest tests/e2e -m e2e -q

# 手動テスト：実 API、画面、ブラウザー。監督下でのみ実行
uv run pytest -m manual --run-manual -s

# plugin suite は独自の pytest 設定を使用
uv run pytest plugin/tests -q
```

現在の `tests/conftest.py` に `--run-e2e` はありません。opt-in CLI flag は `--run-manual` だけです。Performance test は所有テストの `RUN_PERF_TESTS=true` を使います。

## Marker

ルート `pytest.ini` は `unit`、`frontend`、`e2e`、`performance`、`plugin_unit`、`plugin_e2e` を登録します。`conftest.py` は `manual` を追加し、明示的に有効化しない限り手動テストを skip します。

ディレクトリ名だけでは挙動を証明できません。fixture とテストコードを確認し、server 起動、Playwright、外部 credential、ローカル UI／OS 状態の変更があるか判断してください。

## CI の範囲

- `.github/workflows/plugin-tests.yml`：Windows 上の plugin suite と選択されたルート契約。
- `.github/workflows/analyze.yml`：Ruff とプロジェクト固有の静的契約。
- `.github/workflows/docs.yml`：`npm ci` と VitePress build。
- desktop／Docker workflow はパッケージング経路を別に検証します。

一般的な GitHub workflow は、完全な cross-platform ルート `pytest` を保証しません。ローカルで実際に実行した内容を正確に記録してください。

## テスト作成

- Regression test は所有する test cohort に置きます。
- 一時 path と合成されたプライバシー安全な内容を使います。
- 明示的な manual／integration test 以外では実 API を避けます。
- async test を決定的にし、任意 sleep で lifecycle 同期を置き換えません。
- 実行契約が明確な場合だけ marker を追加／再利用します。
- i18n／provider 変更は、一つだけでなく同期対象のすべてを検証します。

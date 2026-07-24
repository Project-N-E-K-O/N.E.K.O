# ソースからの手動セットアップ

```bash
git clone https://github.com/Project-N-E-K-O/N.E.K.O.git
cd N.E.K.O
uv sync
```

Python は 3.11 固定です。すべての Python module/script/test/temporary command は `uv run` 経由です。

Frontend は repository root で `.\build_frontend.bat` または `./build_frontend.sh`。Yui Origin を確認し、`npm ci` で plugin manager と shared React chat bundle を生成します。

通常の起動:

```bash
uv run python launcher.py
```

launcher は ports、memory/main/agent、shutdown を調整し、service 起動前に staged cloud-save snapshot を適用します。報告 URL を使い、48911 を固定しないでください。

診断の split mode:

```bash
uv run python -m app.memory_server
uv run python -m app.main_server
uv run python -m app.agent_server
```

Agent、hosted plugin、browser/computer-use は agent/tool が必要です。Split mode は launcher fallback/lifecycle を再現しません。

Steam RemoteStorage path は Steam/desktop launcher で確認します。Shutdown は runtime changes を自動 stage しないため、Cloud Save Manager で upload 用 snapshot を準備します。

Main URL の `/api_key` で current Provider と credentials を設定し、connectivity check を実行します。Source mode は Docker API initialization variables を読みません。

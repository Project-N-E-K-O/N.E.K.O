# 開発環境の構築

```bash
git clone https://github.com/Project-N-E-K-O/N.E.K.O.git
cd N.E.K.O
uv sync
```

`uv` は Python 3.11 を選択します。Python modules/scripts/pytest/temporary commands は `uv run` 経由です。

```powershell
.\build_frontend.bat
```

```bash
./build_frontend.sh
```

Scripts は Yui Origin を確認し、`npm ci` で Vue plugin manager を `frontend/plugin-manager/dist/`、React chat を `static/react/neko-chat/` に build します。

Iterative work は owning frontend directory で `npm ci && npm run dev`。Plugin manager は 5173 で `VITE_BACKEND_URL` または 48916 へ proxy、React は 5174。Production React bundle は `index.html` と `chat.html` が共有します。

```bash
uv run python launcher.py
```

Launcher URL を開き、`/api_key` で設定、`/health` で診断します。

```bash
uv run pytest
uv run ruff check .
```

CI parity を主張する前に repository-specific checks を含む `.github/workflows/analyze.yml` を確認してください。

# 开发环境搭建

```bash
git clone https://github.com/Project-N-E-K-O/N.E.K.O.git
cd N.E.K.O
uv sync
```

`uv` 选择 `pyproject.toml` 固定的 Python 3.11。Python 模块、脚本、pytest 和临时命令全部通过 `uv run`。

## 构建前端

```powershell
.\build_frontend.bat
```

```bash
./build_frontend.sh
```

脚本校验/解压 `assets/yui-origin.tar.gz`，运行 `npm ci`，将 Vue plugin manager 构建到 `frontend/plugin-manager/dist/`，React chat 构建到 `static/react/neko-chat/`。

迭代时在对应 frontend 目录运行 `npm ci && npm run dev`。Plugin manager 使用 5173，并代理到 `VITE_BACKEND_URL` 或 48916；React 使用 5174。生产 React bundle 同时由 `index.html` 和 `chat.html` 挂载。

## 启动与验证

```bash
uv run python launcher.py
```

打开 launcher 报告的 URL，首选默认值为 `http://127.0.0.1:48911`。在 `/api_key` 配置 Provider，启动异常查看 `/health`。

```bash
uv run pytest
uv run ruff check .
```

CI 还执行 async 阻塞、禁用日志库、prompt/i18n、LLM budget、API 末尾斜杠、模块分层、文档路径和 core 契约脚本。声称与 CI 等价前请读 `.github/workflows/analyze.yml`。

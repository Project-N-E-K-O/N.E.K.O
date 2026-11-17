# 前置条件

| 工具 | 源码开发要求 | 来源 |
| --- | --- | --- |
| Python | 必须为 3.11.x | `pyproject.toml` |
| uv | 当前兼容版本 | `uv.lock` 与 workflow |
| Git | 仍受维护的版本 | 仓库操作 |
| Node.js + npm | plugin manager 要求 `^20.19.0 || >=22.12.0` | package.json |

Python 依赖使用 `uv sync`；不要把手工 `pip install -r requirements.txt` 当项目安装流程。前端和文档使用已提交 lockfile 与 `npm ci`。

按功能还可能需要 Docker Engine + Compose、Steam 环境、平台打包依赖、前端和 e2e 测试用的 Playwright Chromium（`uv run playwright install chromium`），或真实调用外部 Provider 的测试密钥。

Agent 的浏览器操控用的是本机已安装的 Chromium 内核浏览器（Chrome、Edge、Brave 或 Chromium）。应用不自带浏览器，也不会下载浏览器；本机没有时，浏览器操控会报告找不到可用的浏览器。运行应用本身不需要通过 Playwright 安装任何东西。

当前 Provider 数据可能有免费路由，但可用性与限制属于外部状态，不是开发前置条件或本站承诺。

Windows 建议开启长路径，检出目录必须可写。模型准备、前端构建、测试和打包都会生成本地产物。

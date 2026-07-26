# 前置条件

| 工具 | 源码开发要求 | 来源 |
| --- | --- | --- |
| Python | 必须为 3.11.x | `pyproject.toml` |
| uv | 当前兼容版本 | `uv.lock` 与 workflow |
| Git | 仍受维护的版本 | 仓库操作 |
| Node.js + npm | plugin manager 要求 `^20.19.0 || >=22.12.0` | package.json |

Python 依赖使用 `uv sync`；不要把手工 `pip install -r requirements.txt` 当项目安装流程。前端和文档使用已提交 lockfile 与 `npm ci`。

按功能还可能需要 Docker Engine + Compose、Steam 环境、平台打包依赖、Playwright Chromium，或真实调用外部 Provider 的测试密钥。

当前 Provider 数据可能有免费路由，但可用性与限制属于外部状态，不是开发前置条件或本站承诺。

Windows 建议开启长路径，检出目录必须可写。模型准备、前端构建、测试和打包都会生成本地产物。

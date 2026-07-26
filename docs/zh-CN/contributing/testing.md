
# 测试

## 环境准备

```bash
uv sync
uv run playwright install chromium
```

根目录 `tests/conftest.py` 在 Chromium 不可用时也会尝试安装，但显式准备更可预测。不要提交 `tests/api_keys.json`；只有确实调用外部服务的测试才从模板创建该文件。

## 常用命令

```bash
# 完整根测试集
uv run pytest -q

# 聚焦路径或 marker
uv run pytest tests/unit -q
uv run pytest tests/frontend -m frontend -q
uv run pytest tests/e2e -m e2e -q

# 手动测试：真实 API、屏幕或浏览器，仅在有人监督时运行
uv run pytest -m manual --run-manual -s

# 插件测试使用自己的 pytest 配置
uv run pytest plugin/tests -q
```

当前 `tests/conftest.py` 没有 `--run-e2e` 选项；唯一的 opt-in CLI 开关是 `--run-manual`。Performance 测试由所属测试使用 `RUN_PERF_TESTS=true` 控制。

## Marker

根 `pytest.ini` 注册了 `unit`、`frontend`、`e2e`、`performance`、`plugin_unit` 和 `plugin_e2e`。`conftest.py` 另加 `manual`，并默认跳过手动测试。

目录名不能证明执行行为。应检查 fixture 和测试代码，确认是否启动服务、使用 Playwright、需要外部凭据，或修改本机 UI/OS 状态。

## CI 覆盖范围

- `.github/workflows/plugin-tests.yml`：Windows 上的插件测试和选定根契约。
- `.github/workflows/analyze.yml`：Ruff 与项目专用静态契约。
- `.github/workflows/docs.yml`：`npm ci` 和 VitePress 构建。
- 桌面与 Docker workflow 分别验证打包路径。

通用 GitHub workflow 并不代表完整的跨平台根 `pytest` 承诺。请准确说明本地实际运行的内容。

## 编写测试

- 回归测试放入功能所属的测试组。
- 使用临时路径和合成、隐私安全的内容。
- 除非明确属于 manual/integration，否则避免真实 API。
- 保持异步测试确定性；不要用任意 sleep 代替生命周期同步。
- 只有执行契约清楚时才新增或复用 marker。
- i18n 和 provider 修改要覆盖所有同步同类项，而不是只测一个 locale/provider。

# Nuitka 打包注意事项

N.E.K.O 用 Nuitka standalone 把 Python 后端编进单 exe，再被 Electron 打包分发。
Nuitka 有几个**默认行为**容易踩坑，**新加目录或动态 import 之前必读**。

## 规则 1：含 `.py` 的目录名必须用下划线

Python 包名禁止含连字符，所以 `--include-package=plugin.my-tool.public` 会被 Nuitka 拒绝。
自然的退路 `--include-data-dir=plugin/my-tool` 也是陷阱：Nuitka 的 `--include-data-dir` **默认过滤
`.py`、`.pyc`、`.pyd`、`.so`、`.dll`** 等代码后缀（见你本地 Nuitka 安装中
`nuitka/freezer/IncludedDataFiles.py` 的 `default_ignored_suffixes` 元组——这是上游默认行为，不是项目配置）。
打包后的 dist 里只剩 `.md`、`.json` 等非代码文件，运行时 import 直接 `ModuleNotFoundError`，
而 build 看起来一切正常，直到用户打开受影响的功能。

**真实 bug**：`plugin/neko-plugin-cli/` 历史上含有 `public/` 这个 Python 包。server 调用方做
`sys.path.insert(_CLI_ROOT)` 之后 `from public import ...`。源码模式跑得起来；Nuitka standalone
里整个 `public/` 包被静默丢失，embedded user plugin server 起不来，plugin 管理 UI 整个无法访问。

**正确写法**：任何含 `.py` 源文件的目录都用下划线 + 含 `__init__.py`。
若需要带连字符的对外 CLI 工具产品名，用 `pyproject.toml [project.scripts]` 映射；
底层 Python 包仍用下划线。

`tests/unit/test_no_hyphen_python_packages.py` 会在 PR 阶段自动拦截违规。

## 规则 2：`--include-data-dir=` 不带 `.py`

如果真的需要把 `.py` 源文件以非编译形式打包（极少见，比如运行时插件 sandbox），
用 `--include-raw-dir=` 替代——它跳过默认后缀过滤。其他情况一律首选
`--include-package=<dotted.name>`，让 Nuitka 把模块编进二进制。

## 规则 3：新加目录必须同步 build script + CI

两套独立的构建配置：

- `build_nuitka.bat` —— 本地维护脚本，**gitignored**（含签名路径、机器特定设置）。
- `.github/workflows/build-desktop.yml` —— Linux/macOS/Windows release artifact 的 CI 构建。

新加需要进 bundle 的目录必须**两边同步更新**。Nuitka 构建后 CI 会跑
`scripts/check_nuitka_dist.py` 验证关键资产存在；新增必需资产请同步登记到该脚本里。

## 规则 4：别随便拉起打包后的 exe 做诊断

launcher 会拉起多个子进程（`memory_server`、`agent_server`、`main_server`、plugin server 等）。
只杀 launcher 子进程仍然存活，会锁住 `dist/Xiao8/` 文件。下一次构建的
`rmdir /s /q dist\Xiao8` 部分失败、紧接着的 `move dist\launcher.dist dist\Xiao8`
会把新 build 落进残留目录里——产出半截嵌套的烂 bundle，能启动但缺 config/static/templates。

诊断打包问题优先用：

- `scripts/check_nuitka_dist.py dist/Xiao8` 查资产清单
- `grep -r <symbol> dist/Xiao8/` 看内容
- 若必须跑 exe，事后**显式杀掉**所有 `projectneko_server`、`neko_main_server`、
  `neko_memory_server`、`neko_agent_server` 进程

## 防御层

历史 neko-plugin-cli bug（PR #1115，"rename neko-plugin-cli → neko_plugin_cli"）
在生产里潜伏数周，因为没有任何机制告警。现在有三层防御：

1. **构建期检查**——`scripts/check_nuitka_dist.py` 在 CI Nuitka 之后跑，遍历 dist 根目录
   验证每个关键目录存在、每个内置插件都有 `plugin.toml`。
2. **源码期 lint**——`tests/unit/test_no_hyphen_python_packages.py` 在 PR 阶段失败，
   只要任何被跟踪的连字符目录里有 `.py` 文件。
3. **本文档**——加 packaging 相关代码前先读。

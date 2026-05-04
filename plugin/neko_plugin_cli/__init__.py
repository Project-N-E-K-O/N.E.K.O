"""neko-plugin-cli：插件打包/解包/检查工具的 Python 入口。

历史命名带连字符（``neko-plugin-cli``）以贴 CLI 命名惯例，但带连字符的目录
不是合法 Python 包名，导致 server 侧只能 ``sys.path.insert`` + ``from public``
绕路引用，又被 Nuitka 静默漏打包（默认 ``--include-data-dir`` 过滤 ``.py``）。
重命名为下划线后归回普通 Python 包，``--include-package=plugin`` 即可自动跟进。
"""

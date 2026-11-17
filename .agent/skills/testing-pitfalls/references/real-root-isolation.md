# 测试和真实运行数据目录的隔离

开发机上有一份真实的运行数据（角色名册、记忆、配置、日志）。测试进程一旦解析到这个目录，就会往里写。发生过的后果：角色名册被替换成测试夹具的内容，配置文件被截断，记忆目录下多出几十个夹具目录，日志目录里的旧日志被清掉。名册被抹之后还会有次生现象，比如改名时报「目标目录已存在、拒绝覆盖」——那不是改名功能的缺陷，是名册和磁盘对不上。

## 写进真实目录的两条通路

1. **存储根环境变量泄漏。** `NEKO_STORAGE_SELECTED_ROOT` 及其同族变量（anchor、cloudsave）在 `utils/config_manager/storage_roots.py` 里优先级高于所有目录候选。只要它们留在进程环境里，测试辅助函数里对目录候选打的桩全部失效。来源有两个：
   - 从应用里起的 shell 把变量带给了 pytest；
   - 某条用例走到了会导出布局的代码而没打桩。`launcher_core/runtime.py` 的 `_resolve_storage_layout_for_launch` 和 `plugin/core/host.py` 的 `_refresh_child_storage_layout_env` 都调 `export_storage_layout_to_env(layout)` 直接写 `os.environ`，布局来自机器上真实的存储策略。
2. **导入时的迁移。** `get_config_manager()` 默认 `migrate=True`，而不少产品模块在模块级就调了它（例如 `utils/preferences.py`）。谁第一个被导入，谁就在当时解析到的根目录上跑完整的迁移链。这条通路不依赖环境变量。

## 现有的隔离手段

都在 `tests/` 下，各文件的 docstring 写了每一步为什么在那里：

- `tests/process_isolation.py` 的 `isolate_test_process()`：`tests/conftest.py` 和 `plugin/tests/conftest.py` 在导入任何产品模块之前调它。它做四件事：
  - 设 `NEKO_DO_NOT_TRACK=1`。用量上报的开关在 `utils/token_tracker/reporting.py` 里只在导入时读一次，之后创建的 tracker 会注册进程退出时的上报，所以必须先于导入。
  - 清掉继承来的存储根环境变量。
  - 把日志的兜底目录改到临时目录。
  - 调 `tests/real_root_isolation.py` 把 ConfigManager 的目录解析指到一次性的临时根。
- `tests/real_root_isolation.py`：替换 ConfigManager 的四个目录解析方法（标准数据目录候选、两个旧版候选、旧版应用根候选）。只挡标准目录不够，旧版扫描照样会去读真实的文档目录并在里面建工作区。被测对象就是这套解析的用例，用 `real_root_resolution` fixture 临时换回真方法。
- `tests/storage_root_env_guard.py`：会话开始时丢掉继承值并给出警告；每条用例前后快照并还原那几个变量，改了没还原的用例直接报错并点名。
- `tests/unit/test_pytest_process_isolation.py`、`plugin/tests/unit/test_suite_isolation.py`：守这套隔离本身。其中一条断言 `PRODUCT_MODULES_BEFORE_ISOLATION == ()`，即隔离安装之前没有任何产品模块被导入。

## 写测试时要遵守的

- ⚠️ conftest 里在 `isolate_test_process()` 之前不要导入任何产品模块，也不要把它挪到后面。
- 测试起的 Python 子进程不跑 conftest，只继承了 `NEKO_DO_NOT_TRACK`。子进程里一导入产品模块，日志就建在真实目录下。用 `tests/process_isolation.py` 的 `child_process_env(runtime_root)` 生成子进程环境。这些变量只放进子进程的环境，测试进程自己不能带。
- 要设存储根变量的用例用 `monkeypatch.setenv`，它在收尾时还原，不会被守卫点名。
- 测用量上报本身的用例，打桩 `reporting._DO_NOT_TRACK` 并把上报地址指到本地收集器，见 `tests/telemetry_capture.py`。
- ⚠️ 不要拿 `LOCALAPPDATA` 当隔离开关。它同时是 uv 的缓存目录，改掉之后 uv 写不了缓存，会有和本项目无关的用例失败。
- ⚠️ 「把某一处模块级的 `get_config_manager()` 改成 `migrate=False`」解决不了问题：模块级单例有好几处，裸调用有几百处，换一个测试文件触发者就变了。杠杆在 ConfigManager 自己的目录解析上。
- `tests/` 和 `plugin/tests` 是两棵独立的树，各有各的 conftest。验证隔离相关的改动要分开跑，混在一条命令里跑会互相掩盖。

## pytest 之外也会碰真实目录

没有 conftest 就没有隔离。在 pytest 之外执行 `python -c "import memory"` 这样的裸导入，日志模块会在真实目录下初始化并清理旧日志，模块级的 `get_config_manager()` 还可能跑迁移。

- 语法检查只用 `python -m py_compile <文件>`。
- 要确认导入路径，看 `sys.path`，或者把导入放进一条 pytest 用例。
- 临时脚本要导入产品模块时，先自己设好存储根变量指向一个临时目录。

## 测「拦截外发」的钩子

写一个钩子去拦截联网请求（审计钩子、socket 补丁）并自测时，⚠️ 绝不能拿真实的服务地址当测试目标。自测的前提是「拦截已经生效」，而这正是被测的东西：钩子文件只要因为语法错误没加载上，那次自测就是一次真实的外发。

- 目标一律用假的：文档保留网段的地址（`192.0.2.1`）或本机端口，并把目标地址做成可以通过环境变量替换。
- 先单独确认钩子加载了（导入它并打印 `__file__`），再跑任何会联网的东西。

## 排查「谁写了真实目录」

- 怀疑隔离有漏洞时，不要靠比文件修改时间：同时有别的进程在跑就分不清来源。
- 可靠的办法是审计钩子：让 `PYTHONPATH` 指向一个只含 `sitecustomize.py` 的目录，在里面用 `sys.addaudithook` 记录 `open`（写模式）、`os.mkdir`、`os.rename`、`os.remove` 这些事件中路径命中真实根目录的调用栈。子进程会自动继承。
- 想看一次运行往替身根目录里写了什么，设 `NEKO_TEST_KEEP_APPDATA_ROOT=1`，进程退出时就不删它。
- 更稳妥的做法是先把真实目录换成一棵假的目录树（或者临时改名备份），跑完比较整棵树。

## 日志目录可写性探测

`utils/logger_config.py` 的 `_test_directory_writable` 用带进程号和线程号的探测文件名。早先用固定文件名时，多个进程并发探测会互相删文件，误判「不可写」后回落到文档目录下的日志路径。改这段时保持探测文件名按进程、线程唯一。

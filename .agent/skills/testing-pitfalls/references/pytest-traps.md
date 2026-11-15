# pytest 用例的坑

每条按「症状 → 根因 → 做法」写。共同点是：它们制造「单独跑绿、换个跑法红」或者反过来的信号。

## 用例顺序

- 根目录和 `plugin/tests` 的 `pytest.ini` 都钉了同一个 `--randomly-seed`（`plugin/tests` 不继承根目录的配置，两处要一起改）。「可复现」只在同一个提交上成立，增删用例会改变洗牌结果。为什么钉死、逃生开关是什么，写在根 `pytest.ini` 的注释里。
- 症状「单独跑绿、整个文件或整套跑红」先按顺序依赖查：先单文件跑，再把怀疑的两个文件组合跑，不要先动版本控制。
- 最常见的形态是产品代码里的进程级可变全局（缓存、注册表、单例）被前一条用例写脏。修法是给那个测试文件加 autouse fixture，前后清理。现成例子：`tests/unit/test_music_router.py` 的 `_reset_music_proxy_cache`。这通常是测试隔离问题，不是产品缺陷，判断前先看那个全局在产品里是怎么写入的。
- 定时的随机顺序任务（`random-order-tests.yml`）红了，用日志开头打印的 seed 本地复现：`uv run pytest tests/unit -q --randomly-seed=<N>`。

## `caplog` 是顺序依赖的

- 症状：断言日志的用例单跑绿、整套跑红（或反过来）。
- 根因：`utils/logger_config.py` 配置日志时把 `N.E.K.O` 这个父 logger 设成 `propagate = False`，而 `caplog` 挂在 root 上。只要前面有用例把那套日志配置导入进来，记录就到不了 root。
- 做法：不用 `caplog`，直接给模块 logger 的方法打桩收集，例如
  `monkeypatch.setattr(mod.logger, "warning", lambda msg, *a, **kw: seen.append(msg % a if a else msg))`。
  一定要用 `caplog` 时，照 `tests/unit/conftest.py` 的 `arbiter_logs_reach_caplog` 那样，在这一条用例里把 logger 链上的 `propagate` 临时改回 `True`。

## 后台线程泄漏

- 症状：某条用例之后，别的用例出现真实网络请求或莫名失败。
- 根因：`thread.join(timeout)` 超时只是返回，用例照样绿。泄漏的线程在 `monkeypatch` 还原之后带着真实依赖继续跑。
- 做法：在 autouse fixture 的收尾里先让线程停下来（置停止标志，把它正在等的事件或队列放开），再做有界的 join，并 `assert not thread.is_alive()`。⚠️ 让这个 fixture 声明 `monkeypatch` 参数，这样它的收尾先于 monkeypatch 的还原执行，断言时桩还在。
- 断言失败并不会让线程停下，桩随后照样被还原。所以线程必须有一条测试能触发的退出路径；实在保证不了它退出的，把这条测试放到独立进程里跑。

## 模块级 `asyncio.Lock` 绑定事件循环

- 症状：本地按文件顺序全绿，CI 上某条用例 `asyncio.wait_for(...)` 超时。
- 根因：模块级的 `asyncio.Lock`（例如 `utils/character_memory.py` 的 `character_config_mutation_lock`）在第一次被争用时绑定当时的事件循环。pytest-asyncio 每条用例一个新循环，前面的用例在别的循环里争用过它，后面再争用就在等待方抛 `RuntimeError`，异常落在没人 await 的任务里，外面只看到超时。
- 做法：要争用模块级锁或事件的测试，用 autouse fixture 给每条用例换一把新的：`monkeypatch.setattr(模块, "锁名", asyncio.Lock())`。被测代码要在调用时取锁（`from 模块 import 锁` 写在函数里，或通过模块属性访问），不能在导入时绑定。

## 进程级单例和 autouse fixture

- `tests/unit/conftest.py` 里有一组 autouse fixture 在每条用例前后还原进程级状态（共享状态、Steamworks 句柄、路由注册表、`sys.path` 等），各自的 docstring 写了它防的是什么。
- ⚠️ 把进程级单例从一个模块搬到另一个模块时，检查它原先是不是被某条 autouse fixture 的快照覆盖着。覆盖掉了不会报错，只会安静消失。补 fixture 时快照整个模块命名空间，不要列名字清单。
- ⚠️ 有些泄漏只在特定开发机上可见（例如装了 Steam 客户端时初始化才会成功），CI 永远是绿的。给这类状态写护栏要用桩替换初始化函数，让它在任何环境都能复现。
- fixture 还原快照时不要对「借来的」句柄调 `unload()` 之类的进程级释放。只有创建者才释放，理由写在 `_reset_steamworks_handle` 的 docstring 里。

## 并发和时序

- ⚠️ 靠多跑几轮抓不住交错。真实的交错窗口往往只有几微秒，GIL 下线程根本不切换，把锁去掉也照样绿。必须强制交错：用 `threading.Event` 把一方卡在关键点，让另一方插进来，再放行；或者把临界区里的某个调用换成带 `sleep` 的版本撑开窗口。
- 要把并发方精确送进某个窗口，可以用 `Event` 的子类，在 `clear()` / `set()` 里卡住。
- 「协程让出控制权之前状态是否已经更新」要在 `asyncio.to_thread` 的边界上观测。在 worker 里观测，两种实现都能过。
- 断言「读不被写阻塞」：让 worker 持锁卡住，主线程的读必须立刻返回。
- ⚠️ `time.monotonic()` 在 Windows 上粒度约 15 毫秒，「先取 t0、做点事、再取 t1」经常得到 `t0 == t1`。用时间戳区分先后的判据必须另有一条不依赖时钟的硬判据（例如单调递增的代号）。
- 测试桩里 `raise AssertionError` 表示「不该被调用」时，如果产品代码用 `try/except` 包着那个调用点，异常会被吞成默认值，和「没调用」分不开。改用计数断言。
- 夹具里故意吞 `CancelledError` 的 handler，释放动作必须写在 `finally` 里。否则断言一失败，事件循环拆除时它继续吞，用例变成挂起，CI 上表现为超时而不是失败。
- 可能永远等下去、但会响应取消的被测调用，在测试里套一层 `asyncio.wait_for(<调用>, 3)`。实现被改坏时要的是失败，不是挂死。
- ⚠️ `wait_for` 不是硬超时：它超时后是取消被测调用，并等它取消完成。被测代码吞掉 `CancelledError` 继续等的话，`await asyncio.wait_for(...)` 自己就不返回，测试连 `finally` 都进不去，把放开它的动作写在 `finally` 里也救不了。这类调用不要用 `wait_for`：把它起成任务，用 `asyncio.wait` 等到期限（它到点就返回，不取消任务），再放开留给它的出口并收尾。

  ```python
  task = asyncio.ensure_future(<调用>)
  try:
      done, _ = await asyncio.wait({task}, timeout=3)
      assert done, "被测调用没有在时限内返回"
      result = task.result()                     # 必须取结果：被测调用抛的异常在这里才会冒出来
  finally:
      release.set()                              # 放开它在等的事件或哨兵值
      await asyncio.wait({task}, timeout=3)      # 有界地等它收尾
  ```

  `asyncio.wait` 把抛了异常的任务也算进 `done`，只断言 `done` 的话，被测调用一上来就报错测试照样绿。所以要 `task.result()`（或 `await task`）把异常取出来，再对返回值做断言。

  连出口都留不出来的调用，放到能被杀掉的独立进程里设超时。
- 新建了带后台 worker 的对象的用例，一律 `try/finally` 收尾关掉它。否则策略一变，worker 不再自己退出，整场测试挂死。

## `tmp_path` 的位置和长度

- ⚠️ 不要假设 `tmp_path` 在仓库外面。`plugin/tests/pytest.ini` 的 `--basetemp=../.pytest-run` 是相对当前目录的；`unit-test-shards.yml` 把临时目录放在检出目录里的 `.codex-tmp`；`unit-tests.yml` 放在检出目录外。检出本身嵌在另一个检出里面时（worktree 放在主仓库的子目录下），「上一级」也还在仓库树里。
  - 踩法：用 `tmp_path` 造一棵「仓库外的目录」去测向上查找项目根的函数，结果向上一路走到了真实仓库根，兜底分支根本没执行。
  - 做法：让向上查找必然走不到（查找有步数上限就把夹具嵌得比上限更深），或者断言与位置无关的不变量，不断言「等于某个具体路径」。
  - 变异验证时同样要确认用例是因为实现变了才红，不是因为路径变了。
- 反过来，也有存储类用例假设数据目录在系统临时目录下面。自己把 `--basetemp` 指到别处时，这类用例可能失败（实测观察），先单独跑那个文件确认。
- 多个 pytest 进程共用同一个 `--basetemp` 会互相干扰：后启动的会清理这个目录。症状是夹具 setup 阶段整片 `PermissionError`（WinError 32），而且记成 **ERROR 不是 FAILED**。并行开多个检出跑测试时各自指定 `--basetemp`，跑完删掉。
- ⚠️ Windows 的路径上限默认是 260 字符（长路径支持默认不开）。临时目录前缀长、用例名长、再加 xdist 的 `popen-gwN`，路径就超了。症状是 `FileNotFoundError`、`OSError`，或者产品代码把 `OSError` 吞掉之后表现成别的断言失败。
  - 本机红而 CI 绿、报错里路径很长时，先 `len()` 一下完整路径，再怀疑代码。换一个短的 `--basetemp` 能过就是它。
  - 所以 `--basetemp` 要选短路径，不要指到很深的临时目录下面。
  - 产品侧：会拼进文件名的随机段、哈希、时间戳要控制长度。例子是 `services/theater/numeric_v2_archive.py` 的 `quarantined_file_name`。

## 测试里起子进程

- ⚠️ 用 `subprocess` 起 `scripts/check_*.py` 这类守卫脚本时，给子进程设 `PYTHONIOENCODING=utf-8`（现成写法见 `tests/unit/nekoclaw/test_guard_coverage.py` 的 `_GUARD_ENV`），并且断言报告里的文字，不只断言退出码。
  - 根因：输出接到管道时，子进程按机器的 ANSI 代码页编码 stdout。CI 的 Windows 机器是 cp1252，守卫脚本的报告里有箭头这类字符，`print` 抛 `UnicodeEncodeError`，进程以退出码 1 结束、stdout 是空的。退出码恰好和「发现违规」一样，只断言退出码的用例会假绿。中文系统的代码页能编码这些字符，所以本机复现不了。
  - 本地复现 CI 的情况：`PYTHONIOENCODING=cp1252 uv run python scripts/check_xxx.py <有违规的目录>`。
  - 这类「本机过、CI 红」先去下载 CI 日志看原文，不要猜。
- `subprocess.run(..., text=True)` 不写 `encoding` 时按系统区域设置编解码，带中日韩文字的输入在英文 Windows 上直接失败。显式写 `encoding="utf-8"`。
- `subprocess.TimeoutExpired.stdout` 在 POSIX 上是 `bytes`，在 Windows 上可能是 `str`（实测观察）。比较之前先归一，否则用例只在一个平台上绿。
- 起 node 一律走 `tests/node_harness.py`，见 `frontend-node-tests.md`。
- 起会导入产品模块的 Python 子进程，环境用 `child_process_env()`，见 `real-root-isolation.md`。

## 其他

- conftest 里从 `tests/clock_guard.py`、`tests/storage_root_env_guard.py` 导入的 `pytest_runtest_*`、`pytest_sessionstart` 是 pytest 按名字发现的钩子，没有显式调用点。不要当成未使用的导入删掉。
- 结构守卫要扫全仓 Python 源码时用 `tests/repo_ast_cache.py` 的 `parse_source_file()`，一个进程里每个文件只解析一次。返回的语法树是共享的，只读；要往节点上写东西的守卫必须自己另外解析一份。
- 往既有测试文件里加用例之前，先把那个文件的夹具和辅助函数区读一遍，不要手搓一个客户端。作者把判据写进夹具的 docstring，通常正是因为那个坑绊过人。

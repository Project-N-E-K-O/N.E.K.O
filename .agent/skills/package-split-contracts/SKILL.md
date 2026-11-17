---
name: package-split-contracts
description: 后端大文件拆成包之后的门面契约：测试打补丁为什么会打不中、哪些符号必须晚绑定读取、盘点 patch 面要用 AST、拆文件时要同步改哪些地方。改 main_logic/core、app/agent_server、app/memory_server、utils/config_manager、main_routers/game_router、utils/cloudsave_runtime，或者要再拆别的大文件之前读。
---

# 大文件拆成包之后的契约

这些原本是单文件的模块已经拆成了包，导入路径不变：`main_logic/core/`、`app/agent_server/`、`app/memory_server/`、`utils/config_manager/`、`main_routers/game_router/`、`utils/cloudsave_runtime/`。共同的做法是「子模块 + `_shared` + `__init__.py` 门面把原来的顶层名字重新导出」。

每个包的 `__init__.py` 开头都有一段说明自己的 patch 规则，动哪个包先读哪个包的那一段。`main_logic/core/` 的结构契约由 `scripts/check_core_contracts.py` 在 CI 里强制（脚本头部逐条列了规则）。这里写的是几个包共同的、容易踩的部分。

前端也有同类拆分（一个大 JS 拆成同目录多个经典脚本），规则见 `.agent/skills/frontend-architecture` 的 `references/chat-surfaces.md` 第 3 节和 `references/static-dir-migration.md`。

## 1. 打补丁为什么会「打不中」

拆包前，测试 `monkeypatch.setattr("包.符号", ...)` 改的就是所有代码解析这个名字的那一个命名空间。拆包后，函数的全局名字解析的是**它自己所在子模块**的字典：

- 子模块里写 `from 包 import 符号` 或 `from 属主模块 import 符号`，是在导入时拍了一份快照。之后测试改门面上的同名属性，这份快照不变。
- 最危险的是「隔离型」的桩（把磁盘 / 网络调用换掉）：桩没生效，真函数被调用，测试照样绿。

## 2. 规则

- **被测试 patch 的门面符号，消费点一律晚绑定读取。** `main_logic/core/` 的 mixin 里写 `from main_logic import core as _core_facade`，调用处用 `_core_facade.<attr>`；`app/agent_server/` 里写 `from . import _shared`，用 `_shared.Modules.<attr>`。不要 from-import 这些符号。
- **新增一个门面上的 patch 目标时，同时把它的所有读取点改成晚绑定。** 只加测试不改读取点，就是上面说的假绿。
- **会被整体重新赋值的模块级状态不放到门面上重新导出。** 门面上的名字是导入时的快照，属主一重新赋值就过期。例：`app/memory_server` 的组件单例和初始化标志、`main_logic/core/notices.py` 的 `_prominent_notice_seq`。看到门面上「缺」这些名字不要去补。只被原地修改的容器（队列、锁、字典）按引用导出是安全的。
- **patch 单例或可重绑状态，打在属主子模块上**，不打门面。例：`app.memory_server.runtime.fact_store`、`app.memory_server.gates` 里的开关。`app/memory_server/__init__.py` 的说明里列了哪些属于这一类。
- **有些东西必须留在门面模块本身**：`utils/config_manager/__init__.py` 里的 `ConfigManager` 类的组装、单例和模块级便捷函数都留在门面，因为这些函数的全局字典必须是门面模块，已有的 `patch("utils.config_manager._config_manager", ...)` 才改得到它们运行时解析的那个名字。
- **门面 `__init__.py` 里现存的 import 都是有意保留的，不要当成未使用的导入清理掉。** 测试会经由门面访问标准库和第三方名字（如 `utils.config_manager.sys.platform`、`main_logic.core` 上的 `time` / `json`）。
- **函数里的惰性 import 指向门面还是子模块，取决于测试把补丁打在哪。** `utils/config_manager/` 里对 `utils.cloudsave_runtime` 的惰性 import 指向的是包门面；改成指向具体子模块会绕过现有补丁。改之前先查测试。
- **子模块需要引用同包里更「上层」的模块时，在函数内部延迟解析**（如 `main_routers/game_router/postgame.py` 里函数内的 `from . import runtime as _runtime`），不要写在模块顶层：`__init__` 按依赖顺序导入子模块，模块级的向上引用会撞上导入到一半的包；函数内按属性访问也让补丁继续生效。
- **不要把已经改成惰性的重型 import 提回模块顶层。** 启动链路上的导入开销由 `scripts/check_startup_import_lazy.py` 守着。
- **`main_routers/game_router` 的测试打补丁统一用 `tests/unit/game_route_test_helpers.py` 的 `gr_patch_all()`**，不要在各个测试文件里自己写一份「遍历子模块逐个 patch」的辅助函数。这类辅助函数里的子模块列表是手写的，包里新增子模块时不会自动跟上，结果是悄悄漏打。`gr_patch_all()` 自己的列表也是手写的：新增的子模块如果持有被 patch 的名字，要加进去。
- **文件下移一层之后，`Path(__file__).parents[N]` 要改成 `N+1`**，`os.path.dirname` 链同理。

## 3. 盘点 patch 面：用 AST，不用按行 grep

⚠️ 拆之前要先列出「测试都 patch 了这个模块的哪些名字」。按行搜索会漏掉跨行写法：

```python
monkeypatch.setattr(
    "main_logic.core.some_symbol", stub,
)
```

拆 `core` 和 `config_manager` 时各自因此漏过两三个目标，其中有的属于「测试仍然绿、但桩没生效」。做法：

- 用 AST 或跨行正则扫 `tests/` 全部文件，覆盖字符串形式（`setattr("pkg.attr", ...)`）和对象形式（`setattr(module, "attr", ...)`、`patch.object(module, "attr", ...)`），以及通过包别名访问的写法。`scripts/check_core_contracts.py` 里有现成的采集逻辑可以参考。
- 验证补丁真的生效：把桩换成一个哨兵值，确认消费点拿到的是哨兵。不能只看测试通过。
- 抽一个门面读取点故意改回 from-import，确认有测试变红。没有变红说明这个 patch 目标没有被真正守住。

## 4. 拆文件时要同步改的地方

- 启动入口：`docker/entrypoint.sh`、`CONTRIBUTING.md` 等文档里的启动命令。包要用 `python -m` 启动。
- ⚠️ 静态检查脚本的默认扫描路径：`scripts/check_api_trailing_slash.py`、`scripts/check_async_blocking.py`、`scripts/check_no_temperature.py` 的 `DEFAULT_PATHS`。这些脚本对不存在的路径是直接跳过的，路径没跟着改不会报错，只是那部分代码从此不再被检查。
- 打包相关的登记见 `docs/contributing/nuitka-packaging.md`。

## 5. 验证拆分没有改行为的办法

- 顶层符号对账：拆分前后用 AST 逐个比对函数 / 类的源码（把已知的、有意的改动替换掉之后应当零差异）。
- 运行时身份：断言门面导出的对象和属主子模块里的是同一个对象（`is`），共享容器同理。
- 测试失败集合和主分支基线逐条对比，范围包括 `tests/` 根目录下的散测试，不只是 `tests/unit/`。跑基线的时候不要同时改同一份工作区的文件。
- 三种入口各起一次：`python -m app.X`、启动器导入、以及仍然支持的直接运行方式。
- 用词法级工具做批量改写之后跑一遍 `ruff check --select F821,F401`：Python 3.11 的 tokenize 看不到 f-string 插值里的名字，批量改名会漏掉它们。

## 6. 相关的既定做法

- `main_logic/omni_realtime_client` 和 `main_logic/omni_offline_client` 不抽共享基类。两边各自由自己的 mixin 组装，靠接口形状对称，而不是继承同一个父类。
- `scripts/check_core_contracts.py` 的规则已经稳定。评审里「再多查一层」的建议，只有在能指出真实的崩溃或误报时才改脚本；照做会误伤合法写法的不采纳。
- 新写 AST 类的检查脚本时，用 `ast.parse(path.read_bytes())` 读源码（让解析器自己处理 BOM 和编码声明），并把解析失败的捕获放宽到 `(SyntaxError, ValueError)`，否则一个带 BOM 或空字节的文件会让整个门禁崩掉。`check_core_contracts.py` 是这么写的。

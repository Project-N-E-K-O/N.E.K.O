---
name: testing-pitfalls
description: 在本仓库跑测试、写测试、写护栏测试、做变异验证时的规则和踩过的坑：只跑直接相关的测试文件、pytest 的配置和隔离手段、CI 实际跑哪些测试、前端 node 测试、提交前的门禁。跑 pytest、新增或修改测试、写源码守卫、说「测试通过」之前读。
---

# 测试：怎么跑、怎么写

环境准备和基本命令见 `docs/contributing/testing.md`。这里只写文档里没有、读代码也不容易看出来的部分。

## 只跑直接相关的测试文件

维护者明确要求过多次：不要动不动跑全量。

- 迭代中每一轮只跑**直接 import 被改模块的那一两个测试文件**。新写的用例先用完整用例名（`文件::用例名`）只跑它们；改的是测试本身时只重跑失败的（`--lf`）。
- 下面这些都算「全量」：整套 `tests/unit`、整个子目录、同一功能的四五个文件每轮一起跑、真实浏览器整组在两个浏览器上各跑一遍、每轮重跑全部变异。
- 开 PR 前跑一遍影响面：`grep -rl <被改模块名> tests/` 圈出来的文件。整套交给 CI。
- 评审意见攒一批一起改、一起跑，不要来一条跑一轮。
- 换分支、挪提交之后先 `git diff <旧> <新> --stat`，树一样就不重跑。
- 判断要跑多大：看被改的东西有没有被运行时代码 import。`scripts/check_*.py` 这类门禁脚本只被它自己的测试 import，只跑对应的 `tests/unit/test_check_*.py`。

理由：`tests/unit` 有几万条，CI 上并行跑也要十几分钟；一轮只改了几行却全量复跑，反馈周期就从几秒变成十几分钟。

⚠️ 少跑不等于只跑自己新写的。**改变既有行为之前，用被改函数的名字（不是常量名）在 `tests/` 里搜一遍**，把断言旧行为的测试找出来一起跑。

## 跑法上的硬规则

- 一律 `uv run pytest ...`，当前目录是你所在检出的根。很多静态测试靠当前目录找文件；在一个检出里跑另一个检出的测试路径，会出几十条和改动无关的失败。
- `plugin/tests` 有自己的 `pytest.ini` 和 conftest，和 `tests/` 分开跑：`uv run pytest plugin/tests`。
- 根目录和 `plugin/tests` 的 `pytest.ini` 都钉了 `--randomly-seed`，用例顺序是打乱但可复现的。⚠️ 不要加 `-p no:randomly`：pytest 会因为不认识 `--randomly-seed` 直接以返回码 4 退出，一条都没跑。要恢复定义顺序用 `--randomly-dont-reorganize`。
- 确实要跑整套时：`-n auto --dist loadfile`（和 CI 一致；不带 `-n` 要慢很多倍），加 `-v -rfE`，输出落文件，返回码单独记下来。带 `-q` 时失败的名字要等全部跑完才打印，中途卡住就什么都拿不到。
- 看到失败先单独跑那个文件，再判断是不是自己的改动。汇报如实写「几条失败、在哪个文件、单独跑是否通过」。

## CI 实际跑什么

| 范围 | 工作流 | 备注 |
|---|---|---|
| `tests/unit` 里带 `unit_fast` / `runtime` / `integration_serial` / `frontend_contract` 标记的 | `unit-test-shards.yml` | Windows，一个标记一个 job |
| `tests/unit` 里没带这四个标记的 | `unit-tests.yml` | Windows，`-n auto --dist loadfile` |
| `tests/unit/*.test.js` | `unit-test-shards.yml` 的 frontend-contract (Node) | 按通配收集，pytest 不收集这些文件 |
| `tests/frontend/*.cjs`、`static/*.test.cjs` | 同上那一步的**手列清单**，加 `unit-tests.yml` 里 React chat job 点名的几个、`frontend/react-neko-chat` 的 `npm test` | 另有一部分由 `tests/unit` 里的 pytest 用例调起 |
| `frontend/react-neko-chat` 的类型检查和 vitest、`frontend/plugin-manager` 的 vitest 和启动 e2e | `unit-tests.yml` | |
| `plugin/tests` | `plugin-tests.yml` | 在 PR 上按路径过滤，清单见 `pr-workflow` 的 `references/ci-and-gates.md` |
| `scripts/check_*.py` 静态门 | `analyze.yml` | |
| `tests/unit` 随机顺序 | `random-order-tests.yml` | 定时跑，不挡 PR |

CI **不跑**的：

- `tests/frontend/*.py`（Playwright 页面测试；只有 `test_social_hub_presence_browser.py` 被 `unit-test-shards.yml` 单独点名）、`tests/e2e`、`tests/integration`（只点名跑了 `test_voice_enrollment_duration_real.py`）、`tests/` 根目录下的 `test_*.py`。
- 没进手列清单、也没被 pytest 用例调起的 `tests/frontend/*.cjs` 和 `static/*.test.cjs`。写这份文档时这样的文件有几十个。⚠️ 新增这类文件要么加进清单，要么在 `tests/unit` 里包一条 pytest 用例，否则它从不在 CI 执行。
- POSIX 专用分支：Python 单测几乎全在 Windows 上跑，`skipif` 掉 Windows 的用例在 CI 里就是跳过的。例外是 `runtime-process-model.yml`（三个平台跑三个进程模型文件）和 `plugin-tests.yml` 里 Linux job 点名的那批。

改到 CI 不跑的范围，要本地跑并在汇报里写明跑了什么。只有 base 是 main 的 PR 才自动跑这些工作流，见 `pr-workflow` 的 `references/ci-and-gates.md`。

## 测试不许碰真实运行数据

`tests/conftest.py` 和 `plugin/tests/conftest.py` 导入任何产品模块之前先调 `tests/process_isolation.py` 的 `isolate_test_process()`：关掉用量上报、清掉继承来的存储根环境变量、把日志和 ConfigManager 的根目录指到临时目录。细节和排查办法见 `references/real-root-isolation.md`。要记住的几条：

- ⚠️ 不要在 pytest 之外用 `python -c "import <产品模块>"` 做探针。没有 conftest 的隔离，导入时就会在真实运行数据目录里初始化日志、清理旧日志。语法检查用 `python -m py_compile`，导入检查放进 pytest。
- 测试里起 Python 子进程要传 `child_process_env(root)`，子进程不跑 conftest。
- 用例不能把 `NEKO_STORAGE_SELECTED_ROOT` 及其同族变量留在环境里，也不能整进程替换 `time.monotonic` 这类标准库时钟；`tests/storage_root_env_guard.py` 和 `tests/clock_guard.py` 会让这样的用例报错。

## 改前端时最容易漏跑的两处

- 改了 `static/` 下的函数（尤其是新增或改名跨文件的 helper）：跑 `node --test tests/unit/*.test.js`。这组用例只从源码里抽几个函数放进 `vm` 跑，缺了桩就抛 `ReferenceError`。
- 改了 `frontend/react-neko-chat/src/styles.css`（包括解冲突）：跑读它的 Python 静态守卫，vitest 全绿抓不到。

做法见 `references/frontend-node-tests.md`。

## 护栏测试写完必须做变异验证

护栏测试指断言「某个不变量在代码里成立」的测试：源码扫描、AST 守卫、结构断言、差分语料。它们最常见的状态是「绿，但什么也没守住」。

- 写完把被守护的实现改坏，确认**那一条**用例转红，再还原。每条变异只对一个用例下结论。
- 变异不红只说明「没测到」，不说明「删了也对」。先查夹具有没有走到被改的分支，再查变异有没有真的落地，最后才怀疑结论。
- 十三类失效形态见 `references/guard-test-failure-modes.md`；变异怎么做、变异脚本自己的坑、差分和派生测试的盲区见 `references/mutation-testing.md`。
- 修复和护栏本身也可能不生效或者更危险，改完之后的自查见 `references/self-review-after-fix.md`。

## 提交前

- ⚠️ `scripts/check_docstring_no_cjk.py`、`check_prompt_zh_tw.py`、`check_i18n_sync.py` 是和 base 比差异的门：它们看的是 `git diff <base>...HEAD`，**只看已提交的内容**。先 `git commit` 再跑，没提交就跑等于没检查。有问题补一个提交再推。
- docstring 一律英文（`plugin/`、`local_server/` 除外）。只要这次改动碰到了某条含中日韩文字的 docstring，哪怕只是补一句，整条都要改成英文。中文例子放行内注释，注释不受这道门管。被测内容本身就是中日韩文字时用 `# noqa: DOCSTRING_CJK`。
- ⚠️ 验门禁和测试时不要把输出接到 `tail` / `head` 再用 `&&` 串提交：拿到的是管道最后一段的退出码。固定写法见 `pr-workflow`。

## references

- `references/pytest-traps.md`：写 pytest 用例，或者排查「单独跑绿、换个跑法红」时读。`caplog` 顺序依赖、后台线程泄漏、模块级 `asyncio.Lock` 绑定事件循环、进程级单例、并发用例怎么强制交错、`tmp_path` 的位置和 Windows 路径长度、子进程编码。
- `references/real-root-isolation.md`：碰存储根、ConfigManager、日志目录、用量上报相关的测试，或者怀疑测试写进了真实数据目录时读。
- `references/frontend-node-tests.md`：写或改前端的 node 测试、静态源码测试、页面测试时读。静态测试抓不到并发缺陷、node harness 挂住会以 0 退出假绿、node 用例超时怎么分诊。
- `references/guard-test-failure-modes.md`：写源码守卫、结构测试、容量护栏之前读。
- `references/mutation-testing.md`：做变异验证、写变异脚本、用差分语料或派生数据下结论之前读。
- `references/self-review-after-fix.md`：修完一个缺陷、加完一条护栏，准备说「修好了」之前读。

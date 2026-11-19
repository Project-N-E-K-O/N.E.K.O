# agent runtime（`nekoclaw/runtime/`）里不显然的规矩

接口和机制在设计稿 `docs/design/nekoclaw-rewrite.md` 第 7、8 节，落地出入在 8.9 节。下面是那里没有的，或者评审里反复被提、已经定过的。

## 移植来的循环尽量不动

- `nekoclaw/runtime/loop/` 是移植来的核心循环（文件头有来源提交号，目录里留着上游的 LICENSE）。不跟上游；想要上游的某个修复，对着提交手工挑。
- 我们对循环本身的改动只有设计稿 8.2 节那两处（工具运行中的实时进度、只读工具并行）。别的需求在外层做。例子：过程文件末尾半条记录的恢复放在 `nekoclaw/runtime/session.py` 的 `TranscriptStorage`（`JsonlSessionStorage` 的子类）里，没有去改 `loop/session/`。
- 项目锁在 Python 3.11（`pyproject.toml`）。上游用了 3.12 的 `type` 语句，挑代码时要改写。

## 模型给的东西不能卡住进程

一个运行时进程里跑多个会话，共用一个事件循环。

- 模型写的正则（`grep`、`glob`）一律用 `regex` 包带 `timeout=` 匹配，不用标准库 `re`。`re` 匹配时一直占着解释器锁，一个回溯爆炸的模式会卡住这个进程里的全部会话；`regex` 匹配时放锁，并且能超时。
- ⚠️ 写超时测试用 `(a|aa)+$`。`(a+)+$` 在 `regex` 包里触发不了超时（实测观察）。

## 目录搜索不穿过链接

`glob`、`grep` 按目录搜索时不穿过符号链接和目录联接（junction）：这次调用是按它点名的目录过权限的，链接可以指向任何地方。

判 junction 不能靠 `DirEntry`：Python 3.11 下 junction 的 `is_symlink()` 是 False，`is_dir(follow_symlinks=False)` 是 True。要看 `st_reparse_tag`，而且只认 `IO_REPARSE_TAG_SYMLINK` 和 `IO_REPARSE_TAG_MOUNT_POINT` 两种。不要按「带 reparse point 属性」判，网盘的占位文件、去重文件也带这个属性。见 `tools/search.py` 的 `_is_link_stat`。

## 多个任务等同一个 future

要隔着 `asyncio.shield`。不隔的话，其中一个等待者被取消会把共用的 future 一起取消，别的等待者跟着失败。会话的打开和关闭（`core.py`）都是这样等的；新加「大家等同一件事做完」的地方照这个写。

## 上下文：裁剪、摘要、落盘

四层机制见设计稿 8.5 节。下面几条是定过的取舍，同类评审意见再来按这里回。

- **摘要一替换完对话就落盘，中间不能有 `await`**。`ContextManager.fit` 的 `on_compaction` 回调在「摘要替换掉旧消息」之后、下一个 `await` 之前调用。之后的步骤（再量一次、第二次摘要）可能被取消，那时内存里的对话已经建在摘要上，磁盘上却没有。
- **裁旧工具结果、丢旧图片有意不落盘**。过程文件保留完整结果，只有摘要落盘；被裁的行保留条目 id，摘要引用的 id 一直有效；会话重开后在第一次请求前会重新裁。「让裁剪也能重放」这类建议不采纳。已知代价（按代码推断，没实测）：溢出后强制裁掉的最新一批结果，重开后恢复原样，第一次请求可能再溢出一次再恢复。
- **多块文字的工具结果只分两份**：第一块，和后面所有文字合在一起（`context.py` 的 `first_block_and_rest`）。限长（`cap_tool_result`）和裁旧结果（`prune_result`）都按这两份来。原因：能力网关把「一组内置工具的说明和定义」放在结果的第一块，页面内容跟在后面；并成一段处理，页面一长就把定义裁掉了。两种别的做法都试过，不要改回去：
  - 并成一段：定义会丢。
  - 每块各留一份额度：块数没有上限，总量会失控。
- **单个结果的限长取 `max(result_cap, prune_threshold_tokens)`**：不超过 `prune_threshold_tokens`（2048 token）的结果不限长，窗口再小也整块到。原来的限长是窗口的四分之一，窗口小于 8192 时比 2048 小，单独加载的一组工具会被切。多块时，后文拿第一块剩下的（至少 256 token）；不给后文也保 2048 的底，小窗口下一个结果会到 4096、装不下。
- 网关那边「一组交出去的文字不超过 `GATEWAY_TOOL_GROUP_MAX_TOKENS`」必须不大于 `prune_threshold_tokens`，`tests/unit/nekoclaw/test_builtin_tool_budget.py` 守着。改这两个数里的任何一个都要看另一个。

## 运行时不认识 neko 的别的子系统

`nekoclaw/runtime/` 只用 `config`、`utils`（分层守卫里有禁边）。它跑在自己的子进程里，要保持小，也不碰别的子系统的数据。需要插件、记忆、主对话的东西，一律做成宿主回调（`nekoclaw/runtime/host.py`），实现放在 `nekoclaw/host/`。没给某个回调，对应的工具就不出现。

## 量 token 时两边要同一个工作目录

工作目录写在 task agent 的系统提示里（`prompt.py` 拼的环境段）。做改动前后的消耗对比时，两组要用同一个工作目录（跑测试脚本时就是同一个 `--basetemp`）。目录不同，每次请求会差几十 token，两组数就不是同一个条件，差出来的请求次数、耗时也不能算到改动头上。

## 会话沿用打开时的工具定义

会话打开时把系统提示词和工具定义存进 `system.json`，再打开时沿用存下的那份，到下一次摘要才换成现在的；执行用的一直是现在的实现（`session.py` 的 `_adopt`）。所以给已有的工具**加必填参数**以后，旧会话的模型没被告知过这一项，调用会在参数检查那一步报 `missing required`，直到下一次摘要。

产品还没上线，没有要照顾的旧会话：加必填参数就照常加，执行时照样强求，不为旧会话留放宽（owner 2026-11-19 定；PR #3760 曾为 `note_lesson` 的 `confidence` 加过放宽，按这个决定去掉了）。上线以后再遇到，先问 owner。

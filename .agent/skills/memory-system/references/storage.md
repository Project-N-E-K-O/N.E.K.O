# 记忆的落盘、云存档、冷存、外部导入

事件循环里不许同步读写文件的总则见 neko-guide「单进程 + 事件循环零阻塞」。事件日志见 `docs/design/memory-event-log-rfc.md`，同步和冷存见 `docs/design/long-term-memory-sync-v3.md`。

## 1. 角色记忆目录

- **所有读者只看运行时根。** facts、persona、settings、reflection、各个 sidecar、时间索引库，全部经 `memory.ensure_character_dir(memory_dir, name)` 解析路径。`utils/character_memory.py::iter_character_memory_roots` 返回两个根（运行时根和项目目录下的种子根），但种子根只用于枚举、存在性判断、删除 / 改名的记账，以及启动时的迁移。
- ⚠️ 所以「某功能在种子根下的数据读不到」时，**不要给那个读者加种子根回落**。那会让它成为全仓库唯一认种子根的 store，同样搁浅的兄弟文件一个也修不到。正确方向是让数据到达运行时根（修迁移）。反过来，凡是用 `character_memory_exists` 列出角色的界面，都可能列出一个任何读者都打不开的角色。
- 迁移（`utils/config_manager/migrations.py::migrate_memory_files`）是先拷到同盘的暂存目录再原子改名：目标要么完整要么不存在。**已存在的目标不动，也不补缺**：运行时根里缺某个文件不代表迁移没做完，云存档导入和用户删除都会有意移除文件，补缺会让被删的数据每次启动都复活。
- ⚠️ `ensure_character_dir` 会建目录。只读路径不要调它（`PinnedFactStore._file_path` 就专门分了读路径和写路径）；角色改名或删除之后，任何晚到的写入经它都会把旧目录重建出来。写入前要查两样：`utils.character_memory.is_character_write_fenced`（只是改名过程中的临时围栏，改名结束就解除，删除不设它），以及这个名字是不是已经退役（删除走的是退役）。已退役且目录不在时不要调 `ensure_character_dir`。`memory/anti_repeat.py` 的 `_write_file_path` 两样都查，可以照着写，但要连同加锁一起照搬：它自己不加锁，调用方是在按角色名的写锁（`_get_write_lock`）里调它的，`retire_character` 设退役标记时拿的是同一把锁。检查退役和建目录不在这把锁里做，删除过程中仍然可能把目录建回来。新的带缓存的 store 还要在 `_CHARACTER_RUNTIME_CACHE_EVICTORS` 里登记，删除路径才退役得到它。后台任务按 SKILL.md 清单登记成可取消的。
- **给存量角色补字段不要放在 `load_characters` 里写盘。** 启动引导期种子出来的 `characters.json` 一旦被加载路径改写，引导逻辑会认为目标已有用户数据，跳过旧数据根的导入，老用户升级后旧数据导不进来。补字段做成显式步骤，放在启动链路里云存档引导和启动导入之后（现有例子：`app/main_server/__init__.py` 里的 `abackfill_character_uids`）。另外云存档指纹含整个角色 payload，补字段会让已上云的角色显示成「已分叉」，动之前把这个影响算进去。

## 2. 写盘

- 整份 JSON 的替换用 `utils/file_utils.py` 的 `atomic_write_json` / `atomic_write_text`（临时文件、fsync、replace），异步路径用 `*_async`。
- 审计「哪里在写盘」时别只搜 `json.dump`，要搜写盘的出口：`open(..., "w"/"a")`、`write_text`、`write_bytes`、`fdopen`。两行式的 `dumps` 再 `write_text` 最容易漏。
- 一次变更跨两个文件时，先想清楚中断在中间留下什么，按「留下两份」而不是「两边都没有」来排顺序（例：先写归档再写 `facts.json`）。跨文件的状态迁移走事件日志的 `record_and_save`。
- 多文件要一起替换时，换不变量，不要一层层补回滚：让每次写入用唯一文件名，把其中一个文件的写入当成唯一提交点。提交前失败旧的一套完整，提交后新的一套完整，没有需要回滚的东西。
- `scripts/check_async_blocking.py` 只做一层的传递检测，而且按**名字**匹配：经过两层同步 helper 的阻塞它看不见，「守卫是绿的」不等于合规；新写的同步 helper 起名要避开已有的阻塞函数名，否则被误报。

### 把同步落盘挪进线程时

把落盘挪到 worker 线程，等于拆掉一条没人写下来的保证：「这些步骤都在事件循环线程上，彼此不会交错，中间不会被取消」。会出的问题：

1. **取消窗口。** 新加的 `await` 落在「内容已提交」和「收尾信号 / 返回值」之间。`CancelledError` 是 `BaseException`，`except Exception` 接不住，用户已经看到的回复没有结束信号，或被记成没投递、之后再发一遍。
2. 原本中间没有 `await` 的几步写入被拆开，取消后只做了一半。
3. 两个 worker 真的并发，交错成「B 的数据配 A 的元数据」。需要按资源加锁。
4. 读-改-写事务被拆开；锁要覆盖到内部的重读。
5. 内存里的更新不要跟着一起挪。只挪 fsync；内存更新留在调用线程，否则循环上紧接着的读者读到旧值。

⚠️ **锁会把阻塞传回事件循环。** worker 持着一把 `threading.Lock` 做 fsync 或退避时，事件循环上只要有代码去抢同一把锁，阻塞就原路回来。做法：

- 同一把锁的所有入口要么都在 worker 里，要么都不在。
- 数据锁只覆盖内存操作，落盘用第二把只给写者的锁加序号（`memory/anti_repeat.py` 是现成例子）。
- 读路径不拿锁；需要自愈写时，写者在锁内重读一次再写。

**每轮回复都要做的尽力而为落盘**同时受两条约束：内存更新必须在收尾信号之前（客户端看到回合结束就可能发下一条），落盘的等待必须在之后。靠调顺序无解，要拆成同步的 `stage_output()`（无 `await`，不是取消点）和之后的落盘。从「用户已经看见」到「向调用方报告已投递」之间**一个挂起点都不能有**，所以这一段用 `flush_staged_detached()`：同步返回，内部建任务并持强引用到完成，没有运行中的循环时直接放弃而不是就地同步 fsync。现有调用点：`main_logic/core/proactive.py`、`main_logic/core/greeting.py`、`main_logic/omni_offline_client/_lifecycle.py`，要改一起改。守护在 `tests/unit/test_anti_repeat.py`（按 AST 找 `await`，不是子串匹配）。

## 3. 新增记忆文件：进不进云存档

白名单是 `utils/cloudsave_runtime/_shared.py::MANAGED_MEMORY_FILENAMES`。快照的哈希、上传、删除、恢复都遍历它，加一项就全覆盖；可选文件在各遍历处按存在与否跳过。

判据：

| 文件性质 | 进白名单吗 | 现有例子 |
|---|---|---|
| 用户说过的、重算不出来的 | 进 | `pinned_facts.json`、`habitual_phrase_overrides.json` |
| 能从别的数据重算 | 不进 | `habitual_phrases.json`、`behavior_notes.json` |
| 某份白名单数据的幂等账本 | 和那份数据一起进 | `external_import_state.json` |
| 冷存 | 不进（守护：`tests/unit/test_cold_store_guard.py`） | `fact_cold/` |

- ⚠️ **账本和数据同生共死。** 导入的「这一天处理过了」指纹如果单独放一个不随云存档同步的文件，`facts.json` 被云端恢复或多设备回滚成旧版后，残留的指纹会永久压住那一天的重新导入，已导入的记忆就丢了。所以：有事实落盘的天，指纹放在事实自己的 provenance 里，跟着事实一起回滚；sidecar 只承载「重做也无害」的情况（模型判定完全没有事实的天）。「抽出的事实全被去重掉」的天不记账、每次重抽：既有事实在就什么都不加，被回滚掉了就自然补回来。
- ⚠️ **往白名单加文件名有混版本窗口。** 旧客户端导入快照时，遇到白名单之外的记忆文件路径会整包拒绝（`unsupported cloudsave memory path`）。新版本的快照在没更新的客户端上不可恢复，直到它更新。这是有意的失败即拒绝；改成「跳过不认识的文件」会让残留账本的问题复活。加之前想清楚，并在发布说明里提示。
- 有进程内缓存的 store，云存档应用改写文件后要有失效钩子；每次都读文件的 store 不需要（白名单注释里逐项写了）。

## 4. 冷存

规则见同步文档第 10 节和 `memory/cold_store.py` 的 docstring。补几条：

- `cold_store` 的函数第一个参数是**角色目录**（`memory/<角色>`），不是角色名。
- 冷存正文只有恢复、导出成文件、隐私删除可以读；其它调用方只拿不带正文的桩。读正文的调用点由 `test_cold_store_guard.py` 的名单管着。
- 同一个键在冷存和活跃区都有时冷存优先（云存档恢复旧版会造成这种并存）。
- 恢复私聊事实一律用新 id，沿用旧 id 会在下次合并时被名单重新挪回冷存。
- 被反思吸收、因为满 7 天挪进归档的事实不是冷存，它们照旧给召回用。

## 5. 外部记忆导入

入口 `memory/external_markdown_import.py`、`memory/external_molili_import.py`、`memory/persona/fusion.py`，逐日提取在 `FactStore`（docstring 写了幂等和上限的全部规则）。容易被改坏的几条：

- **整天原子落盘。** 一天的内容超过单次输入预算会拆成几批调用；任何一批失败，这一天整天不写、不留指纹。早的批次先落盘、后面失败，重试时这一天会被指纹整天跳过。
- 预览和提交用**同一个**分批函数（`batch_daily_fragments`），估出来的调用次数才不会和实际执行漂移。
- 人格融合的指纹是集合语义（排序后存）；逐日指纹**保序**，因为日记是叙事，重排就是内容变了。两者不对称是有意的。
- 逐日事实的去重键带事件日期：同一天重试幂等，不同日期的重复事件各自落盘；字面相近的命中跨日期放行、同日期仍挡。
- 人格融合里调模型的阶段不持角色锁，前后两个阶段持同一把锁，落盘前核对指纹集合没被并发改过。
- 模型返回的不是数组（例如包了一层对象）时，导入路径按失败处理、这一天可重试（`treat_malformed_as_failure=True`）；对话提取路径不传这个参数，保持容忍。
- sidecar 的写是尽力而为：磁盘错误只告警，不把这一天升级成失败。
- 单次后台模型调用的硬超时是 `MEMORY_LLM_HARD_TIMEOUT_SECONDS`，它必须小于上游代理的硬断时间，不要调大。

## 6. 事件日志

设计见 `docs/design/memory-event-log-rfc.md`。当前实际注册了哪些重放处理器以 `memory/evidence_handlers.py::register_evidence_handlers` 为准。日志里出现没有处理器的事件类型时，那个角色的重放停在那里不再往后，所以新增事件类型必须同时注册处理器。

## 7. 拿真实数据做验证时

- 用真实数据的副本当运行根跑脚本前，先关掉旧数据根的迁移。实测观察：不关的话，真实根会被当成旧数据根，角色记忆被整个搬进副本。
- 单元测试里凡是会经 `ensure_character_dir` 的路径都要隔离到临时目录，漏一条就会在真实运行根下建目录或写文件。

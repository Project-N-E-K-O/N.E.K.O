# 防复读效果存储（`memory/anti_repeat_effects.py`）的不变式

这是后端按角色落盘的一份统计 sidecar，记录防复读的效果。改它之前这几条必须知道。细节和实测数字都写在该文件各方法的 docstring 里（`_copy_payload`、`_write_file_path`、`_prune`、`_flush_snapshot`、`evict_character` / `revive_character` / `retire_character`），这里只列结论。同目录的 `memory/anti_repeat.py`、`memory/startup_greeting_history.py` 是同一种形状，规则通用。

## 1. ⚠️ 跨锁边界一律拷贝，不共享嵌套 dict

落盘（`_flush_snapshot`）在工作线程里序列化，只持有写锁；改缓存（`_apply_decision`、`_prune`）持有的是另一把按角色的锁。两把锁不相交，所以任何被两边共享的嵌套 dict 都可能在被遍历时被修改，轻则写出撕裂的文件，重则抛 `RuntimeError: dictionary changed size during iteration`。

- 暂存快照用 `_copy_payload()`：按固定结构手写的完整拷贝。实测它比 JSON 往返和 `copy.deepcopy` 都快，所以「没改动的子结构直接复用」这类优化建议不采纳——复用就是共享。
- 查询路径（`query_effects`、`query_effects_for_responses`）在锁内用 `_copy_bucket()` 拷出要汇总的桶，汇总在锁外做。锁内还要把需要的标量一起读出来。

## 2. 退休的名字只能写进已存在的目录，永不创建目录

角色被删除或改名走时会先「退休」这个名字（`retire_character`），然后才删目录。序号栅栏只能挡住退休之前暂存的快照；退休之后、删目录之前记录下来的数据如果照常走「目录不存在就创建」，会把刚删掉的目录建回来。

- 退休的名字：目录在才写，目录不在就放弃写入。
- ⚠️ 「目录还在」不能推断「这个角色又活了」——删除流程是先退休、后删目录，中间那一段目录还在。只有明确知道这个名字是活的调用方才能解除退休：`evict_character`（改名的目标、云存档导入等文件被整体替换的情况，同时丢缓存）和 `revive_character`（文件没被替换的情况）。`revive_character` 对没退休的名字什么都不做，缓存和栅栏都不动；对已退休的名字，它先丢掉缓存和暂存的快照、抬高序号栅栏，再解除退休，因为那些数据属于已经删掉的旧身份。
- 未退休的名字照常懒创建目录，和其它记忆写入方一致。仓库里没有任何地方在创建角色时预先建目录。
- 新增一个会为「被复用的名字」重新创建 `memory/<name>/` 的流程时，必须先解除退休（经由丢缓存的那条路径）再写。这一点没有被代码强制，靠各调用方遵守。
- 新增一种带缓存的按角色 sidecar 时，要在 `utils/character_memory.py` 的 `_CHARACTER_RUNTIME_CACHE_EVICTORS` 里登记驱逐、退休、复活三个函数，缺一个都会留下上面的窗口。

## 3. 淘汰时要保护调用方正在写的那个桶

`_prune()` 按容量淘汰时优先保留「已投递」的响应桶。新建的桶还没投递，排在淘汰顺序最前面。存量收敛到全部已投递之后，每一轮都会把自己刚建的桶删掉，后面标记投递时找不到它，响应和消息的关联从此断掉。

- 所以 `_prune()` 接受 `protect_response_key`，调用方把自己正在写的桶传进去。保留期扫描也要跳过它。
- 改淘汰策略时保留这个参数的语义。

## 4. 分析输入超预算时收窄，不报错

`utils/natural_expression_candidates.py` 的用户回看分析有两道闸：输入字符数和 n-gram 出现次数。展开量取决于按标点切段之后的段长，不是总字数——没有标点的长文本字符数远没到上限，出现次数已经爆了。

- 超预算时收窄分析窗口（先丢最旧的消息，必要时再截短单条），而不是抛错。抛错会变成前端的「请重试」，而重试永远失败。具体的收窄顺序见 `build_user_review_report()` 的 docstring。
- 返回的 `summary` 里用 `analyzed_message_count` / `messages_truncated` 报告实际分析了多少。
- 分析窗口始终是消息列表的后缀（保留最新的）。路由侧按这个假设切消息 ID，改收窄方式时要一起改。

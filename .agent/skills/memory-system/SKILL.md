---
name: memory-system
description: 记忆系统（memory/、app/memory_server/、config/prompts/prompts_memory.py）里读代码不容易看出来的不变式、已定取舍和踩过的坑，以及「前台不阻塞」「启动链路不阻塞」两条全局原则。改记忆流水线、记忆类提示词、记忆落盘 / 同步 / 云存档、/new_dialog 开场链路，或处理这些地方的评审意见之前先读。
---

# 记忆系统

## 先读已有文档，这里不重复

| 想知道 | 读 |
|---|---|
| 各层、写入链路、召回、强力记忆开关、文件清单 | `docs/architecture/memory-system.md` |
| 证据分数、衰减、档位、归档 | `docs/design/memory-evidence-rfc.md` |
| `events.ndjson` 写入契约、重放、压缩 | `docs/design/memory-event-log-rfc.md` |
| 长期记忆同步、提取进度、整理权、旧重复清理、冷存 | `docs/design/long-term-memory-sync-v3.md` |
| token 预算和守门脚本 | `docs/design/llm-prompt-budget.md`、`.agent/rules/neko-guide.md` |
| 阈值、间隔、预算的取值和理由 | `config/memory_settings.py`、`config/turn_context_settings.py` 里每个常量的注释 |

`memory/pinned_facts.py`、`memory/introspection.py`、`memory/behavior_notes.py`、`memory/cold_store.py`、`memory/fact_dedup.py`、`memory/script_fold.py` 的模块 docstring 就是各自的设计说明，动之前通读。

## 各层放什么

- **facts**：具体的事。发生过的事件只放这里。
- **reflection**：概括出的关系和相处方式。
- **persona**：性格和偏好，是抽象的。它由两人的关系塑造，但记下的应侧重角色自己的性格和反应。
- **常驻区（pinned facts）**：用户要求记住的、有日期的安排、健康安全、身份变化，外加角色自己许下的承诺。到期就离开，只在私聊渲染。
- **行为提醒（behavior notes）**：对「她实际怎么说话」的提醒，独立文件，不是 persona 条目。

写或改人格 / 反思类提示词（合成、升格合并、精炼、自省、纠正）时，判「空洞」看有没有信息量，不看有没有具体事件。细则见 `references/layers-and-prompts.md`。

## 贯穿全局的原则

1. **只有用户参与过的内容才进记忆。** 提取计数只在用户发言时累加，没有用户发言的窗口直接跳过。这是设计，不是漏洞；评审意见说「纯主动搭话的会话走不到提取」时不要当缺陷修。见 `references/facts-pipeline.md`。
2. **记忆由流水线驱动，不给对话模型写记忆的工具。** 后台任务用便宜档模型、在空闲时跑。也不做面向用户的事实 / 反思 / 人格查看、编辑、回滚界面：记忆浏览器只编辑 `recent.json` 是有意的。不要把「记忆可视化编辑 / 存档点」当改进项提出来。
3. **防自我强化。** 角色自己说的话不能当成用户特点的证据；强化值有上限；提示词禁绝对化措辞。任何新加的「模型自己总结自己」的路径都要有证据门槛、过期时间和复查。见 `references/reflection-persona.md`。
4. **前台对话不被记忆服务阻塞**，启动链路不被次要子系统阻塞。见 `references/foreground-and-startup.md`。
5. **拼进 `messages` 的文本按 token 预算截断**，不用字符切片。⚠️ `scripts/check_llm_budget.py` 不扫 `config/`，提示词拼装函数放进 `config/prompts/` 后要自己核对截断单位。见 `references/layers-and-prompts.md`。

## 动代码前对一遍

- ⚠️ **新增读取 persona / reflection 的路径**（渲染、召回、候选池、升格、精炼、同步导入）要排除 `memory.introspection.is_permanently_suppressed`。漏一处，被自省永久压制的条目就从那条路回到模型眼前。
- ⚠️ **只该处理私聊旧数据的路径**用 `memory.scopes.is_legacy_private_entry()` 判断，不要用「subject 为空」。subject 有三态（没有 / 完整 / 损坏），判空会把损坏的行放进私聊人格。
- ⚠️ **改 `pinned_facts.json` 里某条的内容**（包括别的子系统来改）要给那条的 `rev` 加一。入区决策不持锁调模型，靠 `rev` 发现「我看到的那版已经变了」。只记账的字段（`surfaced_at`、`mentioned_at`）不加。
- **新增记忆目录下的文件**要决定它进不进云存档白名单 `MANAGED_MEMORY_FILENAMES`（`utils/cloudsave_runtime/_shared.py`）。判据和混版本代价见 `references/storage.md`。
- ⚠️ **幂等账本必须和它守护的数据在同一个同步 / 回滚单元里。** 账本活得比数据久，数据被回滚后账本会永久压住重做。能从数据现算的就不要另存账本。
- **不可逆的丢弃只认逐字相同。** 归一化（繁简折叠、去停用名、去标点、大小写、token 集合）可以用来找候选，不能用来判「是同一句」并据此丢数据。反例见 `references/facts-pipeline.md`。
- **新的事件类型**要同时有生产者和已注册的幂等重放处理器（`memory/evidence_handlers.py::register_evidence_handlers`），否则重放停在那条事件上。
- **`memory/` 和 `utils/` 共用的东西放 `utils/`**，由 memory 去 import。分层门 `scripts/check_module_layering.py` 对函数内 import 一视同仁，没有逐行豁免；定向测试照不到它，改了跨包 import 就单跑一次这个脚本。
- **按角色派生的后台任务**（自省、常驻区补查这类）要在 `review._get_review_spawn_lock` 下检查 `_retired_derived_task_names` 并重读角色名单，再登记进 `app/memory_server/review.py` 的任务表。直接 `await` 的话，角色改名或删除后任务收尾写盘会经 `ensure_character_dir` 把旧目录重建出来。守护测试：`tests/unit/test_recent_compress_backup.py::test_every_memory_server_background_spawn_is_drainable`。

## references

| 文件 | 什么时候读 |
|---|---|
| `references/layers-and-prompts.md` | 写或改记忆类提示词；给提示词加截断；想加记忆相关的用户界面 |
| `references/facts-pipeline.md` | 动事实提取、去重、仲裁、纠正、归档、繁简折叠；评审说「某类会话提取不到事实」 |
| `references/reflection-persona.md` | 动反思合成、升格、证据来源、永久压制、每周自省、行为自省 |
| `references/pinned-facts.md` | 动常驻区、承诺、承诺进主动搭话 |
| `references/scoped-memory.md` | 动群聊 / 群成员 / 跨群的人的记忆，或 `/scoped_context`、`/query_memory` 的 subjects |
| `references/storage.md` | 新增记忆文件、动落盘 / 云存档 / 冷存 / 外部导入 / 角色目录解析 |
| `references/foreground-and-startup.md` | 动 `/new_dialog`、`/renew`、`/settle`、热重置、`cross_server` 的记忆写入，或往启动链路上加东西 |
| `references/recall-and-tool-leak.md` | 动 `recall_memory` 工具、回复前的被动注入；排查「工具调用被当成文字念出来」 |

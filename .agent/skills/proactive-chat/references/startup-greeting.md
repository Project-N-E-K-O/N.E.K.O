# 开屏问候：去重窗口、角度轮换、时段提示

代码：`main_logic/core/greeting.py::trigger_greeting`（流程）、`main_logic/startup_greeting_policy.py`（选择和抑制策略）、`memory/startup_greeting_history.py`（已投递问候的历史）、`config/prompts/prompts_proactive.py`（模板、`_TIME_OF_DAY_HINTS`、`get_startup_greeting_guidance`）。

## 触发条件（按顺序）

1. 距上次对话不到 `_STARTUP_GREETING_MIN_GAP_SECONDS` 不问候。
2. 突发抑制：`_STARTUP_GREETING_BURST_SECONDS` 内已经问候过就不再问候，覆盖反复启动、多窗口重连。**用户在上次问候之后说过话时，这一条不生效。**
3. 语音会话在跑或正在启动时不问候（查询记忆等每个 `await` 之后都要重查）。
4. 没有文字会话就先起一个；然后 `try_start_proactive` 占位。问候和 `/api/proactive_chat` 共用同一个会话状态机，互斥由这里保证。
5. 投递前原子预约（`try_reserve`），防止几个错开启动的任务拿着同一份空历史各问候一次。

只有真正投递给用户的文本才进历史（提交回调里写），预览、失败、被抢占的都不算。

## 两层去重窗口

普通防复读的前景窗有效期比问候的最小间隔还短，管不了「今天开了三次，三次问候长得一样」，所以问候有自己的历史和两层窗口：

| 窗口 | 常量 | 作用 |
|---|---|---|
| 召回窗（较长） | `_STARTUP_GREETING_RECALL_SECONDS` | 这段时间内的开场白进 prompt 当「要明显不同」的参考；用过的记忆话题在这段时间内不再用 |
| 严格窗（较短） | `_STARTUP_GREETING_STRICT_SECONDS` | 这段时间内的开场白禁止复述或近义改写；开场角度在这一层里轮换 |

一次读满召回窗，再用 `split_startup_history_windows` 在内存里切成两层，不读两次盘。时间戳在未来的记录（时钟回拨）归到严格层，往收紧的方向偏。

⚠️ 两个窗口的用法方向相反，不要统一：

- **开场角度轮换只看严格层。** 通用角度一共就几个（`_STARTUP_GREETING_GENERIC_VARIANTS`）。拿召回窗去轮换，第一天就把它们全用完了，之后 `_select_startup_greeting_variant` 只能落到最后的顺序轮转，等于没有轮换。
- **记忆话题的冷却看整个召回窗。** 同一个记忆话题被再次提起，比复用一种开场句式更像复读。

## 容量和截断的三个坑

- ⚠️ **历史条数上限 `_MAX_RECORDS` 要按「召回窗 ÷ 最小触发间隔」算，不是「召回窗 ÷ 突发窗」。** 突发抑制在用户说过话之后失效，这时相邻两条问候之间只隔一个最小触发间隔。按突发窗估算会少一半，召回窗最早的那部分会在还有用的时候被挤掉。有测试盯着这个不等式。
- ⚠️ **按条数封顶，不按时间裁。** 这个模块明确不按墙上时钟排序（时钟回拨时，最新提交的仍然必须是最新一条），所以时间戳不能作为「该不该丢」的依据。
- ⚠️ **参考区块按字符数封顶，不按 token 预算。** 这是「拼进 prompt 的文本一律过 token 预算」规则的有意例外：这段代码跑在事件循环上，冷启动时同步初始化分词器会卡住问候。`_select_startup_followup` 里是同样的取舍。两层各自还有条数上限（`_STARTUP_GREETING_STRICT_SAMPLES` / `_STARTUP_GREETING_EARLIER_SAMPLES`）。

## 没有做「相似就拦下重写」

问候只靠 prompt 引导和角度轮换，没有在出口加相似度硬闸。主动搭话的硬闸踩过空闲死锁（见 `anti-repeat.md`），问候不重蹈。要加的话先想清楚被拦下之后窗口靠什么前进。

## 时段提示的写法

`_TIME_OF_DAY_HINTS` 按时段给模型一句提示。判据：

- **保留客观的时段特征和可以用的搭话方向**：夜深了、天刚亮、通常是午饭时间、天正在暗下来、夜里的氛围。
- **去掉对用户状态的断言**：刚睡醒、还没睡、吃过了、刚下班、忙了一天、该睡了。
- **本来就没有特征的时段（上午、下午）只写一句「现在是上午」**，最省 token。
- 夜晚的休息话题只跟不提：近期对话里明确提到休息才顺着聊。

理由：时段是让开场白不一样的最便宜的天然来源。如果把提示写成纯否定式的禁令（「不要推断 X」），模型没有可用的方向，会退回最通用的说法，结果是最雷同。改这些文案时八种语言一起改，并保持「特征 + 方向 + 不断言状态」这个结构。

## 和主动搭话的关系

- 问候投递成功后也写防复读语料（标成主动内容）和问候历史；两处都是先同步改内存、落盘放到后面且不等待，避免「用户已经看到了，但取消点落在记账之前，导致同一条问候又有资格再发」。
- 问候是一轮可见的 AI 发言，会让主动搭话暂存的截图失效（见 `screenshot.md`）。
- 新连上的聊天窗口发 `greeting_check` 时，后端顺带把凝神的指示状态补推一次（见 `focus.md`）。

相关测试：`tests/unit/test_startup_greeting_selection.py`、`tests/unit/test_startup_greeting_history.py`、`tests/unit/test_startup_greeting_prompts.py`、`tests/unit/test_startup_greeting_delivery.py`。

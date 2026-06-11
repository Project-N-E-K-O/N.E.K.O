# Subagent Handoff Protocol (L33 子协议)

主 agent 派 subagent 并行任务时的**固定交付点**. 解决 "wait 不能用 + 靠猜 +
可能错过 subagent 报告" 的 meta-bug.

## 目录约定

```
_subagent_handoff/
  <task-id>.json   # 结构化报告 (schema 见下)
  <task-id>.DONE   # 空文件, subagent 全部完工后才 touch
```

`<task-id>` 由主 agent 在派发前决定并写进 prompt, 例如 `ui-layout-r3` /
`avatar-context-contract`. 同名冲突就加序号 (r3a / r3b).

## Subagent 必须做的事

1. 完成任务 (改代码 / 跑 smoke / ...).
2. 产生 **json 报告**, 写到 `_subagent_handoff/<task-id>.json`, schema:

```json
{
  "task_id": "ui-layout-r3",
  "status": "ok | fail | partial",
  "summary": "一句话摘要, <200 字",
  "root_cause": "如果是 bug 修复, 说明根因; 否则空串",
  "files_changed": ["tests/testbench/static/x.css", "..."],
  "lints_clean": true,
  "smoke_run": { "passed": 11, "total": 11, "elapsed_s": 19.5 },
  "known_limitations": ["..."],
  "followups_for_main_agent": ["..."],
  "diagnostic_notes": ["..."]
}
```

3. **最后一步**才 touch `.DONE`. 顺序严格, 否则主 agent 读到空 json.

## 主 agent 确认流程

```
# 1) 派 subagent (显式给 task_id, 在 prompt 里强制它按协议交付)
# 2) 主 agent 继续做自己的独立工作
# 3) 回来收: 一次 ls 看 <task-id>.DONE 存在? → 读 json → 决定下一步
#    不存在 + 未超时 → 走 "卡住 vs 在跑" 判定 (下一节); 超时 → 按 fail 处理
```

## 卡住 vs 在跑 判定方案 (2026-04-23 r3 加入)

只看 `<task-id>.DONE` 是否存在是二值结果, 不存在时无法区分 "subagent 仍在健康工作" 和 "subagent 被 API 抖断 / token 耗尽 / 陷入死循环". 主 agent 需要**三维证据**判断, 各维度结论并入决策:

### 维度 1 — transcript 心跳检查 (最权威)

Subagent 的所有 tool 调用和思考都以 JSONL 行增量写到:

```
C:\Users\TL0SR2\.cursor\projects\<project>/agent-transcripts/<parent-session>/subagents/<subagent-uuid>.jsonl
```

主 agent 通过 `Glob subagents/**/*.jsonl` 找到最新 `.jsonl` (按 `LastWriteTime` 倒序), 读最后 ~30 行.

**活跃信号** (至少满足 1 条视为在跑):

- `.jsonl` 最后一行时间戳 < **90 秒前** → 刚有 tool 调用, 正常.
- 最后事件是 `tool_call` / `tool_result` / `assistant_thinking`, 不是 `error` / `rate_limit` / `context_limit_reached`.
- 最后一条 `assistant` 消息有**具体任务内容**, 不是 "Let me try again" 类重试独白.

**卡住信号** (满足任意 1 条 + DONE 未出现):

- `.jsonl` 最后修改时间 > **180 秒前** — tool 调用停止 > 3 分钟, 远超正常思考间隔.
- 最后事件是 `error: context_limit` / `rate_limit` / `tool_error` 连续 ≥ 3 次.
- 最后一条 assistant 在"无意义循环" (相同 tool 调用三次以上无进展).
- `jsonl` 文件 size 增长停滞 (两次检查间隔 60s 大小不变).

### 维度 2 — 交付文件半成品检查

即使没 `DONE`, subagent 已经 touch 过的**中间产物**也是活跃信号:

- `<task-id>.json` 存在但 `.DONE` 不存在 → subagent 已写完报告但还没 touch DONE, 通常是**下一毫秒就会 touch**, 此时主 agent 应等 5-10 秒再查一次, 不要判 fail.
- `files_changed` 里的任一文件近期被修改 (git status 看到 `M`) → subagent 确实在改代码, 仍然活跃.

### 维度 3 — 外层 Task 工具状态

Task 工具本身在 subagent 结束时会返回 final message. 如果主 agent 是**同步等待** (block_until_ms 大于预估时长), 则 Task 返回就是 ground truth, 不用进入本判定流程. 本流程只在 **run_in_background=true** 或 **block 提前超时** 时触发.

### 决策表

| DONE 文件 | transcript 心跳 | 决策 |
|---|---|---|
| 存在 | — | 读 `.json`, review + 合并, 正常完成 |
| 不存在 | 活跃 | 继续等, 主 agent 做自己的独立工作, 下次再查 |
| 不存在 | 卡住 + < 5 分钟 | 再等 1-2 分钟, 再查心跳 (有时是 LLM 长时间生成 thinking) |
| 不存在 | 卡住 + ≥ 5 分钟 | 按 fail 处理, **不 resume 不重启** (避免盖掉半成品); 主 agent 自己做兜底; 记录 transcript 尾部证据到 AGENT_NOTES 备查 |
| 不存在 | `context_limit` 硬错 | 立即按 fail 处理, 不等; transcript 已经 abort. 主 agent 根据已交付半成品评估可否接力完成 |

### 常用 ripgrep / glob

```bash
# 找 subagents 目录
Glob subagents/**/*.jsonl (按时间倒序)

# 读最新 jsonl 尾部 (主 agent 可以直接 Read with offset=-30)
Read <subagent.jsonl> --offset=-30

# 找 transcript 里的错误信号
Grep -i "context_limit|rate_limit|tool_error" <subagent.jsonl>

# 找 "已经完成" 之类的 assistant 尾声词
Grep -i "task complete|delivered|handoff written|DONE" <subagent.jsonl>
```

### 反模式 (已踩过的坑)

- ❌ **看不到 DONE 就 resume 或重派同 task_id**: r2 时主 agent 这么干过, 盖掉了已交付半成品, 浪费 2 轮 tool roundtrip.
- ❌ **看不到 DONE 就宣告超时 fail**: 只看 DONE 是二值, 丢失了 "subagent 正在 touch DONE 的那几秒" 或 "LLM 正在长生成 thinking" 的真相.
- ❌ **不读 transcript 尾部就猜**: r2 时用户明确指出 "你应该建立机制而不是靠猜". transcript 是 **ground truth**, 不读 transcript 的判断都是瞎猜.

### 最小 1 次健康检查所需 tool call

```
1. Shell: ls _subagent_handoff/  → 看 DONE 是否存在
2. 若不存在:
   - Glob subagents/**/*.jsonl  → 按时间倒序找最新
   - Read <newest>.jsonl --offset=-30  → 读尾部
   - 对照"活跃信号 vs 卡住信号"查找关键词
3. 决策
```

1 次完整判定 ≤ 3 tool call, 10 秒内完成, 不再靠干等或靠猜.

## 已知适用范围

- 纯前端 CSS / 静态文件调整 (subagent 独立, 不碰后端).
- 孤立的文档撰写 / 规约扫描.
- 无状态的单函数 refactor.

**不**适合 subagent:
- 需要主 agent 的上下文决策 (比如 "这个 bug 算 L36 第几次?").
- 需要启动/重启服务才能验证的端到端测试.
- 跨多个强依赖模块的重构.

## 历史教训来源

LESSONS_LEARNED §7.A candidate **L33.x**: "subagent 并行机制必须配对
显式 handoff 文件 + 完成标志, 否则主 agent 只能靠 transcript 目录轮询猜测."
2026-04-23 P25 Day 2 polish r2 事件. 详见 AGENT_NOTES #113 脚注.

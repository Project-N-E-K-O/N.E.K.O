/**
 * op_catalog.js — Diagnostics Logs 子页用的 "op 字典".
 *
 * 每条 `op` 是一个点分命名空间字符串 (例: `chat.send.begin` /
 * `memory.previews.trigger.facts_extract`). 测试人员光看命名空间不一
 * 定知道"这是什么模块在做什么", 本文件为常见的 op 提供:
 *
 *   - `label`       中文友好名 (展示在 op 字符串旁边, 24 字符内)
 *   - `description` 一句话说明 (UI 里做 title tooltip + op facet 帮助)
 *   - `category`    模块归类 (chat / memory / stage / session / diag …)
 *
 * 未命中的 op 退化为"原始 op + 原始 op", 不报错; 新增 op 时随手补几行就行.
 *
 * ⚠️ 修改原则:
 * 1. 不要把**完整 payload 结构**写进 description — 那是 raw JSON 的事,
 *    这里只说"什么场景会打这条". 目标是让不熟悉代码的测试人员也能猜到
 *    "我刚才点的那个按钮, 就是触发这条".
 * 2. 保持**大小写一致**: 后端都用小写 `.` 分隔, 前端不要重写大小写.
 * 3. 新模块的 op 请用 "模块.动作[.子动作][.阶段]" 的模式 (参考 chat.send.begin).
 */

/**
 * @typedef {{ label: string, description: string, category: string }} OpSpec
 */

/** @type {Record<string, OpSpec>} */
const CATALOG = {
  // ── Chat (发送/消息 CRUD/预览) ────────────────────────────────
  'chat.send.begin': {
    label: '聊天发送 · 开始',
    description: '点 [发送] 后, 把 user 消息 + 已组装的 wire_messages + 模型配置 (API key 已脱敏) 落盘, 失败复现的兜底记录. 每次 Send 一条.',
    category: 'chat',
  },
  'chat.send.end': {
    label: '聊天发送 · 完成',
    description: '一轮 Send 流式结束 (正常/失败都会记). 含最终消息长度 / 耗时 / 结束原因 (done / error / abort). 与 begin 成对出现.',
    category: 'chat',
  },
  'chat.prompt_preview': {
    label: 'Prompt 预览',
    description: '每次 Chat workspace 刷新预览都会调一次. 本身不改后端状态, 默认 DEBUG 级 (不落盘). 需要排查 persona / template / warnings 漂移时打开 Debug 日志开关.',
    category: 'chat',
  },
  'chat.messages.edit': {
    label: '消息 · 编辑内容',
    description: '从消息菜单改了某条消息的 content (文本或多模态 parts). 不动时间戳.',
    category: 'chat',
  },
  'chat.messages.patch_timestamp': {
    label: '消息 · 改时间戳',
    description: '单独改一条消息的 timestamp. 后端会校验相邻消息单调性 (non-decreasing), 违反时返回 422.',
    category: 'chat',
  },
  'chat.messages.truncate': {
    label: '消息 · 从此重跑',
    description: '删掉 N 条之后的消息, 并把 virtual clock 回退到被保留的最后一条. 用于 "从此处重跑" 菜单.',
    category: 'chat',
  },
  'chat.messages.delete': {
    label: '消息 · 删除',
    description: '手动删除一条消息. 不回退时钟 (用 truncate 才会).',
    category: 'chat',
  },
  'chat.messages.inject_system': {
    label: '消息 · 注入 system',
    description: '手动插一条 system-role 中段指令 (composer 的 [注入 sys] 按钮).',
    category: 'chat',
  },

  // ── SimUser / auto dialog ────────────────────────────────────
  'simuser.generate.begin': {
    label: '假想用户 · 开始生成',
    description: 'SimUser (假想测试用户) 开始生成下一句 user 话语. 含传给 LLM 的 wire + 用户画像提示.',
    category: 'simuser',
  },
  'simuser.generate.end': {
    label: '假想用户 · 生成完成',
    description: 'SimUser 本轮生成结束, 含最终文本 + 耗时 + 结束原因.',
    category: 'simuser',
  },
  'auto_dialog.start': {
    label: '自动对话 · 启动',
    description: '"自动对话 N 轮" 按钮触发. 记录轮数限制 / 温度 / 间隔等策略.',
    category: 'simuser',
  },
  'auto_dialog.end': {
    label: '自动对话 · 结束',
    description: '自动对话跑完或被用户停止. 含实际完成轮数 + 结束原因.',
    category: 'simuser',
  },
  'auto_dialog.simuser.done': {
    label: '自动对话 · SimUser 一轮完成',
    description: '单轮 SimUser 生成落地, auto_dialog 会接着触发 assistant 回合.',
    category: 'simuser',
  },

  // ── Stage / Virtual clock ────────────────────────────────────
  'stage.advance': {
    label: 'Stage · 推进虚拟时间',
    description: '把 virtual clock 往前走 (手动或 composer Row1 的 "推进 N 分钟").',
    category: 'stage',
  },
  'stage.rewind': {
    label: 'Stage · 回退虚拟时间',
    description: 'virtual clock 回退到某条消息的 timestamp, 一般 truncate 会附带调用.',
    category: 'stage',
  },

  // ── Script (对话剧本) ─────────────────────────────────────────
  'script.load': {
    label: '剧本 · 载入',
    description: '从 dialog_templates 选一个模板挂到当前会话. 记录 template name / 轮数.',
    category: 'script',
  },
  'script.bootstrap.apply': {
    label: '剧本 · 初始化虚拟时间',
    description: '剧本的 bootstrap.virtual_now 被应用 (只在会话还没消息时生效).',
    category: 'script',
  },
  'script.reference.fill': {
    label: '剧本 · 填入参考回复',
    description: '把剧本里的 assistant.expected 文本写入对应消息的 reference 字段, 供 Comparative Judger 对照评分.',
    category: 'script',
  },

  // ── Session 生命周期 ─────────────────────────────────────────
  'session.create': {
    label: '会话 · 新建',
    description: '左上角 [新建会话] 按钮触发. 生成新 session id + 沙盒目录, 这之后的日志都落到 <sid>-YYYYMMDD.jsonl.',
    category: 'session',
  },
  'session.destroy': {
    label: '会话 · 销毁',
    description: '显式销毁 / 切换时自动销毁. 会 purge 沙盒与内存缓存.',
    category: 'session',
  },

  // ── Persona / Memory import ──────────────────────────────────
  'persona.import': {
    label: 'Persona · 从真实角色导入',
    description: '从主 App 的 characters.json 复制一个角色到当前沙盒. 不回写主 App.',
    category: 'persona',
  },
  'persona.import_builtin_preset': {
    label: 'Persona · 载入内置预设',
    description: '用 tests/testbench/presets 里的最小完整示例角色覆盖当前沙盒. 可重复点击.',
    category: 'persona',
  },

  // ── Memory (P10) ─────────────────────────────────────────────
  'memory.previews.trigger': {
    label: 'Memory · 触发预览',
    description: '记忆 op (recent.compress / facts.extract / reflect 等) 的 dry-run: 只算不写.',
    category: 'memory',
  },
  'memory.previews.commit': {
    label: 'Memory · 提交预览',
    description: '确认预览结果, 把 payload (含用户 edits) 原子写入对应 memory 文件.',
    category: 'memory',
  },
  'memory.previews.discard': {
    label: 'Memory · 丢弃预览',
    description: '放弃预览缓存, 后端状态不变.',
    category: 'memory',
  },

  // ── Judger (P15/P16) ─────────────────────────────────────────
  'judge.run.begin': {
    label: '评分 · 开始',
    description: '一次评分任务启动 (schema + 数据切片). 含选中的 schema id + turns 数.',
    category: 'judge',
  },
  'judge.run.end': {
    label: '评分 · 完成',
    description: '评分跑完, 含每个维度分数 + 总分.',
    category: 'judge',
  },

  // ── Diagnostics 自产 ─────────────────────────────────────────
  'http.unhandled_exception': {
    label: '异常 · 未处理',
    description: '全局异常中间件捕获的请求错误, 同时写 session JSONL + Errors ring buffer. 这种条目一般对应 Errors 子页的一行.',
    category: 'diagnostics',
  },
  'log.parse_failed': {
    label: '日志 · 行解析失败',
    description: 'JSONL 某一行格式破损, 前端会把原始前缀放 payload.raw_sample 里, 不丢数据.',
    category: 'diagnostics',
  },
  'log.serialize_failed': {
    label: '日志 · 序列化失败',
    description: '某条 payload 含无法 JSON 化的对象, 已退化为 safe record 落盘. 极罕见.',
    category: 'diagnostics',
  },
  'diagnostics.debug_toggle': {
    label: 'Debug 日志 · 切换',
    description: 'Debug 日志总开关 (默认关) 被切换. 含 previous/enabled 状态.',
    category: 'diagnostics',
  },
};

export function lookupOp(op) {
  if (!op) return null;
  return CATALOG[op] || null;
}

export function opLabel(op) {
  const spec = lookupOp(op);
  return spec ? spec.label : null;
}

export function opDescription(op) {
  const spec = lookupOp(op);
  return spec ? spec.description : null;
}

export function opCategory(op) {
  const spec = lookupOp(op);
  return spec ? spec.category : null;
}

/** Used by the `?` help popover: list every category once with its ops. */
export function groupedCatalog() {
  const out = {};
  for (const [op, spec] of Object.entries(CATALOG)) {
    (out[spec.category] ||= []).push({ op, ...spec });
  }
  for (const cat of Object.keys(out)) {
    out[cat].sort((a, b) => a.op.localeCompare(b.op));
  }
  return out;
}

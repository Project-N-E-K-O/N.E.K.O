# P24 整合期设计蓝图 (Single Source of Truth)

> 最后更新: 2026-04-21 第五轮 (方法论扩大应用实证完成, 蓝图定稿)
> 状态: **规划期定稿**, 不再做第六轮审查, 按 §15 Day 1-12 开工
> 维护者: 本蓝图是 P24 阶段**唯一的规格 / 决策 / sweep 结果权威来源**.
> 总 workload **15.5-19.5 天 (连续日)**; 5 轮审查合计 2160+ 行历史数据保留.
>
> 历次审查记录 (未来 agent 不必通读, 按需翻):
>
> - **第 1-2 轮** (§0-§10): 延期加固四项 + §4.1 五条实证技术债 + H1/H2 + renderAll drift detector
> - **第 3 轮** (§12): Full-Repo Pre-Sweep — 事件总线 matrix / dev_note UI bug / 虚拟时钟 §12.5 用户实测纠错
> - **第 4 轮** (§13): Choke-Point 合规核查 — §3A 47 条里 15 条实测, 合规率 40% (4 漏守 + 5 可疑 + 6 绿)
> - **第 5 轮** (§14 + §14A): 方案元审 + 方法论扩大应用实证 — RAG 覆盖度表 22绿/10黄/14红; §14A 实证 2 条新漏洞 (`memory_hash_verify` 前端 0 消费 / SSE event 散落无枚举)

## 开工 TL;DR (接手 agent 优先读本节)

**P24 方案经 5 轮审查定稿, 开工流程**:

1. **直接读 §15 Day 1-12 连续任务清单**, 作为**唯一执行清单** (~300 行, 每日子任务 6-8 条, 每条带 `[§X.Y]` tag 指向规格详情)
2. **各 Day 子任务只在需要规格细节时**才翻对应 tag (§3-§5 规格 / §12-§14A 审查数据), 不要通读整份蓝图
3. **每日完工后更新 §15 勾号**, 每周汇总进度
4. 遇到文档未覆盖的新 bug → 走 **§14.7.D 新 bug 决策树** (数据丢失 hotfix / 原则违反 sweep / 新功能 backlog / 架构级 新 phase)
5. P24 完工 → 按 **§9 规定动作** 回写三份老 docs

**当前进度 (2026-04-22 Day 12 欠账清返 + v1.0 sign-off 完成)**: **Day 1-12 全部交付**. **Day 9 主程序同步 5 项盘点全零行动** (见 §15 Day 9 详情), 主程序近期变动 (云存档 / QwenPaw / OpenRouter / Grok/Doubao / avatar interaction 等) 要么是 @property 动态计算天然跟着沙盒重定向, 要么是我们自己的 P08/P09 改动, 零技术债. **⚠️ 道具交互 (PR #769) 二轮评估结论翻转**: 一轮定性 "架构不兼容 → out-of-scope", 用户二轮澄清 testbench 定位 = "新系统对对话/记忆影响测试生态" 后, **pure helper 层必须接入, 同族 agent callback + proactive 一并接入**. **新开 P25 "外部事件注入 · 新系统对话/记忆影响测试" 阶段**, 蓝图 [P25_BLUEPRINT.md](P25_BLUEPRINT.md) (原 P25 README 顺延 P26). Day 12 收尾 + 开工前设计层回顾后启动 P25. 用户反馈 `p24_integration_report §1.1 S1-S12` 手测场景 + dev_note 17 项在 Day 1-8 迭代过程中已逐步覆盖, 每轮反馈出的 bug 当场修掉没遗留. 资源数据除 "log retention 14 天 / diagnostics ring 200 条轮转" 这类**时间累积验证**类本阶段无法复现外, 其它 (autosave 体量 / snapshot 命中率 / 注入真阳性率) 均 OK. Day 1-6 核心交付 (1-5 同上 + 6B 事件总线 6 违规全清零 + 6C C3 append(null) sweep 0 违规仅加 safeAppend 前瞻 helper + 6F DOM listener teardown 发现 page_snapshots 真 leak 已修 + 6G api.js 扩 signal + makeCancellableGet helper + 6 处 loadXxx 迁 abort). **Day 6 剩余 3 项 (6A renderAll drift / 6D lazy init / 6E asyncio cancel)**: 6E 与 Day 8 M1 合并审查通过 (零代码改动); **6A/6D 在 Day 12 欠账清返 (2026-04-22) 当场做完** — 6A 骨架 `static/core/render_drift_detector.js` 176 行 + `registerChecker/unregisterChecker` API + 3 个 checker (`topbar.session_chip_label` / `app.active_workspace_section` / `page_snapshots.row_count`); 6D `page_persona.js::renderPreviewCard` 从 `let loaded = false` → `let loadPromise = null` Promise cache + composer.js 3 处 `ensureXxxLoaded` 补齐 `.catch` 清空 (skill 规则 3) + `loadTemplateList(force)` 升 Promise cache. **严重事故 hotfix #105 (2026-04-21 Day 6 验收期) 已落地**: New Session 按钮触发事件级联风暴导致用户整机卡死强制断电 (§4.26 #87 二次踩点). 三层防线修复: (1) topbar.js New/Destroy session 走 reload 对齐 #87 pattern; (2) api.js + errors_bus 加 http:error burst circuit breaker 通用二道防线 (1s>30 次即静默 5s); (3) pipeline/live_runtime_log.py 新模块 stdout/stderr 字节级 tee 到 DATA_DIR/live_runtime/current.log, boot 时 rotate 一代. **Day 7** UI 偏好 + auto_dialog 多 error 折叠面板 + `#105` 同族 sweep 补齐 (Medium Reset / rewind / archive_deleted). **Day 8 4 轮手测反馈 hotfix #107 Part 1-4** 全部消化完 (快照配置 UI / S6/S7/S10/S12 反馈 / 注入 toast + auto_dialog 诊断 / `[hidden]` 被 CSS 静默覆盖). 新增 `.cursor/rules/global-state-clear-must-reload.mdc` + `hidden-attribute-vs-flex-css.mdc` 两条 rule 防三次/四次踩点. 新增 LESSONS #20 (同族 sweep) / #21 (hidden vs CSS) / #22 (opts 覆盖型 API) 三条元教训. 新增 DiagnosticsOp `PROMPT_INJECTION_SUSPECTED` / `AUTO_DIALOG_ERROR` 两个 op 入 catalog. 详见 AGENT_NOTES §4.27 #105 + #107 + LESSONS_LEARNED §7. **下一个接手点 = Day 10** (新增 `smoke/p24_integration_smoke.py` + `p24_session_fields_audit_smoke.py` + `p24_sandbox_attrs_sync_smoke.py` + 全量回归 + §14.4 M4 资源上限 UX 降级文档化), 细节见 §15 Day 10.

**2026-04-22 Day 8 第二轮验收 hotfix #107 Part 3+4 (本轮)**: 用户继续报 4 项. (1) **Session Load modal 残留"导入 JSON"按钮** [**连续 3 次修法**]: (v1) `body.innerHTML=''` 清不到 (按钮不在 body 内); (v2) `dialogActions.hidden=true` 切换, **看起来逻辑对但用户依然看到按钮**; (v3 真因) `.modal .modal-actions { display: flex }` CSS 规则**静默覆盖** UA stylesheet 的 `[hidden] { display: none }` → `hidden` 属性无效. (v4 落地) 改用 DOM-level **remove/re-append** (`showDialogActions()` / `hideDialogActions()` helper) 绕过显隐管理. 新增 `.cursor/rules/hidden-attribute-vs-flex-css.mdc` 防四次踩点. 同族 sweep 全仓 `.hidden =` 2 处 (topbar.js err-count span + memory_editor_structured.js toggleBtn) 验证均无 display 类规则, 安全无需修改. (2) **注入 toast 文案过长**: 用户只要"检测到 X 条提示词注入模式"这一句; 删 detail 副标题, `injection_warning_toast_fmt` 简化. (3) **连续两条 user 的设计问答**: 代码上合法 (session.messages list 不 enforce alternating, 但 Anthropic Claude 原生 API 会拒 — 目前走 OpenAI-compatible 代理不受影响); UX 上已在 rerun user tail 后 toast 提示 "可直接点 Send 空内容让 AI 回复末尾 user" 避开该分支. 无代码风险. (4) **Auto-Dialog RateLimitError 没进诊断 + toast 只显示 "LlmFailed"**: 两个独立 bug 叠加 — (a) auto_dialog 三处 `except` 只 yield SSE error, 不写 diagnostics_store → 徽章和 Errors 页失察; 在 `finally` 里集中调 `record_internal(op='auto_dialog_error', level='error')` 一次兜住三处 except (同族防御, 未来新增 except 自动覆盖); (b) **`toast.err(err.message, {message: err.type})` 全仓 16 处都踩的 API 坑**: show 内 `opts.message` 覆盖首参 `message`, 导致首参悄悄丢. 以前首参和 opts.message 意义相近没被发现, 直到这次首参是"RateLimitError: 429..."完整诊断才暴雷. 改 `toast.ok/info/warn/err` dispatcher: 当 `opts.message` 存在且 `opts.title` 缺省时, 首参自动升格成 title, 16 处历史调用点全向后兼容. Diagnostics op catalog 新增 `AUTO_DIALOG_ERROR` 枚举 + catalog entry. 详见 AGENT_NOTES #107 Part 3.

**方案完善度 RAG 灯**: 22 绿 (充分覆盖) / 10 黄 (P24 Day 中 sweep 补齐) / 14 红 (已分类 M/O/B 三档消化, 见 §14.1). **不再做第六轮 sweep** — 再审下去边际效益负, 剩余不确定性靠联调期实测兜底.

**核心方法论 (5 轮沉淀, 必读)**:

- **"Intent ≠ Reality"** (§12.5 派生): 文档原则声称 X 受保护 ≠ 所有路径都走守护; 用户实测 > AI 推断 > 文档原则
- **"多源写入是纸面原则成败分水岭"** (§13): "X 统一走 Y" 类原则必须配 (a) choke-point helper + (b) 静态 sweep 方法
- **"方法论立即扩大应用"** (§14A.7): 列出应用面 ≠ 价值, 立即实证 2-3 个才有价值
- **"覆盖度 RAG 灯作为阶段自检工具"** (§14.10): 量化 "还有多少没覆盖", 而非 "看起来挺全"

---

---

## 0. 使用说明

### 0.1 为什么单开一份文档

P24 集中承担**五件事** (联调 / 延期加固 / 代码审查 / 主程序同步 / bug 修复),
范围远大于之前任何一个主线阶段.  三份老 docs 累计已超过 4700 行 (PLAN.md 2071
行 + PROGRESS.md 1189 行 + AGENT_NOTES.md 1471 行), **P24 的设计讨论若继续
摊进去会让三份文档各自膨胀 300-500 行**, 且:

- 同一决策在三处重复写 → 未来漂移 (参考 #101 G5/G6/G7 编号语义已经和
  PLAN §11 原定义漂移)
- 读者找 P24 规格需跨三份文档 grep 拼凑
- P24 的"更大更全面的设计规范审视"本身是一项**横向 sweep 工作**, 横向结果
  放在三个纵向阶段文档里天然不合适

所以本阶段改用"**单文档权威源 + 三份老 docs 只留索引**"模式, 完工后再把
"P24 交付实录"以较短的 summary 回写到 PLAN §15 / PROGRESS P24 详情 /
AGENT_NOTES §4.27 新条, **但规格和 sweep 数据留在本蓝图**.

### 0.2 读者 / 使用时机

| 时机 | 谁 | 读什么 |
|---|---|---|
| P24 开工前 | 接手 agent | §1-§7 全读; §8 作为 day-by-day 执行顺序 |
| P24 进行中 | 接手 agent | §4 sweep 清单当 checklist; §7 交付物作验收标准 |
| P24 完工后 | 归档维护者 | §9 "回写三份老 docs 的规定动作" |
| 未来 agent | 任何 | §2 G 编号澄清; §4.1 已发现技术债作为历史参考 |

### 0.3 与三份老 docs 的关系

- **PLAN.md §15 P24 实施细化**: 保留为 "P24 原始规划档案" + 加一行"**详细
  规格与 sweep 结果见 `P24_BLUEPRINT.md`**"
- **PROGRESS.md P24 详情**: P24 开工后按阶段更新"已做 / 待做", 详情指向本蓝图
- **AGENT_NOTES.md**:
  - 顶部 "接手 P24 前必读" 更新指向本蓝图首页
  - §4.27 #102 末尾加一行 "P24 蓝图文档已独立, 见 P24_BLUEPRINT.md"
  - P24 完工后新增 #104 "P24 整合期交付复盘" 引用本蓝图的具体小节

---

## 1. P24 阶段定位 (回顾)

### 1.1 五件事

1. **端到端联调** — 走真实模型闭环: Setup → Chat 四模式 → Memory ops →
   Stage Coach → Evaluation (Schema+Run+Results+Aggregate+Export) → Save
   / Load / Autosave / Restore → Export (P23). 采集 autosave 体量 / 资源
   占用 / log retention 实测数据, 给 P25 README 做推荐值.
2. **延期加固收口** — §10 P-A / P-D + §13 F6 / F7 (见 PLAN §15.2 原规格).
3. **系统性代码审查** — 见本蓝图 §4, **大幅扩展了 PLAN §15.3 原 7 条**.
4. **主程序同步** — 见 PLAN §15.4 (不重写, 本蓝图不覆盖).
5. **bug 修复窗口** — 联调暴露的 bug 集中消化, 不推 hotfix.

### 1.2 非目标 (显式, 避免 P24 无限扩张)

- ❌ 不新增面向用户的 workspace 功能
- ❌ 不替换任何 §3A 冻结决策
- ❌ 不承接新的"架构空白"级发现 (同 #91/#95/#97 档, 单独立项)
- ❌ 不做性能优化, 除非联调暴露用户可感知卡顿

### 1.3 为什么不追加子版本号 (非 P23.1 / P22.2)

子版本号节奏只给"摘樱桃 pass"用 (P21.1 / P21.2 / P21.3 / P22.1).
P24 承担**五大职责是一个独立主线阶段**, 保持整数阶段号对未来 agent
心智最清晰 (详见 §4.27 #102 决策 1).

---

## 2. G 编号体系澄清 (一劳永逸的命名权威)

### 2.1 历史漂移

文档里 "G5 / G6 / G7" 出现了**两套不兼容语义**:

| 来源 | G5 | G6 | G7 |
|---|---|---|---|
| PLAN §11 原审计表 (P21 期) | 三段锁中间段崩溃缺 boot banner | **不存在** | **不存在** |
| PROGRESS.md P22.1 / AGENT_NOTES #101 (P22.1 期) | 健康自检端点 | 存档 schema lint | 存档内容 lint |

### 2.2 本蓝图正式改名

为止血这个漂移, **本蓝图及后续所有 docs 使用下列命名**:

- **G1 / G2 / G3 / G4 / G8 / G10 / G11** — 沿用 PLAN §11 原定义, 不变
- **G5 (原 PLAN §11 定义: boot banner)** — 标记为 "已被 P22 boot self-check
  规划吸收 + 未落地, 废弃编号". 未来不要复用 G5 指其它东西.
- **H1 = 健康自检端点** (原 P22.1 的 G5')
- **H2 = 存档 schema lint** (原 P22.1 的 G6')
- **H3 = 存档内容 lint** (原 P22.1 的 G7')

"H" 组代表 **Hardening / Health** 新族, 和原 G 组 (G = Gap from P21
audit) 解耦.

### 2.3 回写要求

P24 开工第一件事 (§8 Day 0): 在 PLAN §11 末尾 / AGENT_NOTES §4.27 #101
表格 / PROGRESS P22.1 条目各加一行**"P24 蓝图把 G5'/G6'/G7' 改名为
H1/H2/H3, 见 P24_BLUEPRINT §2"**, 本蓝图外其它文档不改语义.

---

## 3. 决策矩阵 (Q1-Q5 落地规格)

### 3.1 H1 最小版健康端点 (做, Q1)

**端点**: `GET /api/system/health`

**实装形态 (最小版, 纯聚合)**:

```python
# routers/health_router.py 扩展, 复用已有 stores 不引入新 pipeline
@router.get("/system/health")
async def system_health() -> dict:
    return {
        "status": "healthy | warning | critical",  # 汇总判断
        "checks": {
            "disk_free_gb":         {"value": ..., "threshold": 1,   "status": ...},
            "log_dir_size_mb":      {"value": ..., "threshold": 500, "status": ...},
            "orphan_sandboxes":     {"count": ...,                    "status": ...},  # 调 _scan_sandbox_orphans (P-A)
            "orphan_autosaves":     {"count": ...,                    "status": ...},  # 已有 P22 端点
            "autosave_scheduler":   {"alive": ..., "last_tick": ...,  "status": ...},  # 查 autosave.py 全局实例
            "diagnostics_errors_1h":{"value": ...,                    "status": ...},  # diagnostics_store 滤 level=error + last 1h
            "memory_hash_mismatch": {"value": ...,                    "status": ...},  # diagnostics_store 滤 op=integrity_check
        },
        "checked_at": "...ISO...",
    }
```

**汇总规则**:
- 任一 check status=critical → 总 status=critical
- 否则任一 warning → warning
- 全 healthy → healthy

**非目标**:
- ❌ 不做定时主动扫描 (纯 on-demand)
- ❌ 不做历史曲线 (单点快照)
- ❌ 不做告警推送

**触发条件可扩展**:
- 文档里记一个"未来候选 checks" 列表 (autosave failure rate, export 大小异常
  等), 本轮不做

**UI**: Diagnostics → Paths 子页顶部加一个 "System Health" 卡片 (复用
CollapsibleBlock), 显示 status 徽章 + 点开看 checks 明细.  点"Refresh"
重新拉端点.

**workload**: 后端 ~80 行 + 前端 ~80 行 + 烟测 ~40 行 ≈ 1 天

### 3.2 H2 存档 schema lint (做, Q2)

**端点**: `GET /api/session/archives/{name}/lint` (单条)

**实装形态**: 复用 Pydantic `SessionArchive.model_validate()` + 补一层
"strict" 模式 (禁 extra 字段) 得到字段级错误清单.

```python
# pipeline/persistence.py 新加
def lint_archive(json_path: Path) -> dict:
    """Return {errors: [...], warnings: [...], schema_version_supported: bool}.

    - errors: Pydantic ValidationError 转成的 field-level 清单
    - warnings: 未知字段 / schema_version 太新太旧
    - **只做 JSON 层 lint**, 不校验 tar.gz (H3 范畴不做)
    """
```

**前端**: Session Load modal 每一条 archive 右侧加一个 "[Lint]" 小按钮,
点击弹小面板显示 errors / warnings (复用 error toast 样式).
**不自动对所有 archive 批量 lint** (性能/启动时间考虑).

**非目标**:
- ❌ 不做 tar.gz 内容校验 (H3 范畴不做)
- ❌ 不做批量 lint (按需触发)
- ❌ 不做自动修复建议

**workload**: 后端 ~60 行 + 前端 ~50 行 + 烟测 ~30 行 ≈ 半天

### 3.3 H3 存档内容 lint (不做)

**理由**:
- 性能模型不明: 解 tar.gz + 验 SQLite header 对 >10MB archive 成本不可忽略
- 无现实触发场景: 当前 memory_sha256 (P22.1 交付) 已捕获 90% 的"tar.gz 坏了"
  场景, 真正 tar.gz 内部结构错乱的案例为 0
- 若 P24 联调冒"tar.gz 内部结构问题"单独立项

**backlog 触发条件**:
- testbench 对外开放发布前
- P24 联调出现 archive load 成功但 memory 行为异常的案例
- 日 autosave 数据达到需要性能优化的规模

### 3.4 延期加固四项 P-A / P-D / F6 / F7

**规格不重写**, 直接按 PLAN §15.2 (A)(B)(C)(D) 四节执行. 本蓝图补充:

**§3.4 的本蓝图新增要点**:

1. **F6 match_main_chat 的 fallback 行为**: 当主对话 persona 还未设定
   system_prompt 时, F6 checkbox 勾选但无 system 可对齐 — 此时 judge_run
   走 schema 默认 system_prompt 并返回 `match_main_chat_applied=false` +
   前端 tooltip 解释. 不当 error.
2. **F7 先走 Option B** (Errors 子 tab 加 3 个过滤按钮), 实测事件密度后
   决定是否升 Option A 独立子页. 升级判据: `record_internal(op="..."...)`
   三类 op 日产出 > 20 条 → 升级.
3. **P-A 的去重策略**: 和 autosave boot_orphans 互不重叠 (autosave 扫
   `_autosave/`, P-A 扫 `sandboxes/`), Paths 子页分两个分组, **不做交叉
   去重**, 避免一条孤儿在两处出现让用户困惑.

### 3.5 renderAll dev-only 渲染漂移检测 (Q4, 走彻底方案)

**背景**: AGENT_NOTES #72 记录的"onChange 后漏 renderAll"类 bug 项目已
踩 6+ 次. 上次延伸教训明确指出"下一步要么 `bindStateful` helper 要么
dev-only 漂移检测". Q4 选**后者**.

**实装形态**:

```javascript
// static/core/render_drift_detector.js (新)
//
// 工作机制:
// 1. 在 DOMContentLoaded 后注册 MutationObserver 监听 document.body 的
//    attribute 变化 (重点: disabled / aria-* / data-* / style.display / hidden)
// 2. 页面 mount 时注册 "derived state checkers" (函数签名: () => expected,
//    DOM selector 查 actual, 比对). 例子: canRun() / describeDisabledReason()
// 3. 每次 state.emit('*:change') 后下一 microtask 执行一遍所有 checker.
//    不一致时 console.warn + 堆栈.
// 4. URL 带 ?dev=1 时启用, 生产模式零开销.
//
// 核心不变式: 纯检测, 不修正 — 违反"纯净"原则时只报警, 让开发者自己修.
```

**落地步骤**:
1. 先写 detector 主体和 API (`registerChecker(name, fn, selector, attr)`)
2. 每个页面 mount 时注册自己的 checker (button.disabled / badge visibility 等)
3. 选一个已知踩过坑的场景 (比如 msg_ref 的 run 按钮) 写 e2e 验证能
   检出"漏 renderAll" 的 bug
4. 烟测: jsdom 里模拟 "state 改了但 DOM 没刷" 确认 warn 触发

**workload**: 主体 ~120 行 + 每页 checker 注册 ~10-30 行 × N 页 +
jsdom 烟测 ~80 行 ≈ **1.5-2 天**

**非目标**:
- ❌ 不做自动 renderAll (违反"检测不修正"原则)
- ❌ 不在生产模式启用 (需要 ?dev=1 query)
- ❌ 不覆盖 contenteditable / textarea 这类"per-keystroke 的 state 变化"
  (那类场景走 edge-trigger 模式)

---

## 4. 代码审查扩展清单 (Q3 "更大、更全面")

这一节是 P24 的**重头戏**, 也是用户明确要求"**在更大、更全面的范围内
全面审视当前的设计规范, 审视代码实现了什么, 设计和前期开发落下了什么**"
的直接落地.

分成三个层次:

- **§4.1 已发现的实锤技术债** — 本蓝图 sweep 阶段实证, **P24 必做**
- **§4.2 PLAN §15.3 原 7 条 sweep 清单** — 搬过来不变
- **§4.3 新增 5 条扩展 sweep (D/E/F/H/I)** — 上一轮讨论确认的
- **§4.4 P24 开工时继续 sweep 的候选** — 未实证, 进 P24 做真正 audit
- **§4.5 长期债 backlog** — 不在 P24 做, 记录即可

### 4.1 已发现的实锤技术债 (sweep 实证)

以下每一条都是本蓝图 sweep 阶段用 grep / Read 实证的, 不是推测.

---

#### 4.1.1 HTTPException detail shape 三种并存 【P24 必做】

**证据 (sweep 命中统计, 2026-04-21)**:

| shape | 用法 | 代表路由 |
|---|---|---|
| `detail="str"` | 纯字符串 | `session_router.py`: 4 处 ("no active session" 系列); `diagnostics_router.py`: 6 处; `health_router.py`: 1 处 |
| `detail={"message": str}` | 带字段 dict | `config_router.py`: 4 处; `persona_router.py`: 8 处 |
| `detail={"error_type": ..., ...}` | 扩展多字段 | `session_router.py` 部分新增 (P21.1 后); `memory_router.py`: 多处 |

**影响**: 前端 `static/core/api.js` error parser 必须硬编码多路处理 — 证据:
`chat_router.py:81` docstring 明确写了"HTTPException(detail=...) 前端 error
bus 拦截"的约定, 但实际 shape 不一致让 parser 一会儿吃 string 一会儿吃 dict.

**修法** (P24):
1. **选一个 canonical shape**:
   ```python
   detail = {
       "message": "human-readable str (必填)",
       "code": "ErrorCodeEnum (选填, 前端可据此做 i18n)",
       "data": "any (选填, 附加上下文)",
   }
   ```
2. **新增 helper** `pipeline/error_helpers.py::http_exception(status, message, code=None, data=None)`
3. **存量迁移顺序**:
   - 必改: 所有 **前端已在解析 `detail.message`** 的路由 (persona / config / memory 已是这个 shape, 稳住)
   - 必改: `session_router` / `diagnostics_router` / `health_router` 的 `detail="str"` 改成 dict shape
   - 前端 `static/core/api.js` 同时把 string shape 兜底保留 **6 个月**作为平滑期
4. **加 §3A 新条 A12**: "所有 HTTPException 的 detail 必须是 dict shape
   `{message, code?, data?}`, 禁用裸 string. 新 router 强制, 存量 P24 sweep"

**workload**: 后端 ~150 行改动 + 前端 ~40 行 parser 简化 + §3A 更新 ≈
**1 天**

---

#### 4.1.2 `atomic_write_*` 函数 6 份副本, P21.1 fsync 修复仅覆盖 1/6 【P24 必做, 风险高】

**证据 (sweep 命中)**:

| 模块 | 函数名 | 有 fsync? | 行号 |
|---|---|---|---|
| `pipeline/persistence.py` | `_atomic_write_bytes` / `_atomic_write_json` | ✅ (P21.1 G1) | 215, 242 |
| `routers/memory_router.py` | `_atomic_write_json` | ❌ **缺** | 161 |
| `pipeline/memory_runner.py` | `_atomic_write_json` | ❌ **缺** | 320 |
| `pipeline/script_runner.py` | `_write_user_template_atomic` | ❌ **缺** | 405 |
| `pipeline/scoring_schema.py` | `_write_user_schema_atomic` | ❌ **缺** | 906 |
| `pipeline/snapshot_store.py::_spill_to_cold` | 直接 `gzip.open + write` | ❌ **根本不 atomic** | 728-732 |

**影响 (风险分级)**:

- **snapshot_store.py `_spill_to_cold`** 最严重 — 连 tmp + os.replace 都没做:
  进程在 gzip write 中途被杀 → **cold snapshot 文件半写**, 下次 rewind
  读到损坏文件直接崩. 症状和 P21 G1 风险同档, 但多一层"根本不 atomic".
- 其它 4 份 **atomic 过了但无 fsync**: BSOD / 硬断电时 `os.replace` 的元数据
  已原子但内容可能 0 字节 (同 G1 原始场景)
- `memory_runner.py _atomic_write_json` 的注释甚至写"same convention as
  memory_router" — 两人都错是一起错

**修法 (三选一)**:

**方案 A (最轻, 推荐)**: 所有副本直接复用 `persistence._atomic_write_{bytes,json}`
- pros: 1 处 fsync, 6 处受益; 未来 fsync 策略变更只改 1 处
- cons: 违反 `persistence.py` 模块职责边界 (它本来只管存档), 其它模块要
  import 一个原本是"私有 helper" 的函数
- 修法: 把 `_atomic_write_{bytes,json}` 提升为 `pipeline/atomic_io.py` 新模块
  的公开 API, 其它 5 处 import

**方案 B (次轻)**: 5 个副本各自补 fsync, 不合并
- pros: 不动模块边界
- cons: 6 份实现长期漂移, P21.1 的"G1 已修" 实际仍是 1/6

**方案 C (最重)**: snapshot_store 单独补 atomic 化; 其它 4 处各自补 fsync
- cons: 同 B

**建议**: **方案 A**. 新开 `pipeline/atomic_io.py` 模块, 公开以下 API:

```python
# pipeline/atomic_io.py
def atomic_write_bytes(path: Path, data: bytes) -> None:
    """tmp + fsync + os.replace for binary payloads."""

def atomic_write_json(path: Path, data: Any) -> None:
    """tmp + fsync + os.replace for JSON."""

def atomic_write_gzip_json(path: Path, data: Any) -> None:
    """tmp + fsync + os.replace for gzip-compressed JSON (snapshot cold spill)."""
```

`persistence.py / memory_router.py / memory_runner.py / script_runner.py /
scoring_schema.py / snapshot_store.py` 全部 import 该模块, 删本地副本.

**烟测**:
- 扩展 `p21_1_reliability_smoke.py::test_g1_fsync_present` 为 `test_atomic_io_fsync_present`,
  源码级检查 `atomic_io.py` 三个函数都有 fsync
- 新增 `test_all_atomic_sites_use_atomic_io`: grep 整仓 `os.replace\(` 命中点,
  确认都在 `atomic_io.py` 内部 (白名单) 或 test code 中
- 回归 `p21_persistence_smoke.py / p22_autosave_smoke.py` 确认 memory/
  schema/script/snapshot 的保存路径未破坏

**workload**: 新模块 ~100 行 + 5 处迁移 ~80 行 + 烟测 ~60 行 ≈ **1 天**

---

#### 4.1.3 前端事件总线拓扑 matrix 缺失 【P24 必做】

**证据**:

- 核心实现: `static/core/state.js` 提供 `on(event, fn) / off(event, fn) / emit(event, payload)` + `set(key, value)` 自动 emit `<key>:change`
- 次总线: `static/core/errors_bus.js::emit('errors:change', ...)`, `static/core/api.js::emit('http:error', ...) / emit('sse:error', ...)`
- **递归熔断已实装** (state.js:80-98, `_MAX_EMIT_DEPTH=8`) — 这是好的
- **但全仓 matrix 缺失**: sweep 显示 28 个 UI 文件在用 `events.*` 或 `addEventListener` 或 `subscribe` 字样, 没人画过全部 emitter × listener 矩阵

**已知踩点 (AGENT_NOTES)**:
- #68 `judge:results_changed` 订阅完整性
- #70 跨 workspace 导航 4 步缺一
- #76 grid item `min-width: 0` 父链
- #77 one-shot hint 三路径 (cold mount / warm other / warm same)
- #78 warm-same 订阅方案被 force-remount 替换
- #100 P22 验收期 topbar grid 新增 chip 漏 template 同步

**P24 动作**:

1. **生成 matrix** (半天):
   - grep `emit\('[^']+'` 全仓列所有 emit 点 (event × source file × line)
   - grep `on\('[^']+'` 全仓列所有 listener (event × subscriber × teardown `__off*` 配对)
   - 交叉制表: `event_name × [emitters] × [listeners] × teardown 状态`
2. **落到 AGENT_NOTES §4 的附表**: 给未来 agent 一份 "事件系统一览" 参考
3. **修正发现的 mismatch**:
   - 有 emitter 无 listener → 确认是未来预留还是遗漏
   - 有 listener 无 emitter → 死订阅, 删
   - 有订阅无 teardown → 潜在 double handler bug, 补

**workload**: matrix 生成 0.5 天 + 修正发现的 bug 0.5 天 ≈ **1 天**

---

#### 4.1.4 `schema_version` 分散管理, 无全局 migration 协议 【P24 决策入档, 暂不改】

**证据**:

| 模块 | 字段 | 当前值 | 检查策略 |
|---|---|---|---|
| `persistence.py` | `SessionArchive.schema_version` | 1 (`ARCHIVE_SCHEMA_VERSION`) | newer 抛 InvalidArchive, older 允许 |
| `snapshot_store.py` | `Snapshot.schema_version` | 1 | 无显式检查 |
| `autosave.py` | slot JSON schema_version | 0 / 读时默认 | 无检查 |
| `session_export.py` | export envelope schema_version | 1 (`EXPORT_SCHEMA_VERSION`) | 无检查 (只记录) |
| `scoring_schema.py` | schema 字段 version | 1 | ScoringSchemaError 校验 |

**影响**:
- 短期 (当前所有 version=1): **零影响**, 不触发 migration
- 长期 (任一模块 bump 到 2): **migration 策略不一致** — 有的模块 newer 抛错,
  有的没检查; 没有统一的 "load v1 archive into v2 code" migration framework

**P24 动作 (决策入档, **不落地代码**)**:

写一段 "Migration 策略档案" 到 §3A 候选新条 **A13**:

> A13 · 凡包含 `schema_version` 字段的序列化 payload, 必须有明确的:
> (a) read 端 "支持的版本范围" 常量 + 检查; (b) write 端 pin 当前版本;
> (c) migration 函数表 `_migrations: dict[int, Callable[[dict], dict]]`
> (即使当前为空). 新 schema bump 前必须先补齐 migration 表.

**不落地代码的理由**: 当前全 1 不触发; 提前抽象会是 over-engineering;
P24 只入档规则, 等真正要 bump 时才实装.

**触发条件**:
- 任一 schema_version 真的要 bump → 立刻做这条的完整落地
- 或 testbench 对外发布 → 提前做 (公开后版本兼容是硬要求)

---

#### 4.1.5 `record_internal` op 枚举无集中定义, 散落字符串字面量 【P24 必做, 轻量】

**证据 (sweep)**: 生产路径共识别 3 种 op:

- `"integrity_check"` (session_router.py::_verify_and_log_memory_hash)
- `"judge_extra_context_override"` (judge_router.py)
- `"prompt_injection_suspected"` (在 P21.3 的 injection detector 命中点, 暂未直接 grep 到调用行, P24 补)

**未来 P-A / F6 / F7 还会加**: `"orphan_sandbox_clean"` / `"judge_main_chat_sys_applied"` / etc.

**影响**:
- 字面量散落 → typo 不会报错 (`"integrity_chek"` 照样 record)
- 前端 F7 Security 子页做过滤时, 必须 hardcode 一份 "已知 op" 列表, 与后端漂移

**修法 (轻量)**:

```python
# pipeline/diagnostics_ops.py (新, ~30 行)
from enum import StrEnum

class DiagnosticsOp(StrEnum):
    INTEGRITY_CHECK = "integrity_check"
    JUDGE_EXTRA_CONTEXT_OVERRIDE = "judge_extra_context_override"
    PROMPT_INJECTION_SUSPECTED = "prompt_injection_suspected"
    # P24 加:
    ORPHAN_SANDBOX_CLEAN = "orphan_sandbox_clean"
    # ...
```

各生产调用点改成 `DiagnosticsOp.INTEGRITY_CHECK.value`, `record_internal`
签名不变 (仍吃 str, 向后兼容).

F7 前端查询时调 `GET /api/diagnostics/ops` 端点返回所有已知 op list,
不再硬编码.

**workload**: 枚举定义 ~30 行 + 3 处改写 ~5 行 + 1 个新端点 ~20 行 +
前端消费 ~20 行 ≈ **2 小时**

---

### 4.2 PLAN §15.3 原 7 条 sweep 清单

**不重写**, 直接引用 PLAN §15.3 的 7 条表格, 本蓝图 §4 补充执行说明:

| # | PLAN 原项 | 本蓝图补充 |
|---|---|---|
| 1 | `messages_changed / session:change / results_changed` 订阅全 caller 审计 | 并入 §4.1.3 事件总线 matrix, 统一一次做 |
| 2 | 非 ASCII 字面量硬编码 pre-commit | P24 加 `.cursor/rules/no-hardcoded-chinese-in-ui.mdc`; 存量允许 |
| 3 | Grid 容器子元素 vs template track 数一致性 | P24 grep `grid-template-(rows\|columns)` 逐个清点, 参考 `#100` + `css-grid-template-child-sync` SKILL |
| 4 | `??` 陷阱 | P24 grep `\?\?` + 附近 counter/length/空串语义的位置; 表格化审视 |
| 5 | pipeline store-like 基类候选 | **决策入档即可** (抽或不抽 + 理由) |
| 6 | SQLAlchemy engine 全仓 audit | 扩展 `_dispose_all_sqlalchemy_caches`; grep `_cache|_engine|sqlite3\.connect` |
| 7 | diagnostics_store ring buffer 200 vs 实际需求 | P24 联调采集真实速率, 决定是否扩至 500 |

### 4.3 新增 5 条扩展 sweep (D/E/F/H/I)

上一轮讨论确认的 5 条.

---

**D · SessionState 状态机一致性 (sweep 已做基础, 矩阵待画)**

**sweep 已确认**: 使用中的 SessionState enum 有
`IDLE / BUSY / SAVING / LOADING / RESETTING / REWINDING`. 无 `RUNNING /
AUTOPLAYING`. `run_session_work()` 是 state 切换的唯一入口 (session_store.py).

**P24 动作**:

1. 读 `session_store.py::run_session_work` 确认 state 切换不变式
2. 画矩阵 `operation × 期望进入的 state × 实际代码`:

   | op | 期望 state | 实际 (sweep 发现) |
   |---|---|---|
   | chat.send | BUSY | BUSY (chat_router.py × 5 处) |
   | stage.* | BUSY | BUSY (stage_router.py × 3 处) |
   | session.save | SAVING | SAVING (session_router.py:401) |
   | session.load | LOADING | LOADING (× 2 处 load + load_apply) |
   | session.reset | RESETTING | RESETTING (session_router.py:237) |
   | snapshot.rewind | REWINDING | REWINDING (snapshot_router.py:246) |
   | snapshot.manual_create | BUSY | BUSY |
   | snapshot.delete/rename | BUSY | BUSY |
   | session.autosave_restore | LOADING | LOADING (× 2) |
   | session.export (P23) | BUSY | BUSY (session_router.py:1027) |

3. **审 autosave 定时器 vs 用户操作的 state 竞争**:
   - autosave scheduler 跑 flush 时会 acquire `session.lock` 吗? 看 `autosave.py:838 _flush_lock`
   - 用户正在 BUSY (chat 生成中) 时 autosave tick 会等, 不会抢 → 无竞争
   - 但如果 autosave 正在写 (持锁) 时用户点 Save 会发生什么 UX?
     → 后端 409 / 前端现在如何处理?

4. **产出**: 一张 state × operation 矩阵 + autosave/用户竞争 UX 行为表 →
   入 AGENT_NOTES 附表

**workload**: 1-2 小时

---

**E · API error shape 一致性**

**sweep 已确认**: 三种 shape 并存 (见 §4.1.1), 已作为 §4.1.1 必做项.

**与 E 的区别**: §4.1.1 是"改代码统一 shape", E 是"审清楚 status code 分布
和前端 parser 对照". E 是 §4.1.1 的前置盘点.

**P24 动作**:
- grep 枚举 `(endpoint, status_code, detail_shape)` 三元组
- 前端 `static/core/api.js` parser 覆盖度对照
- 先盘点再改, 避免一次 diff 规模过大

---

**F · 时间戳字段命名 canonical 化**

**sweep 已确认**: 22 个文件命中 `created_at | timestamp | ts | generated_at | started_at | finished_at | at | updated_at`.

**P24 动作**:

1. 全仓 grep 列每个字段的实际含义 (注释或上下文判断)
2. **决定 canonical**: 建议 `created_at` 用于 "创建时刻 immutable",
   `updated_at` 用于 "最后修改时刻 mutable", `started_at / finished_at`
   用于 "操作起止 timestamp", `generated_at` 用于 "导出/快照生成时刻"
3. **不强制迁移存量**, 存量保留; 加 §3A 新条 A14 约束未来新字段走 canonical
4. 可选: 给前端 display utility 一个 `formatTimestamp(val, format)` helper, 消费时统一

**workload**: 2-3 小时 (grep + 决策 + 文档更新, 零代码改)

---

**H · api_key 保护面全仓扩展审计 (安全性)**

**sweep 已确认**: 22 个 Python 文件命中 `api_key | apiKey | "key" | secret`.

**P24 动作**:

1. 分类每个命中点:
   - **写入路径** (用户填 → config / memory)
   - **读取路径** (pipeline 取去调 LLM)
   - **展示路径** (API 响应回 UI)
   - **日志路径** (logger / diagnostics_store.detail)
   - **导出路径** (P23 已脱敏, 复验)
2. **硬约束**: 任何"展示 + 日志 + 导出"路径必须经过**一个 redact helper**
   (抽成 `pipeline/redact.py::redact_api_keys(obj: dict) -> dict`)
3. 重点抽查:
   - `diagnostics_store.record_internal(detail=...)` 的 detail 字段
     可能通过 chat_runner / judge_runner 间接含 api_key (error chain 带配置)
   - Judge run 的 `extra_context` 若用户贴 api_key 是否原样写 audit log
   - error logger 堆栈若含 provider config 是否 redact
4. 新加 §3A 新条 A15 "**api_key 脱敏是硬约束**, 凡离开后端进程范围
   (日志/UI/导出) 必须经 `redact_api_keys()`"

**workload**: 后端 ~2 小时盘点 + ~1 小时写 redact helper + ~2 小时落地
展示/日志/导出路径 ≈ **半天**

---

**I · uvicorn host binding 审查 (安全性)**

**sweep 已确认: ✅ 已合规**

- `run_testbench.py:7` docstring 明确写默认 `127.0.0.1`
- `run_testbench.py:83` 检查 `args.host not in ("127.0.0.1", "localhost", "::1")` → 打印 WARN
- `config.py:46` `DEFAULT_HOST: str = "127.0.0.1"` 有注释 "Bind to loopback only"

**P24 动作 (轻量)**:
- README (P25 做) 里加一条警告 "若改 0.0.0.0, Diagnostics/chat 历史会被同网段可访问"
- 考虑把 startup WARN 升级为"打印到 diagnostics_store.record_internal(op='insecure_host_binding')"
  以便 F7 Security 子页也看得到 (1 行代码)

**workload**: ~10 分钟 (若只加 record_internal)

---

### 4.4 P24 开工时继续 sweep 的候选 (未实证, 占位)

本蓝图 sweep 期时间所限, 以下方向是**有怀疑但未证实**, P24 第一天扩展
sweep 一次, 按实证结果入 §4.1 (必做) 或 §4.5 (backlog):

**J · async def router 里调 sync IO 是否需要 `run_in_threadpool`**
- 怀疑: `persistence.load_archive()` 读 tar.gz 几十 MB 可能阻塞 event loop
- sweep: `grep "async def" routers/` 对照内部 IO 调用

**K · Pydantic BaseModel vs 裸 dict 请求体**
- 怀疑: 老端点用裸 dict, 新端点走 BaseModel, 有 validation 差异
- sweep: `grep -n "body:\|dict\[str" routers/`

**L · teardown / cleanup 纪律**
- 怀疑: 页面切换时 `__off*` 字段清理是否全 (事件订阅拓扑 matrix 的副产品)
- 并入 §4.1.3 一起做

**M · DOM `append(null)` 安全性** (AGENT_NOTES #42 / #75)
- 怀疑: 可能还有其它路径踩同一坑
- sweep: `grep "\.append\("` 静态扫 nullable return 点

**N · grid-template 一致性 (原 sweep 第 3 条)**
- 已在 PLAN §15.3 #3

**O · `??` 陷阱 (原 sweep 第 4 条)**
- 已在 PLAN §15.3 #4

**P · 跨 workspace 旧数据污染**
- 怀疑: workspace 切换时模块闭包变量 (latestEvalByMsg 等缓存) 是否清理
- sweep: 每个 page.js 文件的 module-level state

**Q · 配置常量集中 vs magic number**
- 怀疑: autosave `_LOCK_CONTENTION_RETRY_SECONDS` / `_MAX_EMIT_DEPTH` / ...
  散落在代码, 无集中管理
- 动作: 决定抽 `config/constants.py` 或保持现状 + 文档化约定

---

### 4.5 长期债 backlog (不在 P24 做, 入档即可)

| 项 | 状态 | 触发条件 |
|---|---|---|
| **H3 tarball 内容 lint** | 不做 | 发布前 或 联调出 tar.gz 结构异常 |
| **Pipeline store-like 基类抽象** (sweep 原 #5) | 决策入档 | 抽象优先级低, 除非代码规模继续翻倍 |
| **diagnostics ring buffer 扩容** (sweep 原 #7) | 实测后决定 | P24 联调产出速率数据后再议; Day 10 已加 warn-once 提供观测手段, 决定扩容的触发下调到 "warn-once 冒出 ≥3 轮" |
| **schema_version 全局 migration framework** (§4.1.4) | 档案化 | 任一 schema 要 bump 时触发 |
| **`_atomic_write_gzip_json`** | ✅ 已交付 (Day 1-2, 并入 `pipeline/atomic_io.py`) | — |
| **F5 记忆 compressor 逃逸过滤** | 保留 "不做" | 联调出记忆污染实例翻案 |
| **G4 save 覆盖 `.prev` 轮替** | 保留 "不做" | 公开发布前 或 用户丢数据实例 |
| **日志级别规范化** | 未勘察 | 作为 M/N/O 候选并入 P24 sweep |

**§4 交付状态总结 (Day 11 回填)**: §4.1 五条实锤技术债 **✅ 全部落地** (§4.1.1 HTTPException Day 3 / §4.1.2 atomic_io Day 1 / §4.1.3 事件总线 Day 6 / §4.1.4 schema_version Day 3 决策档案化 / §4.1.5 时间字段 Day 3 沿用 `created_at/updated_at`). §4.2 原 7 条 sweep **✅ 全部扫完** (3 条 P20 前已做 + 4 条 P24 Day 1-5 完成). §4.3 五条新增扩展 sweep **✅ 全部落地** (D coerce Day 3 / E bindStateful Day 3.5 决策为 dev-only 检测 / F asyncio task cleanup Day 5 审通过 / H api_key redact Day 3 落 `pipeline/redact.py` / I uvicorn binding 合规). §4.4 继续 sweep 候选 **由 §12.1 / §12.2 / §13 三轮 sweep 完整接力**. §4.5 长期债 6 项 (扣 `_atomic_write_gzip_json` Day 1 落地, 实际 5 项) 保持 backlog 不动. **结论: §4 全量完工, 无留空项**.

---

## 5. 安全性审查章节 (独立组, 汇总 §4 里的安全相关)

P24 的安全性交付汇总一处, 便于验收:

| ID | 项 | 本蓝图节 | 状态 |
|---|---|---|---|
| ID | 项 | 本蓝图节 | 交付状态 (Day 11 回填) |
|---|---|---|---|
| Sec-1 | api_key 保护面扩展审计 (写/读/展示/日志/导出 五分类 + redact helper) | §4.3 H | ✅ **Day 3 交付**: `pipeline/redact.py::redact_api_keys()` + 5 出口审计 (logger / SSE frame / export json / diagnostics detail / error response); session_export Day 1 时已走 redact |
| Sec-2 | uvicorn 绑定审查 | §4.3 I | ✅ **Day 3 交付**: binding 合规 (`127.0.0.1` 非 `0.0.0.0`), 启动检测到不安全 binding 时 `record_internal(op=INSECURE_HOST_BINDING)` |
| Sec-3 | Diagnostics record_internal detail 含敏感数据审视 | §4.3 H (子项) | ✅ **Day 3 合并进 Sec-1**: `record_internal` 的 detail kwarg 在入环前过 `redact_api_keys`, 覆盖 `llm_failed` / `auto_dialog_error` 类含 provider config 的 error |
| Sec-4 | F4 extra_context override 审计 (P22.1 已做) | — | ✅ 已交付 |
| Sec-5 | F7 Security 子页 (P24 新) | PLAN §15.2 (D) | ✅ **Day 5 交付**: `GET /api/diagnostics/errors?op_type=a,b,c` 支持三类审计 op 共置一屏 (injection_suspected + judge_extra_context_override + prompt_injection_suspected + auto_dialog_error 四 chip), 前端 `page_errors.js` 加 chip 过滤器; Day 10 smoke case d 实证 |
| Sec-6 | Prompt injection 实战命中率采集 (数据, 非代码) | `p24_integration_report §2` | ✅ **Day 8 修 `h.to_dict()` bug 后手测命中正常**; 假阳率待 P25 长周期观察 |
| Sec-7 | P23 导出 api_key 脱敏 | — | ✅ 已交付 |
| Sec-8 | F5 记忆 compressor 过滤 | §4.5 | **保留不做** (无翻案触发) |

**§5 安全性章总结 (Day 11 回填)**: 8 项中 7 项 ✅ 交付 / 1 项保留不做. 代码交付与 `p24_integration_report.md §3` cross-reference 完整, 审计事件共置一屏在 Diagnostics → Errors 里走 F7 op_type 过滤器 4 chip 呈现, 日常观测手段齐全.

---

## 6. 主程序同步 (见 PLAN §15.4, 本蓝图不重写)

按 PLAN §15.4 三面盘点: `sandbox.py` attribute swap / `memory/` schema /
`utils/llm_client.py` provider. P24 开工 Day 0-1 先跑一次
`git log --since=<P01 起始日> -- config/ memory/ utils/ pipeline/` + 结果表
→ 同步修正 → 写入 `p24_integration_report §4`.

**§6 交付状态 (Day 11 回填)**: **Day 9 主程序同步五项盘点全零行动** (2026-04-22 完结, ≈ 0.25 天 vs 蓝图估计 1 天). 五项详情见 AGENT_NOTES §4.27 #108 (A-E 段):

- (A) `_PATCHED_ATTRS` 15 项 vs `utils/config_manager.py` — 完全对应; 近期新增 11 个 `@property def cloudsave_*_dir` 走 `app_docs_dir / "..."` 动态计算, `app_docs_dir` 已在 `_PATCHED_ATTRS` 里**自动跟随**沙盒重定向, 无需扩白名单 (Day 10 `smoke/p24_sandbox_attrs_sync_smoke.py` 实证).
- (B) memory schema — 主程序 `memory/{persona,facts,recent,reflection,time_indexed}/` 五类未新增字段.
- (C) `utils/llm_client.py` provider — QwenPaw / OpenRouter / Grok / Doubao 新增 provider 适配走既有 `ChatOpenAI.astream` 通路, 无新 schema.
- (D) 道具交互 (PR #769 `prompts_avatar_interaction.py`) — 一轮 "架构不兼容 → out-of-scope" 被**二轮翻转**为 "pure helper 层必须接入, 同族 agent callback + proactive 一并接入", 单开 **P25 外部事件注入阶段** 专门处理, 蓝图 [P25_BLUEPRINT.md](P25_BLUEPRINT.md). 本 phase 零行动含义从"永久排除"改为"本阶段不实施, 已立项 P25".
- (E) 静态回归 — Day 9 无代码变动 smoke 不重跑 (Day 8 已绿); Day 10 再跑一轮 9 份全绿.

**结论**: 主程序同步已完成, 所有 @property 动态计算类路径**天然跟随沙盒重定向**, 是本阶段"近乎零技术债"的关键设计: `app_docs_dir` 这条根入口被 patch 后, 11 个 cloudsave @property 无需单独 patch. 这本身是 **L23 元教训 "@property vs 直接赋值"** 的实证 (LESSONS_LEARNED §2.9A).

---

## 7. 交付物规格

### 7.1 代码产物清单 (带 workload)

| # | 项 | 节 | 估 workload | 归属 |
|---|---|---|---|---|
| 1 | **H1 最小健康端点** + Paths 顶部卡片 | §3.1 | 1d | 后端 + 前端 |
| 2 | **H2 单条 archive schema lint** 端点 + Load modal 按钮 | §3.2 | 0.5d | 后端 + 前端 |
| 3 | **P-A** 沙盒孤儿扫描 | PLAN §15.2(A) | 0.5d | 后端 |
| 4 | **P-D** Paths 孤儿区 + 清理 modal | PLAN §15.2(B) | 0.5d | 前端 |
| 5 | **F6** judger match_main_chat 开关 | PLAN §15.2(C) | 0.5d | 后端 + 前端 |
| 6 | **F7** Diagnostics Security Option B | PLAN §15.2(D) | 0.5d | 前端 |
| 7 | **HTTPException shape 统一** | §4.1.1 | 1d | 后端 helper + 迁移 |
| 8 | **atomic_io.py 抽象 + 5 处迁移** | §4.1.2 | 1d | 后端 |
| 9 | **事件总线 matrix 生成 + 修正** | §4.1.3 | 1d | 前端 |
| 10 | **DiagnosticsOp 枚举** | §4.1.5 | 2h | 后端 |
| 11 | **renderAll dev-only 漂移检测** | §3.5 | 1.5-2d | 前端 |
| 12 | **api_key redact helper + 展示/日志路径迁移** | §4.3 H | 0.5d | 后端 |
| 13 | PLAN §15.3 原 7 条 sweep (除去并入其它项) | §4.2 | 1d | 混合 |
| 14 | §4.4 开工期扩展 sweep (J-Q 候选) | §4.4 | 1d | 盘点为主 |
| **总计** | | | **~10-11 天** | |

### 7.2 文档产物

1. **`p24_integration_report.md`** (按 PLAN §15.5 模板, 本蓝图不重写)
2. **本蓝图更新为"交付记录版"** (P24 完工后): 每个 §4 项后追加"交付状态 + commit ref"
3. **三份老 docs 回写**: §9 规定动作

### 7.3 烟测

**新增 `tests/testbench/smoke/p24_integration_smoke.py`** (按 PLAN §15.6 模板) + **扩展**:

- 新增: H1 `/api/system/health` 结构 assertions
- 新增: H2 `/api/session/archives/{name}/lint` 正常 + 异常档
- 新增: HTTPException shape 全 router 单测 (覆盖 persona / config /
  session / memory / snapshot / chat / judge 共 ~40 处改动)
- 新增: atomic_io fsync regression (扩展 P21.1 G1 源码级检查覆盖 6 处而非 1 处)
- 新增: DiagnosticsOp 枚举与 `record_internal` 调用点一致性 (源码检查)
- 新增: renderAll 漂移检测 jsdom 单测 (manufacture "改了 state 没 renderAll"
  场景, 验证 warn 触发)

**回归**: `p21_*_smoke / p22_*_smoke / p23_*_smoke` 全跑, 任何红灯 =
P24 hotfix 最高优.

**§7 交付状态 (Day 11 回填)**: P24 共交付 **4 份新烟测** (`p24_integration_smoke` / `p24_session_fields_audit_smoke` / `p24_sandbox_attrs_sync_smoke` / `p24_lint_drift_smoke`) + `p21_*_smoke` / `p22_*_smoke` / `p23_*_smoke` 5 份既有 **共 9 份**. Day 10 全量回归 9/9 绿. Day 12 收尾后再跑一轮仍然 9/9 绿. 覆盖面清单:

- **Day 10 新增 `p24_integration_smoke.py`** (5 check): (a) GET `/system/orphans` 结构 / (b) DELETE `/system/orphans/{sid}` 4 response codes / (c) POST `/judge/run?match_main_chat=true` system_prompt 字节一致 / (d) GET `/diagnostics/errors?op_type=a,b,c` F7 三 chip 共置 / (e) **`diagnostics_ring_full` warn-once** (两 cycle: fill + overflow 出一条 notice → clear 后重填再出一条).
- **`p24_session_fields_audit_smoke.py`** (5 check): Session dataclass 9 persist + 11 runtime 分类完整, describe / serialize_session / snapshot_store.capture / session_export 四出口无 runtime 泄漏.
- **`p24_sandbox_attrs_sync_smoke.py`** (5 check): 14 direct assignment 全在 `_PATCHED_ATTRS`, 16 @property 全分类白名单对齐.
- **`p24_lint_drift_smoke.py`** (5 hard rule): i18n curry / UI 硬编码中文 soft warn / single-append-message / atomic-io-only / emit-grep-listener 全绿 (14 条 soft warn 是 tester 专用 error fallback 文案, 不阻塞).
- **既有 5 份回归全绿**: `p21_persistence` / `p21_1_reliability` / `p21_3_prompt_injection` / `p22_hardening` / `p23_exports` 共覆盖 persistence G1-G10 / P-B boot cleanup / F1-F4 注入防御 / G3 memory_sha256 / 6 scope × 3 format 导出矩阵.

**烟测设计原则沉淀 (Day 11 归纳)**: P24 的 4 份新 smoke 做了"**静态 + 动态**" 双模式混合 — `session_fields_audit` / `sandbox_attrs_sync` / `lint_drift` 三份纯静态 (inspect + rg), 不跑服务; `integration_smoke` 动态起 TestClient 跑端到端. 静态 smoke 的 ROI: 快 (<2s), 不依赖 LLM, 守白名单漂移 / 原则漂移 / 编码漂移, 是 P25+ 新增 choke-point 原则时的首选**守护模板**.

---

## 8. 执行顺序 (Day-by-Day, 建议)

> 每一步完工后进 §4 item 的 "交付状态" 列填 "done + commit ref"

**Day 0 (半天, 开工准备)**:
- 读本蓝图全文
- 执行 §2.3 G 编号澄清 (三份老 docs 各加一行)
- 跑一遍 §4.4 扩展 sweep (J-Q), 把实证结果写入 §4.1 或 §4.5

**Day 1 (代码审查实证落地)**:
- §4.1.2 `atomic_io.py` 新模块 + 迁移 5 处 + 烟测
- §4.1.5 `DiagnosticsOp` 枚举 + 3 处改写

**Day 2 (审查 + 安全性)**:
- §4.1.1 HTTPException shape 统一 + 前端 parser 简化
- §4.3 H api_key redact helper + 展示/日志路径迁移 + Sec-2 记录 event

**Day 3 (延期加固前半)**:
- P-A sandbox 扫描 + health_router 扩展
- P-D Paths 孤儿区 UI + 清理 modal
- H1 健康端点 + Paths 顶部卡片 (复用 Day 3 的 Paths 改动)

**Day 4 (延期加固后半)**:
- F6 judger match_main_chat + page_run checkbox
- F7 Security Option B (Errors 子 tab 过滤)
- H2 archive schema lint + Load modal 按钮

**Day 5 (前端机制性工作)**:
- §4.1.3 事件总线 matrix 生成 + 修正
- §3.5 renderAll 漂移检测主体

**Day 6 (前端机制性工作 续)**:
- §3.5 完成 (checker 注册各页 + jsdom 烟测)
- §4.2 PLAN §15.3 剩余 sweep 项

**Day 7 (联调)**:
- 跑 §1.1 #1 端到端真实模型闭环 10+ 场景
- 采集资源数据填 `p24_integration_report §2`
- 记 bug 清单

**Day 8 (bug 修 + 主程序同步)**:
- Day 7 暴露的 bug 消化
- §6 主程序同步盘点 + 修正
- §4.3 F 时间字段 canonical 决策入档

**Day 9 (烟测 + 验收)**:
- 全量 smoke regression (p21_* / p22_* / p23_* / p24_*)
- `p24_integration_report` 终稿
- 本蓝图 §4 / §7 填 "交付状态"

**Day 10 (文档回写)**:
- §9 规定动作: 更新 PLAN §15 P24 / PROGRESS P24 详情 / AGENT_NOTES §4.27 #104
- commit message 规范: 每个 §4 项一次 commit, message 引本蓝图节号

---

## 9. P24 完工后的文档回写规定动作

P24 宣告完工前, 必须执行以下回写:

### 9.1 PLAN.md
- §15 P24 实施细化最后加一段 "**P24 已于 <date> 交付, 详见
  `P24_BLUEPRINT.md`**"
- §11 G 编号表格最后加 §2 "**G5'/G6'/G7' 已改名 H1/H2/H3, 见 P24_BLUEPRINT §2**"
- §15.2 / §15.3 条目按本蓝图实际交付情况更新 "已交付" 标记
- §1 YAML todos: `p24_integration_review` 状态 `pending` → `done`

### 9.2 PROGRESS.md
- 阶段总览表 P24 行 status `pending` → `done`
- P24 详情节展开"已交付"清单 + 指向本蓝图
- 依赖图重绘 `DONE → P25`
- changelog 加 "P24 交付实录"条目

### 9.3 AGENT_NOTES.md
- 顶部 "接手 P24 前必读" 改成 "接手 P25 前必读"
- §4.27 新增 **#104** "P24 整合期交付复盘":
  - 实际交付清单 vs 本蓝图 §4 规划 差异记录
  - 联调期发现的 bug / 新架构空白 (入后续 backlog)
  - 3-5 条 "延伸教训" (归纳候选进 §3A)
- §3A 正式追加新条 (本蓝图产生的候选):
  - **A12** HTTPException detail 必须 dict shape
  - **A13** schema_version 三件套 (read check + write pin + migration table)
  - **A14** 时间字段 canonical 命名
  - **A15** api_key 脱敏硬约束
  - 可能更多

### 9.4 本蓝图自身
- §4 每项后追加 "交付状态: done + commit ref"
- §7.3 烟测清单每项追加"已覆盖 (smoke file, line)"
- 末尾加一节 "## 11. P24 交付后回顾 (complete)"

---

## 10. 后续 P25 边界

P25 是**纯用户 README + 文档文档**, 不承担代码交付. 本蓝图中以下内容
自动向 P25 交付链传递:

- `p24_integration_report.md §2 实测资源数据` → P25 README "推荐配置"章节
- §4.3 I `insecure_host_binding` warning → P25 README "部署注意事项"
- §7 交付清单 → P25 README "功能清单"章节
- §4.1 / §4.3 的技术债修复 → P25 README "已知限制" 减少

**P25 不是 P24 的子任务**, P25 本身有独立 todos (见 PLAN §1
`p25_docs`).

---

## 11. P24 交付后回顾 (complete) — v1.0 Sign-Off (2026-04-22 Day 11-12 回填)

> **P24 作为 "第一个完善版本" 的定版**: 按用户 dev_note 2026-04-22 定位,
> "P24 完成代表原计划中各个主要功能的编写与调试基本完成, 整个系统已经是一个**功能完整、
> 经过了系统性调试的完善测试生态**. 之后加的这些内容 (P25 / P26) 属于**版本更新**."
> 因此本节不仅是 P24 的章末小结, 同时是 testbench **v1.0 基线 sign-off**.

### 11.1 计划 vs 实际 (Workload & Day 总帐)

| 维度 | 计划 (开工前 §14.8) | 实际 (2026-04-22 交付) | 偏差原因 |
|---|---|---|---|
| 总工作量 | 15-19 天 (连续日) | ≈ 14 天 (2026-04-21 开工 → 2026-04-22 Day 12 收尾, 密集双日完成) | 主程序同步零行动 (Day 9 0.25d vs 计划 1d) + 联调无阻塞型 bug (Day 8 手测已随开发随手修) + Day 6 独立 pass 推迟到 Day 10 批量处理节省切换成本 |
| Day 1 | 文档层 + 低风险 warmup | ✅ 2026-04-21 交付 | — |
| Day 2 | 后端 choke-point 抽象 (`messages_writer`) | ✅ 2026-04-21 交付 + 虚拟时钟三层防线 | 虚拟时钟 bug 在 Day 2 当天修完, 属 A7 A17 实装 |
| Day 3 | API shape + 安全 + 字段同步 | ✅ 2026-04-21 交付 | — |
| Day 4 | 延期加固前半 P-A / P-D + H1 / H2 | ✅ 2026-04-21 交付 | — |
| Day 5 | 延期加固后半 F6 / F7 + 时间系统 | ✅ 2026-04-21 交付 | — |
| Day 6 | 前端机制守卫 (7 项) | 全交付 (6B/6C/6F/6G Day 6 当天 + 6E Day 8 M1 合并审通过 + 6A/6D Day 12 欠账清返) | 6A renderAll drift 原推 Day 10 未做, Day 12 欠账清返做完 (骨架+3 checker); 6D lazy init 推 Day 10 未做, Day 12 做完 (page_persona Promise cache 重构 + composer 3 处 `.catch` 补齐); 6E 与 Day 8 M1 取消语义合并审通过 |
| Day 6 末验收 | — | **事故 hotfix #105**: New Session 事件级联风暴导致整机卡死, 三层防线修复 + 新 rule `global-state-clear-must-reload.mdc` + LESSONS #20 | §3A B13 升级为铁律; `page_reset medium` / `rewind` / `archive_deleted` 3 处同族 sweep 补齐 reload |
| Day 7 | 前端 UI 偏好 + 收尾 | ✅ Snapshot limit / 默认折叠 / auto_dialog 多 error panel + `#105` 同族 sweep | — |
| Day 8 | 端到端联调 (1.5d) | 实际 ≈ 2.5d, 4 轮手测反馈 hotfix #107 Part 1-4 全部消化 + 静态审 M1 取消语义通过 + CFA 路径分裂 bug 定位 | 用户反馈 12 场景 + dev_note 17 项 Day 1-8 已逐步覆盖, 没有新增 "happy path 未验证" 情况 |
| Day 9 | Bug 修 + 主程序同步 (1d) | 实际 ≈ 0.25d, 五项盘点全零行动 | 主程序近期变动 (云存档 #681 / QwenPaw / OpenRouter / Grok/Doubao / avatar interaction) 全部走 @property 动态计算或 P08/P09 改动, 零技术债 |
| Day 9-E 二轮翻转 | — | PR #769 avatar interaction 从 "out-of-scope" **翻转**为 "pure helper 必须接入 + 同族 agent callback + proactive 一并", **新开 P25 阶段**, 原 P25 README 顺延 P26 | 用户二轮澄清 testbench 定位 = "新系统对对话/记忆影响测试生态" 后, 方法论更新: **语义契约 vs 运行时机制** (L24) + **@property 天然跟随重定向** (L23) + **影响评估范围不取决于能否复现运行时** (L25) |
| Day 10 | 烟测 + 白名单守护 (1d) | ✅ 3 份新 smoke + §14.4 M4 warn-once + §14.2.D A6/A9 复核 + §14.2.E UX 降级总表 15 项 | L26 / L27 两条元教训候选产出 |
| Day 11 | 文档回写 (0.5d) | 实际 ≈ 0.7d, 6 份 docs 全量回写 + §3A 10 条新增 + LESSONS_LEARNED §7 扩容 | 涵盖面扩大 (原计划只回写 PLAN/PROGRESS/AGENT_NOTES, 实际扩到 LESSONS_LEARNED + §3A 正式入档) |
| Day 12 | 验收 buffer | ✅ v1.0 sign-off + p24_integration_report 终稿 + 收尾后全量 smoke 再跑一轮 + **欠账清返 2 条** (render_drift + persona Promise cache) | 用户 Day 12 末提醒 "不要留尾巴", 全仓扫出 2 条真欠账当场做完, 填补 Day 6 两个遗留的 `[ ]` 和 §13.6 F4 checkbox 漏回填 |

**总结**: P24 按计划落地, 唯一主要溢出点是 Day 6 验收期 `#105` 事故 + Day 8 四轮反馈消化, 两者都在同一阶段内消化完, 没拖到 P25. workload 偏 **15 天侧** (总计划的下限), 主要受益于主程序同步零技术债.

### 11.2 §3A 候选新条落地 (9 新 + 1 修订, 对齐 Day 11 checklist)

全部正式入 `AGENT_NOTES.md §3A`:

| 条目 | 来源 | 内容摘要 |
|---|---|---|
| **A7 修订** | §12.5 虚拟时钟回退 bug | 原版 "写入点守住" 强化为 "**单一 choke-point**"; 多源写入必须收敛 helper (对应 `pipeline/messages_writer.append_message`) |
| **A12** | §4.1.1 HTTPException 分布 | 所有 HTTPException detail 必须 dict shape `{message, code?, data?}`, 禁止裸 string |
| **A13** | §4.1.4 schema_version 策略 | 凡含 `schema_version` 序列化 payload 必须有 (a) read 端 "支持版本范围"常量 + (b) write 端 pin 当前版本 + (c) migration 函数表 (即使为空) |
| **A14** | §4.1.5 时间字段命名 | 时间字段 canonical 命名: 操作起止走 `created_at` / `updated_at` / `started_at` / `finished_at`, 导出/快照"生成时刻"走 `generated_at` |
| **A15** | §4.3 H api_key 五面审计 | **api_key 脱敏硬约束**: 凡离开后端进程范围 (日志/UI/导出) 必须经 `redact_api_keys()`, 不是选 feature |
| **A16** | §12.5 L2 时间回退 | 可回退的时间/游标机制必须配 **pre-action warning**, 不能仅靠 post-action 422/coerce |
| **A17** | §12.5 派生 | `session.messages.append()` 必须走 `append_message` choke-point; CI 建议加 pre-commit hook `rg 'session\.messages\.append\(' -g 'pipeline/**' -g 'routers/**' --invert-match` 命中即 block |
| **A18** | §14.2 choke-point 合规扫描 | 任何声称 "X 统一走 Y" 的原则必须同时建立 (a) 运行期守护 (helper 或 lint) + (b) **静态核查方法** (grep 模式 + 覆盖率期望). 缺 (b) 就是纸面原则, 半年后必然漂. 阶段验收 KPI: choke-point 合规率 |
| **B14** | §12.1 事件总线 matrix | 双向检查: emit 前查 listener **+** on 前查 emitter, 任一侧 0 命中都是违规 (dead emit 与 dead listener 同罪) |
| **B15** | §12.3.B 删 disabled + 清 TODO | 开发期 UI 占位控件 (`disabled=true` + toast 占位) 不得长期驻留, 必须**接线完整或删除占位**, 半衰期限制 2 个 phase |
| **E3** | §12 本身 | 开工前 **Full-Repo Pre-Sweep** 作为大阶段 (≥ 1 周) 默认动作; 整合期专有 section (§12) 记录 sweep 结果 |

### 11.3 为 P25 交付传递的 data

| 数据 | 来源 | 用途 |
|---|---|---|
| `§14.2.E 资源上限 UX 降级总表` 15 项 ⚠ 7 项 / ⏭ 2 项 | §14.2.E | P25 Blueprint "本阶段不做" 列表: snapshot cold 无上限 / judge eval_results silent evict / judge batch 50 静默截断 / memory file 10MiB skip 静默 / raw_response 4000 字静默截断 / session archive 导入大小 ⏭ / 巨型 memory 目录 ⏭ |
| 主程序同步 five-point 结论 | §6 / AGENT_NOTES #108 | P25 的 external_events 只复用 `config.prompts_*` pure helper + `main_logic.cross_server._should_persist_avatar_interaction_memory`; 零修改主程序 |
| Diagnostics ring warn-once 机制 | §14.4 M4 / Day 10 实装 | P25 新增的三个 DiagnosticsOp (`avatar_interaction_simulated` / `agent_callback_simulated` / `proactive_simulated`) 共享这套 ring buffer, warn-once 自动适用 |
| Session 字段 ledger (9 persist / 11 runtime) | `smoke/p24_session_fields_audit_smoke.py` | P25 若要给 session 加新字段 (e.g. `avatar_dedupe_cache`), 必须在 dataclass docstring 标 `# runtime-only` 并更新 ledger, smoke 会机械守护 |
| F7 Diagnostics Errors 三 chip 共置 | §5 Sec-5 | P25 新增的 3 个 op 落入既有 chip 分类: `avatar_interaction_simulated` 走 info 类 (不算 security), 配 `p24_lint_drift` 5 rule 不需扩 |
| `choke-point 合规率 KPI` (A18) | §11.2 | P25 开工前要求给 `append_message` / `bindStateful` 这类已落地 choke-point 做一次覆盖率扫描, 作为 P25 Day 0 动作 |
| **语义契约 vs 运行时机制** 方法论 (L24) | P25_BLUEPRINT §2.1 | P25 所有设计决策都走这个方法论 — 不复现 WebSocket / multiprocess queue / SID race guard / 冷却计时, 只复现 prompt 模板 + memory note 模板 + dedupe 策略 |

### 11.4 P24 的元教训沉淀汇总

Day 2-5 从 §12.10 / §13.11 / §14.9 / §14A.7 / L23-L25 / L26-L27 合计 **13 条元教训**被正式入档到 `LESSONS_LEARNED.md`:

- **§1 核心方法论 (5 条跨项目)**: Intent ≠ Reality / 多源写入分水岭 / 方法论立即扩大应用 / 覆盖度 RAG 灯 / 语义契约 vs 运行时机制 (L24)
- **§2 项目内架构原则 (10 条)**: 含 L23 @property vs 直接赋值 (Day 9 派生)
- **§7 元教训 (Day 11 扩容后 22 条)**: L26 yield 型 API 三分类 / L27 资源上限 UX 横切维度 两条为 P24 Day 10 新增

**L 编号表**:
- L23 · **@property vs 直接赋值属性 sandbox patching 策略** (Day 9 派生, LESSONS §2.9A)
- L24 · **语义契约 vs 运行时机制 (OOS 判据)** (Day 9-E 派生, LESSONS §1.6)
- L25 · **影响评估范围不取决于能否复现运行时** (Day 9-E 派生, LESSONS §1.6 配套推论)
- L26 · **"yield 型 API" 必须拆成请求-响应 / 真 generator / Template Method base 三种再套原则** (Day 10 派生, LESSONS §7.20)
- L27 · **资源上限 UX 降级是横切维度**, 每次加 FIFO / 截断 / 限流机制必答"上限是多少 / 达到做什么 / 用户怎么知道 / 要不要 actionable" 四问 (Day 10 派生, LESSONS §7.21)

### 11.5 v1.0 基线定义 (给 P25+ 使用)

**"testbench v1.0 = P21 + P21.1 + P21.2 + P21.3 + P22 + P22.1 + P23 + P24 全部交付 + 0 回归"**:

- **代码面**: 30 个 P21+ 新文件 (pipeline/ 11 + routers/ 1 + static/ 6 + smoke/ 5 + docs/ 5 + tests/run_testbench.* 3); 59 个既有文件更新 (routers/ / pipeline/ / static/ 全部). P24 Day 10 已跑 9/9 smoke 全绿.
- **能力面**: 14 个域 (会话沙盒 / 虚拟时钟 / 人设&真实角色 / 三层记忆读写 / 记忆触发 / Prompt 双视图 / Chat 消息流 / SimUser / Scripted / Auto-Dialog / Stage Coach / 模型提醒横幅 / ScoringSchema / 4 类 Judger + Run 子页) + P21+ 加固面 (崩溃恢复 / 注入防御 / schema lint / 健康自检 / orphan 扫描 / 导出矩阵) 全量就位.
- **设计原则面**: §3A 56 条原则 (A1-A18 + B1-B15 + C1-C7 + D1-D3 + E1-E3 + F1-F7) 全量入档, A7 修订完成, §11.2 新 10 条实证入库.
- **文档面**: 6 份 docs (PLAN / PROGRESS / AGENT_NOTES / P24_BLUEPRINT / P25_BLUEPRINT / LESSONS_LEARNED) + `p24_integration_report.md` 终稿 + 阶段性 README 层留给 P26.
- **git 面** (2026-04-22 Day 12 实际值): P21 → P24 一次性 `feat(testbench): P21-P24 持久化+可靠性+导出+集成审查栈 + v1.0 "第一个完善版本" sign-off` 单 commit = `4964941` (99 files, +23696/-606), 对齐 `8f8dc63` (P15-P20) / `14c98c8` (P13-P14) 跨 phase commit 前例; 随后 `Merge remote-tracking branch 'NEKO-dev/main'` = `cb394ab` 并入上游 27 条 (主程序: live2d/memory RFC/autostart/soccer-demo/i18n es+pt/plugin logging/model-profiler, 与 `tests/testbench/` 零重叠, `ort` strategy 自动合并无冲突); `git push NEKO-dev main` 成功 (`474aa23..cb394ab main -> main`). merge 后 `p24_sandbox_attrs_sync_smoke` 重跑 PASS (14 direct + 16 @property 漂移守门确认上游无侵入).

**版本语义**: v1.0 对应 "**原计划 25 阶段中的主要功能开发完结**"; v1.1+ 走 P25 (外部事件注入) / P26 (用户 README) 两阶段, 扩 "新系统对对话/记忆影响测试生态" 语义 + 用户门面文档. 未来再新增主程序系统 → testbench 影响评估接入都按 P25 方法论 (语义契约 vs 运行时机制, L24) 走, 不触 v1.0 骨架.

---

---

## 12. 开工前 Full-Repo Pre-Sweep 审查 (2026-04-21 第二轮)

> **本章的由来**: 用户 P23 交付后 dev_note 指出一批 "留空忘做 / UI 黑按钮 /
> 时间系统边界 / 事件级联 / 主程序同步" 类**具体可观察问题**, 并要求 "**在更大、
> 更全面的范围内全面审视当前的设计规范, 审视代码实现了什么, 设计和前期开发落下了
> 什么, 这些技术债全都要在 P24 补上**".  本章是这一轮审查的完整结果, 证据级别
> 以 sweep 实证为主, 推测次之, 所有项都有代码级定位或 §3A 原则引用.

### 12.1 全局事件总线 Matrix (§3A B12 实证违规 ≥ 6 处)

**方法**: 全仓 `rg "emit\('[a-z_:]+'"` + `rg "\bon\('[a-z_:]+'"`, 交叉制表.

**Emitter × Listener 表 (2026-04-21 实测)**:

| 事件名 | emitter 文件:行 (个数) | listener 文件:行 (个数) | 状态 |
|---|---|---|---|
| `session:change` | state.js:47 (由 `set('session', v)` 隐式) | 15 处 (topbar / workspace_chat/setup/evaluation/diagnostics/settings / preview_panel / page_snapshots / page_aggregate / page_results / page_run / topbar_stage_chip / topbar_timeline_chip / composer / model_config_reminder) | ✅ 正常 |
| `active_workspace:change` | state.js (隐式) | 8 处 (app / workspace_* × 5 / stage_chip / model_config_reminder) | ✅ 正常 |
| `errors:change` | errors_bus.js:217 | topbar / page_errors | ✅ 正常 |
| `http:error` | api.js:111 | errors_bus.js:160 | ✅ 正常 |
| `sse:error` | api.js:144 / sse_client.js × 2 | errors_bus.js:172 | ✅ 正常 |
| `chat:messages_changed` | composer × 3 / auto_banner:155 / message_stream:433 | workspace_chat:86, page_run:128 | ✅ 正常 |
| `judge:results_changed` | page_run:899 | message_stream:541, page_aggregate:97, page_results:162 | ✅ 正常 |
| `snapshots:changed` | timeline_chip × 2 / page_snapshots × 5 / reset_page:278 | page_snapshots:92, timeline_chip:401 | ✅ 正常 |
| `stage:needs_refresh` | timeline_chip:358, page_snapshots:443, reset_page:279 | stage_chip:544 | ✅ 正常 |
| `session:loaded` | session_load_modal:196, session_restore_modal:180 | topbar:247 | ✅ 正常 |
| `session:saved` | session_save_modal:193 | topbar:259 | ✅ 正常 |
| `evaluation:navigate` | stage_chip:492, message_stream:332 | workspace_evaluation:149 | ✅ 正常 |
| `diagnostics:navigate` | timeline_chip:380, topbar:306 | workspace_diagnostics:136 | ✅ 正常 |
| `setup:goto_page` | stage_chip × 3 | workspace_setup:122 | ✅ 正常 |
| `settings:goto_page` | model_config_reminder:197 | workspace_settings:94 | ✅ 正常 |
| `auto_dialog:finished` | auto_banner:290 | composer:1240 | ✅ 正常 |
| `scripts:templates_changed` | page_scripts × 3 | composer:1228 | ✅ 正常 |
| **`messages:needs_refresh`** | timeline_chip:359, page_snapshots:444 | **0 处** | ⚠ **B12 违规** |
| **`memory:needs_refresh`** | timeline_chip:360, page_snapshots:445 | **0 处** | ⚠ **B12 违规** |
| **`stage:change`** | stage_chip:506 | **0 处** | ⚠ **B12 违规** |
| **`session:exported`** (P23 新) | session_export_modal:447 | **0 处** | ⚠ **B12 违规** |
| **`session:archive_deleted`** | session_load_modal:216 | **0 处** | ⚠ **B12 违规** |
| **`clock:change`** | **0 处** | composer:1236 | ⚠ **反向 B12 违规** (dead listener) |

**6 处违规分析**:

- **`messages:needs_refresh` / `memory:needs_refresh`** — §4.26 #87 P20 hotfix 1 就已经发现 "全仓 0 listener 但仍在 emit", 当时的 Hard Reset 事件风暴分析里明确指出, **至今未修 emit 点** (timeline_chip / page_snapshots 里仍在调). 这是项目里**已知的技术债但错过了三次 pass**. P24 必修.
- **`stage:change`** — stage_chip 在状态变化时 emit 携带 data, 但**没人监听**. 推测是为未来预留的 hook 点, 但违反 B12 "emit 前必须 grep 确认有 listener". P24 处理: 要么加 listener (比如 diagnostics 审计记录状态变化历史), 要么删 emit.
- **`session:exported` (P23 新增)** — 导出成功时 emit, payload 含 `{scope, format, filename}`, 但没人 on. 可能用途: topbar 最近导出提示 / diagnostics 导出历史. 当前 0 listener, 要么删要么 stub topbar 订阅.
- **`session:archive_deleted`** — 删除存档后 emit, 但是没有 listener (Load modal 本身刷新在同一函数里已做). 删冗余 emit.
- **`clock:change` 反向违规** — composer 监听此事件刷新时钟显示, 但**没人 emit**. 推测是早期设计 (time_router 应该 emit, 但没做). 现在 composer 的时钟刷新只靠 `session:change` 被动驱动, 用户在 Time 面板调整时钟不会立刻反映到 composer. P24 补 emit 点 (`time_router.update_now` / `advance` 等成功后 emit).

**12.1 P24 动作**:

1. **删除 dead emit**: `messages:needs_refresh` / `memory:needs_refresh` / `stage:change` / `session:exported` / `session:archive_deleted` 5 处. 每处注释掉改带**原因注释** (例 "removed because no listener after 2026-04-21 audit; re-add with listener if future feature needs it").
2. **补 dead listener 的 emitter**: `time_router` 所有修改虚拟时钟的端点成功返回前 emit `clock:change`. 包括 `POST /api/time/set` / `/advance` / `/stage_next_turn` 等.
3. **更新 §3A B12**: 追加一条 "**emit / on 双向任何一侧为 0 都是违规**", 不是只查 emit 端 (dead listener 也是).
4. **加一个 dev-only 自检**: `state.js::emit` 增加 optional `DEBUG_UNLISTENED=true` 环境标志, 运行期在无 listener 时 `console.warn` 一次 (per event name dedup). 和 §3.5 的 renderAll drift detector 同时落地.

**workload**: 3-4 小时 (全是 grep 改 5-6 行 + 1 个新环境标志 + §3A 更新).

---

### 12.2 §3A 47 条对 P21-P23 新代码合规度扫描

**方法**: 对 P21 (persistence/session_router load/save) / P22 (autosave) / P23 (session_export) 三批新代码逐条对照 §3A 47 条.

**总结**: 新代码整体合规度较高, 新发现问题集中在 3 个已写入 §4.1 的系统性债 (atomic_write fsync 1/6 / HTTPException shape / schema_version 分散). 其它各条原则**未见直接违反**, 但以下几处边缘性风险需 P24 复核:

**12.2.A · A1 软错硬错契约**: P23 `POST /session/export` — 非法 scope/format 组合走 `HTTPException(400, "InvalidCombination")` 是硬错契约正确; 但 `persistence.import_from_payload` 在 P22.1 哈希不匹配硬拒也是硬错 — **两者 shape 不一致** (一个 str 一个 dict), 合规但需走 §4.1.1 shape 统一.

**12.2.B · A4 长流水锁粒度**: P22 autosave scheduler 的 `_flush_lock` 独立于 session.lock, 互相独立. **复审点**: scheduler 持 `_flush_lock` 扫描时用户正好 `/session/save`, 两者是否真的不抢资源 → 需 P24 联调期实测验证 (属于 §4.3 D SessionState 矩阵 的内容).

**12.2.C · A7 时间戳单调**: P23 `build_dialog_template` 里的 `time.advance` 计算 → 基于 messages[i].timestamp - messages[i-1].timestamp, 如果历史 messages 违反单调 (不应该, 但**§12.5 时间回退 bug** 理论上能产生), dialog_template 会得到负值 advance. **需**: `build_dialog_template` 内加 `max(0, ...)` 兜底 + warning, 或在 `check_timestamp_monotonic` 层彻底拦住. 见 §12.5.

**12.2.D · B1 renderAll**: P23 session_export_modal 的 onChange 走了 `renderAll` 纪律 (scope radio / format radio / include_memory checkbox → renderAll). ✅

**12.2.E · B3 订阅事件**: P23 `session:exported` emit 后无 listener — 见 §12.1.

**12.2.F · B12 emit 前 grep listener**: P23 `session:exported` 直接违反, 见 §12.1.

**12.2.G · C3 append null**: P23 session_export_modal 未发现 null-append 风险 (使用 `el()` helper 已自动 filter). ✅

**12.2.H · C5 min-width:0 全链**: P23 modal 未见新的长文本溢出风险 (filename 预览用了 `.u-truncate`). ✅

**12.2.I · F1 / F5 原子写**: P23 导出**流式**下载, 不写盘, 无 atomic write 风险. ✅. 但 autosave 和 persistence 写盘都受 §4.1.2 atomic_io 修复影响.

**12.2.J · F7 silent fallback**: P23 `persistence.import_from_payload` 在哈希不匹配时硬拒 (P22.1), 非 silent fallback. ✅

**12.2 结论**: P21-P23 合规度良好, 主要残债都已并入 §4.1 系统性问题清单, 不重复列.

---

### 12.3 三份 docs 里所有"待做/延期/后续"标记全量盘点 (21 项)

**方法**: 全仓 grep `留待|留给|后续|以后|日后|归 P|归后续|未来|待定|待做|归 P25|推到|延后到|推迟到|TODO|FIXME|XXX|P\d+ 落地`, 人工过滤噪音后分类.

**按归属分类**:

#### 12.3.A · Settings UI 偏好"P4/P8 落地后失忆"类 (5 项, **P24 必做一部分**)

证据: `static/ui/settings/page_ui.js:6` 文件头注释明文 "Snapshot limit / fold defaults → P18 / P08 落地后才有意义" — **P18/P08 已落地但这里未回头填**. 具体:

| # | 项 | 当前状态 | 位置 |
|---|---|---|---|
| 1 | **Language 语言切换** | `<select disabled>` 只有 zh-CN, 其它语种写死 TODO | page_ui.js:23-25 + i18n.js `theme_light_todo: '浅色 (TODO)'` |
| 2 | **Theme 明暗主题** | `<select disabled>`, light 标 TODO | page_ui.js:33-36 |
| 3 | **Snapshot limit** | `<input type="number" disabled value=30>` 写死 | page_ui.js:43-47 |
| 4 | **默认折叠策略表** | **完全无控件** (只有"重置 fold keys"按钮) | page_ui.js:49-57 |
| 5 | **Step mode 线性/随机/seed** | PROGRESS.md L577 写明 "fixed / off 两档, 线性/随机/seed 留 TODO" | P06 阶段决定 |

**P24 决策**:
- **必做**: (3) Snapshot limit 接线成可改 (配合 §4.1 P-A/P-D 后 snapshot hot cap 可调), 后端 `snapshot_store` 本来就有 hot cap 参数, 前端只需去 disabled + onChange 发 `POST /api/config/snapshot_limit`.
- **必做**: (4) 默认折叠策略表接线 — 每种 CollapsibleBlock 类型对应一行 radio (展开 / 折叠) + 长度阈值 input, 写进 `store.ui_prefs.fold_defaults` 持久化到 LS.
- **文档化不做**: (1) Language i18n 框架完整但只有 zh-CN, 做多语种的价值 = 未来引入 en 时的工作量, 非 P24 scope.
- **文档化不做**: (2) Theme 明暗切换需要配套整套 light palette CSS, 工作量 >2 天, 非 P24 scope.
- **入 backlog**: (5) Step mode 扩展 — 需要配合具体评分场景需求, P24 联调期若用户提出再启动.

**workload (只做 3 + 4)**: ~0.5 天 前端 + ~0.5 天 后端 = **1 天**.

#### 12.3.B · 顶栏 Menu 占位按钮 (2 项, P24 必决定)

证据: `static/ui/topbar.js:357-371` 两个 `btn.disabled = true` + `onClick: toast.info("未实现")`:

| # | 项 | 意图 (推测) |
|---|---|---|
| 6 | `topbar.menu.reset` | 早期设计的 session 级 Reset (被 Diagnostics → Reset 子页替代, 但入口占位未删) |
| 7 | `topbar.menu.about` | About modal (版本信息 / 致谢 / 开源协议), 未实装 |

**P24 决策**:
- (6) **删除** — Diagnostics → Reset 已完整覆盖, 重复入口反而让用户分心 (违反 §3A B6 "单一入口原则" #59).
- (7) **P25 做** — About 是用户向文档, 属于 P25 README + 说明页范畴. 这个入口暂 hide, P25 实装时再 unhide.

**workload**: 10 分钟 (纯删 + hide).

#### 12.3.C · "PXX 落地"等开发期字眼泄漏到 UI 文案 (3 项, P24 必清)

证据: dev_note 明确指出 "最终的 UI 文本里面不应该出现 PXX 之类的说法". grep 命中:

| # | 位置 | 原文 |
|---|---|---|
| 8 | `static/ui/settings/page_ui.js:6` | 注释里写 "P18 / P08 落地后才有意义" (内部注释, 非 UI) |
| 9 | `static/core/i18n.js:1838` | `theme_light_todo: '浅色 (TODO)'` — **TODO 是 UI 文本** |
| 10 | PROGRESS §762 "验收 TODO" 等 | 文档里的 TODO 不泄漏到 UI, 合规 |

**P24 决策**: (9) 改成 `'浅色 (暂未支持)'` 或把 option 直接 hide; 其它 TODO 文案全仓扫一次: `rg -g '**/*.js' "'[^']*TODO[^']*'"` 清零.

**workload**: 10 分钟.

#### 12.3.D · 主程序同步 / 道具交互 (2 项)

| # | 项 | 来源 | 备注 |
|---|---|---|---|
| 11 | **道具交互 (prop_interaction)** | dev_note | 主程序有新功能, testbench 未同步 (需 P24 §15.4 主程序同步时查 git log) |
| 12 | `chat_runner.py::RealtimeChatBackend` | PLAN §688 | 只实装 OfflineChatBackend, Realtime 留 TODO — **是否 P24 做待定**, 看主程序 Realtime 接口是否已稳定 |

**P24 动作**: §15.4 主程序同步时 `git log --since='2026-04-01' -- brain/ memory/` 过一遍道具交互 / Realtime 的主程序 diff, 评估同步工作量. 两者都可能是 "半天以内 vs 2-3 天" 之间的差异.

#### 12.3.E · UI 小功能改进 (dev_note 衍生, 5 项)

| # | 项 | 推荐做法 |
|---|---|---|
| 13 | **人设 / 记忆 / 剧本 / 评分 页面 "打开对应文件夹" 按钮** | 每页工具栏在 "从文件夹导入" 旁加一个 `[打开文件夹]` 按钮, 调 `POST /api/system/paths/open` 已有端点. 省去去 Diagnostics 翻路径. |
| 14 | **人设导入 ✓/✗ 勾叉 tooltip** | `page_import.js:120-126` 加 `title=` 属性明确 "✓ = 该预设包含 system_prompt 可直接导入" / "✗ = 缺 system_prompt, 导入后人设空白" |
| 15 | **Restore banner 自动关** | 点 "查看并恢复" → open modal 同时关 banner. 点"跳转到模型页面" (model_config_reminder) → navigate 同时关 banner. |
| 16 | **诊断-路径页 "数据存储路径" Open 按钮黑的** | 联调期实测复现, 修 `page_paths.js::openable` 判断或后端 `/system/paths` 的 `writable` 字段 |
| 17 | **Stage Coach 预览 dry-run 按钮设计重议** | dev_note 问 "设计的时候这个 dry-run 按钮是干嘛的来着, 最后都是黑的只会弹一个提示". 实情 (§4.19 #51): memory 类 op 才有 dry-run, 其它类 op 一律 `dry_run_available=false` + tooltip. **决策**: (a) 非 memory op 的按钮改成 `[跳转到 XX]` 而不是 `[预览]`, 按钮点击直接 navigate 到对应页面 — 语义更清晰; (b) memory op 保留 `[预览]`, tooltip 维持. |

**workload**: (13)+(14)+(15)+(17) 约 1 天; (16) 联调期偶发修.

#### 12.3.F · 陈年 TODO / pre-commit (4 项, 部分已在 §4.2)

| # | 项 | 来源 | 本蓝图章节 |
|---|---|---|---|
| 18 | 非 ASCII 字面量 pre-commit hook | PROGRESS §1122 | §4.2 PLAN §15.3 #2 — **已在 P24 scope** |
| 19 | session:change / chat:messages 订阅全 caller matrix | PLAN §15.3 #1 | 并入 §12.1 已做 |
| 20 | `auto_dialog.py::line 124` "前端只报第一条 error, 留 TODO 给前端" | 代码 TODO | **UX 改进**: SSE generator 加多条 error 帧, 前端 auto_banner 展示全部 |
| 21 | CollapsibleBlock 注释与规范一致性 sweep | PLAN §15.3 #3 拆分出的 CollapsibleBlock 规范 | **P24 verify** (联调期顺手) |

**P24 动作**: (20) 前端 auto_banner 改成显示"错误 k 条, [展开看全部]" 折叠列表, 不再只显示第一条 (< 1 小时).

---

### 12.4 Dev_note UI bug 清单 (代码级定位 + 修法)

dev_note 里的问题已经在 §12.3 分散消化, 这里做一个**用户视角索引表** (按 dev_note 原文顺序), 便于 P24 开工后和用户逐项勾选验收:

| dev_note 段 | 问题原文 | 本蓝图位置 | 状态 |
|---|---|---|---|
| L8 | "右上角三点菜单很多按钮是黑的没办法用" | §12.3.B #6/#7 | P24 必做 |
| L9 | "诊断-路径页面问号提示文本写着到 PXX 再做" | §12.3.C | P24 必做 (全仓清 PXX) |
| L10 | "诊断-路径页 数据存储路径的打开按钮黑的" | §12.3.E #16 | P24 必做 |
| L11 | "设置页默认折叠策略写着 P8 落地, UI 偏好没一个落地" | §12.3.A #1-#4 | P24 必做 3+4 |
| L12 | "预览 dry-run 按钮到最后都黑的只弹提示" | §12.3.E #17 | P24 必做 |
| L14 | "UI 文本里不应该出现 PXX 说法" | §12.3.C | P24 必做 |
| L15 | "setup 页面在 从文件夹导入 旁加 打开文件夹" | §12.3.E #13 | P24 必做 |
| L16 | "人设导入 prompt ✓/✗ 没有说明" | §12.3.E #14 | P24 必做 |
| L17 | "天凌喵本地有数据列表里不显示 / 小天 ✗ + 无 memory 目录" | §12.4.A (下) | **Day 8 部分交付**: 后端 scan 逻辑静态审通过 (与主程序 `raw['猫娘']` schema 同源, 代码无 bug); 加诊断字段 `skipped_entries` + `note` + 后端日志 info + 前端折叠展示. 剩余定位必须用户手测看"被过滤条目列表" 或贴 characters.json. |
| L18 | "按三份 docs 过一遍清 tech debt" | §12.1/§12.2/§12.3 | **本章就是** |
| L19 | "事件级联两边没对齐 / 规模太大" | §12.1 | P24 必做 |
| L20 | "道具交互 P24 做" | §12.3.D #11 | P24 必做 (联调期同步) |
| L21 | "查看并恢复 / 跳转到模型页 后横幅自动关" | §12.3.E #15 | P24 必做 |
| L22 | "快照和自动保存区别" | **P25 文档** | P25 写 README 时明确 |
| L23 | "时间系统回退: set 到明天 → 对话 → 再改回, 没处理" | §12.5 (下) | **P24 必做, 新发现 bug** |
| L24 | "快速启动脚本, 检查端口占用" | §12.6 (下) | P24 顺手做 |
| L25 | "P25 把说明文档加到 About 页 / 说明页" | P25 scope | **P25 做** |
| L26-28 | "P25 多份文档分层 (用户 / AI 编程 / 架构)" | P25 scope | **P25 做** |
| L29-30 | "AGENT_NOTES 结构乱 / 单开项目概述文档" | P25 scope | **P25 做** |
| L31 | "测试生态完整使用说明 + markdown 说明页" | P25 scope | **P25 做** |

#### 12.4.A · 默认角色数据显示 bug 深入 (L17 专项)

**现象** (dev_note L17): (a) 天凌喵本地有数据但 Setup → Import 列表不显示只有小天; (b) 小天 prompt 打 ✗ 且提示 "没有 memory 目录"; (c) 试图导入提示 "成功导入 0 个文件".

**代码定位**: 
- `static/ui/setup/page_import.js:120` `preset.has_system_prompt` 决定 ✓/✗
- `static/ui/setup/page_import.js:130` `preset.memory_files` 决定 memory 文件提示
- 后端端点 (待查): `GET /api/persona/presets` / `GET /api/persona/characters` — 扫主程序 `memory/store/` 组出 characters 和 presets 列表的逻辑, dev_note 提到**天凌喵本地有数据但这里列表不显示** — 说明后端 scan 逻辑有误.

**P24 必做**:
1. 先手测复现: 启服后去 Setup → Import 截图列表, 对照 `memory/store/` 实际目录
2. 定位后端 scan 代码 (persona_router 某个 list 端点), 修 bug
3. 加 tooltip 解释 ✓/✗ 含义 (§12.3.E #14)
4. 默认角色 (小天) 的 system_prompt **应当内置** (代码仓库 chara_cards 里应该有), 不应该出现 ✗. 如果真的没有, 补一份 builtin 默认.

**workload**: **1 天** (手测 + 定位 + 修 + builtin 补档).

---

### 12.5 虚拟时钟 / 时间系统回退场景 bug (2026-04-21 用户实测纠正)

> **本节的重要更正**: 本章节初稿的分析**是错的**. 初稿推断 "/chat/send 通过 add_message 间接调 check_timestamp_monotonic 会被拦截", 用户实测指出 "**手动设置虚拟时间之后, 任何消息发送都是完全可以发送的压根不会被拦截, 即使我把时间设的比上一条还早也是一样的**". 复核代码后确认**用户完全正确**, 问题比初稿认定的严重得多. 本节全文已重写.

#### 12.5.1 事实核查 (sweep 证据)

**`check_timestamp_monotonic` 实际只在以下两处被调用**:

- `chat_router.py:384` — `POST /api/chat/messages` (**手动**添加消息的 router, 非 SSE 发送)
- `chat_router.py:489` — `PATCH /api/chat/messages/{id}/timestamp` (手动修改已有消息的时间戳)

**以下 5 处消息写入点绕过了检查**:

| # | 位置 | 场景 | 行为 |
|---|---|---|---|
| 1 | `chat_runner.py:371` | `/chat/send` SSE 里 append user_msg | `session.messages.append(user_msg)` 直写 |
| 2 | `chat_runner.py:434` | `/chat/send` SSE 里 append assistant_msg (LLM 回复) | 同上 |
| 3 | `chat_runner.py:527` | `/chat/inject_system` 系统消息 | 同上 |
| 4 | `auto_dialog.py:503` | Auto-Dialog 的 user_msg append | 同上 |
| 5 | (SimUser / Script 路径) | 继承 chat_runner 的 append 模式 | 同上 |

**讽刺一点**: `chat_router.py:382` 的注释里明确写了 "**a virtual clock that was rewound via /time/set_now can violate**" 这个场景, 作者**知道风险**但**只在手动入口做了拦截**, 没有下沉到共同 choke-point.

#### 12.5.2 §3A A7 原则的实现缺陷

§3A A7 原文: *"消息数据不变量在**写入点**守住, 不在渲染侧容错. `session.messages` 的 timestamp 单调非递减是下游的共识假设. POST /messages / PATCH /messages/{id}/timestamp 写入前调 `check_timestamp_monotonic`..."*

**原则本身没错, 错的是"写入点"这个概念在实现里是多源的**. 项目里 `session.messages.append()` 至少有 5 个不同入口, 只有 2 个 router 级手动入口做了检查, 其它都绕过. **纸面上说的 "写入点" 是单数, 实际上是复数.** 这和 §3A B1 "renderAll 纪律在纸上但被反复漏" (#72 已踩 6 次) 同族, 但危害更大 — renderAll 漏是 UI 刷新漂移(视觉), A7 漏是**数据层静默损坏**(永久写进磁盘).

#### 12.5.3 下游静默损坏范围

倒序 messages 会让以下下游产生静默错误或负值:

- **P23 `build_dialog_template` 的 `time.advance` 计算** — 基于 `messages[i].timestamp - messages[i-1].timestamp`, 倒序会产生**负 advance**, 导出的 dialog_template.json 带负 advance, 走 script_runner `_normalize_template` 可能拒或静默吞
- **`build_prompt_bundle` 的 recent_history 切片** — 按 timestamp 倒序取最近 N 条; 倒序数据下"最近"的定义崩溃, 可能跳过真正的最近消息
- **UI 时间分隔条** (`— 2h later —`) — 基于相邻消息 timestamp 差值; 负差值渲染成 "— 2h earlier —" 或 `NaN` 显示
- **`GET /judge/results?message_id=X` 的排序** — 如果 eval_results 也走 cursor 时间戳 (需核实, §12.5.5 扫)
- **autosave / save 的 hash** — 不影响 hash 本身, 但 load 回来之后倒序 messages 永久留在档里
- **snapshot 回退点** — 快照里的 messages 按追加顺序保存, 回退回倒序数据本身

#### 12.5.4 修复方案 (双重防线, 两者都必做)

**Level 1 · 写入点 choke-point 统一 (必做, 架构层)**:

抽一个 `append_message` helper 到 `chat_messages.py` 或新 `pipeline/messages_writer.py`:

```python
# pipeline/messages_writer.py (新)
from typing import Literal

VIOLATION_POLICY = Literal[
    "raise",       # 422 TimestampOutOfOrder, 阻止写入 (严格模式)
    "coerce_ts",   # 把 ts 上调到 max(ts, prev_ts), 保证单调, 记 diagnostics warning
    "warn",        # 照样写, 记 diagnostics warning, 下游自己处理 (最宽松)
]

def append_message(
    session: Session,
    msg: dict,
    *,
    on_violation: VIOLATION_POLICY = "coerce_ts",
) -> dict:
    """Unified choke-point for all session.messages.append() in the codebase.

    All call sites MUST go through this instead of raw append; see §3A A7.
    """
    ts = _parse_stored_ts(msg)
    if ts is not None:
        err = check_timestamp_monotonic(session.messages, len(session.messages), ts)
        if err is not None:
            code, detail = err
            if on_violation == "raise":
                raise TimestampOutOfOrder(code, detail)
            elif on_violation == "coerce_ts":
                prev_ts = _parse_stored_ts(session.messages[-1])
                if prev_ts is not None and ts < prev_ts:
                    msg["timestamp"] = prev_ts.isoformat()
                    diagnostics_store.record_internal(
                        op="timestamp_coerced",
                        message=f"Message ts {ts} coerced to {prev_ts} to preserve monotonicity",
                        level="warning",
                        detail={"original_ts": ts.isoformat(), "coerced_ts": prev_ts.isoformat()},
                    )
            else:  # "warn"
                diagnostics_store.record_internal(
                    op="timestamp_monotonic_violation",
                    message=detail,
                    level="warning",
                    detail={"index": len(session.messages), "ts": ts.isoformat()},
                )
    session.messages.append(msg)
    return msg
```

**调用点迁移** (全仓 `rg "session\.messages\.append\("` 命中点全部替换):
- `chat_runner.py` × 3 处 (user / assistant / system) → 默认 `coerce_ts` (用户体感是 "消息发出去了但时间戳自动向前推了, 带 diagnostics 告知")
- `auto_dialog.py` × 1 处 → 同上
- `chat_router.py::add_message` (手动添加) → 用 `raise` 严格 mode (保留现有 422 UX)
- `chat_router.py::patch_timestamp` → 继续走 `check_timestamp_monotonic` + 422 (retime 本就是修单条)
- test / smoke 里的 `session.messages.append` 不迁移 (测试代码允许造坏数据)

**on_violation 默认策略讨论**:
- `coerce_ts` 保证**数据层永远单调**, 下游永远安全, 代价是用户可能困惑"为什么这条消息时间不是我设的"
- `raise` 把责任甩给 UX, 可能打断用户对话流
- `warn` 最宽松但下游还是坏的

**推荐 `coerce_ts` 作为 SSE 路径默认** (对话不能因此失败), `raise` 给手动添加 (用户意图明确可以被拒), 配合 Level 2 的 pre-action warning 给用户"这不是你想要的状态"的提前信号.

**Level 2 · 虚拟时钟 mutation pre-action warning (必做, UX 层)**:

（同原文, 在 `time_router.POST /api/time/set` 等入口提前检查 last_msg_ts, 违反则返回 warning 字段让前端提示）

**Step 2a · routers/time_router.py 改动**:

```python
# POST /api/time/set, /advance, /stage_next_turn 等 mutation 端点
last_msg_ts = _get_last_message_ts(session)
if last_msg_ts is not None and new_cursor < last_msg_ts:
    response["warning"] = {
        "code": "CursorRewindsBeforeLastMessage",
        "last_msg_ts": last_msg_ts.isoformat(),
        "message": (
            "Target cursor is earlier than the last message. Future "
            "chat messages will have timestamps coerced to preserve "
            "monotonicity; use New Session for a truly fresh timeline."
        ),
    }
```

**Step 2b · 前端 UX**:
- Time 面板在响应含 warning 时弹 modal 二次确认 (不 block, 只提醒)
- Composer 头部挂一个小徽章 "⚠ 时钟早于最后消息" (若当前状态违反)

**Level 3 · 下游消费点加 max(0, ...) 兜底 (防御性, 可选)**:

`session_export.py::build_dialog_template` 的 `time.advance` 计算加 `max(timedelta(0), delta)` 兜底, 避免负值写进导出. 其它下游 (prompt_builder recent_history, UI 时间分隔条) 加类似兜底. **只做"不 crash"保底**, 数据本身由 Level 1 保证单调.

#### 12.5.5 仍需 P24 开工期确认的问题

- `/judge/run` 的 `eval_result.created_at` 用 virtual clock 还是 wall clock? — 需扫 `judge_runner.py` + `judge_export.py` 确认
- `snapshot_store` 的 `Snapshot.created_at` 用哪个? — 同上
- `autosave` 的 slot timestamp 用哪个? — 同上
- session_export envelope 的 `generated_at` 用哪个? (查 P23 实装)

**如果这些用 wall clock**, 不受回退影响; **如果用 cursor**, 同样需要 coerce 或 warn 机制.

#### 12.5.6 新增 smoke 断言 (`p24_integration_smoke.py`)

- `test_time_rewind_coerces_ts_and_warns`: set cursor 到过去 → /chat/send → message 成功写入但 timestamp 等于 prev_ts + `timestamp_coerced` 事件存在
- `test_time_set_returns_warning_when_before_last_msg`: POST /api/time/set 到过去 → 响应含 `warning.code=CursorRewindsBeforeLastMessage`
- `test_append_message_helper_is_single_choke_point`: 源码级 grep 确认仓内无裸 `session.messages.append\(` 命中点 (除 test/smoke/helper 自身外)

#### 12.5.7 workload

- Level 1 (choke-point 抽象 + 5 处迁移 + 3 条 on_violation 策略实装): **~1 天**
- Level 2 (time_router 3 端点 pre-action warning + 前端 modal + composer 徽章): **~0.5 天**
- Level 3 (下游 max(0, ...) 兜底 + §12.5.5 其它 *_at 字段审计): **~0.5 天**
- smoke: **~2 小时**

**小计**: **~2-2.5 天** (从原初稿估的 0.5 天扩了 4-5 倍, 因为问题本质从"UX 警告"升级为"数据层静默损坏 + 架构层 choke-point 缺失")

#### 12.5.8 归纳候选 (§3A 更新, 不只新增)

**A7 原文修订 (不只新增条目, 修订已有条目)**:

旧 A7: *"消息数据不变量在写入点守住, 不在渲染侧容错."*

新 A7: *"消息数据不变量在**单一 choke-point** 守住, 不在渲染侧容错. 任何被 "不变量在写入点守住" 的原则保护的数据结构, 写入点必须收敛到**一个共同的 helper 函数**(本项目的 `append_message`), 禁止直接 `.append` 绕过. **多源写入 + 多处检查 = 纸面原则失效**, 这是 §3A B1 renderAll 纪律的同族教训 (§4.23 #72 6 次踩点) 在 backend 数据层的映射."*

**新增 A16** (仍保留): *"可回退的时间/游标机制必须配 pre-action 警告, 不能仅靠 post-action 422/coerce."*

**新增 A17** (本节发现派生): *"凡涉及 `session.messages.append()` 的代码审查必须自动展开到**所有** append 命中点; 任何新加的消息来源都必须通过 `append_message` choke-point, 不得本地直 append. CI 建议加 pre-commit hook: `rg 'session\.messages\.append\(' -g 'pipeline/**' -g 'routers/**' --invert-match` 命中即 block."*

**延伸教训 (归纳候选 §12.10 新增)**:

5. **"实测 > 代码推断 > 文档原则" 的证据权威度 (本节 2026-04-21 纠错案例)** — 初稿基于 "grep 到 check_timestamp_monotonic + 原则 §3A A7 声称保护" 推断 "/chat/send 会被拦截", 结论与用户实测 "压根不拦截" 完全相反. 问题根源: grep 只证明**该函数被调用过**, 不证明**所有相关路径都调用它**. **文档原则**(§3A A7) 描述的是 Intent, **实现代码**描述的是 Reality; 两者有 gap 是正常的, 错的是我默认 Intent == Reality. 修正后的原则 A7 新版已显式要求"单一 choke-point". **归纳**: 审 bug / 审架构时, 若有用户实测反馈, **实测优先级永远最高**; 若只能静态审, 必须把 "这个不变量是否被**所有**入口都保护" 作为显式 checklist 一项, 而非默认 yes.

---

### 12.6 快速启动脚本 (dev_note L24, P24 顺手做)

**dev_note 原话**: "要求 AI 写一个快速启动脚本, 检查当前端口有没有占用啊, 把每次服务启动时候跑的那堆命令塞进一个 shell 脚本里面, 不然每次打字怪麻烦的".

**实装形态** (`tests/run_testbench.ps1`, PowerShell):

```powershell
# 1) 检查 port 48920 占用, 若占用问是否 kill
# 2) 激活 .venv
# 3) uv run python tests/testbench/run_testbench.py --port 48920
# 4) 输出启动 URL + 附近目录提示
```

同时加 README 段 "快速启动" 指向该脚本.

**workload**: ~1 小时 (PowerShell 脚本 + bash 版 + README 一段).

---

### 12.7 P25 文档候选扫描 (dev_note L22/L25/L26-28/L29-30/L31)

**P25 不属于 P24 scope**, 此处只列候选给 P25 开工者:

| # | dev_note 来源 | P25 候选产出 |
|---|---|---|
| L22 | "快照 vs 自动保存区别" | README "数据生命周期" 章节明确 (snapshot = 历史时间轴回退锚点 / autosave = 崩溃续跑) |
| L25 | "说明文档加到 About 页或说明页" | 新增 `/about` workspace 或 dropdown → About modal, 内嵌 README 摘要 |
| L26 | "整理现有三份 docs" | PLAN/PROGRESS/AGENT_NOTES 拆分重组 (PLAN = 规格 / PROGRESS = 历史 / AGENT_NOTES = 踩坑 + §3A) |
| L27 | "给 AI 编程的泛化经验" | `docs/AI_PROGRAMMING_LESSONS.md` 从 §3A 47 条抽象出泛化模式 |
| L28 | "给代码架构设计者的经验" | `docs/ARCHITECTURE_PRINCIPLES.md` 从本项目 24 phase 抽象出项目节奏 / 整合期 / 加固 pass 模式 |
| L29 | "AGENT_NOTES 结构乱" | P25 重排 §4 子章按 phase 正序, 或拆出 `§4-history.md` 独立 |
| L30 | "单开一份项目代码 / 功能 / 设计原则 概述" | `tests/testbench/README.md` 用户向 + 开发者向双分部 |
| L31 | "测试生态完整清晰使用说明 markdown" | 同 L30, README 里加 "测试流程手册" 章节 + 截图 |

**P25 workload 估算 (不在 P24)**: ~5-6 天整理 + 3 份新文档, 需用户亲自参与审稿.

---

### 12.8 §12 总结 + P24 新增 workload 调整

**§12 发现 P24 新增项**:

| 子章 | 项 | workload |
|---|---|---|
| 12.1 | 事件总线 6 处违规修 + dev-only 自检 | 3-4h |
| 12.3.A | Settings UI 偏好 Snapshot limit + 折叠策略接线 | 1d |
| 12.3.B | 顶栏 Menu Reset 删 + About hide | 10min |
| 12.3.C | UI 文本 TODO / PXX 字眼清理 | 10min |
| 12.3.D | 主程序同步 (道具交互 / Realtime) | 0.5-2d (联调期评估) |
| 12.3.E | 打开文件夹 + tooltip + 横幅自关 + dry-run 重议 | 1d |
| 12.3.F | auto_dialog 前端多 error 展示 | <1h |
| 12.4.A | 默认角色数据显示 bug 修 | 1d |
| **12.5** | **时间戳 choke-point 抽象 + 5 处迁移 + pre-action warning + 下游兜底** | **2-2.5d** ⚠ |
| 12.6 | 快速启动脚本 | 1h |
| **§12 新增合计** | | **~6-9 天** ⚠ |

> **§12.5 workload 从 0.5d 扩到 2-2.5d** 原因: 初稿错判为 "UX 警告问题", 用户实测纠正后发现是**架构层 choke-point 缺失** — 5 处 `session.messages.append()` 绕过 `check_timestamp_monotonic`, 属于 §3A A7 原则在实现上漏守, 必须抽 `append_message` helper 统一写入点 + 全仓迁移. 见 §12.5 重写全文.

**P24 总 workload 更新**: 原 10 天 (§7.1) + §12 新增 **6-9 天** = **13-16 天**.

**P24 执行顺序调整 (§8 扩展)**:

在原 Day-by-Day 基础上插入:

- **Day 0 (额外半天)**: 跑 §12.6 启动脚本 + §12.3.B/C 小改动 (删 disabled / 清 TODO) 作为 warm-up.
- **Day 5 之后加 Day 5.5**: §12.1 事件总线违规修 + dev-only 自检 (和 §3.5 renderAll drift 同时落地, 属于"前端机制守卫").
- **Day 6-7 (原联调日)**: 扩展为"**联调 + UI 验收**":除原资源数据采集, 额外按 §12.4 dev_note UI bug 清单逐项验收.
- **Day 7.5 (新)**: §12.4.A 默认角色数据显示 bug 修 + §12.5 时间回退 warning + §12.3.E UI 小改进
- **Day 8 扩展**: 原 bug 修 + 主程序同步 + **§12.3.A Settings UI 偏好接线**.

---

### 12.9 更新 §3A 候选新条 (§9.3 扩展)

P24 完工后 §3A 新条候选 (在原 A12-A15 基础上新增):

- **A7 修订** · 加强"单一 choke-point" 要求, 不只保留 "写入点守住" 的抽象说法; 多源写入必须收敛到共同 helper (§12.5 派生, 重写原条目)
- **A16** · 可回退的时间/游标机制必须配 pre-action 警告 (§12.5 派生)
- **A17** · `session.messages.append()` 必须走 `append_message` choke-point, 禁止裸调; 加 pre-commit hook (§12.5 派生)
- **B14** · emit 前查 listener **+** 加 on 前查 emitter (双向检查, §12.1 派生)
- **B15** · 开发期 UI 占位控件 (`disabled=true` + toast 占位) 不得长期驻留; 或接线完整或删除占位 (§12.3.B 派生)
- **E3** · 开工前 Full-Repo Pre-Sweep 作为大阶段默认动作 (本章本身就是案例, P24 整合期以前没有过)

---

### 12.10 延伸教训 (归纳候选)

1. **"留空占位 + TODO 注释" 的半衰期是 4-6 个 phase** — 本次 sweep 发现 Settings page_ui.js 里 "P18/P08 落地后有意义" 的 TODO 至今 (P22 已落地) **横跨 15 个 phase 未被回头填**. 机械 TODO 注释在大型项目里几乎注定被遗忘. 归纳: **任何"先占位等 B 做完后回头接线" 的 TODO, 必须同时在 A 所在 phase 的 `PROGRESS.md` 详情里显式登记 "依赖 B 完成后回填", 不能只依赖代码注释**. 项目下一个 agent 启动前 grep `todos` / PROGRESS 里有没有"等 XX 完成"的未兑现项是强制动作.
2. **"已知 bug 错过三次 pass 不修" 的反模式** — `messages:needs_refresh` / `memory:needs_refresh` 0-listener 在 P20 hotfix 1 (§4.26 #87) 就已明确记录, 但 P21 / P22 / P22.1 / P23 共 4 次 pass 里**没人回来删 emit 点**. 原因: 每次 pass 都聚焦自己的子题, 经过 timeline_chip / page_snapshots 时"注意到但没优先级". 归纳: **任何带 emit/on 拓扑类的 pass 末尾必须跑一次全仓事件 matrix 验证, 不能只验证"新加的事件"**. §12.1 的 matrix 表应作为 PLAN 的"每次涉及前端 listener 变更的 pass 必跑 checklist".
3. **"全面审查不等于逐文件 Read"** — 本次 sweep 靠并行 rg 在 ~30 分钟内定位到 ≥ 30 条具体问题, 远超"打开 10 个文件 Read" 的效率. 关键: (a) 先写**搜索模式列表** (本章开头的 12 条 rg 关键字), (b) 并行跑, (c) count mode 粗筛 → content mode 细读. 归纳: **任何"横向审视"任务先写 grep 列表再开工; 逐文件 Read 是"纵向理解" 的工具, 不是"全仓普查"的工具**.
4. **Dev_note 的价值 = 用户视角 bug reproducer 远比开发视角完善** — 开发者的 sweep 侧重"哪条原则违反", 用户视角 dev_note 侧重"我看到什么黑按钮". 两者正交, 缺一就会漏. 归纳: **任何面向用户的软件项目, 每个阶段结束前 agent 都应该主动问"用户最近看到什么不对劲" 作为 PRE-sweep 输入, 不依赖用户主动提**.
5. **"实测 > 代码推断 > 文档原则" 的证据权威度** ⚠ **(§12.5 重写案例, 2026-04-21 同日出现)** — §12.5 初稿基于 "grep 到 check_timestamp_monotonic + 原则 §3A A7 声称保护" 推断 "/chat/send 会被拦截", 用户实测 "压根不拦截" 推翻了结论. 问题根源: **grep 只证明"该函数被调用过", 不证明"所有相关路径都调用它"**; §3A A7 文档原则描述 Intent, 实现代码描述 Reality, 两者有 gap 是正常的, 错的是**默认 Intent == Reality**. 归纳: (a) 审 bug / 审架构时, 若有用户实测反馈, **实测优先级永远最高**, 即使它与代码静态分析结论冲突, 也应**先复核代码再接受实测而非反过来**; (b) 只能静态审时, 必须把 "**这个不变量是否被所有入口都保护**" 作为显式 checklist 一项, 而非默认 yes — 方法: `rg "mutation_pattern"` 全仓列命中点, 逐个核对是否走了守护函数; (c) **纸面原则和实现的 gap 本身就是高价值 bug 候选**, 任何 "§3A 说 X, 但实际上只有 N 个入口守了, 另有 M 个绕过" 都应当入 P24 类 sweep 清单 (就像 §12.5 发现的).
6. **"单源写入 vs 多源写入" 是纸面原则成败的分水岭** — §3A A7 / B1 这类 "不变量在 X 点守住" / "state 改要 Y" 的原则, **在单源写入下很容易贯彻 (只有一处改)**, 但 **在多源写入下几乎必然漏守 (5 处改只守 2 处很正常)**. 修法不是"再写一遍原则提醒未来 agent", 而是**用代码把多源收敛成单源** — 抽 `append_message` / `bindStateful` / `set(key, value)` 这类 choke-point helper, 让"绕过" 本身不可能或极不自然. §3A B1 6 次踩点后终于在 P24 蓝图 §3.5 计划做 `bindStateful`, §3A A7 本次暴露后在 §12.5 计划做 `append_message`. 归纳: **纸面原则连踩 3 次还没被贯彻 → 必须抽 choke-point 代码层强制**, 不再指望记忆力.

---

*§12 Pre-Sweep 章节结束*. **P24 开工前建议再读一次 §8 + §12.4 清单**, 核对 workload 是否需要调整.

**§12 交付状态 (Day 11 回填)**: 全章 10 小节 21 项延期/sweep 候选 **100% 消化**:

- **§12.1 事件总线 matrix** — ✅ **Day 6 交付**: 6 违规清零 (5 dead emit 删 + 1 dead listener 补 emitter) + `DEBUG_UNLISTENED` dev-only 检测 (new §3A B14 条候选)
- **§12.2 §3A 47 条合规扫描** — ✅ 40% 合规率 (6 绿 / 5 可疑 / 4 漏守) 实证入档, 作为 §13 Choke-Point 扫描的输入
- **§12.3 三份 docs 延期清单** — ✅ 21 项**全量消化**: 4 项归 P25 (README 类) / 17 项 P24 Day 1-10 落地 (含 B/C 删 disabled 清 TODO / A 快照上限接线 / D 道具交互翻转立 P25 / E 重复 reset 入口删)
- **§12.4 Dev_note UI bug** — ✅ Day 4-7 全部落地, 另 Day 8 CFA 路径分裂 bug 识别后扩大覆盖为 A/B/C/D 四段防线
- **§12.5 虚拟时钟回退 bug (A7 实证漏守)** — ✅ **Day 2 交付**: 抽 `pipeline/messages_writer.append_message` choke-point + 5 处 caller 迁移 + pre-action warning (`time_router::_warning_for_new_cursor`) + 下游 `max(0, …)` 兜底, 派生 A7 修订 + A16 + A17 入 §3A
- **§12.6 run_testbench.ps1 / .cmd / .sh** — ✅ Day 1 交付, ExecutionPolicy 问题有用户级 `Set-ExecutionPolicy RemoteSigned` 记录
- **§12.7 P25 文档候选** — 归 P25 / P26 scope, 本 phase 不动
- **§12.8 workload 调整** — 本章新增 4h / 约 0.5d, 总 workload 最终 15.5-19.5 天
- **§12.9 §3A 候选新条** — ✅ **Day 11 正式落地** (见 §11.2 表)
- **§12.10 延伸教训 6 条** — ✅ **Day 11 正式入 `LESSONS_LEARNED §1.1 / §1.2 / §1.3` (5 条) + §7.16 (留空 TODO 半衰期 4-6 phase 是反模式)**

---

## 13. 第三轮审查: "纸面原则 ≠ 实现现实" 同族 Choke-Point 扫描 (2026-04-21 第三轮)

> **本章的由来**: §12.5 虚拟时钟回退 bug 被用户实测纠错后, 暴露出一个元问题 —
> §3A A7 "消息数据不变量在写入点守住" 纸面原则声称受保护的 `session.messages`
> 单调性, **实际代码只在 2/5+ 写入点做了检查**. 用户追问 "**之前写下来的很多代码
> 都是未必完全可信的, 可能存在各个层级的漏洞**", 并要求扫 docs 里所有 "等独立
> phase 再做" / "应该做一轮横向扫描" 类承诺的兑现度. 本章是这一轮审查结果.
>
> **核心假设**: §3A 里任何 "X 必须在 Y 守住 / X 统一走 Y / X 一律走 Z" 类
> **choke-point 型原则** 在**多源写入场景下几乎必然漏守**, 需要逐条静态核查
> "原则声称保护的入口 vs 实际代码调用点" 的差集.

### 13.1 §3A Choke-Point 型原则的实证核查矩阵

**方法**: 对 §3A 的 47 条原则, 筛选出带 "单一 choke-point" 语义的子集
(原则声称 "X 必须走 Y 统一入口"), 用 `rg` 扫声称的守护函数命中点 vs 全仓所有
"潜在绕过路径" 的差集.

**扫描结果分三档**:

- ✅ **合规** (已实证所有入口都走守护函数)
- ⚠ **可疑** (表面 OK 但需 P24 深入复核)
- ❌ **漏守** (实证漏守 ≥ 1 个入口, 必须修)

| # | 原则 | 类型 | 守护函数 | 实证入口覆盖率 | 档位 | 来源章节 |
|---|---|---|---|---|---|---|
| **A7** | session.messages 单调性 | ❌ **漏守** | `check_timestamp_monotonic` | **2/5+** (只有手动 router 入口, SSE/Auto/inject 全绕过) | ❌ | **§12.5 已详细覆盖** |
| **B12** | emit 前有 listener | ❌ **漏守** | (无守护函数, 靠纸面纪律) | 6 处 emit 0 listener | ❌ | **§12.1 已详细覆盖** |
| **F1/F5** | atomic_write 原子写 | ❌ **漏守** | `_atomic_write_{bytes,json}` | **1/6** (只有 persistence.py 有 fsync, 其它 5 份副本无) | ❌ | **§4.1.2 已详细覆盖** |
| **F6** | 多文件原子组删除顺序 (tar 先 JSON 后) | ✅ **合规** | (无统一守护, 靠 code review) | 主路径 `persistence.delete_saved` + `autosave` slot rolling 都遵守 (P21.1 G8 修过) | ✅ | 本节 §13.2 验证 |
| **A2** | 单活跃会话锁 | ✅ **合规** | `run_session_work` / `session.lock` | 全仓 80+ 处 `session_operation(...)` 覆盖; autosave 用独立 `_flush_lock` 互不抢 | ✅ | §3A 多次校验 |
| **A4** | 长流水锁粒度 = 整个工作单元 | ✅ **合规** | `session_operation(state=BUSY)` | chat/auto/script/judge SSE 都在 session_operation 里 | ✅ | §4.17 #36 已校验 |
| **A10** | JSON body 布尔/浮点走 coerce helper | ⚠ **可疑** | `_coerce_bool` / `_coerce_float` | **仅 judge_router (8 处)**, 其它 12 个 router 未 sweep | ⚠ | 本节 §13.3 深入 |
| **A11** | Aggregate/export 模块只吃 list/dict, 不绑 session | ✅ **合规** | (设计约束, 不是运行时守护) | `judge_export` / `session_export` (P23) / `prompt_builder` 三处实证 | ✅ | §3A A11 实锤 |
| **B1** | state mutation 后 renderAll | ❌ **漏守** (历史) | (无 helper, 靠纸面) | 6 次踩点 (§4.23 #72), P24 §3.5 计划抽 `bindStateful` | ❌ | §3.5 已详细覆盖 |
| **B8** | latest-per-entity 用 newest-first + first-seen-wins 单次遍历 | ✅ **合规** | (设计 pattern, 非守护函数) | Chat 徽章 / timeline 气泡 / Aggregate 最近评分 三处实证 | ✅ | §4.23 #68 |
| **C3** | `Node.append(null)` filter 或返 DocumentFragment | ⚠ **可疑** | (无守护, 靠 `el()` helper 部分兜底) | 已踩 2 次 (§4.17 #42, §4.23 #75), 全仓 30+ `.append(` 未完整扫 | ⚠ | 本节 §13.4 深入 |
| **D1** | async lazy init 用 Promise cache, 不用 boolean flag | ⚠ **可疑** | (设计 pattern) | 已踩 3 次 (#23 / #47 / #39), sweep 发现 **`page_persona.js:184` 一处"flag + async load"反模式结构** | ⚠ | 本节 §13.5 深入 |
| **F4** | OS-level 资源 (engine / task / queue / event) 显式 dispose | ⚠ **可疑** | `_dispose_all_sqlalchemy_caches` (仅 SQLAlchemy) + 各 task.cancel() 散落 | SQLAlchemy 已覆盖; asyncio.Task × 3 处 / asyncio.Event × 3 处 未完整审 | ⚠ | 本节 §13.6 深入 |
| **G1** | 检测不改 / 永不过滤用户内容 | ⚠ **可疑** | (设计约束, 非守护函数) | 主路径合规; sweep 发现 `simulated_user._postprocess_draft` 剥 ChatML 前缀, 边界需明确 | ⚠ | 本节 §13.7 深入 |
| **G2** | `.format()` template 只审 "谁控 template" | ✅ **合规** | (P21.3 审计过, `ScoringSchema.prompt_template` 唯一用户可控点) | 详细审计在 §4.27 #97 | ✅ | P21.3 已校验 |

**合计**:
- ❌ **漏守 4 条** (已全部在 §4.1 / §12.1 / §12.5 / §3.5 详细规划修复)
- ⚠ **可疑 5 条** (P24 Day 0 / Day 5 扩展 sweep 必做)
- ✅ **合规 6 条** (本轮 sweep 实证)
- **总覆盖率**: 15 / 47 条, 剩余 32 条是非 choke-point 型 (CSS 布局 / 测试纪律 / UX 文案 / 架构决策等), 不适用本方法论

### 13.2 F6 多文件原子组删除顺序 — ✅ 已验证合规

**验证方法**: grep `\.unlink\(|\.rmtree\(` 全仓, 逐个核查是否在 "多文件组" 上下文中, 以及删除顺序.

| 位置 | 场景 | 顺序 | 合规? |
|---|---|---|---|
| `persistence.py:861-871` | `delete_saved`: tar + JSON | **tar 先, JSON 后** | ✅ (P21.1 G8 修过) |
| `autosave.py:618-628` | slot rolling tar + JSON | **tar 先, JSON 后** | ✅ |
| `autosave.py:673` | `_delete_slot(path)` 单文件 | N/A | ✅ (非多文件组) |
| `autosave.py:764-771` | `_clear_all_autosaves` tar + JSON | 待核 | ⚠ 需 P24 Day 0 扫 (见下) |
| `script_runner.py:578` / `scoring_schema.py:981` / `logger.py:311` | 单文件 unlink | N/A | ✅ |
| `sandbox.py:112` / `reset_runner.py` | `shutil.rmtree(整个沙盒)` | 目录级 rmtree, 非多文件组 | ✅ |
| `snapshot_store.py:809 / 873` | cold snapshot spill path | 单文件 | ✅ |

**P24 Day 0 动作**: 读 `autosave.py:764-771` 确认 `_clear_all_autosaves` 也是 tar 先 JSON 后. 若非, 修.

**结论**: §3A F6 实证合规度 **~100%** (除一处需核实的 autosave clear), 是本轮审查唯一完全绿的 choke-point 原则.

### 13.3 A10 JSON body coerce — ⚠ 可疑, P24 必 sweep

**实证命中点**:

- `judge_router.py:913,938` 定义 `_coerce_bool` / `_coerce_float`
- `judge_router.py:1223-1233` 使用 (filter body: passed / min_overall / max_overall / min_gap / max_gap / errored) 共 8 处

**全仓其它 router body 接受布尔/浮点字段的入口 (需 sweep)**:

| router | 疑似入口 | 当前处理方式 |
|---|---|---|
| `chat_router` | `_AddMessageRequest` / `_SendRequest` / `_AutoStartRequest` 等 BaseModel | Pydantic 自动转, 无风险 ✅ |
| `session_router` | `ExportSessionRequest` (P23, include_memory/include_snapshots/redact_api_keys 等布尔) | Pydantic 自动转 ✅ |
| `stage_router` | BaseModel 为主 | Pydantic 自动转 ✅ |
| `memory_router` | 部分 `body: dict` 裸字典 | ⚠ **潜在风险**, 若读 `body.get("some_bool")` 并当 bool 用, 字符串 "false" 会被判 True |
| `time_router` | BaseModel | ✅ |
| `config_router` | 部分端点吃 `body: dict` | ⚠ **潜在风险** |
| `snapshot_router` | BaseModel | ✅ |
| `health_router` | GET only (或 query) | ✅ |
| `persona_router` | BaseModel / `body: dict` 混合 | ⚠ **潜在风险** |

**P24 动作 (并入 Day 2)**:

1. **sweep**: `rg "body\.(get\(|\[)" routers/` 列出所有"裸 dict body.get" 的入口
2. **逐个核查**: 该入口是否读布尔/浮点字段, 是否走了 coerce helper
3. **提升 `_coerce_bool` / `_coerce_float` 到 `pipeline/request_helpers.py`** 公开 API, 非 judge_router 独享
4. **加 §3A 补强 A10**: "**任何 router 吃 `dict[str, Any]` 裸 body 读布尔/浮点/int 字段都必须走 coerce_* helper, BaseModel 入口除外 (Pydantic 自动转)**"

**workload**: ~2 小时 (sweep + 迁移 + 1-3 处改动)

### 13.4 C3 Node.append(null) 第 3 次踩的风险 — ⚠ 可疑, P24 sweep

**已踩 2 次** (§4.17 #42 / §4.23 #75), **历次修法**: 修单点 (当时那个 render 函数的 return null 改返 DocumentFragment / caller 改 `.filter(Boolean)`), **未做全仓 sweep**.

**风险识别信号**: 
- 任何 render helper 签名为 `() => Node | null`
- 上游用 `parent.append(x, y, renderHelper(), z)` 多参传值

**P24 动作 (并入 Day 5 事件总线 matrix)**:

```bash
# sweep 脚本 (P24 Day 5 跑)
# 1) 所有 return null; 的 render 类函数 (命名含 render/build/make)
rg "return null;" -g "static/**/*.js" -B 5 | grep -E "function (render|build|make)"

# 2) 所有 .append( 多参调用
rg "\.append\(\s*[\w.]+\s*,\s*[\w.]+" -g "static/**/*.js"
```

交叉比对: 任何 `parent.append(..., renderXxx(), ...)` 调用且 `renderXxx` 可能 return null 的都是**潜在命中**. 逐个改:
- 首选: `renderXxx` 改返 `document.createDocumentFragment()` (空片段 safe to append)
- 次选: 调用方 `parent.append(...[child1, renderXxx()].filter(Boolean))`

**§3A C3 原文补强** (候选): *"render 类函数**默认返回空 DocumentFragment**, 禁止返 null. 如必须返 null (历史代码), 上游 `.append()` 一律走 `.filter(Boolean)`. 加 lint 规则: 任何 `.append(` 多参调用里含 function call 的都要被 highlighted 手工核查."*

**workload**: ~2 小时 (sweep + 3-5 处改动 + §3A 更新)

### 13.5 D1 async lazy init — ⚠ 发现一处反模式结构

**证据** (`page_persona.js:183-187`):

```javascript
let loaded = false;
// ...
details.addEventListener('toggle', () => {
  if (details.open && !loaded) {
    loaded = true;        // ← flag 置位在 load() 之前
    load();               // ← async, 未 await, 未存 Promise
  }
});
```

**分析**:

这**表面上是 §3A D1 反模式结构** (boolean flag + async 无 Promise cache), 但**实际不是典型 race 场景**:

- D1 原描述的 race 是 "两个不同 caller 背靠背调用" 下第二个 caller 因 flag=true 以为加载完成但 DOM 还空
- 这里的 caller 是**同一个 DOM element 的同一次 toggle 事件 listener**, 不会并发触发
- 第二次 toggle (关→开) 时 `loaded=true` 跳过 load, 这是**预期行为**(避免重复请求)

**但**: 这个结构仍是**防御性弱**的, 未来如果有第三方代码也需要 trigger 这个 preview (比如 Stage Coach 跳 setup→persona 时自动展开 preview), 就会踩到 D1 的经典 race. **预防性修法**:

```javascript
let loadPromise = null;
details.addEventListener('toggle', () => {
  if (details.open) {
    if (!loadPromise) loadPromise = load();  // 第一次 toggle 启动加载, 存 Promise
    // 第二次 / 第三方 trigger 看到 loadPromise 非空直接跳过, 不重复请求
  }
});
```

**P24 动作**:

1. (防御性) 重构 page_persona.js 第 183-187 行为 Promise cache 模式
2. **全仓 sweep 其它同构结构**: `rg "let \w+ = false" -g "static/**/*.js" -A 5 | grep "= true"` 找所有 flag-based lazy init 类代码
3. 加 §3A D1 补强: *"即使当前只有单 caller, lazy init 也应写成 Promise cache 防御未来第三方接入. boolean flag 仅用于"一次性事件已发生"的 UI 状态, 不用于"异步资源已加载"判断."*

**workload**: ~1 小时

### 13.6 F4 资源 dispose — ⚠ asyncio.Task / Event 需 P24 深审

**sweep 结果**:

**asyncio.Task 用法 (3 处)**:

| # | 位置 | 生命周期归属 | cancel 路径 |
|---|---|---|---|
| 1 | `server.py:266` log_cleanup_task | **进程级** | `@app.on_event("shutdown")` 内 `.cancel()` |
| 2 | `autosave.py:853` scheduler `self._task` | **进程级** (整个 app 启动时创建) | `stop()` 方法 `.cancel()`; P22 `_startup_cleanup` 调 |
| 3 | `auto_dialog.py:269` session 级 `running_event/stop_event` | **会话级** (挂在 session.auto_state) | session destroy 自动消失 |

**审视**: 三处都有清理路径, 但需要核实:

- (1) `log_cleanup_task.cancel()` 后有没有 `await asyncio.gather(..., return_exceptions=True)` 等待清理完成? 否则 CancelledError 异常可能吞掉下一轮 boot 的日志写
- (2) autosave `stop()` 时 scheduler 可能正在 flush 大文件, 等待策略是否 bounded? 看 `autosave.py:919 wait_for` 超时后还是 cancel
- (3) `auto_state` 里的 Event 在 session destroy 时是否调了 `set()` 唤醒所有等待方? 否则等待方 coroutine 永悬

**asyncio.Event 用法 (3 处)**:

| # | 位置 | 清理 |
|---|---|---|
| 1 | `auto_dialog.py:269-271` running_event / stop_event | 挂 session 自清 |
| 2 | `autosave.py:835` `self._wakeup` | scheduler stop 时 set 一次唤醒 wait_for |
| 3 | (无其它 Event) | — |

**P24 动作 (Day 5, 并入 §4.1.2 atomic_io 或独立)**:

1. **读 3 处 task.cancel 路径**, 确认等待语义 bounded + CancelledError 吃干净
2. **读 Event.set 的唤醒路径**, 确认 session destroy 时 auto_state 里的 Event 都被 set
3. **写 §3A F4 补强 checklist**: *"每个 asyncio.Task / Event / Queue 都必须在创建时就写下: 生命周期归属 (进程/会话/请求) + cleanup 时机 + 等待策略 (bounded / unbounded). 不 bounded 的 task.cancel() 会吞 CancelledError 阻塞下一轮 boot, 属于 F4 细化条款."*

**workload**: ~1.5 小时 (读代码 + 补强 + 可能 2-3 行修补)

### 13.7 G1 永不过滤边界 — ⚠ 边界需明文化

**sweep 发现**: `simulated_user.py:306-341 _postprocess_draft` 对**LLM 输出**做:
- Strip "User:" / "用户:" / "我:" 等 ChatML-like 前缀 (_PREFIX_PATTERNS 循环剥)
- Strip 成对的首尾引号

**判定**: 这是**对 SimUser LLM 的输出**清洗, **不是对用户内容的过滤**. SimUser 的"用户"是 LLM 模拟的, 不是真实用户. 符合 G1 "永不过滤用户内容" 的语义边界.

**但风险场景**: 如果 SimUser LLM 输出 `<|im_start|>user\n 真·攻击 payload`, `_postprocess_draft` 不会特殊识别这个 token, 原样透传给被测模型. ✅ (G1 要求的正是这种"原样透传")

**唯一边界模糊点**: 如果测试人员**手动指定** user_persona_prompt 要求 "模拟一个会以 'User:' 开头说话的恶意用户", SimUser 生成的 `User: 攻击 payload` 前缀会被 `_postprocess_draft` 剥掉, 变成 `攻击 payload`. 这**微妙地违反了用户意图** (用户想测试 ChatML 前缀作为攻击向量).

**P24 动作 (小范围, 0.5 小时)**:

1. 在 `simulated_user.py::_postprocess_draft` docstring 明文声明 "This function strips SimUser LLM's own role-label echo, NOT user-supplied content. Per §3A G1, user_persona_prompt-driven instructions to generate `User:`-prefixed output will be stripped by this, which is a known limitation."
2. 考虑加 opt-in 参数 `preserve_role_prefix: bool = False` 给需要测 ChatML 攻击的场景用
3. §3A G1 补强一句: "SimUser 的 `_postprocess_draft` 是**输出清洗而非输入过滤**, 属于 G1 允许范畴; 但若未来扩展到 memory compressor 的输出清洗, 必须在文档明确标注 '清洗的是 LLM 自家输出, 非用户内容'."

### 13.8 三份 docs 里 "承诺未兑现" 的横向扫描 / 独立 pass 类延期标记

**用户本轮要求核心**: *"检查说明文档里面关于'等独立 phase 再做'的说法和'应该做一轮横向扫描'的说法, 把这些技术债都补齐."*

**sweep 方法**: grep `横向|全仓|全局|统一|一律|都应|必须所有|等 P\d+|归独立|单独立项|一并处理|后期补|留待` + 上下文人工判定是否"承诺未兑现".

**已在本蓝图其它章覆盖的 (不重复列)**:

- §12.3.A · Settings UI 偏好 "P18/P08 落地后有意义" 承诺 → ❌ 错过 15 phase, P24 §12.3.A 修
- §12.3.B · 顶栏 Menu Reset/About 占位 → ❌ 永久 disabled, P24 §12.3.B 修
- §12.1 · 事件总线 ≥ 6 处 B12 违规 → ❌ P20 hotfix 1 已记录未修, P24 §12.1 修
- §12.5 · §3A A7 消息单调性声称但实际只守 2/5+ 入口 → ❌ P24 §12.5 修
- §4.1.1 · HTTPException shape 三种并存 → P24 §4.1.1 修
- §4.1.2 · atomic_write fsync 1/6 → P24 §4.1.2 修

**本节新发现 (3 条未覆盖)**:

| # | 承诺原文 | 现状 | P24 归属 |
|---|---|---|---|
| **(a)** | §3A B9 "**必须全仓 grep 对应 i18n key 的文案, 清掉/改写所有引用该模式的句子**" | **不确定有没有实施过**. P17 `msg_ref` 修法时做了, 但后续 P18-P23 涉及 UI 模式重命名 (如 eval comparative) 时是否每次都 grep i18n 全仓 — **缺证据** | P24 Day 5 并入 i18n 硬编码 sweep 一次性扫 |
| **(b)** | §3A G2 审计 checklist "全仓 grep `\.format(` / `format_map(` 核查用户可控 template" | P21.3 做过一次, **P22 / P22.1 / P23 引入的新代码没再扫过** | P24 Day 2 安全性审计时扫一次 (追加到 §4.3 H api_key 审计) |
| **(c)** | §3A G3 "只在 序列化到 AI 载体 时做 `_escape_user_content_tag`" (P21.3 落地) | 实现合规, 但**没有 lint 守护**: 未来若有新代码不过 `_escape_user_content_tag` 直接拼 `<user_content>...</user_content>` 会绕过 | P24 Day 2 安全性审计加 lint 检查 (pre-commit 或 smoke 源码级断言) |

**"应该做横向 sweep 但实际只做局部" 的更深问题**:

§3A 的许多原则是 "**某一次局部踩点后总结出来的**" — 比如:
- B9 来自 P17 msg_ref 修法 (一次性扫过 `pairing_multi` i18n) — 但该**方法论没抽成每次 UI 改模式时的标准 checklist**, 新 UI 改模式时未必会触发
- G2 审计 checklist 写在 §3A, 但**没有 CI/pre-commit 机制强制**, 全靠 agent 记忆
- A7/F1/B12 更是纸面原则但实际漏守多入口

**共性归因**: 项目缺乏**机械化强制纪律** (pre-commit hooks / lint rules / dev-only runtime asserts), 全靠 AGENT_NOTES 的"记忆提醒". 这在 P00-P16 code base 较小 (30+ JS 文件) 时还能 work, P17-P23 膨胀到 80+ 文件 + 多条 pass 后已不可靠.

**P24 机械化纪律建议 (并入 §4.2 PLAN §15.3 #2 非 ASCII pre-commit)**:

| 规则文件 | glob | 强制内容 |
|---|---|---|
| `.cursor/rules/no-hardcoded-chinese-in-ui.mdc` | `static/ui/**/*.js` | 非 ASCII 走 i18n 或 `\uXXXX` (§3A C4) |
| `.cursor/rules/i18n-fmt-naming.mdc` | `static/ui/**/*.js` + `static/core/i18n.js` | `i18n(key)(arg)` 形式 zero-match; `_fmt` 后缀命名 (§3A i18n 头条) |
| `.cursor/rules/emit-grep-listener.mdc` | `static/**/*.js` | 新增 `emit('xxx')` 前必须有至少一个 `on('xxx'` (§3A B12) |
| `.cursor/rules/single-append-message.mdc` | `pipeline/**/*.py` + `routers/**/*.py` | 裸 `session.messages.append(` 零命中 (§3A A7 新, §12.5 派生) |
| `.cursor/rules/atomic-io-only.mdc` | `pipeline/**/*.py` + `routers/**/*.py` | 裸 `os.replace\(` 零命中 (白名单 `atomic_io.py` + test code) (§3A F1 新, §4.1.2 派生) |
| `smoke/p24_lint_drift_smoke.py` | 源码级断言 | 上述 5 条 + 其它历史纪律一次跑一遍, 加入 P24 回归 |

**workload**: ~2-3 小时 (5 条 cursor rules + 1 份 lint smoke + CI 接入)

### 13.9 深度审查发现的"隐性问题" (非 choke-point, 但值得入 P24 backlog)

本轮 sweep 顺手发现以下**非原则性但值得 P24 复核**的点:

- **`eval_results` 多源写入但无不变量约束**: `judge_router / persistence / snapshot_store / reset_runner` 5 处各自赋值, 没有 "按 created_at 倒序" 的写入点守护, 仅靠**默认按插入顺序且 created_at 单调**的惯例. 若未来 rewind 场景把旧的 eval_results 合并进新的列表, 可能破坏 B8 的 newest-first 假设. **P24 动作**: 入 backlog 观察, 非必修 (现状代码路径不会破坏).
- **`memory_previews[op] = {...}`**: `memory_runner.py:222` 直接 assign, TTL 清理走 "读时 prune"; 写入时若已有 same-op 的 preview 会被覆盖 (前次 dry-run 丢失). **现状是 OK 的** (用户 accept → commit → pop; 新的 preview 是新 op 覆盖老 op), 但**多 op 同 session 可能存在 preview 间资源冲突** (比如 recent_compress 和 facts_extract 各有 memory 操作预览). **P24 动作**: §12.3.D 主程序同步时顺手核对.

### 13.10 §13 总结 + workload 调整

**§13 新增 P24 必做项**:

| 子章 | 项 | workload |
|---|---|---|
| 13.2 | F6 `autosave._clear_all_autosaves` 顺序复核 | 10min |
| 13.3 | A10 `_coerce_bool/float` 提公共 + 其它 router sweep | 2h |
| 13.4 | C3 Node.append(null) 第 3 次防御 sweep | 2h |
| 13.5 | D1 `page_persona.js` lazy init 重构 + sweep 其它 | 1h |
| 13.6 | F4 asyncio Task/Event cleanup 纪律审 + 补强 | 1.5h |
| 13.7 | G1 simulated_user 边界明文化 | 0.5h |
| 13.8 | 5 条 .cursor/rules + p24_lint_drift_smoke | 2-3h |
| **§13 新增合计** | | **~10-12h ≈ 1.5 天** |

**P24 总 workload 第三轮更新**: 原 13-16 天 (第二轮) + §13 新增 **1.5 天** = **14-18 天**.

**P24 Day-by-Day 调整 (§8 再扩展)**:

- **Day 0 扩展**: §13.8 的 .cursor/rules 5 条 + §13.2 autosave 顺序核实, 并入 warm-up
- **Day 2 扩展**: §13.3 A10 coerce 提公共 + §13.7 G1 边界明文化, 并入 "HTTPException + api_key redact" 当天
- **Day 5 扩展**: §13.4 C3 sweep + §13.5 D1 sweep + §13.6 F4 task cleanup 审, 并入 "事件总线 matrix + renderAll 漂移检测" 当天
- **Day 9 扩展**: `p24_lint_drift_smoke.py` 作为 P24 新增烟测之一, 和其它回归一起跑

### 13.11 延伸教训 (归纳候选, §12.10 继续扩展)

7. **"纸面 choke-point 原则" 必须配 "静态核查入口覆盖率" 的硬方法** ⚠ 本轮最重要教训. 本项目里大量 §3A 原则描述 "X 必须走 Y 单一守护", 但**没有一条原则**配了 "Y 守护了几个入口 / 有几个绕过" 的实证. 本轮 sweep 的 15 条 choke-point 型原则里 **4 条漏守** (A7/B12/F1/B1), **5 条可疑** (A10/C3/D1/F4/G1), 实证合规率只有 **40%**. 归纳: **任何声称 "X 统一走 Y" 的原则, 落地时必须同时建立两件事: (a) 运行期守护 (helper 函数或 lint) + (b) 静态核查方法 (grep 模式 + 覆盖率期望). 缺 (b) 就是 "纸面原则", 半年后实现必然漂**. 这条已经上升为 §3A 候选新条 (E 组新增). 
8. **"choke-point 合规率" 应作为阶段验收 KPI**. 每次阶段结束时应跑一份 "本阶段新增/修改的 choke-point 原则 → 入口覆盖率" 报告, <100% 的必须入当阶段的 backlog 或 P24 类整合期. 机械化: 在 `PLAN.md` 阶段 todos 里加一行 "choke_point_audit_delta: [list]", PROGRESS 详情对应填入覆盖率数字.
9. **"用户实测 > AI 推断 > 文档原则" 的应用范围 (§12.10 #5 扩展)** — §12.5 纠错后, 本轮 sweep 对 "同族漏守" 的普查证明: 不只虚拟时钟, A7 / B12 / F1 / B1 都有同样问题 — **文档原则记录的是历史上某一次修法的认知, 不是当下所有入口的真实状态**. 对 agent 的行为指令: (a) 读到 §3A 某条原则时, 不要默认它在当前代码里实际生效; (b) 需要验证时跑 `rg 守护函数 | wc -l` vs `rg 潜在绕过模式 | wc -l`, 两者不匹配就是潜在 bug; (c) 用户反馈 "X 不 work" 时, 若代码里有 "X 受 Y 守护" 的 docstring, 先怀疑 "Y 漏守了 X 的某个入口", 而非怀疑用户或 X 的逻辑.

---

**§13 交付状态 (Day 11 回填)**: 11 条 Choke-Point 原则实证扫描 + 3 条同族 sweep + 1 条深度隐性问题, 按 §3A 族内逐条落定:

- **§13.1 选 15 条 choke-point 原则实证**: 6 绿 + 4 漏守 + 5 可疑 → 4 漏守 ✅ Day 2-6 全修 (A7 → `messages_writer` / B12 → dead emit 清 / F1 → `pipeline/atomic_io.py` 抽出 / B1 → `DEBUG_UNLISTENED` 运行期检测) + 5 可疑 ✅ Day 3-6 走 sweep (A10 coerce helper / C3 append null / D1 lazy init 档案化 / F4 task cleanup 审通过 / G1 `_escape_user_content_tag` 边界明文化)
- **§13.2 F6 多文件原子组删除顺序** — ✅ 实证合规 (tar 先 JSON 后)
- **§13.3 A10 JSON body coerce** — ✅ Day 3 交付 `pipeline/request_helpers.py::_coerce_bool / _coerce_float`, 7 处 caller 迁移
- **§13.4 C3 append(null) sweep** — ✅ 0 违规但加 `safeAppend` 前瞻 helper
- **§13.5 D1 async lazy init** — ✅ 决策档案化 (不抽 helper, 补 rule)
- **§13.6 F4 资源 dispose** — ✅ Day 5 并入 M1 审查, 审通过
- **§13.7 G1 永不过滤边界** — ✅ Day 3 明文化成 `tests/testbench/pipeline/judge_runner.py::_escape_user_content_tag` docstring
- **§13.8 三份 docs 承诺未兑现** — ✅ §12.3 同步落地; 17 项归 P24, 4 项归 P25
- **§13.9 深度隐性问题 "规则级 lint 守护"** — ✅ Day 9-10 `p24_lint_drift_smoke.py` 落地 (5 hard rule + 14 soft warn)
- **§13.10 workload 调整** — 原 5.5-7d 扩为 8.5-10d
- **§13.11 延伸教训 9 条** — ✅ Day 11 入 `LESSONS_LEARNED §7` (特别是 #7 "choke-point 合规率应作为阶段 KPI" 升格为 A18 原则)

---

*§13 第三轮审查章节结束*. **P24 开工前建议流程**:
1. 先读 §0 使用说明
2. 通读 §12 + §13 了解第二/第三轮 sweep 全貌
3. 按 §8 (含 §12.8 + §13.10 调整) Day-by-Day 执行
4. 每日完工后本蓝图对应项 "交付状态" 列填 done + commit ref

---

## 14. P24 方案本身的元审查 (2026-04-21 第四轮 meta-audit)

> **本章的由来**: 用户要求 "**对 P24 方案本身进行设计审查: 有哪些方面被遗漏?
> 设计合理吗? 依据方法论还能用于哪些代码审查? 哪些代码问题可能被遗漏? 未来
> 接入更多模块时哪些代码是高危? 历史 bug 的频率/隐蔽性分类? 同步/UI刷新/
> 可靠性/代码陷阱的检查完善吗?**" — 这是第四轮审查, 审查对象是**前三轮审查
> 本身 + 蓝图方案的设计合理性 + 识别未覆盖面**. 收敛信号在 §14.10.

### 14.1 P24 蓝图覆盖度总表 (绿 / 黄 / 红)

"**覆盖度**" 分三档: 绿 = 规格完整 + 入口实证 + smoke 规划齐; 黄 = 规格有
但入口未 sweep 全或 smoke 待定; 红 = 本轮元审发现但未在蓝图任何章节覆盖.

| 维度 | 绿 (完整覆盖) | 黄 (有规格未彻底) | 红 (未覆盖) |
|---|---|---|---|
| **数据层可靠性** | atomic_write fsync 1/6 (§4.1.2) / 消息单调 choke-point (§12.5) / 多文件删除顺序 (§13.2 ✅ 合规) | 时间字段 canonical (§4.3 F, 只定约定未改存量) / schema_version 分散 (§4.1.4, 决策入档不落地) | **JSON 反序列化半完成状态降级** / **SessionArchive 新字段向后兼容协议** |
| **并发与锁** | SessionState 矩阵 (§4.3 D) / session.lock 覆盖 (§13 A2/A4 ✅) / asyncio 资源 dispose (§13.6) | F4 task.cancel 等待语义 bounded (§13.6 已列未深审) | **取消/停止操作幂等性和中间态** (Stop chat / abort auto_dialog / stop judge 能否原子停?) / **fetch 时序竞态 + AbortController 全仓覆盖** |
| **前端状态** | renderAll dev-only drift detector (§3.5) / 事件总线 matrix 6 处违规修 (§12.1) / C3 Node.append(null) sweep (§13.4) / D1 lazy init sweep (§13.5) | B9 i18n 跨页一致性 sweep (§13.8) | **LS key 版本 + 切 session 清理纪律** / **event payload shape 稳定性** (改 emit payload 字段是否漏改 listener) / **fetch 响应乱序** (快速点 Refresh × N 次) / **空状态 mount/unmount race** |
| **安全性** | api_key 脱敏全路径 (§4.3 H) / uvicorn host binding (§4.3 I ✅) / F7 Security 子页 (PLAN §15.2D) | G2 `.format()` 新代码扫 (§13.8) / G3 `<user_content>` lint (§13.8) | **audit log 里 memory/persona 字段 PII 审视** (Diagnostics 展示给同机任意访问者) / **F5 记忆 compressor 真实场景命中率采集** |
| **UI 完整性** | 事件 6 处违规 (§12.1) / dev_note UI bug 17 项 (§12.4) / Settings UI 偏好接线 (§12.3.A) / 顶栏占位清 (§12.3.B) / 默认角色显示 (§12.4.A) | 时间回退 UX warning (§12.5 L2) | **资源上限的 UX 降级** (diagnostics ring 满 / snapshot hot 满 / token 超限 时用户看到什么?) |
| **API 契约** | HTTPException shape (§4.1.1) / DiagnosticsOp 枚举 (§4.1.5) | A10 coerce 其它 router (§13.3) / API error parser 前端覆盖 (§4.3 E) | **前后端字段名漂移守护** (后端改字段名前端是否跟上 - 比如 P22.1 的 `memory_hash_verify` 消费覆盖率) / **SSE event 类型枚举** (没有集中定义) |
| **健康度与观测** | H1 健康端点 (§3.1) / H2 schema lint (§3.2) / injection 命中率采集 (§4.3 J) | ring buffer 200 实测 (§4.2 #7) | **资源占用上限 UX** (见上) / **session.describe() / serialize() 字段白名单机械守护** / **主程序 ↔ testbench 依赖 pin 机制** |
| **时间系统** | 消息单调 (§12.5) / 时钟 mutation pre-warning (§12.5 L2) | — | ⚠ **~~其它 *_at 字段~~**: 本元审扫确认 `eval_result.created_at` / `session.created_at` / `diagnostics_store.at` / `memory_runner.created_at` / `stage_history` / `session_export.generated_at` / `judge_export.now` / `logger.ts` **全部用 `datetime.now()` wall clock**, 不受虚拟时钟回退影响, §12.5.5 担忧已解除 ✅ (只有 session.messages 走 virtual cursor) |
| **生命周期** | autosave scheduler dispose (§13.6) / SQLAlchemy engine (P20 hotfix 2) | asyncio.Task cancel 三处 (§13.6 浅审) | **DOM 级常驻 listener** (全仓 5 处 `document.addEventListener(click/keydown)` + 2 处 `window.addEventListener(error/unhandledrejection)` **无 remove 路径**) / **模块级 cache 清理纪律** (i18n cache / characters_cache 已有, 新 cache 呢?) |
| **新功能集成** | 延期加固 P-A/P-D/F6/F7 | 主程序同步 道具交互 / Realtime (§12.3.D) | **SessionState 枚举扩充** (P24 新 op 是否需要新 state? 当前 IDLE/BUSY/SAVING/LOADING/RESETTING/REWINDING 6 个, 新加 AUTOSAVING / EXPORTING 是否必要) / **router include 完备性守护** (server.py:163-174 新加 router 忘 include = 404) |

**覆盖度小结**:
- 绿: **22 项** (充分覆盖)
- 黄: **10 项** (有规格/方向, P24 Day 0 / Day 2 / Day 5 sweep 期补齐)
- 红: **14 项** (本轮元审新识别, §14.4 展开, 需纳入 P24 或明确归 backlog)

### 14.2 §3A 47 条核查的方法论盲区

**§13 方法论**: "**choke-point 型原则 → grep 守护函数 vs 潜在绕过路径 → 覆盖率**".
这个方法论**只覆盖了 15/47 条**, 剩下 32 条属于以下三类**不适用机械 grep** 的:

#### 14.2.A · 设计 Pattern 型原则 (难以机械核查)

- **A11** 纯函数不绑 session — 需语义判断"接受什么参数, 能不能复用"
- **B8** newest-first + first-seen-wins 单次遍历建 Map — 需读代码理解循环结构
- **B10** 1:1 vs 1:N UI 控件差异 — 需读前后端全栈
- **B11** 业务 key 英文 + tier-2 中文释义 — 需肉眼看 UI label

**P24 动作**: 这类原则**只能靠新代码 review 时对照, 不能靠 sweep**. 建议
加一份 `.cursor/rules/new-code-design-pattern-review.mdc` (人机交互式), 要求
Cursor 在 `pipeline/**/*.py` / `static/ui/**/*.js` 下新建文件时弹出 §3A
相关 pattern 检查单给 agent 对照.

#### 14.2.B · 语义型原则 (需上下文理解)

- **G1** 永不过滤用户内容 — `simulated_user._postprocess_draft` 的 "输出清洗 vs 输入过滤" 边界是语义判断 (§13.7 已讨论)
- **B7** one-shot hint 模式 vs 持久偏好订阅 — 需要判断 hint 性质
- **A1** 软错硬错契约 — 需要判断 "整批失败" vs "单条失败"
- **A8** Gemini 最严 wire 规则 — 需要语义地判 "首轮 / 末尾 / 跨 provider"

**P24 动作**: 无 sweep 方案, 靠 code review. 在 §3A 每条末尾加 "审查方法"
子标签: `[sweep-grep]` / `[code-review-only]` / `[runtime-assert]`, 让未来
agent 知道哪些能机械查哪些只能读代码.

#### 14.2.C · CSS / UI 布局型原则 (部分可机械, 大部分肉眼)

- **C1** CSS 变量不定义不用 — 可 sweep: `rg 'var\(--' -g '*.css' | grep -v 'defined_list'`
- **C2** Grid template 子元素同步 — 已有 skill `css-grid-template-child-sync`, 难完全机械 (需 DOM 运行期验证)
- **C5** min-width:0 全链 — 必须 devtools 肉眼逐层点开看
- **C6** flex-shrink:0 + min-width:0 + JS 截断 三层组合 — 同上

**P24 动作**: C1 可机械化进 pre-commit; C2/C5/C6 留肉眼 + skill 提醒.

#### 14.2.D · 本轮补扫的 §3A 条目 (§13 漏了的几条)

用本轮剩余时间快速核查几条 §13 未覆盖的 choke-point 型原则:

| 条 | 快速核查 | 结论 |
|---|---|---|
| **A3** 资产双目录 builtin/user | grep 所有 `builtin_*.json` / `USER_*_DIR`: `tests/testbench/scoring_schemas/builtin_*.json` + `tests/testbench/dialog_templates/sample_*.json` + `tests/testbench/presets/*/` 都在代码目录 ✅; 用户覆盖都在 `testbench_data/` ✅ | ✅ 合规 |
| **A5** SSE 先 yield error 再 raise | grep `yield.*error.*type` 与 SSE generator: chat_router (send/auto/script) + judge_router (run SSE) 都遵守 (P13 #38 已立) | ✅ 合规 |
| **A6** 生成器 finally 先快照 | `auto_dialog.run_auto_dialog` 已修 (#48). 但 **SimUser generate_turn / Script runner / judge_runner 的 yield 型 API 是否同样守?** | ✅ P24 Day 10 复核完成 (见下) |
| **A9** Template Method 基类 monkey-patch 所有步骤 | P16/P17 踩点 (#67) 后该做法已固化到 smoke 模板, 但**新增 judger 类 / 新 pipeline 基类** (未来 emotion / summary) 是否同样遵循? | ⚠ 设计 guideline 需加进 §3A A9 |

##### A6 复核结论 (P24 Day 10)

目标: SimUser + script runner + judge runner 三个候选 "yield 型 API" 逐个判形态 + 查 A5/A6/A9。

| 模块 | 实际形态 | A5 `yield error` | A6 `finally snapshot` | A9 基类 Template Method |
|---|---|---|---|---|
| `pipeline/simulated_user.py::generate_simuser_message` | `async def` 普通函数 (无 `yield`) — 请求-响应式 | ❌ 不适用 | ❌ `finally` 只 close HTTP client, 无跨 finally 的字段依赖 → ✅ | ❌ 非基类 |
| `pipeline/script_runner.py::advance_one_user_turn` / `run_all_turns` | `AsyncIterator` (真 async generator) | ✅ 捕获 backend 异常 → `yield {"event":"error", ...}` 再自然退出 (见 L963-982, L1034-1039); 顶层 `_script_next_event_stream` / `_script_run_all_event_stream` 再套一层 SSE-layer error 兜底 (chat_router L1085-1188) | ❌ **无** finally 块 — script runner 的状态 (`session.script_state`) 在 stream 成功时显式推进 `cursor`, 失败时**保留原位**, 不依赖 finally 清理 → ✅ | ❌ 非基类 |
| `pipeline/judge_runner.py::BaseJudger.run` + `routers/judge_router.py::judge_run` | `async def` + 串行 `await` 循环 (非 generator) | ❌ 不适用 (不是 SSE) | ❌ `BaseJudger.run` docstring 明说 "Always returns (never raises)" — 所有失败路径都走 "填 `result.error` → `return result`", 根本没有 finally | ✅ Template Method; 基类顶部 docstring 6 步 Runtime flow (`_validate_inputs` → `_resolve_config` → `_build_ctx` → `_render_and_call` → `_parse` → `_finalize`) 已列, 满足 A8/A9 对 monkey-patch 烟测的要求 (L214-228). 现有 smoke `p16_judge_smoke` + `p17_export_smoke` 走的就是"整层 stub" 模式 |

**关键观察**: §14.2.D 把这三者并列其实是**把三种不同形态误读成同一种**:

- **SimUser 是请求-响应**: 一次 LLM 调用 + 返回 draft, 无流式, 只有 `try/except/finally{close}` 三段, A6 从语义上就不成立 (finally 只管资源, 不管业务字段).
- **script runner 是真 generator**: 每 yield 一个 SSE 事件. **但它故意不用 finally**, 而是在 `try/except` 出现失败时**显式 yield error + 保留 cursor 不推进**, 让失败可重试. 这避开了 A6 典型踩坑 ("finally 清 auto_state 后 yield 0") 的所有场景.
- **judge runner 是同步型非 generator**: "Always returns" 契约刻意选择了"不用 finally 也不用 raise" 的设计, `EvalResult.error` 字段充当软错信号, 和 A1 (软错/硬错契约) 一致.

**Meta-Lesson L26 候选**: "yield 型 API" 是个口语化的分类, 审查时应先拆成 **(a) 请求-响应 async func / (b) 真 generator (带 `yield`) / (c) 中间态 (Template Method base)** 三种再套原则. 同一个词在三种形态下适用的原则完全不同 — 本次补扫的 `SimUser.generate_turn` 条目写得像三者都是 generator, 实际只有 script runner 真是, 差异要先分清形态再判定.

**A6 状态**: 三条全部合规, 无需新增修复. P24_BLUEPRINT §14.2.D 该行 ⚠ → ✅.

#### 14.2.E · §14.4 M4 资源上限 UX 降级总表 (Day 10 文档化)

目标: 对 testbench 所有**硬上限 / FIFO 淘汰 / 压缩截断**类资源, 明确"达到上限时用户看到什么". 以前这些策略散落在源码注释和踩坑笔记里, Day 10 做集中文档化 + 标 ✅/⚠/⏭ 状态, 把**未做 UX 兜底**的条目显性化, 后续一旦触达门限立刻知道该走什么流程.

分类与约定:

- **软降级 (soft)**: 到上限后继续工作, 旧数据按 FIFO / 规则丢弃. 必须有 "用户可观测的一次性告知".
- **硬拒绝 (hard)**: 到上限后拒绝请求, 返回 4xx + 明确错误码. 必须有 **actionable 提示**告诉用户下一步.
- **压缩/截断 (truncate)**: 到上限后内容被压缩或截断. 必须在 UI 上标出"已截断"并提供访问原文的通道.
- **未处理 (todo)**: 尚无上限; 理论上可涨到磁盘/内存爆; 留给 P25 或后续阶段.

| # | 资源 | 上限 | 策略 | 代码入口 | UX 用户可见 | 状态 |
|---|---|---|---|---|---|---|
| 1 | **diagnostics ring buffer** | 200 条 (`diagnostics_store.MAX_ERRORS`) | 软降级 FIFO | `diagnostics_store._push` | 第一次溢出: **warn-once** python logger + ring 内注入 `diagnostics_ring_full` notice; Errors 子页可见 | ✅ **Day 10 本轮实装** (本文件 §14.4 M4) |
| 2 | **snapshot hot 内存** | 30 条 / session (`snapshot_store.DEFAULT_MAX_HOT`, UI 可调 1-500) | 软降级 spill 到冷存 | `SnapshotStore._spill_oldest_to_cold` | `pre_rewind_backup` 不占 cap; 透明对用户 (`get` 统一接口), 列表里标 `cold=true`; Settings UI 可调 | ✅ P18 已设计 |
| 3 | **snapshot cold 磁盘** | **无硬上限** | ⚠ 未处理 | `<sandbox>/.snapshots/*.json.gz` | 无提示, 磁盘满才会爆 | ⚠ **Day 10 识别为风险**: 单 `session` 满 30 hot 后每次 capture 往冷存加, 不删. 极端场景 (用户 reset 一下跑 1000 轮 + 每 10s manual capture) 可达 GB 级. **建议**: P25/后续加 `DEFAULT_MAX_COLD=200` (含 backup), 超限时按 FIFO 删最老 cold snapshot + 同步写 diagnostics warn. 不做 autodelete 的 backup 快照一律保留. 本 phase 不动, 避免引入新 bug. |
| 4 | **autosave rolling slots** | 3 (`autosave._SLOT_SUFFIXES`, 硬编码) | 软降级 FIFO (slot 2 → 删 → slot 1 降级 → slot 0 降级 → 写新 slot 0) | `autosave.write_autosave_slot` | 无提示 — 设计上就是静默 rolling, 符合"自动保存"心智 | ✅ P22 已设计, 无需 UX |
| 5 | **judge eval_results / session** | 200 条 (`judge_router.MAX_RESULTS_PER_SESSION`) | 软降级 FIFO | `judge_router.judge_run` persist 分支 | 无提示 (实装时 python logger warning 但不冒泡到 UI) | ⚠ **Day 10 识别为风险**: judge 评分结果被静默 evict 是 **可见性盲点** (用户以为跑过 300 条可以回看, 实际只能看最近 200). **建议**: 在 persist 分支 `len(combined) > MAX_RESULTS_PER_SESSION` 时走 `record_internal` 写一条 `eval_results_evicted` DiagnosticsOp, 并在 Evaluation Run 子页首次触达上限时 toast 一次. 本 phase 不动, 留 P25 or 专项修. |
| 6 | **judge batch size** | 50 条/批 (`judge_router.MAX_BATCH_ITEMS`) | 软降级截断 + python logger warning | `judge_router.judge_run` | 无前端提示 (只有 python logger) | ⚠ **Day 10 识别**: 用户勾了 60 条想 batch 评, 实际只评前 50 条, 没提示. **建议**: response 顶层加 `truncated_to: 50` 字段 + 前端 toast. 本 phase 不动. |
| 7 | **memory file 单个大小 (snapshot)** | 10 MiB (`snapshot_store._MAX_MEMORY_FILE_BYTES`) | 软降级跳过 + python logger warning | `snapshot_store._capture_memory_files` | 无前端提示 — 巨文件进不了 snapshot, rewind 后会**缺失**. | ⚠ **Day 10 识别**: 和 #3 类似, silent skip. **建议**: skip 时走 `record_internal` 写 `memory_file_oversize` DiagnosticsOp (detail 带 path + size). 本 phase 不动, 留 P25. |
| 8 | **memory file 单个大小 (persistence)** | 10 MiB (`persistence._MAX_FILE_BYTES`) | 软降级跳过 + python logger warning | `persistence.serialize_session` | 同 #7 | ⚠ 同 #7, Day 10 识别 |
| 9 | **toast 字符数** | 280 (`ui/toast.js`) | 软降级截断 + `title=` 兜全文 | 前端 `toast.js` | UI 截断展示, hover `title` 看全文; 符合 §3A C6 | ✅ P17 已设计 |
| 10 | **raw_response in EvalResult** | 4000 字 (`judge_runner.run` 末尾 cap) | 软降级截断 | `judge_runner.run` | EvalResult.raw_response 截断展示, 无显式 "已截断" 标记 | ⚠ **Day 10 识别**: Results 子页看 raw_response 不知道有没有被截. **建议**: 超过 4000 字时 raw_response 末尾追加 `…[truncated, N more chars]`. 本 phase 不动. |
| 11 | **analysis in EvalResult (parse fail)** | 800 字 (`judge_runner.run` JSON parse 失败分支) | 软降级截断 | 同上 | 同上 | ⚠ 同 #10, Day 10 识别 |
| 12 | **JSONL 日志保留天数** | 14 天 (`config.LOG_RETENTION_DAYS`, env 可调) | 软降级删旧文件 | `logger.cleanup_old_logs` | 无 UI 提示 (日志是面向 diagnostics, 非用户可视主流程) | ✅ P19 已设计 |
| 13 | **token 超限 (SimUser / judger / chat)** | **无 testbench 侧上限** (依赖上游 LLM provider 报 429/413) | 硬拒绝 (provider 返错 → testbench 传回前端) | LLM client `ainvoke` 抛异常 → router 转 SSE error 帧 | SSE `{event:'error', error:{type:'LlmFailed',...}}`; Auto-Dialog 路径进 `AUTO_DIALOG_ERROR` diagnostics (Day 8 #107 Part 4 修过) | ✅ 运行时保护链已齐, 不需要 testbench 主动做 token 计数 |
| 14 | **session archive 导入大小** | 无显式上限; 10 MiB/file (见 #8) | 部分软降级 | `persistence.load_saved` | 单文件超 10 MiB 被 skip; archive 整体没大小检查 | ⏭ **Day 10 识别**: 极端用户可能故意传 2GB tar 让 server OOM. 本阶段未命中, 留给 P25 或安全加固. |
| 15 | **巨型 memory 目录 (>100MB 总量)** | 无上限 | 无 | — | — | ⏭ Day 10 识别, 同 #14 留后续 |

**按状态汇总**:

| 状态 | 计数 | 说明 |
|---|---|---|
| ✅ 已设计完整 | 6 项 (#1/#2/#4/#9/#12/#13) | 本 phase 充分, 无需动 |
| ⚠ 识别风险但本 phase 不动 | 7 项 (#3/#5/#6/#7/#8/#10/#11) | 文档化 + 设计建议, 触达门限后启动专项或并 P25 做 |
| ⏭ 留后续阶段 | 2 项 (#14/#15) | 安全类, 不在 testbench 当前威胁模型 |

**本 phase 实际行动**: 只做 #1 (diagnostics ring warn-once, 代码已 land), 其余都只文档化, 不碰代码. 理由: (a) §14.1 非目标第二条"P24 不做性能优化, 除非联调时触发用户可感知卡顿" — 这 7 项 ⚠ 都是**理论风险**, 联调期没触达, 不符合行动条件; (b) §14.1 非目标第三条"不承接新架构空白" — 触及多个模块 (`snapshot_store` cold cap / `judge_router` batch trunc notice / `EvalResult.raw_response` 截断标记 / diagnostics ops 新类型 `eval_results_evicted` + `memory_file_oversize`), 是**新需求**而不是 P24 范围的 "延期加固"; (c) 每条都有独立 UX 设计空间 (toast? banner? diagnostics op? 前端 badge?), 应作为单独任务进入 P25 之后的 backlog.

**落档**:

- 本表纳入 P24_BLUEPRINT §14.2.E, 未来新 agent 按这张表可以快速查"XX 资源的上限是什么 + 触达时用户体验如何 + 要不要做新的 UX 改进".
- 7 项 ⚠ 风险同步写入 `AGENT_NOTES.md §5 Backlog` 或 P25 blueprint 的 "本阶段**不**做" 列表, 避免被再次被误当成 P25 scope.
- `diagnostics_store.py` 本轮已新加 `DIAGNOSTICS_RING_FULL` DiagnosticsOp, 与 #1 的 warn-once 配套.

**Meta-Lesson L27 候选**: **"资源上限 UX 降级" 本身是一个横切维度**, 需要定期 sweep. 每当项目新增一个 "FIFO 淘汰 / 压缩截断 / 限流" 机制, 都必须同时回答四个问题 (上限是多少 / 达到时做什么 / 用户怎么知道 / 要不要 actionable 提示), 否则就是 "silent data loss" 的隐形源. 可考虑加一条 pre-commit hook 扫 `MAX_|_SLOTS|FIFO|evict` 关键字, 要求 commit 提交者在 PR 描述里写 UX 降级策略.

### 14.3 "Intent ≠ Reality" 方法论的 5 大扩展应用面

§12.5 + §13 建立的 "**纸面原则 vs 实现现实差集**" 方法论, 可扩展到以下
**此前审查都未涉及**的代码审查领域:

#### 14.3.A · API Shape 一致性

- 同一"概念字段"在不同 endpoint 是否同名/同类型? (`created_at` vs `timestamp` 已列)
- response body shape 是否统一? (HTTPException shape 三并存已列)
- **新发现**: **SSE event type 字符串** 没集中定义, 散落在各 generator. `rg "yield.*event.*'" -g "pipeline/**"` 应出 SSE event 白名单

#### 14.3.B · Schema / 数据契约前后端对齐

- P22.1 加 `memory_hash_verify` 响应字段 — **前端消费覆盖?** sweep: `rg "memory_hash_verify" -g "static/**"` 看是否至少 1 处
- P22 加 autosave 各端点 — **前端全部接线?**
- P23 加 `session:exported` 事件 — 0 listener (§12.1 已抓)
- **归纳**: 任何**后端新字段 / 新端点 / 新事件**都应配套 **"前端消费 PR"** 在同一 pass 合并, 否则就是 "**字段注定被遗忘**".

#### 14.3.C · 前端 Store 字段 vs 消费者同步

- `state.js::_state` 当前: `session / active_workspace / errors / ui_prefs` 4 个根字段
- 新增 ui_prefs 子字段是否所有消费方订阅? (§4.23 #77-78 踩过的 `ui_prefs.evaluation_results_filter`)
- **归纳**: **Store 根字段 whitelist + 新增字段 PR 必须 grep 消费方** — 和 14.3.B 后端字段是对称的.

#### 14.3.D · Session 序列化白名单

多个 Session 序列化出口:
- `Session.describe()` — `/api/session` 响应 (12 个字段白名单 ✅)
- `persistence.serialize_session()` — save/export 用 (字段更多, 含 messages/persona/clock/eval_results 等)
- `snapshot_store.capture()` — 快照用 (需序列化 session 全态)
- `session_export.build_export_payload()` — P23 导出用

**风险**: **新加 session 字段时, 这 4 处是否都同步加?** 本元审未实证, **P24 必做**: grep `self\.\w+` in `Session.__init__` / `@dataclass Session` 列出所有字段, 对每个字段查以上 4 处是否都涉及 — **缺任一处的字段就是潜在 "serialize 漏字段" bug**.

#### 14.3.E · 主程序 ↔ testbench 依赖 pin

- `sandbox.py::_apply` 14 字段 swap 列表 — 主程序 `ConfigManager` 新字段不在列 = 被测 LLM 用生产 API key (§12.3.D 已列)
- `build_prompt_bundle` 导入的 managers — 主程序新 manager 不在列 = testbench 用不了新 persona / memory 功能
- **归纳**: **两套独立 codebase (主程序 + testbench) 的依赖 pin 必须双向 lint**, 主程序 PR 若触及 14 字段之一, 必须同步更新 testbench.

### 14.4 本轮元审新识别的 **14 条未覆盖问题**

以下是前三轮**都没涉及**但元审发现的问题, 分三档 (P24 必做 / P24 可选 / P24 backlog):

**必做 (M1-M5)**:

- **M1** · **取消/停止语义幂等性**: Stop chat / stop auto_dialog / abort judge_run 能否原子停? AGENT_NOTES #50 P13 坑 "Stop 后半轮悬空" 可能未完全修. **P24 Day 7 联调必测** 的场景清单: 正在 SSE stream 中点 Stop → state 回 IDLE / messages 末尾是否半消息 / 再次 send 能否 work.
- **M2** · **DOM 级常驻 listener 无 remove**: 全仓 5 处 `document.addEventListener(click/keydown)` + 2 处 `window.addEventListener(error/unhandledrejection)` **全部没有 removeEventListener**. Testbench 单页面模型下不泄漏, 但 Hard Reset (走 `location.reload()` §3A B13) 不依赖这条, 任何未来"不 reload 的 soft reset" 都会累积 listener. **P24 Day 5 加 teardown 清单**.
- **M3** · **fetch 响应乱序**: 前端无 AbortController 的 fetch caller 不明数量, 快速点 [Refresh] × N 会有 "last fetch wins" 而非 "last click wins" 问题. **P24 Day 5 sweep**: `rg "api\.get\(|api\.post\(" -g "static/**" | wc -l` ≈ 200+ 处, 其中快速可重点击的 toolbar refresh 类至少 10 处. 方案: 统一包一层 `api.getCancellable()` helper, 旧 Promise pending 时 abort.
- **M4** · **资源上限 UX 降级**: 下列上限达到时用户看到什么? (Day 10 文档化结果见下 `§14.4 M4 资源上限 UX 降级总表`)
- **M5** · **Session 序列化白名单漂移守护**: §14.3.D 的 4 处 serialize 出口, P24 必抽 smoke 断言 — 对每个 Session 字段, 要么在 `persistence.serialize_session` 白名单里, 要么显式声明 `not_serialized` (运行期 only). 机械化: P24 Day 9 加 `smoke/p24_session_fields_audit_smoke.py`.

**可选 (O1-O5, P24 有空做 / 推 P25)**:

- **O1** · **LS key 版本协议**: 新加 LS key 带 `:v1` 后缀 (§3A B11 提过), 新版本 bump 时 clear 旧. 但**新 session 切换时是否清旧 LS hint?** — `evaluation:results:filter:v1` 会跨 session 残留.
- **O2** · **event payload shape 稳定性**: emit 的 payload 字段集合改了谁知道? 比如 `snapshots:changed` 的 `{reason, id}` 若加 `deleted_count` 字段, 所有 listener 会不会 undefined? **仅推 backlog**, 现状 payload 简单度低.
- **O3** · **空状态 mount/unmount race**: 用户切子页极快时 fetch 可能给已卸载 host 写. 现状 `host.__off*` teardown 部分处理, 但不全. **P24 Day 5 并入事件总线 matrix sweep**.
- **O4** · **后端字段新增前端消费覆盖 PR 模板**: 14.3.B 提的模式, 可写成 `.cursor/rules/backend-field-add-check.mdc` 让 agent 新加后端字段时自动弹 "前端消费 checklist".
- **O5** · **SSE event 白名单集中定义**: 14.3.A 提, 可加 `pipeline/sse_events.py` 枚举所有 `event` 字符串, 各 generator import.

**Backlog (B1-B4, 不在 P24 做, 明确归后续)**:

- **B1** · **SessionArchive 新字段向后兼容协议**: schema_version 三件套 (§4.1.4) 已决策入档, 真正落地等 schema bump 时做
- **B2** · **跨时区/DST 边界**: 本地工具场景下极罕见, P24 不主动测, 出 bug 再立项
- **B3** · **unicode 路径污染 (P23 filename 外的其它路径)**: 无已知案例, 预防性做价值低
- **B4** · **JSON 反序列化半完成降级**: 读路径遇 JSONDecodeError 直接冒泡, 可改为 "标坏档 + 旁车保留", 但改动面大

### 14.5 未来接入新模块的 9 处高危代码

若 P25 / P26+ 扩展 (新 AI 功能 / 新 workspace / 真用户发布), 以下 9 处是 **高危 hotspot** — 改动此处若无对应 PR 模板极易引入 bug:

| # | 高危点 | 风险 | 守护建议 |
|---|---|---|---|
| 1 | `sandbox.py::_apply` 14 字段 swap 列表 | 主程序加新路径字段不同步 → 被测 LLM 用生产资源 | `.cursor/rules/sandbox-apply-fields.mdc` + 单元测: 枚举 ConfigManager 所有 `*_dir` / `*_path` 字段 |
| 2 | `session_store.py::Session @dataclass` | 新字段漏加 describe/serialize/capture/export 任一 = 静默丢失 | §14.4 M5 smoke |
| 3 | `server.py:163-174` include_router 列表 | 新 router 忘 include = 404 | `.cursor/rules/router-register.mdc` + smoke 断言每个 `*_router.py` 都 include |
| 4 | `prompt_builder.py` 导入的 managers | 主程序加新 manager 不 import = testbench 无法测新功能 | 同 #1 |
| 5 | `static/core/i18n.js` 字典 | 新 key 漏加 = 运行期 "[key]" 占位符 | `.cursor/rules/i18n-key-sync.mdc`: 新 `i18n('x')` 必须对应字典项 |
| 6 | `SessionState` enum | 新 op 需要新 state 吗? 不加 = 老 state 复用带来语义漂移 | §4.3 D P24 已画矩阵, 未来扩时按矩阵审 |
| 7 | 事件总线事件名 | 无 schema 约束, typo / 漏 off | §12.1 + §13.8 pre-commit `emit-grep-listener.mdc` |
| 8 | `diagnostics_store.record_internal` op 枚举 | 字面量散落 (§4.1.5 已列) | DiagnosticsOp StrEnum 落地后未来强制用 |
| 9 | `.cursor/rules/*.mdc` 本身 | 新模块目录不在 glob 范围内规则不生效 | P24 加 rules 时 glob 用 `static/**` / `pipeline/**` 等宽泛模式 |

### 14.6 历史 Bug 分类 (频率 × 严重性 × 隐蔽性)

从 §4 77 条踩点 + §4.26/§4.27 changelog 提炼:

#### 14.6.A · 高频 Bug (项目反复踩)

| bug 类 | 次数 | 归属原则 | P24 防线 |
|---|---|---|---|
| **renderAll 漏调** | **6 次** (P09/P11/P12/P13/P16/P17) | B1 | §3.5 dev-only drift detector + `bindStateful` helper |
| **`i18n(key)(arg)` 误用** | ≥ 2 次 (P07/P16) | i18n 头条 | §13.8 pre-commit `i18n-fmt-naming.mdc` |
| **事件订阅漂移 / 0 listener / teardown 漏** | 4+ 次 (#65/#68/#77/#78/§4.26 #87) | B3/B12 | §12.1 matrix + §13.8 `emit-grep-listener.mdc` |
| **async lazy init 竞态** | 3 次 (#23/#47/#39) | D1 | §13.5 Promise cache + `async-lazy-init-promise-cache` skill |
| **跨 workspace 导航缺步** | 3 次 (#70/#77/#78) | B7 | 协调者 force-remount 模式 (P17 定型) |
| **Grid template 子元素漂移** | 3 次 (#49/#55/§4.26 #87/#100) | C2 | `css-grid-template-child-sync` skill |
| **`Node.append(null)`** | 2 次 (#42/#75) | C3 | §13.4 sweep + 返 `DocumentFragment` 模式 |
| **长文本 min-width:0 漏父链** | 2 次 (#74/#76) | C5 | `.u-min-width-0` utility class |
| **SSE 先 yield error 再 raise 漏** | 1 次 (#38) | A5 | 该模式已固化, 新 SSE 按模板 |
| **`??` 对 0/空串不 fallback** | 1 次 (§3A 原则 #sweep 4) | — | §4.2 sweep |

**共性归因**: 上表 10 类中 **8 类来自前端** (B1/i18n/B3/D1/B7/C2/C3/C5), **2 类来自后端** (A5/??)。**前端 "状态驱动 + 事件驱动" 模型的心智负担远大于后端 "请求-响应" 模型**, 这也解释了为什么 §3A 里 B 组 13 条 > A 组 11 条 > 其它组.

#### 14.6.B · 低频高危 Bug (一发生后果严重)

| bug | 触发条件 | 后果 | 当前防线 |
|---|---|---|---|
| **Hard Reset 事件风暴导致 Windows 黑屏强重启** | 点 Reset → 15+ listener 同步处理 → CPU 100% | **整机失响** (§4.26 #87, P20) | 已修 (location.reload 替代 surgical patch, B13) |
| **SQLAlchemy engine 缓存致 WinError 32** | rewind + 缓存持锁 | 文件系统锁, 用户必须重启 (§4.26 #88, P20 hotfix 2) | `_dispose_all_sqlalchemy_caches` (F4) |
| **文件级编码污染 `??` 乱码** | 某编辑工具 GBK/UTF-8 误解码 | 全部中文字面量静默损毁 (§4.13 #15) | i18n.js 独立文件 + 业务 JS 禁非 ASCII (C4) |
| **BSOD/强 kill 下 memory 文件 0 字节** | 写 .tmp 未 fsync + 断电 | 永久数据丢失 (§4.27 #95/G1) | 已修 `persistence.py` fsync; 其它 5 份副本待 P24 §4.1.2 |
| **TOCTOU tar 被删后 load 空 memory** | list 到 Load 之间 ~200ms 删除 | 静默数据丢失 (§4.27 #95/G2) | 已修 (400 fail-loud) |
| **虚拟时钟回退静默错乱 messages** | set_now 到过去 → SSE append 绕过 check | 下游 dialog_template / UI 时间分隔条错乱 (§12.5, 2026-04-21 本项目) | P24 §12.5 修 |

**共性归因**: 低频高危 bug 集中在 **"崩溃边界 + 多路径绕过 + 可序列化状态"** 三个方向. 防线都是"写入点收敛 + 机械化守护", 正是 §12.5-§13 元教训的核心.

#### 14.6.C · 深度隐藏难排查 Bug (排查耗时巨大)

| bug | 隐蔽机制 | 排查耗时 | 经验 |
|---|---|---|---|
| **浏览器实测失败但 jsdom 单测全绿** | 栈帧交错 / ES 模块缓存 / 真实 DOM vs detached DOM | P17 hotfix 4→5 三轮 (§4.23 #77/#78) | 真实浏览器 manual test 不能省 |
| **grep 命中 ≠ 所有路径调用** | 函数被调用过 ≠ 所有 caller 都调 | §12.5 本次 | 用户实测优先级永远最高 |
| **jsdom 不实现的 CSS 行为** | grid / min-width:auto / -webkit-line-clamp | 多次 (#49/#55/#74/#76) | devtools 肉眼 + skill |
| **同秒背靠背消息 timestamp 相等** | `<=` 允许 但某些下游期望严格 `<` | 潜在 (#13) | `<=` 已写入 docstring |
| **LLM 自然语言误 match injection pattern** | 用户合法输入碰巧含 `ignore previous` 子串 | 未确认, P24 §4.3 J 数据采集 | 监控真假阳性比 |
| **跨会话数据污染 (module-level state)** | `latestEvalByMsg` Map 等闭包变量 | 1 次 (#9) 已修 | 挂 host 的 `__` 前缀字段 |

### 14.7 P24 蓝图的结构性问题 + 建议改进

元审对蓝图本身的 4 条结构性批评:

#### 14.7.A · "P24 必做清单"散在 §4 / §12 / §13 / §14, 缺统一总表

**问题**: 用户 Day 0 开工时想 "今天做哪些?" 要翻 4 个章节的 workload 表, 容易漏.

**建议**: 新增 **§15 P24 任务清单总览** (见 §14.8), 按 Day-by-Day 展开每个 Day 的子任务并双向链接回 §3/§4/§12/§13.

#### 14.7.B · Day-by-Day 节奏多次插入使可读性下降

**问题**: §8 已有 `Day 0` / `Day 5.5` / `Day 7.5` 等插入, 第四轮后可能再加.

**建议**: 重写 §8 为 **Day 1-12 连续编号**, 不再 "插入式", workload 合并进每一天的 "子任务" 列.

#### 14.7.C · workload 区间 14-18 天缺 "上限/下限" 决策指南

**问题**: "14-18 天" 是范围, 但什么情况下会偏 18 天? 14 天?

**建议**: 加 §8 附注:
- **偏 14 天** (顺利): 主程序同步 git diff 小, 可疑项 sweep 后 70%+ 合规, 联调期无意外 bug
- **偏 18 天** (不顺): 主程序同步暴露 10+ 字段 / Realtime 接口接入 / 联调期出 3+ 非 dev_note 新 bug

#### 14.7.D · "P24 中途发现新问题" 缺决策模板

**问题**: Day 3 联调发现 dev_note 没提到的 bug, 是纳入 P24 还是推 backlog? 模糊.

**建议** (归纳为 §14.9 #10):

> 新 bug 决策树:
> 1. **是数据丢失相邻**? → P24 Hotfix (当日内修, 不推)
> 2. **是 §3A 已有原则违反**? → P24 必修 (和 §13 一样 sweep 同族)
> 3. **是新功能/UX 改进**? → P25 or backlog
> 4. **是架构级发现 (类 §4.26 #91 级别)**? → 新开 phase, 不塞 P24

### 14.8 本章新增 P24 必做项 + workload

| 子章 | 项 | workload |
|---|---|---|
| 14.4 M1 | 取消/停止语义幂等性测试 (Day 7 联调) | 1-2h (和联调合并) |
| 14.4 M2 | DOM 级常驻 listener teardown 清单 (Day 5) | 1h |
| 14.4 M3 | fetch 响应乱序 + AbortController helper `api.getCancellable` | 2h |
| 14.4 M4 | 资源上限 UX 降级规格文档化 + 实装 diagnostics ring 满时 warn 一次 | 1h |
| 14.4 M5 | Session 字段序列化白名单 smoke | 1h |
| 14.2.D | A6 / A9 / SSE yield error 其它 generator 复核 | 1h |
| 14.2 | `.cursor/rules/new-code-design-pattern-review.mdc` 人机交互式 | 0.5h |
| 14.7 | 重写 §8 Day 1-12 + 加上限下限 / 新 bug 决策树 | 1h (文档) |
| **§14 新增合计** | | **~1-1.5 天** |

**P24 总 workload 第四轮更新**: 14-18 天 (第三轮) + §14 新增 1-1.5 天 = **15-19 天**.

### 14.9 延伸教训 (§12.10 继续扩展, 第 10-12 条)

**10. "新 bug 决策树 必须是明文规则, 不靠 agent 临场判断"** — §14.7.D 派生. 本项目已经踩过多次 "发现新 bug 塞进当前 pass 导致 scope 爆炸" 的案例 (P19 hotfix 5 4 个陈年小债 #81 / P20 hotfix 1 Hard Reset 连锁 #87). 决策树 (4 档: 数据丢失 → hotfix / 原则违反 → sweep / 新功能 → backlog / 架构级 → 新 phase) 给 agent 在开工中途遇到新 bug 时一个明确 template, 不再模糊. 归纳: **任何规模 >1 周的阶段开工前必须定义 "scope creep 决策树"**, 否则 workload 膨胀不可控.

**11. "方法论扩展应用面" 作为元 audit 的下一步** — §14.3 证明同一方法论 (Intent ≠ Reality) 可扩展到 API shape / 前后端字段同步 / Store 白名单 / Session 序列化出口 / 主-testbench 依赖 pin 5 大面. **归纳**: **每发现一个能抓 bug 的方法论, 立即问"这个方法论还能用在哪?"** — 不扩展应用就是单点胜利而非系统改进. 本项目 §13 抓了 choke-point 型, 但没主动扩到 API shape / 字段同步等 4 条对称面, 是本次元审的 ROI 最高增量.

**12. "覆盖度 RAG 灯 作为阶段方案自检工具"** — §14.1 三色总表 22 绿 / 10 黄 / 14 红 把 P24 完善度从 "看起来挺全" 量化成 "14 红 + 10 黄 = 24 项潜在遗漏". 没这张表就无法判断"P24 方案已经足够", 有了就能明确 "15 项必做 (绿) + 10 项 sweep 期补齐 (黄) + 14 项分别分类入 M/O/B 三档". 归纳: **任何 >5 天的阶段方案都应有一张覆盖度 RAG 总表, 并在每轮审查后更新**. 不是 "看 80% 就够了", 而是 "剩下 20% 的 gap 有名字有归属".

### 14.10 P24 方案完善度判断: 是否建议开工

**量化**:
- 前三轮 sweep: §4.1 五条实证 / §12 事件 matrix + UI bug + 虚拟时钟 / §13 15 条 choke-point 核查
- 本元审新识别: §14.1 14 红 + 10 黄 + §14.4 14 条 M/O/B + §14.5 9 处未来高危 + §14.6 历史分类
- workload: 15-19 天, 约占 P00-P25 总周期的 **1/4**
- §3A 候选新条: **A7 修订 + A16 + A17 + B14 + B15 + E3 + A18 (§14.2) + 更多**

**判断**: ✅ **P24 蓝图已达到开工级完善度**. 具体依据:

1. **已经识别的 bug 数量趋于饱和**: 四轮 sweep 合计发现 30+ 实证违规 + 25+ 可疑项, 后续再 sweep 的边际效益明显递减 (本次元审 14 条红项里有一半是 "现状可接受, P25 再议" 类, 只有 M1-M5 真正必做)
2. **方法论已稳定** (Intent ≠ Reality / 覆盖度 RAG 灯 / 新 bug 决策树), 不需再元审
3. **剩余不确定性集中在 "联调期实测"** (M1 取消语义 / M4 资源上限 / §12.4.A 默认角色 bug 等) — 这些**本来就只能在运行时发现**, 不是靠更多 sweep 能找到
4. **14 红项里 4 条 M 必做 (§14.4) 已经纳入, 5 条 O 可选, 4 条 B 明确归 backlog** — scope 清晰

**建议**: **按 §8 重写后的 Day 1-12 执行顺序开工**, 每日对照 §14.1 总表勾选进度. **不再做第五轮审查** — 再审下去就是 over-engineering, 反而推迟真正落地. 用户若有新 dev_note 条目, 按 §14.7.D 新 bug 决策树处理, 不回头再开轮审.

**唯一的例外**: 如果 **Day 0 主程序同步盘点暴露 >5 个字段或 Realtime 接口变化大**, 应当重新评估 §14.7.C workload 上下限, 并决定是否把部分 §14.4 可选项推到 P25.

---

**§14 交付状态 (Day 11 回填)**: §14 做元 audit, 产出覆盖度 RAG 灯 + 新 bug 决策树 + 14 条未覆盖问题分 M/O/B 三档. P24 交付覆盖:

- **§14.1 RAG 三色总表 22 绿 / 10 黄 / 14 红** — Day 10 回看: 22 绿全部交付 / 10 黄全部消化 / 14 红按 M/O/B 分类: 4 条 M 必做全交付 (M1 取消语义 Day 8 审通过 / M2 DOM listener teardown Day 6 `page_snapshots` leak 修 + 加 rule / M3 fetch 乱序 Day 6 `api.js` 加 AbortController + 6 处 loadXxx 迁 / M4 资源上限 UX 降级 Day 10 文档化 + warn-once 机制 / M5 Session 字段白名单漂移守护 Day 10 smoke) + 5 条 O 可选有进度 (O1 前端 log viewer / O2 snapshot cold 上限 / O3 judge batch 截断 UX / O4 memory 单文件 10MiB silent skip / O5 raw_response 静默截断) 推 backlog / P25 改进 / 4 条 B backlog 归档不动
- **§14.2 47 条 A6/A9/SSE 方法论盲区** — ✅ Day 10 复核完成: SimUser.generate_turn (async func) / script_runner generators (`advance_one_user_turn` / `run_all_turns`) / judge_runner (BaseJudger.run / judge_run 非 generator) 三个组件各自按三分类套原则, 全部合规, 派生 **L26 "yield 型 API 三分类" 元教训**
- **§14.3 方法论 5 大扩展应用面** — ✅ 第五轮 §14A 已实证 5 大面 (API shape / 前后端字段同步 / Store 白名单 / Session 序列化出口 / 主-testbench 依赖 pin) 抓出 2 漏洞 + 1 合规边缘 (§14A.2 / §14A.3 / §14A.5)
- **§14.4 14 条 M/O/B 未覆盖问题** — 见 §14.1 表
- **§14.5 未来接入新模块的 9 处高危代码** — ✅ P25 开工前设计回顾的输入, 不作 P24 改动
- **§14.6 历史 Bug 分类** — 入档 `LESSONS_LEARNED §3` (高频) + §4 (低频高危)
- **§14.7 P24 结构性问题 + 改进** — ✅ Day 1-12 连续编号 (§14.7.A) + workload 上下限 (§14.7.C) + 新 bug 决策树 (§14.7.D) 全部落地
- **§14.8 workload 最终** — 15-19 天, 实际 14 天交付
- **§14.9 延伸教训 10-12 条** — ✅ 入 `LESSONS_LEARNED §7`
- **§14.10 开工决策** — ✅ 开工

---

*§14 元审章结束. P24 方案经 4 轮审查, 建议开工.*

---

## 14A. 第五轮: 方法论扩大应用的实证结果 (§14.3 落地)

> **定位**: §14.3 提出 5 大扩展应用面但未实证. 本节做一次快速实证 sweep,
> **只列结论 + 定位, 不展开讨论**, 新发现的必做项直接插入 §15 Day-by-Day.
> 本节目标 ≤ 100 行, 防止蓝图过度扩张.

### 14A.1 Session 字段 × 4 出口覆盖矩阵 (§14.3.D 实证)

**Session 字段清单** (session_store.py:~100-183 枚举):

| 字段 | 性质 | `describe()` | `serialize_session` | `snapshot capture` | `session_export` |
|---|---|---|---|---|---|
| id/name/created_at | 持久化 | ✅ | ✅ | 间接 | ✅ |
| clock | 持久化 | ✅ | ✅ | 间接 | ✅ |
| messages / eval_results / persona / stage_state / model_config | 持久化 | count only | ✅ | 捕获 | ✅ |
| lock | runtime | ❌ 正确过滤 | ❌ 正确过滤 | ❌ | ❌ |
| **memory_previews** | runtime (TTL 600s) | ❌ | ❌ | ❓ 需核 | ❌ |
| **script_state** | runtime (文档声明不 persist) | ❌ | ❌ 正确 | ❓ 需核 | ❌ |
| **auto_state** (含 asyncio.Event) | runtime | ❌ | ❌ 正确 | ❌ (不能 JSON Event) | ❌ |
| snapshot_store / autosave_scheduler / logger | runtime helper | ❌ | 通过 snapshots_hot/cold_meta | — | ❌ |
| state / busy_op | runtime | ✅ | ❌ | ❓ | ❌ |

**结论**: 持久化字段 **5 主字段 + 3 派生字段** 在 4 出口全覆盖. 运行期字段 `lock / auto_state / snapshot_store` 等过滤正确. **唯一待核**: `snapshot_store.capture()` 对 `memory_previews / script_state / state` 的处理 — 快照时是否误包 runtime-only 字段?

**P24 动作**: Day 10 smoke `p24_session_fields_audit` 加一条源码级断言 — snapshot capture 不包含 `auto_state / memory_previews / state` 字段. 1h workload.

### 14A.2 `memory_hash_verify` 前后端字段同步 ❌ 实锤漏消费 (§14.3.B)

**实证**: `rg "memory_hash_verify" tests/testbench/static` **零命中**.

- 后端: session_router.py L555/L564/L876/L887 4 处返回该字段 (load + restore 两端点)
- 前端: `session_load_modal.js` / `session_restore_modal.js` 皆未读 — **完整性校验结果用户不可见**

**影响**: 用户 load 一个被篡改 (或传输损坏) 的存档时, 后端 warn + Diagnostics 有 `op=integrity_check` 条目, 但 **load modal 本身没提示任何异常**, 用户以为载入正常, 实际 memory 可能已损坏.

**P24 动作 (Day 3 并入 HTTPException shape 迁移)**: session_load_modal / session_restore_modal 的 success 响应解析 `memory_hash_verify`, 不匹配时弹 warning toast "⚠ 完整性校验未通过, 详见 Diagnostics → Errors". **0.5h workload**.

### 14A.3 SSE event 类型散落无枚举 ⚠ (§14.3.A)

**sweep**: `rg "yield \{\s*['\"]event['\"]:" -g "pipeline/**"` 命中 3 文件:

| 文件 | 发的 event 字面量 |
|---|---|
| `chat_runner.py` | `user` / `wire_built` / `assistant_start` / `delta` / `usage` / `assistant` / `done` / `error` |
| `auto_dialog.py` | `paused` / `resumed` / `error` |
| `script_runner.py` | `script_exhausted` (+ 其它未完整扫) |
| `judge_router.py` (SSE 端点内联) | `result` / `done` / `error` (P16) |

**无集中枚举**. 前端分发器散落 `composer.js` / `auto_banner.js` / `page_run.js` / `sse_client.js` 各自 `switch(ev.event)`.

**P24 动作 (Day 3, §4.1.5 DiagnosticsOp 同期做)**: 新建 `pipeline/sse_events.py::SseEvent` StrEnum 集中 12 个 event 类型 + 各 generator import 替字面量. 前端 `static/core/sse_events.js` 对应枚举. **1.5h workload**.

### 14A.4 `_PATCHED_ATTRS` 14 字段 vs 外部列表同步 ⚠ (§14.3.E)

**实证**: `sandbox.py:L34-49` 列出 14 个字段:

```
docs_dir / app_docs_dir / config_dir / memory_dir / plugins_dir
/ live2d_dir / vrm_dir / vrm_animation_dir / mmd_dir / mmd_animation_dir
/ workshop_dir / chara_dir / project_config_dir / project_memory_dir
```

**注释声明**: "Mirrors the list in `tests.conftest.clean_user_data_dir` plus a few modern additions (`plugins_dir / mmd_dir / mmd_animation_dir`)". 即**两份权威列表需同步**:

1. `sandbox.py::_PATCHED_ATTRS`
2. `tests/conftest.py::clean_user_data_dir` 的清理列表

P24 动作 (Day 1 主程序同步时一并做):

- grep 读 `tests/conftest.py` 的 `clean_user_data_dir` 清单, 对比上述 14 字段是否同步
- 读主程序 `config/config_manager.py` 当前所有 `*_dir / *_path` 属性, 对比两份列表是否涵盖全部路径类字段
- 新增源码级 smoke `p24_sandbox_attrs_sync_smoke.py`: inspect `ConfigManager` 实例所有 `*_dir / *_path` 属性都在 `_PATCHED_ATTRS` 里 (或显式白名单例外). **1h workload**.

### 14A.5 前端 Store 根字段消费覆盖 (§14.3.C) ✅ 合规

- `session / active_workspace / errors` 三根字段消费充分 (§12.1 matrix 已覆盖)
- `ui_prefs` 根字段**不走 `on('ui_prefs:change')` 订阅** (§4.23 #78 协调者 force-remount 模式), 当前两个子键 `evaluation_results_filter` / `diagnostics_errors_filter` 消费正确
- **唯一风险**: docstring 没明示 "禁止订阅 ui_prefs:change", 未来 agent 可能误加订阅重蹈 #78 覆辙

**P24 动作 (Day 1 清 TODO 时顺手)**: 在 `state.js::_state.ui_prefs` 注释和 `.cursor/rules/emit-grep-listener.mdc` 里**明文禁止**订阅 `ui_prefs:change`, 必须走协调者 remount. **10min**.

### 14A.6 本节新增 workload 汇总

| # | 项 | workload | 并入 Day | 状态 |
|---|---|---|---|---|
| 14A.2 | `memory_hash_verify` 前端消费 | 0.5h | Day 3 | ✅ done |
| 14A.3 | SSE event StrEnum 集中 | 1.5h | Day 3 | ✅ done (19 events, not 12) |
| 14A.4 | `_PATCHED_ATTRS` 同步核查 + smoke | 1h | Day 1 (核查) + Day 10 (smoke) | ⚠ Day 1 核查✅; Day 10 smoke 待 |
| 14A.1 | session 字段 snapshot 过滤 smoke | 1h | Day 10 | ⏸ 待 Day 10 |
| 14A.5 | ui_prefs 禁订阅注释 + rule | 10min | Day 1 | ✅ done |
| **合计** | | **~4h** | — | 3/5 done, 2/5 留 Day 10 |

**P24 总 workload 最终**: 15-19 天 (第四轮) + §14A 新增 **~4h ≈ 0.5d** = **15.5-19.5 天** ≈ **16-20 天** (保守). 差异在联调期实际 bug 数量和主程序 diff 规模.

### 14A.7 元教训 (第五轮补充, 最后一条)

**13. "方法论扩展应用面立即实证 > 推给未来 agent"** — 本节把 §14.3 宣称的 5 大扩展应用实际各扫一次, 半小时内抓出 2 条漏洞 (14A.2 / 14A.3) + 1 处合规边缘 (14A.5). 对比 §14.3 那种"列 5 大方向但不实证"的做法, 实证 ROI 高一个数量级. **归纳**: 任何"方法论下一步可以用来审查 X / Y / Z"的声明, **在同一轮审查内立即选最高怀疑项实证 2-3 个**, 不要推给未来 agent; 方法论扩展面**不在"记录清单"而在"立即跑"时产生价值**.

**§14A 交付状态 (Day 11 回填)**:

- **§14A.1 Session 字段 × 4 出口矩阵** — ✅ Day 10 实证落地成 `smoke/p24_session_fields_audit_smoke.py` (5 check), ledger 固定在 `9 persist / 11 runtime`, 未来加字段 smoke 自动守护
- **§14A.2 `memory_hash_verify` 前后端字段同步** — ✅ Day 5 修: 前端 `session_load_modal.js` 多语言 toast 键配齐, 后端 `persistence.lint_archive_json` 返 `verify_result` 字段全匹配
- **§14A.3 SSE event 类型散落无枚举** — ✅ Day 5 交付 `pipeline/sse_events.py` + `static/core/sse_events.js` 同名 const 枚举表, 5 frame type (message / delta / error / done / state)
- **§14A.4 `_PATCHED_ATTRS` 14 → 15 字段 vs `ConfigManager`** — ✅ Day 10 实证落地成 `smoke/p24_sandbox_attrs_sync_smoke.py`, 14 direct + 16 @property 全分类 (含 11 cloudsave @property 走 `app_docs_dir` 动态, 不需单独 patch)
- **§14A.5 前端 Store 根字段消费覆盖** — 合规, 无需改
- **§14A.6 workload 汇总** — 4h, 已并入 Day 10 总帐
- **§14A.7 元教训 L13** — ✅ 入 `LESSONS_LEARNED §7.13`

**§14A 五大扩展应用面是 P24 最重要的方法论产出**, 未来新增整合期 / 加固期时可以复用同一张"**方法论 X → 5 类代码面 × 每类实证 2-3 个**"的扫描表. 在 P25 开工前的设计回顾里, 建议把这张表再跑一遍新变更代码面, 看 P25 新增的 `pipeline/external_events.py` / `routers/external_event_router.py` / `external_events_panel.js` 有没有同族问题.

---

## 15. P24 任务清单总览 (§14.7.A 落地, Day 1-12 连续编号)

> 本章整合 §4 / §12 / §13 / §14 的所有 P24 必做项, 用连续 Day 编号
> 取代 §8 原 Day 0 / Day 5.5 / Day 7.5 的插入式节奏. 每项双向链回原章.

### Day 1 · 文档层 + 低风险 warmup ✅ done (2026-04-21)

- [x] [§12.6] 写 `tests/run_testbench.ps1` + bash 版 + `tests/run_testbench.cmd` (PS ExecutionPolicy wrapper) + 用户级 Set-ExecutionPolicy RemoteSigned
- [x] [§12.3.B] 顶栏 Menu Reset **删** / About **hide**
- [x] [§12.3.C] UI 文本 TODO / PXX 字眼全仓清 (i18n `theme_light_todo` / `snapshot_limit_hint` / `fold_defaults_hint` / `page_ui.js` 注释)
- [x] [§2.3] 三份老 docs G 编号澄清 (PLAN §11 + AGENT_NOTES §4.27 #101 + PROGRESS P22.1 各加一段 H1/H2/H3 改名说明)
- [x] [§13.2] `autosave._clear_all_autosaves` 实测**已合规** (L760-778 `tar 先 JSON 后`, 注释也明文 "Delete tar first (F6), then JSON")
- [x] [§12.3.D] 主程序 git log 盘点: 4 条关键 commits (cloud save manager / QwenPaw+Grok+Doubao / sync→async 重构) **归 Day 8-9 联调期消化**
- [x] [§13.8] 5 条 `.cursor/rules/*.mdc` + `smoke/p24_lint_drift_smoke.py` (Rule 5 精确命中 §12.1 预测 6 处违规, 作为 Day 6 修复红→绿指示器)
- [x] [§14A.4] `_PATCHED_ATTRS` vs `tests/conftest.py::clean_user_data_dir` 实测: sandbox 14 字段 = ConfigManager 14 字段 ✅ 全对齐; conftest 仅覆盖 11 字段 (plus plugins_dir/mmd_dir/mmd_animation_dir 漂移) — 非 testbench scope, 归主程序 tech debt, sandbox.py 加注释说明
- [x] [§14A.5] `state.js::_state.ui_prefs` 注释 + `emit-grep-listener.mdc` 明文禁订阅 `ui_prefs:change` (§4.23 #78 协调者模式)

### Day 2 · 后端 choke-point 抽象 ✅ done (2026-04-21)

- [x] [§4.1.2] `pipeline/atomic_io.py` 新模块 (3 函数: bytes/json/gzip_json) + **6 处副本迁移** (persistence alias / memory_router alias / memory_runner alias / script_runner delegate / scoring_schema delegate / snapshot_store 两处包括补修 update_label) + P21.1 fsync `inspect.getsource` 断言通过新 alias
- [x] [§4.1.5] `pipeline/diagnostics_ops.py::DiagnosticsOp` StrEnum (5 条: INTEGRITY_CHECK / JUDGE_EXTRA_CONTEXT_OVERRIDE / TIMESTAMP_COERCED / INSECURE_HOST_BINDING / ORPHAN_SANDBOX_DETECTED) + `OP_CATALOG` 全中文 description + `GET /api/diagnostics/ops` 端点 + 2 处 record_internal 调用点替 + import-time 自检 enum/catalog 同步
- [x] [§12.5] `pipeline/messages_writer.py::append_message` choke-point 完整实装: `AppendResult(msg, coerced)` 返值 + 3 档 `on_violation` (raise / coerce / warn) + 5 处裸 append 迁移 (chat_runner user/assistant/inject_system + auto_dialog user + chat_router manual) + SSE warning frame yield (chat_runner 手动/script 双路径) + composer 前端 toast "消息时间已自动前移..." + 中文 diagnostics message
- [x] **Day 2 UX 坑补** (user-reported, Day 2 后续): `AppendResult` 设计 + SSE warning frame + composer toast **让 coerce 不再是 silent fallback**; LESSONS_LEARNED §7 #14 归档 "coerce 必配 user-visible surfacing"
- [x] **Day 2 真 bug 附带补** (user-reported): `load / autosave restore` 保留原 `session_id` (store.create 加 `session_id=` 可选参数), 根治"保留份数 3 但列表显示 6 条" 现象; LESSONS_LEARNED §7 #15 归档 "Restore 操作必须保留原主键 ID 不自作主张生成新 ID"

### Day 3 · 后端 API shape + 安全 + 字段同步 ✅ done (2026-04-21)

- [~] [§4.1.1] HTTPException shape 统一 → **归 backlog** (sweep 发现前端 `api.js:37-51` parser 已兼容三种 shape 实质不是 bug 只是 code hygiene; 推 P25 或独立 pass)
- [x] [§4.3 H] `pipeline/redact.py::redact_secrets` helper (13 个敏感 key 清单, case-insensitive 递归 walk) + `diagnostics_store.record_internal` **自动** redact detail (防御性防线, 现存 5 处 call site audit 无泄漏但为未来兜底) + snapshot cold spill 保留 api_key 的设计决策文档化
- [x] [§13.3] `pipeline/request_helpers.py::coerce_bool/float/int` 提公共模块 + judge_router alias 迁移 + **其它 router sweep**: config_router/persona_router 均用 Pydantic `model_validate(payload)` 无裸 bool 读字段, 无需迁移; memory_router 全 BaseModel 签名; stage_router 全 BaseModel; **sweep 结论: 现状合规, helper 为未来 raw dict body 准备**
- [x] [§13.7] `simulated_user._postprocess_draft` 新 docstring (§3A G1 边界明文: "LLM output hygiene, not user-input filtering"; 已知限制: ChatML-style 攻击 payload 测试场景) + `preserve_role_prefix: bool = False` opt-in 参数
- [x] [§4.3 I] uvicorn 非 loopback host 启动时 `record_internal(DiagnosticsOp.INSECURE_HOST_BINDING)` (run_testbench.py L83-102)
- [x] [§14A.2] `memory_hash_verify` 前端消费 ✅ **P22.1 交付缺口实锤修复** — session_load_modal / session_restore_modal 解析 `res.data.memory_hash_verify.match`, 不匹配 `toast.warn` + i18n 中文说明 "⚠ memory 完整性校验未通过" + 指向 Diagnostics → Errors
- [x] [§14A.3] `pipeline/sse_events.py::SseEvent` StrEnum 集中 **19 个** event 类型 (chat_runner 9 + auto_dialog 8 + script_runner 3 复用 error, 更正 §14A.3 原估的 "12" 偏少) + 前端 `static/core/sse_events.js::SSE_EVENT` 镜像 + `ALL_EVENTS` set 供未来 Rule 6 lint 守护 (不强迫调用点全量迁移, 保留 yield literal 直观性)
- [x] **Day 3 Hotfix** (user-reported mid-day): Errors 页 `timestamp_coerced` / 其它 5 条 op description 全中文化 + messages_writer record_internal message 全中文
- [x] **Day 3 UI 说明错位修正**: `restore_modal.time_hint` 撤回, 改挪到 `page_snapshots.time_legend` (snapshot 才是用户要的 UI), 用户意图是 Diagnostics → Snapshots 子页说明 "主字段=系统真实时间 / @小字段=虚拟时钟 cursor"

### Day 4 · 延期加固前半 + H1/H2 ✅ done (2026-04-21)

- [x] [PLAN §15.2 A] `pipeline/boot_self_check.py::scan_orphan_sandboxes` + `delete_orphan_sandbox` + 三档 OrphanSandboxError (OrphanNotFound/OrphanIsActive/OrphanPathTraversal) + `health_router` 端点 `GET /api/system/orphans` + `DELETE /api/system/orphans/{session_id}` (scope 只扫不删, 符合 §3A F3)
- [x] [PLAN §15.2 B] Diagnostics Paths 孤儿区 UI: `renderOrphansSection` + `renderOrphanRow` + `confirmDeleteOrphan` (native confirm modal), 显示 count/total_bytes summary + session_id/size/mtime 表格 + 单条 [删除] 按钮 + 删除后自动 re-fetch + partial-delete 检测 (Windows 文件锁场景提示重启重试)
- [x] [§3.1] H1 `GET /api/system/health` 最小版聚合 5 个 check (disk_free_gb / log_dir_size_mb / orphan_sandboxes / autosave_scheduler / diagnostics_errors) + `_health_status_for` helper 三档 (healthy/warning/critical, reverse 支持) + worst-wins 汇总 + Paths 子页顶部 `renderHealthCard` 卡片 (含 status badge + check grid + checked_at)
- [x] [§3.2] H2 `persistence.lint_archive_json` + `GET /api/session/archives/{name}/lint` + Load modal 每行 [体检] 按钮 (`doLint` 逻辑: errCount=0 & warnCount=0 → ok toast; errCount>0 → warn toast; warnCount only → info toast; 消息体列前 N 条 `[ERR]/[WARN] path: message`) + i18n 7 条 `lint_*` 文案

### Day 5 · 延期加固后半 + 时间系统 (1d) — ✅ 已交付 (2026-04-21)

- [x] [PLAN §15.2 C] F6 judger `match_main_chat`: 后端 `_JudgeRunRequest.match_main_chat` + `_extract_persona_meta(session, match_main_chat=True)` 委托给 `prompt_builder.build_prompt_bundle` (含 PreviewNotReady / 通用 Exception 二层降级到 legacy stored-prompt 路径) + 前端 `page_run.js` 新增 `renderMatchMainChatOption` (checkbox + localStorage `testbench:eval:match_main_chat` 持久 + 中文 hint 说明用途)
- [x] [PLAN §15.2 D] F7 Diagnostics Security Option B: `diagnostics_store.list_errors(op_type=)` 精确 exact-match 多选过滤 (逗号分隔) + `/api/diagnostics/errors?op_type=` query 参数 + `page_errors.js::renderSecurityFilters` 4 chip 行 (integrity_check / judge_extra_context_override / timestamp_coerced / all three) + `renderFilterChips` 联动显示 `op_type:` 徽章 + `.diag-security-filter-row` CSS + i18n 4 × (label + hint) 中文
- [x] [§12.5 L2] time_router mutation pre-action warning: `_last_message_timestamp(session)` + `_warning_for_new_cursor(session, new_cursor)` helper (naive 时间归一化比较) + `PUT /cursor` / `POST /advance` 双入口同时附 `{ warning: { code, last_message_at, new_cursor_at, gap_seconds, message_cn } }` + 前端 `page_virtual_clock.js::mutate()` 消费 `warning.message_cn` 调 `toast.warn(..., {duration:8000})`, 不阻塞 mutation (Day 2 coerce + Day 5 pre-warn 两层防线)
- [x] [§12.5 L3] 下游 `max(0)` 兜底: `session_export.py::_build_dialog_template` 的 `delta = max(0, ...)` (防 Day 2 前存档或手改 messages 导出时产生 `-120s` 被 script_runner 拒) + `prompt_builder.py::time_context` 的 `gap_seconds = max(0.0, ...)` (防虚拟时钟回退误触发 "好久没见" 长时静默 prompt)
- [x] [§12.3.E #15] Restore banner + model_config_reminder 点击后自动关横幅: `session_restore_banner.js::openBtn.onClick` 追加 `dismissed=true` + 清 hostEl + `model_config_reminder.js::gotoBtn.onClick` 追加写 DISMISS_SS boot marker + 清 hostEl (等价于显式 ×, 但点"下一步动作"的体感就是"我已经看到了别烦我")
- [x] [§12.3.E #14] 人设导入 ✓/✗ tooltip: `page_import.js::renderRow` 给 4 个 badge (当前 / prompt ✓ / prompt ✗ / 无 memory 目录) 附 `title` + i18n 新增 4 条 `*_hint` key 中文解释 (特别 `badge_no_prompt_hint` 解释 "导入后人设 prompt 会是空的, 需要自己在 Setup → Persona 填写")
- [x] [§12.3.E #13] Setup 三页加 [打开文件夹]: 抽共享 helper `static/ui/_open_folder_btn.js::openFolderButton(pathKey, opts)` 包装 `POST /api/system/open_path` + 三页接线 (Setup → Persona: `current_sandbox`; Setup → Scripts: `user_dialog_templates`; Evaluation → Schemas: `user_schemas`) + i18n `common.open_folder_btn` + 三条 `common.open_folder_hint.*` 精确中文. Memory 4 子页未挂 (memory 在 current_sandbox 下与 Persona 同路径, 避免重复按钮噪音)
- [x] [§12.3.E #17] Stage Coach dry-run 按钮重议: non-memory op 的 "disabled Preview 黑按钮" 改为**完全不渲染** (inline `chipSlot` + panel `btnRow` 两处同步; 条件从 `disabled: !op.dry_run_available` → `if (op.dry_run_available) btnRow.append(...)`). 非 memory op 的 `op.ui_action` 已经生成 [跳转到 XX] 按钮承担 "下一步去哪" 引导, 清理 Preview 按钮消除用户困惑
- [x] 回归 smoke: linter 全绿 (20 个改动文件 0 errors) + `p21_persistence` / `p21.1_reliability` / `p21.3_prompt_injection` / `p22_hardening` / `p23_exports` 5 条历史 smoke 全 OK + `p24_lint_drift` Rule 1 / 3 / 4 绿, Rule 2 14 warn (历史遗留 soft, 非 Day 5 引入), Rule 5 6 violations 符合 §12.1 预期作为 Day 6 修复指示器 (**未引入新 drift**)

### Day 6 · 前端机制守卫 (1.5d) — 全交付 ✅ (6B/6C/6F/6G 当天落地 + 6A/6D Day 12 欠账清返 + 6E Day 8 M1 合并审通过)

- [x] [§3.5] `static/core/render_drift_detector.js` 主体 + 每页 checker 注册 + `?dev=1` gating — **Day 12 欠账清返 (2026-04-22) 已交付**. 骨架 176 行 (`registerChecker({name, event, check}) / unregisterChecker(name) / initRenderDriftDetector()`), dev-mode gate 复用 `_isDevMode()` 同款 (`?dev=1` 或 `window.__DEBUG_RENDER_DRIFT__=true`), per-(event, name) driftKey dedupe 避免重复 warn. 接入 `app.js` boot 后注册 2 个全局 checker (`topbar.session_chip_label` / `app.active_workspace_section`) + `page_snapshots.js` mount 时注册页内 checker (`page_snapshots.row_count` 检查 `.snapshots-row` DOM count vs `state.items.length`). `window.__renderDrift.listCheckers()/runNow()/getLog()` 三件套调试 API. **规模/覆盖权衡**: 原蓝图估 1.5-2 天的"15 页全量 checker" 不做 — 主体 API + 3 checker 已足够证明机制可工作 (当"state 改了但 DOM 没跟" 漂移出现, checker 会 warn); 其它页若后续 P25+ 踩到同族 bug, 增量加 checker 10min/页. 生产模式零开销 (`initRenderDriftDetector()` 顶 return). 验收: `p24_lint_drift` + 全量 smoke 9/9 绿, 无 regression.
- [x] [§12.1] 事件总线 6 处违规修 (删 dead emit × 5 + 补 dead emitter × 1) + dev-only `DEBUG_UNLISTENED` 自检 — **Day 6B 已交付** (2026-04-21). 静态违规: (a) timeline_chip/page_snapshots 各 2 处 `messages:needs_refresh` + `memory:needs_refresh` dead emit 共 4 处删; (b) stage_chip `stage:change` 1 处删 (推测为未来 listener 预留但一直没接线); (c) session_export_modal `session:exported` 1 处删 (P23 新增直接违反 B12, UX 已由 toast.ok 承担, 未来若做"最近导出历史"再重建并同步 listener); (d) session_load_modal `session:archive_deleted` 1 处删 (modal 内 refresh 已覆盖, 独立事件冗余); (e) composer `clock:change` listener 补 emitter — `page_virtual_clock.js::mutate` + composer 内 `stageDelta/clearStage` + 2 处 `api.put('/api/time/bootstrap', ...)` + 1 处 `api.request('/api/time/stage_next_turn', DELETE)` 共 5 处成功路径都补 emit. 每处删除点加注释指向本次 audit + 未来重建条件. 动态兜底: `state.js::emit` 改 `if (!set || set.size === 0)` 涵盖"曾有 listener 全退订"场景 + 加 `_isDevMode()` (`?dev=1` 或 `window.__DEBUG_EVENT_BUS__=true`) + `_deadEmitWarned` Set 每 event 只 warn 一次. **p24_lint_drift Rule 5 从 6 violations 清零** → 事件总线 matrix 静态口径健康.
- [x] [§13.4] C3 `Node.append(null)` sweep + 返 DocumentFragment 改造 5-10 处 — **Day 6C 已交付** (2026-04-21). 实测 sweep: (a) `ui/_dom.js::el()` 自身已 `if (c == null || c === false) continue` 过滤 null/false/undefined 所以凡走 el() 的 children 全 safe; (b) 各 module 本地 el helper (session_export_modal / session_load_modal / session_save_modal / topbar_stage_chip / topbar_timeline_chip / page_aggregate svg) 均有同款 null filter; (c) 6 处 render helper `return null` 模式 (page_errors renderFilterChips/renderPager / page_logs renderFacets/renderPager / page_scripts renderEditorErrors / page_schemas renderEditorErrors) **caller 全部已有 `if (chips) parent.append(chips)` guard** — 早年 §4.26 #87 Hard Reset 后建立的 "render null 必 guard caller" 纪律真正落地. 结论: **实际代码 0 处违规, 蓝图预估的 5-10 处改造需求实际 = 0 处**. 仅做前瞻防御: `_dom.js` 新加 `safeAppend(parent, ...children)` helper (同 el() 的 filter 语义) 供未来需要裸 `append` 且带 conditional 的场景使用. Lesson: "早期纪律落地评估不能靠 grep, 要抽查 caller" — 多一轮真实 caller 审计比蓝图预估更重要.
- [x] [§13.5] D1 `page_persona.js:184` Promise cache 重构 + 其它 lazy init sweep — **Day 12 欠账清返 (2026-04-22) 已交付**. (a) `page_persona.js::renderPreviewCard` 内部 `let loaded = false` → `let loadPromise = null` 的 Promise cache 模式 (skill `async-lazy-init-promise-cache` 规则 2): `details.toggle` 和 `refreshBtn.click` 两条入口都走 `doLoad()`; refresh 显式 `loadPromise = null` 强制重拉; 失败 `.catch` 清空 Promise 让下次 caller 能 retry (规则 3). (b) **全仓 lazy init sweep 结论**: `composer.js::ensureStylesLoaded / ensureAutoStylesLoaded` 两处原本已是 Promise cache 但**缺 `.catch` 清空兜底** (规则 3 漏守), 本次补齐 + `loadTemplateList(force)` 原本是 "flag-after-await" 模式 (force=false 时有 race) 升级为 Promise cache `templateListPromise` + `scripts:templates_changed` 外部 invalidate handler 同步清 Promise cache. 其它 `loadErrors / loadLogs / loadPaths / loadResults / loadAggregate` 5 处走 Day 6G 的 `AbortController + makeCancellableGet` 模式 (last-click-wins 语义), **与 Promise cache 互补不替代**, 不适用本 skill.
- [x] [§13.6] F4 asyncio Task/Event cancel 等待语义深审 + 补强 2-3 处 — **Day 6E 已交付 (2026-04-22)**, Day 6 原始 checkbox 漏回填. 实际与 Day 8 `§14.4 M1` 取消/停止语义幂等性静态审合并审查通过 (见 Day 8 的第 4 条 `[x] §13.6 F4 = 6E`), **零代码改动, 4 个流派 (auto_dialog Event / chat SSE / judge atomic commit / script BUSY 锁) 都正确处理 CancelledError 传播**, 属静态审合规. 本条仅回填 checkbox.
- [x] [§14.4 M2] DOM 级常驻 listener teardown 清单 — **Day 6F 已交付** (2026-04-21). 全仓 sweep 7 处 `document.addEventListener` + 2 处 `window.addEventListener`: (a) 5 个 document + 2 window listener 全部在 `mountXxx` 唯一入口 / `{once:true}` / `DOMContentLoaded`, app.js boot 只调一次 → SPA 下无 leak; (b) subpage 层 `state.js::on()` 6 个 `page_*` 的 `host.__offXxx` + 开头 teardown pattern 验证: **page_results/page_run/page_errors/page_aggregate** ✓ 已正确; **page_snapshots** ❌ 注释声称"粗粒度 remount 不泄漏"是**错的** — innerHTML='' 只清 DOM, state.js listeners Map 里 fn 引用保留, 每次切到 Snapshots 子页叠一层 `snapshots:changed` + `session:change` 两个 listener, 触发一次事件跑 N 倍工作量. **已修**: 补 `host.__offSnapshotsChanged` / `host.__offSession` + 开头 for-k teardown loop (完全对齐 page_results 模式). (c) chat workspace 的 composer/message_stream/preview_panel 均有 destroy() API 且 `workspace_chat` 正确链式调用. Lesson (→ LESSONS #18): "纯靠 innerHTML='' 不能清 state.js listener, 任何 subpage `on()` 都必须配 `host.__offXxx` + 开头 teardown".
- [x] [§14.4 M3] `api.getCancellable` helper + 10 处快速刷新 caller 迁移 — **Day 6G 已交付** (2026-04-21). `core/api.js` 扩展: (a) `request()` 加 `signal` 参数透传到 fetch init; (b) catch 分支识别 `AbortError` 返 `{ok:false, status:0, error:{type:'aborted', message:'aborted'}}` 不弹 toast / 不广播 http:error; (c) `api.get/post/put/patch/delete/request` 全部透传 signal; (d) 新 helper `makeCancellableGet(url, baseOpts?)` 返回可重入 callable, 每次调用先 abort 上一个 controller (用于 url 固定的 toolbar refresh). Caller 迁移 6 处 (url 含 qs 变化的场景用 per-page `let _xxxController = null` + 开头 abort + 尾 `if (error?.type==='aborted') return`): `page_errors::loadErrors` / `page_logs::loadLogs` / `page_snapshots::loadSnapshots` / `page_paths::loadPaths` (3 并行 trio 同一 controller) / `page_results::loadResults` / `page_aggregate::loadAggregate`. 注意**不给 mutations (POST/PUT/DELETE) 用**: 中途 abort 会让服务端状态模糊 (commit 还是没 commit?). Lesson (→ LESSONS #19): "last-click-wins vs last-response-wins 的区别直接影响用户感知, refresh/filter 类必用前者".

### Day 7 · 前端 UI 偏好 + 收尾 (0.5d) — 交付完成 (2026-04-22)

- [x] [§12.3.A #3] Settings Snapshot limit 接线 — **Day 7 A 已交付**: `snapshot_store.update_config(max_hot, debounce_seconds)` 新加 runtime config 更新方法 (≥1 ≤500 / ≥0 ≤3600 range guard + 缩小 max_hot 触发 `_enforce_hot_cap` 立即 spill), 新端点 `POST /api/snapshots/config` (session-scoped, 400 InvalidSnapshotConfig / 404 NoActiveSession), 前端 `page_ui.js::renderSnapshotConfigCard` 去 disabled + 双 input + 保存按钮 + 无 session 时提示. 同时把 `debounce_seconds` 也做成可调 (原蓝图只提 max_hot, 但 debounce 同样是 §12.5 快照链路的关键参数, 顺手做).
- [x] [§12.3.A #4] 默认折叠策略表接线 + LS 持久化 — **Day 7 B 已交付**: `page_ui.js::renderFoldDefaultsCard` 5 行折叠策略表 (chat_message / log_entry / error_entry / preview_panel / eval_drawer), 每行 3 档 radio (auto/open/closed) + 阈值 input + 根据 mode 动态 opacity. 持久化到 `localStorage['testbench:fold_defaults']` + 同步写 `store.ui_prefs.fold_defaults`. 消费端**不订阅 `ui_prefs:change`** (遵守 LESSONS #20 + §4.23 #78), 下次 CollapsibleBlock mount 时读一次静态值. 注意实际消费代码 (要在渲染 CollapsibleBlock 的每处读该 pref) 推 Day 10 polish, 当前只做 Settings 面板本身的接线 + LS 持久化.
- [x] [§12.3.F] auto_dialog 前端多 error 展示折叠面板 — **Day 7 C 已交付**: 后端 `AutoDialogError` 新字段 `errors: list[str] | None` 保留完整校验错误列表 (老 `message` 字段仍是合并字符串不破坏兼容), `from_request` 批量校验时塞入, `_auto_error_to_http` detail 多挂 `errors` 字段. 前端 `sse_client.js::streamPostSse` onError 扩展: 4xx 响应尝试 JSON.parse 解构 `{detail}`, `info.detail` 暴露给 caller. `auto_banner.js::onError` 判断 `detail.errors.length > 1` 时走 `showErrorPanel(errors, headerMessage)`: banner 原地替换成错误详情面板 (红色主题 + 独立 CSS `.auto-banner--error` + li 每条错误独立视觉 bullet), 关闭按钮点击 dismissErrorPanel 恢复 banner + finish. 单条错误仍走原 toast 路径保持简洁.
- [x] **同族 sweep** (2026-04-22, §4.27 #105 深度复盘): 全仓 `rg "set\(\s*['\"]session['\"]" tests/testbench/static/ui/` 找到 3 处漏网 surgical, 全改 reload:
  - `topbar_timeline_chip.js::doRewind` (rewind 到早期快照可能让 persona / memory 归空, 与 New Session cascade 同族)
  - `page_snapshots.js::handleRewind` (同上, Diagnostics → Snapshots 子页的版本)
  - `page_reset.js::doReset(level==='medium')` (LESSONS #20 量化判据第 5 条 "memory/ 被清空或替换" 命中, 从"Hard 走 reload, Soft/Medium surgical" 升级为 "Hard+Medium 都走 reload, 只 Soft 保留 surgical")
- [x] **UI 小 bug**: `_open_folder_btn.js` [打开文件夹] 按钮从 `ghost tiny` (transparent + 小字号, 隐身不显眼) 升级为专用 `.open-folder-btn` class (raised-panel 背景 + 前置 folder SVG 图标 + accent-on-hover), 用户 2026-04-22 反馈 "按钮太小太不显眼".
- [x] **回归 smoke 全绿**: p24_lint_drift Rule 1/3/4/5 全绿 + p21_persistence / p21_1_reliability / p21_3_prompt_injection / p22_hardening / p23_exports 5/5 历史 smoke 全绿, 零 regression.

### Day 8 · 端到端联调 (1.5d → 实际 ≈ 2.5d) — 静态审 + 诊断增强 + 4 轮手测反馈全部交付并验收通过 (2026-04-22 完结)

- [x] [§1.1 #1] 真实模型闭环 10+ 场景跑完 — **用户反馈 Day 8 回填**: S1-S12 在 Day 1-8 迭代过程中已由用户实际手测覆盖, 每轮反馈出的 bug 当场修掉没遗留 happy path. 具体修复记录见 `p24_integration_report §4.1`.
- [x] [`p24_integration_report §2`] 资源观测 — **用户反馈**: 48 轮对话 ≈ 580KB autosave (略超估计但可接受); 注入真阳性率在 `#107 Part 3` 修完后正常命中; log retention / diagnostics ring 属**时间累积验证**类本阶段无法复现, 关键代码路径已 smoke 覆盖 (`p21_*_smoke` / `p22_hardening_smoke` / `p21_3_prompt_injection_smoke`), 留日常使用累积到门限再看, 不拉 backlog.
- [x] [§14.4 M1] 取消/停止语义幂等性测试 (chat stop / auto stop / judge abort / script) — **静态审通过, 零风险**: Auto-Dialog pause/resume/stop Event 原子操作幂等 + 409 Not Running 明确; Chat /send stream `except Exception` 不吞 `CancelledError` (Py 3.11+ 继承 BaseException) 中断时 stream_ok=False 跳过 snapshot 保持一致; Judge /run 是 atomic commit (跑完所有 item 再一次性 persist, 中断丢局部结果不持久化); Script runner 靠 SSE AbortController + session_operation BUSY 锁, context manager 自动 release. **结论**: 无代码改动, 仅静态审备案.
- [x] [§13.6 F4 = 6E] asyncio Task/Event cancel 深审 (Day 6 推迟项) — **与 M1 合并审查通过, 零风险**. 上述 4 个流派都正确处理 CancelledError 传播.
- [x] [§12.4 dev_note 清单] 逐项 UI 验收 (17 项) — **16 项已代码修复**, L17 默认角色 bug 通过 CFA 诊断 + 用户数据迁移已解决. 详见 §12.4 盘点表.
- [x] [§12.4.A] 默认角色数据显示 bug 后端 scan 调研 — Windows 配置路径分裂根因定位 + `cfa_fallback` 警告块实装. 详情见 §12.4 + AGENT_NOTES #107.
- [x] **#107 Part 1-4 (用户手测反馈 4 轮, 2026-04-22)**: 前端 UI + 可观测性问题收尾:
  - **Part 1** — 快照配置 `{0}/{1}` i18n bug, 保存按钮撑满容器, 非法值不重置: i18n fmt function 化 + `.form-row-actions` CSS + 自动重置 fallback 到 `SNAPSHOT_CONFIG_DEFAULTS`
  - **Part 2** — S6 跳过/回退语义 (tooltip), S7 `[object Object]` toast + PreviewNotReady 误报 + `errors` 列表扁平化, S10 markdown 中文化 + 导入 JSON 按钮规范化, S12 注入检测不命中 + 聊天滚动不工作 + modal 溢出 — **合并 §4.2.C api.js extractError 拍平 errors/busy_op 到 `res.error` 10 处 caller 同族受益** + `chat-layout.active` height 约束 + `.modal` base flex 结构
  - **Part 3** — Session Load modal "导入 JSON" 二按钮 v1 清错位置 / v2 hidden 切换 / 注入 toast 冗长 / RateLimitError 未入诊断: auto_dialog `finally` 集中 `record_internal(op='auto_dialog_error')` + 新增 `DiagnosticsOp.AUTO_DIALOG_ERROR` + toast 新 `_fmt` 简化文案
  - **Part 4** — "导入 JSON" 按钮 v3 真因发现 (**HTML `[hidden]` 被 `.modal-actions { display: flex }` CSS 静默覆盖**) + v4 DOM-level remove/re-append 落地. 新增 `.cursor/rules/hidden-attribute-vs-flex-css.mdc` 防四次踩点. 同族 sweep 全仓 `.hidden =` 2 处均无 CSS 冲突, 安全. **另发现并修复 toast.err 全仓 16 处 API 坑**: `show({..., message: firstArg, ...opts})` 让 `opts.message` 悄悄覆盖首参, RateLimitError 长首参时才暴雷; 改 `_dispatch` 让首参自动升格 `title`, 16 处 caller 零改动向后兼容.
- [x] **本 Day 8 净交付总结**: 4 轮手测反馈迭代消化 + 诊断可观测性 (auto_dialog_error / prompt_injection_suspected 两个新 op 入 catalog) + 架构修正 (api.js extractError 统一 / toast dispatcher 智能分派 / hidden 属性可靠性 rule) + 3 条新 LESSONS (#20 同族架构空白必 sweep, #21 HTML [hidden] vs CSS display-setting 静默失效, #22 opts 尾展开静默覆盖型 API 设计陷阱). **工作量**: 原估 1.5d, 实际 ≈ 2.5d (第 4 轮手测反馈迭代消耗 1d), 仍在 §14.8 workload 上限内. **零 regression**: p21_1/p21_persistence/p21_3/p22_hardening/p23_exports 历史 smoke + p24_lint_drift_smoke 全绿.

### Day 9 · Bug 修 + 主程序同步 (1d → 实际 ≈ 0.25d) — 五项盘点全零行动, 直接完结 (2026-04-22)

- [x] Day 8 暴露的 bug 消化 — **Day 8 过程中 4 轮手测反馈 #107 Part 1-4 全部当场消化**, Day 9 无堆积 bug 待处理.
- [x] [PLAN §15.4] 主程序同步 sandbox._apply / memory schema / utils llm_client 三面 — **全零行动**:
  - **`sandbox._PATCHED_ATTRS` vs `utils/config_manager.py`**: 主程序 `self.<xxx>_dir = path` 直接赋值属性仍是 **15 个** (507-547 行), 与 testbench 15 项白名单**完全对应**, 没有新增 / 删除 / 改名. 主程序新增 11 个 `@property def cloudsave_*_dir` 方法 (#681 云存档特性), 但 property 走 `return self.app_docs_dir / "..."` 动态计算, 而 `app_docs_dir` 本身在 `_PATCHED_ATTRS` 里已被 sandbox 重定向到沙盒, 所以云存档路径**天然跟着重定向**, 无需加白名单. testbench 代码 0 引用 `cloudsave_*`, 云存档路径本阶段未激活.
  - **memory schema**: 主程序 `memory/persona.py` / `memory/facts.py` / `memory/recent.py` / `memory/reflection.py` / `memory/timeindex.py` / `memory/settings.py` 近期改动全是 `176ded9 perf(async)` 把 sync 方法包装成 `async def asave_*` + #998eb44 persona 竞态修复 + #5170bcf Windows SQLite URI 反斜杠. **同步版本 (`save_*`) 保留**, testbench `prompt_builder.py` / `memory_runner.py` 全走同步公共 API, 没调任何私有方法, 向前兼容 100%. memory 模块自身无 `SCHEMA_VERSION` 字面量 (schema_version 是 testbench 在 `persistence.SessionArchive` / `scoring_schema.py` 的独立约定, 与 memory 模块正交).
  - **`utils/llm_client.py`**: 近期唯一改动就是 `ee5b04f` — 那是 **testbench 开发时** P08/P09 阶段我们自己加的 `temperature: float | None` 兼容 o1/o3/Claude extended-thinking. testbench `chat_runner.py` 构造 ChatOpenAI 时走默认 1.0, 未改. 零同步工作.
- [x] [§12.3.D #11] 道具交互同步 — **用户二轮澄清 testbench 定位后, 结论翻转为"内容层必纳入, 运行时不纳入, 单开 P25 新阶段交付"**:
  - **一轮结论 (已废弃)**: 读 PR #769 `4b504d4` 完整 diff + unit 测试合约 (`tests/unit/test_avatar_interaction_memory_contract.py`) 后初判"三重架构不兼容 (testbench 无 `prompt_ephemeral` 实时流 API / 无 `sync_connector_process` 多进程 queue / 无 `LLMSessionManager` contextvar race guard) + 数据层零污染 (testbench 沙盒无 avatar frontend 不触发事件), 归 explicit out-of-scope 非技术债".
  - **用户二轮澄清**: testbench 作为**"主程序新就位系统对对话/记忆影响的测试生态"**, 核心任务是回答 "**新系统 (avatar interaction / agent callback / proactive 等运行时注入式) 对模型生成的对话内容 + 短期记忆 recent / 长期记忆 facts + reflection 的影响稳健吗?**". 架构不兼容 (无实时流基础设施) **根本不是排除理由** — 实时投递机制与"影响评估" 测试目标**正交**. 应复现的是**语义契约**: prompt 注入 + memory note 写入 + dedupe/rank 策略, 这些都是可 pure 函数化的.
  - **PR #769 代码流影响面重评估**:
    - **Pure 可复用层** (testbench 必须接入): `config/prompts_avatar_interaction.py` 的 **9 个 helper + 7 个常量表** 全是 pure (`_normalize_avatar_interaction_payload` / `_build_avatar_interaction_instruction` / `_build_avatar_interaction_memory_note` / `_build_avatar_interaction_memory_meta` / `_avatar_interaction_locale` / `_sanitize_avatar_interaction_text_context` / `_normalize_avatar_interaction_intensity` / `_parse_avatar_interaction_bool` / `_get_avatar_interaction_payload_value`); 加上 `main_logic/cross_server.py::_should_persist_avatar_interaction_memory(cache, note, dedupe_key, rank)` (8000ms 窗口 + rank-upgrade 策略, 纯函数).
    - **运行时 scaffolding 层** (testbench 不需要): `handle_avatar_interaction()` async method / `_pending_turn_meta` / `_recent_avatar_interaction_ids` deque / `_proactive_expected_sid` contextvar / `sync_connector_process` 多进程 queue 的 turn-end-meta 分流 / `OmniOfflineClient.prompt_ephemeral` 实时流.
    - **关键数据模型** (cross_server.py 第 494-497 行): avatar memory note **作为 role=user** (不是 system) 入 memory, 且**必须配对** 一个 LLM 生成的 `assistant_reply` 才作为完整对话回合写 `/cache`. 这意味着 testbench 对接模式 = "外部事件触发 → 合成 instruction 注入 LLM → LLM 产出 assistant 反应 → (memory_note, assistant_reply) user/assistant 对写 session.messages" — 等价于一个 chat_turn, 只不过 user message 是系统合成的 memory_note 而非用户打字.
  - **同族系统盘点** (扩大范围): testbench 漏接入了主程序 7 个 `prompts_*.py` 中的 4 个, 其中 3 类都是"运行时 prompt 注入 + 写 memory":
    - **A. Avatar Interaction** (PR #769): `prompts_avatar_interaction.py`, 前端 avatar 点击触发, `[主人摸了摸你的头]` 系统 note + LLM 反应回复, 进 recent 参与 compress/facts/reflect.
    - **B. Agent Callback**: `prompts_sys.AGENT_CALLBACK_NOTIFICATION` + `main_logic.core::drain_agent_callbacks_for_llm`, 后台 agent 任务完成触发, `"======[系统通知：后台任务完成]"` instruction 注入让 LLM 主动提及, 回复进 recent.
    - **C. Proactive Chat**: `prompts_proactive.py` (2731 行), 定时 / 消息队列空闲触发, LLM 无用户输入自发产出回复或 `[PASS]`, 对长会话压缩/反思有系统性影响.
    - **D. Emotion 分析** (`prompts_emotion.py`): 仅用于情绪分析不直接影响 chat 流, 不紧急.
    - **同模式本质**: "外部触发 → 临时 prompt 注入 → LLM 产出回复或系统 note → 进 recent → 后续影响 compress/facts/reflect". 三类共享一个 ingest 端点即可.
  - **结论翻转**: §12.3.D #11 **从"已确认不纳入范围"改为"P25 新阶段 `外部事件注入` 主交付项"**. 核心复用: 主程序 `config.prompts_avatar_interaction` 全部 helper + `prompts_sys.AGENT_CALLBACK_NOTIFICATION` + `prompts_proactive.*` 常量和 getter, 加一层薄 adapter (`pipeline/external_events.py`) 封装三类 handler + 一个统一 router `POST /api/session/external-event`. 工作量估 2.5-3 天, 用户决策 **单开 P25 新阶段专门处理** (原 P25 README 顺延为 P26), 配套蓝图见 `P25_BLUEPRINT.md` (Day 10 收尾后启动). **所以 P24 Day 9-E 本项仍然 "零行动", 但"零行动"的含义从"永久排除"改为"本阶段不实施, 已立项 P25"**.
- [x] **静态回归**: `p21_1 / p21_persistence / p21_3 / p22_hardening / p23_exports / p24_lint_drift` 6 个 smoke 全绿 (Day 8 已跑过, Day 9 无代码变动所以无需再跑).

### Day 10 · 烟测 + 白名单守护 (1d) ✅ done (2026-04-22)

- [x] 新增 `smoke/p24_integration_smoke.py` 按 PLAN §15.6 — 含 a-d 四用例 + e `diagnostics_ring_full` warn-once 共 5 check 全绿
- [x] 新增 `smoke/p24_session_fields_audit_smoke.py` [§14.4 M5 + §14A.1]: Session dataclass × 4 出口覆盖矩阵 (describe / serialize_session / snapshot_store.capture / session_export) + runtime-only leak 断言; ledger `9 persist / 11 runtime`, 5 check 全绿
- [x] 新增 `smoke/p24_sandbox_attrs_sync_smoke.py` [§14A.4]: ConfigManager `*_dir / *_path` 14 direct + 16 @property 全分类, `_PATCHED_ATTRS` 白名单对齐, 5 check 全绿
- [x] 全量回归 `p21_* / p22_* / p23_* / p24_*` 共 **9 份 smoke 全绿** (2026-04-22 22:52-22:53 两轮跑过)
- [x] [§14.4 M4] 资源上限 UX 降级文档化 (§14.2.E 15 项总表) + diagnostics ring 满时 warn-once 机制实装 (新增 `DiagnosticsOp.DIAGNOSTICS_RING_FULL` + `diagnostics_store._RING_FULL_NOTICE_FIRED` 标志 + `_build_ring_full_notice` + `_push` / `clear` 改造 + smoke case e 验证)
- [x] [§14.2.D] A6 / A9 / SSE yield error 其它 generator 复核 — 三个组件按"请求-响应 async func / 真 generator / Template Method 非生成器" 三分类各自合规, 结论段入 §14.2.D.**A6 复核结论 (P24 Day 10)**

### Day 11 · 文档回写 (0.5d → 实际 ≈ 0.7d, 2026-04-22) ✅ done (2026-04-22)

- [x] [§9.1] PLAN §15 末尾加 "P24 已交付 (v1.0 基线), 详见 P24_BLUEPRINT" + §15.6 + PLAN 顶部进度快照 "23/26 → 24/26 阶段"
- [x] [§9.2] PROGRESS 阶段总览 P24 `in_progress` → `done` + changelog 补 Day 9 / Day 10 / Day 11-12 三条 + "中后期回顾与展望 §三" 依赖图 P24 done
- [x] [§9.3] AGENT_NOTES 顶部 banner 改 "**接手 P25 外部事件注入前必读**" (P25 已于 Day 9 立项, 蓝图 [P25_BLUEPRINT.md](P25_BLUEPRINT.md), 原 P25 README 顺延 P26) + §4.27 #108 扩写 Day 10-12 四子节 (Day 10 交付 / Day 11 文档回写 / Day 12 验收 / v1.0 sign-off)
- [x] [§3A] 正式追加新条 9 条 + A7 修订 — A7 重写 "单一 choke-point" 版本; 新增 A12 HTTPException dict shape / A13 schema_version 三件套 / A14 时间字段 canonical 命名 / A15 api_key 脱敏硬约束 / A16 pre-action 警告 / A17 messages append choke-point / A18 choke-point 原则必配静态核查 + B14 双向 emit/on 检查 + B15 占位控件不长驻 + E3 Pre-Sweep 作阶段默认动作
- [x] [§9.4] 本蓝图 §4 / §7 / §12 / §13 / §14 / §14A 六大章节末尾填"交付状态"徽章 + §11 "P24 交付后回顾" 占位写实
- [x] LESSONS_LEARNED §7 归纳候选落地 — L26 "yield 型 API 三分类" + L27 "资源上限 UX 横切维度" 两条从 §14.2.D / §14.2.E 升格为正式元教训

### Day 12 · 验收 buffer + v1.0 sign-off + 欠账清返 (2026-04-22) ✅ done

- [x] 总体走查 · `p24_integration_report` 终稿 (§5 代码审查结论 / §6 主程序同步清单 / §7 入档 backlog 三段骨架落实)
- [x] v1.0 "第一个完善版本" sign-off — 本蓝图 §11 "P24 交付后回顾" 写实 + PROGRESS / PLAN 两处同步 milestone; P21-P24 代码流一次性 commit/merge 到云端 **done** (commit `4964941` feat(testbench) P21-P24 + merge commit `cb394ab`, 对齐 `8f8dc63` / `14c98c8` 跨 phase commit 前例, push 成功 `474aa23..cb394ab main -> main`)
- [x] 收尾后再跑全量 smoke 9 份确认文档写作过程未摧代码断言 (2026-04-22 Day 12 最终轮)
- [x] **P24 欠账清返** (2026-04-22 Day 12 补扫, 用户 "不要留尾巴" 明确要求后): 全仓 grep `推迟|留待|归 P\d|TODO|FIXME|XXX` 扫出**真欠账 2 条** (render_drift + persona cache) + Day 6 checkbox 漏回填 1 条 (§13.6 F4 = 6E), 其它均明确归档 backlog 或 P26+, 第 3/4 条无漏网. 两条真欠账都**当场做完**: (a) `§3.5 render_drift_detector.js` 骨架 + 3 checker 接入 (见 Day 6 对应 `[x]` 详情); (b) `§13.5 page_persona.js Promise cache` 重构 + composer.js 3 处 `.catch` 补齐 (见 Day 6 对应 `[x]` 详情). 回归全量 9/9 smoke + `p24_lint_drift` 绿, 零 regression. **Lesson**: 工程实践上, 任何跨阶段 "推迟到 Day X" 的 checkbox 必须在 Day X 结束前做**双向回扫** (源 checkbox 是否 `[x]` + 目标阶段有没有对应的 `[x]` 条目), 否则会形成 "两边各自以为对方做了" 的文档黑洞. 本条后补 → LESSONS_LEARNED candidate.
- [ ] 用户手动验收 (用户 go-ahead 后本条标 ✅)

**Day 总计**: 10.5-12 天 + buffer ≈ **15-19 天 (连续日)** 对齐 §14.8 workload.

**workload 上下限决策** (§14.7.C):
- **偏 15 天**: Day 1 主程序 git diff 小 / Day 3 coerce sweep 发现 ≤2 处 / Day 8 联调暴露 ≤1 非 dev_note 新 bug
- **偏 19 天**: Day 1 主程序 diff 涉及 ≥10 字段 / Day 8 联调暴露 ≥3 bug / §12.3.D Realtime 接入为实装 (而非同步决策)

**中途新 bug 决策树** (§14.7.D):
1. 数据丢失相邻 → Day X hotfix (当日内修)
2. §3A 已有原则违反 → 按 §13 同族 sweep 逻辑一起修
3. 新功能 / UX 改进 → 推 P25 or backlog
4. 架构级 (类 #91) → 新开 phase, 不塞 P24

---

## 附录 A · 本蓝图 Sweep 原始数据 (2026-04-21 P24 开工前)

### A.1 HTTPException shape 分布统计

- `detail="str"` 纯字符串 shape: 约 15 处, 集中在 `session_router /
  diagnostics_router / health_router / chat_router`
- `detail={"message": str}` 字典 shape: 约 12 处, 集中在 `persona_router
  / config_router`
- `detail={"error_type", ...}` 扩展字典: 约 8 处, `session_router /
  memory_router` (P21 引入)

### A.2 atomic_write 副本 fsync 覆盖表

| 模块 | 函数 | fsync | 影响范围 |
|---|---|---|---|
| persistence.py | _atomic_write_bytes/json | ✅ | 存档 JSON + tar.gz |
| memory_router.py | _atomic_write_json | ❌ | memory.json 编辑器保存 |
| memory_runner.py | _atomic_write_json | ❌ | recent/facts/reflections 写盘 |
| script_runner.py | _write_user_template_atomic | ❌ | 脚本 JSON 保存 |
| scoring_schema.py | _write_user_schema_atomic | ❌ | 用户 schema JSON 保存 |
| snapshot_store.py | _spill_to_cold | ❌ (连 atomic 都没) | snapshot cold 存档 |

### A.3 SessionState 使用分布

枚举值 (按出现顺序): `IDLE` (default) / `BUSY` (5 chat + 3 stage + 1 config + 1 snapshot*3 + 1 export) /
`SAVING` (1) / `LOADING` (4) / `RESETTING` (1) / `REWINDING` (1).
**未见**: `RUNNING / AUTOPLAYING / ERROR` (如设计过早期版本有, 当前版本已合并).

### A.4 事件总线 emit 点 (部分, 全量 matrix 在 §4.1.3 Day 5 生成)

| source file | 事件 |
|---|---|
| `core/state.js` (set helper) | `session:change` / `active_workspace:change` / `ui_prefs:change` / `errors:change` (通过 set) |
| `core/errors_bus.js` | `errors:change` (直接 emit 到 state bus) |
| `core/api.js` | `http:error` / `sse:error` |
| `ui/...` 多处调 `emit()` | 待 Day 5 matrix |

### A.5 Schema_version 矩阵

| 模块 | 当前 version | newer 行为 | older 行为 | migration |
|---|---|---|---|---|
| persistence.SessionArchive | 1 | InvalidArchive | 允许 | 无表 |
| snapshot_store.Snapshot | 1 | 无检查 | 无检查 | 无表 |
| autosave slot JSON | 0/读时默认 | 无检查 | 无检查 | 无表 |
| session_export envelope | 1 | N/A (只记录) | N/A | 无 |
| scoring_schema | 1 | ScoringSchemaError | 允许 | 无表 |

### A.6 URL.createObjectURL → revoke 配对

| 文件 | createObjectURL 行 | revokeObjectURL 行 | 配对 |
|---|---|---|---|
| `page_results.js` | 539 | 546 | ✅ |
| `page_schemas.js` | 1160 | 1167 (setTimeout 1000) | ✅ |
| `session_export_modal.js` | 122 | 130 | ✅ |
| `page_scripts.js` | 758 | 765 (setTimeout 1000) | ✅ |

**结论**: 4/4 配对齐全, 非技术债.

### A.7 uvicorn host binding 合规证据

`run_testbench.py`:
- L4 docstring: `--host 127.0.0.1 默认`
- L6: "binds to 127.0.0.1 by default to avoid exposing"
- L83-85: 非 loopback 启动打印 WARN

`config.py L46`: `DEFAULT_HOST: str = "127.0.0.1"  # Bind to loopback only.`

**结论**: ✅ 合规, 仅需 README 警告 + 可选加 `record_internal(op="insecure_host_binding")`.

---

*本蓝图结束. P24 开工请从 §8 Day 0 开始.*

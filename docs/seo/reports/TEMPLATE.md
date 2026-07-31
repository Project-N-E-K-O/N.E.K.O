# N.E.K.O SEO / GEO 日报 — YYYY-MM-DD

> 生成时间：ISO-8601（Asia/Shanghai）  
> 总体状态：COMPLETE / PARTIAL / FAILED  
> 北极星指标：进入 Google Top 10 的跟踪关键词数量 → Steam 商店访问  
> Skill 执行链：技术健康 → GSC/GA4 表现 → 排名/AIO 可见性 → 1–2 个可验收动作；Top 10 固定置顶。

## 🏆 首页战况（HEADLINE）

- **`.cn` Top 10：X/Y**；若跟踪集合改变，必须写明分母变化与日期。
- `.cn` Top 10 词名与命中 URL：逐项列出 `关键词（#排名 → 实际排名 URL）`；没有则写“无”。
- **`.online` Top 10：X/Y**；英文、中文文档段合计，并注明缺失/失败数。
- `.online` Top 10 词名与命中 URL：逐项列出 `关键词（#排名 → 实际排名 URL）`；没有则写“无”。
- Top 10 同口径变动：**±X**（当前 X、上次 X、可比 X/Y）；没有上一份同口径且已执行的逐词结果时写 `N/A`。
- 今日新进 Top 10：逐项列出 `段 · 关键词（上次 → 本次 → 命中 URL）`；没有则写“无”。
- 今日跌出 Top 10：逐项列出 `段 · 关键词（上次 → 本次 → 命中 URL）`；没有则写“无”。
- 全部跟踪词：Top 3 **X** · Top 30 **X** · Top 100 **X** · 100 名外 **X**。
- 搜索可见频率：`.cn` / `.online` 各自近 7 个完整日的 GSC 日均曝光及对前 7 日变化。
- AI Overview 频率：触发数/已观察查询数、目标域引用数/已观察查询数、引用数/已触发查询数。
- GSC sitemap 覆盖：`.cn` indexed/submitted（覆盖率）· `.online` indexed/submitted（覆盖率）；API 未返回时明确写 `N/A + 原因`。
- DataForSEO 已报告费用：**$X**（X/Y 个段提供费用证据）。
- 一句话结论：只根据有效 artifact、GSC 和 GA4 数据下结论。

## 📋 关键词 → 落地页 → 排名 → CTA 主表

| 站点/段 | 关键词 | 意图 | Volume | KD | 主落地页 | 命中 URL | 排名 | Δ上次 | AIO | CTA | 状态 |
|---|---|---|---:|---:|---|---|---:|---:|---|---|---|
| .cn zh-CN / China | 示例 | BOFU | NOT_RUN | UNSUPPORTED | / | https://project-neko.cn/ 或 N/A | >100 | N/A | 未触发 | Steam 商店访问 | OBSERVED |

- 排名 11–20 冲首页机会：X。
- 落地页不一致：X。
- `Δ上次 = 上一份排名 - 本次排名`，正数表示提升；没有同口径逐词 artifact 时必须是 `N/A`。

## 📊 搜索频率与月搜索需求

### DataForSEO 月搜索需求估算

| 关键词段 | 有 Volume / 跟踪词 | 月搜索量合计 | 已知词平均月搜索量 | 状态 |
|---|---:|---:|---:|---|
| .cn zh-CN / China | X/8 | X | X | COMPLETE |
| .online en / United States | X/19 | X | X | COMPLETE |
| .online zh-CN / China | X/3 | X | X | COMPLETE |

Volume 是配置地区/语言下 Google Ads 的月搜索需求估算，不是 N.E.K.O 的访问量。中国区仍跳过不受支持的 Labs KD，但必须采集 Volume；单词没有返回量时保留 `N/A`，不能写成 0。

### GSC 实际搜索可见频率

| 站点 | 最新完整日曝光 | 近 7 日曝光 | 日均曝光 | 前 7 日日均曝光 | 日均 Δ | 状态 |
|---|---:|---:|---:|---:|---:|---|
| .cn 产品主页 | X | X | X | X | ±X（±X%） | COMPLETE |
| .online 文档站 | X | X | X | X | ±X（±X%） | COMPLETE |

`GSC 日均曝光 = 完整窗口 impressions ÷ 窗口天数`，它才是 N.E.K.O 实际出现在 Google 搜索结果中的频率。日报禁止用 DataForSEO Volume 冒充曝光。

## 🔎 GSC 搜索表现

> “最新完整日”必须来自 Search Console API `metadata.first_incomplete_date` 的动态解析，并使用 `America/Los_Angeles` 日期口径；不能固定写成运行日前 3 天。

| 站点 | 数据截止 | 最新完整日点击 | 曝光 | CTR | 平均排名 | 近7日点击变化 | sitemap |
|---|---|---:|---:|---:|---:|---:|---|
| .cn 产品主页 | YYYY-MM-DD | X | X | X% | X | ±X | indexed/submitted（覆盖率）+ errors/warnings |
| .online 文档站 | YYYY-MM-DD | X | X | X% | X | ±X | indexed/submitted（覆盖率）+ errors/warnings |

- 高曝光低 CTR 页面：列出页面、曝光、CTR 与证据窗口。
- GSC 新查询：列出新增 query；API 没有数据时写 `N/A`。

### GSC 连续 7 日环比

| 站点 | 当前窗口 | 点击 | Δ前7日 | 曝光 | Δ前7日 | CTR | Δ前7日 | 平均排名 | Δ前7日 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| .cn 产品主页 | YYYY-MM-DD → YYYY-MM-DD | X | ±X | X | ±X | X% | ±X pp | X | 改善/下降 |
| .online 文档站 | YYYY-MM-DD → YYYY-MM-DD | X | ±X | X | ±X | X% | ±X pp | X | 改善/下降 |

## 🤖 GEO / AI 搜索战况

- DataForSEO AIO 触发频率：X/Y（X%）。
- N.E.K.O AIO 引用频率（全部已观察查询）：Z/Y（X%）。
- N.E.K.O AIO 触发后引用率：Z/X（X%）；没有触发时写 `N/A`，不能写 0%。
- 与上次同口径比较：可比查询数，以及上述三项频率的百分点变化；没有历史日报时写 `NOT_RUN`。
- AIO 引用缺口：列出“触发但未引用”的关键词。
- GA4 AI 来源会话（昨日）：`.cn` X · `.online` Y。
- AI 来源 Steam CTA（昨日）：`.cn` X · `.online` Y。
- AI 来源文档→主页（昨日）：`.cn` N/A（不适用）· `.online` Y。
- 人工 AI 引用抽查：`NOT_RUN / COMPLETE`；若执行，逐条保存平台、query、是否提及/引用、回答 URL 或截图，不与自动 AIO/GA4 混算。

自动频率只涵盖 Google organic AI Overview。ChatGPT/Perplexity 等人工引用抽查，以及 GA4 AI referral 访问/转化，必须继续分表报告，不得合成一个模糊的“AI 引用率”。

## 📈 转化漏斗

| 阶段 | `.cn` | `.online` |
|---|---:|---:|
| GSC 曝光（最新完整日） | X | X |
| GSC 点击（最新完整日） | X | X |
| GA4 Organic 会话（昨日） | X | X |
| Steam CTA 总数（昨日） | X | X |
| 文档→主页总数（昨日） | N/A | X |
| Organic Steam CTA（昨日） | X | X |
| Organic 文档→主页（昨日） | N/A | X |
| AI 来源会话（昨日） | X | X |
| AI 来源转化：Steam CTA（昨日） | X | X |
| AI 来源转化：文档→主页（昨日） | N/A | X |

### GA4 连续 7 日环比

| 站点 | 当前窗口 | Organic 会话 | Δ前7日 | Organic 浏览 | Δ前7日 | Steam CTA 总数 | Δ前7日 | 文档→主页总数 | Δ前7日 | AI 会话 | Δ前7日 | AI Steam CTA | AI 文档→主页 | AI/全站会话 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| .cn 产品主页 | YYYY-MM-DD → YYYY-MM-DD | X | ±X | X | ±X | X | ±X | N/A | N/A | X | ±X | X | N/A | X% |
| .online 文档站 | YYYY-MM-DD → YYYY-MM-DD | X | ±X | X | ±X | X | ±X | X | ±X | X | ±X | X | X | X% |

`AI/全站会话` 的分母是同域名全部会话，不是 Organic，会避免把 AI referral 错算为自然搜索。

CTA 追踪契约：

- 主转化：`steam_cta_click` → Steam 商店；
- `.online` 辅助转化：`docs_home_click` → 对应语言主页；
- 到 Steam 的出站 CTA 使用站点自身作为 `utm_source`、`utm_medium=referral`、页面级 `utm_campaign` 与位置级 `utm_content`；入站 Organic/AI 归因由 GA4 session source/medium 判定，不用出站 UTM 冒充；
- 日报只读取真实事件，用 UTM / `cta_location` 归因，不从 page_view 推断转化。

## ⚡ IndexNow 与技术 SEO

- `.cn` IndexNow：COMPLETE/PARTIAL/FAILED/NOT_RUN；最近提交时间、URL 数、HTTP 状态。
- `.online` IndexNow：COMPLETE/PARTIAL/FAILED/NOT_RUN；最近提交时间、URL 数、HTTP 状态。

| 检查项 | `.cn` | `.online` |
|---|---|---|
| 首页 HTTP | HTTP 200 | HTTP 200 |
| robots.txt | HTTP 200 + Sitemap | HTTP 200 + Sitemap |
| AI crawler 访问 | GPTBot / OAI-SearchBot / ChatGPT-User / ClaudeBot / PerplexityBot 均允许 | 同左 |
| sitemap.xml | URL 数 | URL 数 |
| canonical | URL | URL |
| hreflang | 数量 | 数量 |
| Schema | 类型或 N/A | 类型或 N/A |
| GA4 Measurement ID | `G-2D1RSKSR72` | `G-N4QZK4PHE3` |

## 🔌 数据可信度

| 数据源 | 目标 | 状态 | collectedAt | 结果 | evidence |
|---|---|---|---|---|---|
| DataForSEO | 每个段各一行 | COMPLETE/PARTIAL/... | ISO 时间 | Top 10、AIO | Actions run URL |
| GSC / GA4 / IndexNow / Technical | 每站各一行 | COMPLETE/PARTIAL/... | ISO 时间 | 摘要 | artifact/URL |

## 🔧 今日执行队列

只列真实证据触发的 1–2 个最优先 TODO；每项包含 owner、页面、动作、证据、验收指标。存在任何 P0/P1 数据或技术阻塞时不混入 P2；数据完整后，同优先级按 BOFU → MOFU → TOFU，再按机会量排序，并且同一站点的同一目标页每天最多入选一次。没有 commit、PR、部署或内容证据前不得标记 `DONE`。

1. **TODO · P1 / data_blocker** — 先修缺失数据；负责人：X；依据：X；验收：对应数据源恢复 `COMPLETE/ok`。
2. **TODO · P2 / rank_11_20** — 数据完整后执行增长动作；负责人：X；依据：X；验收：目标词进入 Top 10。

- 跳过/延后：写明因数据阻塞、每天最多 2 项、或规则未触发而未执行的动作；不能静默省略。
- 本次未触发：明确列出排名 11–20、低 CTR、AIO 引用缺口或落地页错配中没有证据的类别。
- 兜底：只有四类主规则都没有候选时，才允许从真实 `#21–100` / `>100` 排名中选择“到 Top 20 / Top 100”，或从 `#4–10` 选择“冲 Top 3”；`NOT_RUN/UNKNOWN/FAILED` 不得生成动作。

### 完整动作队列

- 数据/技术阻塞：X。
- 11–20 名：X。
- 21–100 / >100 排名积压：X。
- Top 4–10 冲 Top 3：X。
- 高曝光低 CTR：X。
- AIO 触发未引用：X。
- 落地页不一致：X。

## 🚩 P0 / P1 / P2 与负责人

- **P0**：阻断抓取、错误 canonical、核心脚本失效等必须当天修复的问题。
- **P1**：核心数据源缺失、部分失败、过期或字段不完整。
- **P2**：由完整数据触发的 11–20、CTR、内容、内链、FAQ/Schema 与引用优化。

## 🚩 需要产品负责人处理（Agent 做不了）

- 登录授权、DNS/域名验证、DataForSEO 充值、GSC/GA4 权限、fine-grained token 创建；
- 每项写明当前状态、平台入口和重跑后的验收条件；
- 代码、workflow 和 artifact 修复不应甩给产品负责人，由 SEO 自动化维护者处理。

## 🗓 Daily / Weekly / Monthly 节奏

- **Daily**：刷新同口径排名与数据状态，执行 1–2 个 TODO，次日报告复查实现证据与指标。
- **Weekly**：盘点 GSC 新词、低 CTR、11–20 名、内链结构，以及 Organic / AI → Steam CTA 漏斗。
- **Monthly**：所有段重拉 Volume，仅对支持地区重拉 KD；扩充 tracked 集、更新衰退内容、复盘 AIO 引用缺口和高转化页面类型。

## 🎯 明日复查

- 检查 Top 10 分子与跟踪集合分母是否同口径。
- 检查当天动作是否已落地，并等待正确的数据延迟窗口。
- IndexNow “无 URL 变更”可为 `COMPLETE + 0`；未执行必须为 `NOT_RUN/N/A`。

## 机器可读摘要

同一 artifact 内的 JSON 是唯一机器可读真相源；Markdown 只展示经过解释的摘要。

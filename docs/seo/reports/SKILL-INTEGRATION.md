# N.E.K.O SEO/GEO Agent Skill 整合说明

本文把 `gingiris-seo-geo-agent` 的通用 SOP 落到 N.E.K.O 的双站点现实中，并规定日报、自动化、Agent 执行动作与人工授权之间的边界。它不是一份空白方法论；生产入口、字段和验收条件都指向本仓库中的真实实现。

## 1. 北极星与站点分工

| 项目 | N.E.K.O 口径 |
|---|---|
| 产品 | N.E.K.O |
| 北极星 | Google Top 10 跟踪关键词数量 |
| 最终 CTA | Steam 商店访问，GA4 事件 `steam_cta_click` |
| `.cn` | 中文产品主页，承接品牌词、品类词和核心功能词；独立 GA4、GSC、IndexNow |
| `.online` | 多语言文档站，教程/功能查询必须指向具体文档页；追踪 `docs_home_click` 后再观察 Steam CTA |
| 排名源 | DataForSEO：`.cn zh-CN / China`、`.online zh-CN / China`、`.online en / United States` |
| 搜索真相源 | GSC 的真实点击、曝光、CTR、平均排名与 sitemap 覆盖 |
| 转化真相源 | 两个独立 GA4 Property 的 Organic、AI referral、Steam CTA 和文档→主页事件 |

重要适配：DataForSEO Labs 不支持 China `2156` 的 KD，因此中国段的 KD 必须写 `UNSUPPORTED`，不能写 `0`。Google Ads search volume 使用的是另一套 Google geographical target，日报仍要为中国段采集 Volume，不能因为 Labs KD 不可用而连同搜索需求、SERP、AIO、落地页匹配或 GSC 一起跳过。

## 2. Skill 与本仓库实现的逐项映射

| Skill 要求 | N.E.K.O 实现 | 证据 |
|---|---|---|
| 日报第一行回答 Top 10 数量 | `.cn` 与 `.online` 分别报告分子/分母、Top 10 词名、排名 URL | [`TEMPLATE.md`](./TEMPLATE.md) |
| Top 10 对昨日有可审计变化 | 只比较相同 segment/地区/语言/设备/depth/关键词且两次均已执行的行，列出净变化、新进与跌出 | 统一 JSON 的 `topTenChange` |
| 索引覆盖进入头条 | 两站 GSC `indexed/submitted` 与覆盖率置顶；API 缺失时写原因，不用公网 sitemap URL 数冒充 | GSC API collector |
| 关键词与落地页绑在同一主表 | 表中同时保存意图、Volume、KD、指定页、命中页、排名、AIO、CTA | `report.mjs` 生成器 |
| 技术 → 表现 → 可见性 → 动作 | 技术探针、GSC/GA4、DataForSEO/AIO、P0/P1/P2 TODO 形成一条链 | `SEO GEO Daily Report` workflow |
| GSC 真实表现 | 最新完整日 + 连续两个 7 日窗口 + sitemap submitted/indexed | GSC API collector |
| 搜索频率不能混淆 | DataForSEO Volume 表示月搜索需求；GSC 近 7 日日均 impressions 表示 N.E.K.O 实际搜索可见频率，并报告对前 7 日变化 | 统一 JSON 的 `searchFrequency` |
| GA4 转化漏斗 | 昨日完整日 + 连续两个 7 日窗口；Organic / AI / Steam / docs→home | GA4 Data API collector |
| AI 引流单独识别 | Session source regex 覆盖 ChatGPT、Perplexity、Claude、Copilot、Gemini、DeepSeek、Qwen、豆包、Poe 与 Bing AI | `monitoring.config.json` |
| GEO 三件套 | IndexNow 执行 artifact、robots AI crawler 检查、AIO 引用缺口动作 | 双仓 workflow + 技术探针 |
| AI 引用频率可审计 | 分别报告 AIO 触发率、全查询目标域引用率、触发后引用率及同口径历史变化；不与人工平台抽查或 GA4 referral 混算 | 统一 JSON 的 `aiCitationFrequency` |
| CTA 4 要素与可追踪性 | Steam CTA 使用真实 `steam_cta_click`；文档→主页使用 `docs_home_click`；内容 CTA 使用 UTM 与 `cta_location` 归因 | GA4 collector + analytics consent |
| 人工 AI 引用抽查不伪装成自动数据 | 没有逐条平台/query/回答证据时固定 `NOT_RUN`；不得与 AIO 或 GA4 AI referral 相加 | 日报 GEO 段 |
| 每天只做 1–2 个动作 | 自动选择最多 2 个 TODO，附 owner、证据和验收指标；P0/P1 阻塞时不混入 P2，P2 按站点与目标页去重 | `buildActions()` |
| 数据完整时不空转 | 四类主规则都没有候选时，只从真实的 `#21–100`、`>100` 或 `#4–10` 排名中生成积压/冲 Top 3 动作 | `buildActions()` |
| BOFU 优先 | 同优先级按 BOFU → MOFU → TOFU，再按机会量排序 | `buildActions()` |
| 跳过也要有原因 | 数据阻塞、每日上限或规则未触发都在日报明示，不把“未做”写成“已优化” | 日报动作段 |
| 日/周/月循环 | 每日报告固定提醒 Weekly 与 Monthly 队列 | 日报尾部节奏段 |

## 3. 每次生产运行的四阶段

### Phase 1：技术健康与可抓取性

两站逐项检查：

1. 首页必须返回 HTTP 200；
2. `/robots.txt` 必须返回 200 并声明正确 sitemap；
3. GPTBot、OAI-SearchBot、ChatGPT-User、ClaudeBot、PerplexityBot 不得被 `Disallow: /` 阻断；
4. `/sitemap.xml` 必须可下载且包含 URL；
5. Bing 验证文件与 IndexNow key 文件必须可访问；
6. canonical、hreflang、Schema 与各自 GA4 Measurement ID 必须在线可观察。

抓取阻断、错误 canonical 或关键 discovery 文件失效属于 P0。技术探针不应把 404 写成“尚无数据”。

### Phase 2：GSC 与 GA4 表现

GSC 使用最终数据，但不硬编码 2–3 天延迟：

- 先用 `dataState=all + dimensions=date` 读取 `metadata.first_incomplete_date`，将其前一天确定为 API 真实最新完整日；若探测范围没有未完整日期，则使用探测末日；
- 最新完整日：点击、曝光、CTR、平均排名；
- 最近连续 7 日对前 7 日：四项指标及变化；
- 高曝光低 CTR 页面、新查询；
- sitemap submitted、indexed、coverage、errors、warnings。

GA4 使用昨日完整数据：

- 全站会话、Organic sessions/pageviews；
- 两站 `steam_cta_click` 的全站总数、Organic 子集与 AI 来源子集；
- `.online` 的 `docs_home_click` 全站总数、Organic 子集与 AI 来源子集；
- 最近连续 7 日对前 7 日；
- `AI 来源会话 ÷ 全站会话`，不把 referral 冒充 Organic。

两个域名必须使用两个数字 Property ID。Measurement ID 只能验证前端代码，不能传给 GA4 Data API。

### Phase 3：排名与 AI 可见性

同一次日报必须读取三个同口径 DataForSEO 段：

- `.cn`：8 个中文产品/功能词；
- `.online-en`：19 个英文品类、部署和功能词；
- `.online-zh`：3 个中文教程/功能词，绑定具体中文文档页。

每个查询固定 Google Organic、desktop、depth 100，并请求 AIO。结果必须区分：

- `#1–10`：首页；
- `#11–20`：冲首页队列；
- `#21–100`；
- `>100`：真实查询后未发现；
- `NOT_RUN / UNKNOWN / FAILED`：没有可信结果，绝不能伪装成 `>100` 或 `0`。

上一份日报只有在 segment、地区、语言、设备、depth 与关键词完全一致时才能计算排名变化。

### Phase 4：动作与验收

自动动作按以下顺序选取最多 2 个：

1. **P0 技术阻断**：先修抓取、canonical、discovery 文件或 AI crawler 阻断；
2. **P1 数据闭环**：再修失败或缺失的 DataForSEO、GSC、GA4、IndexNow 权限/配置/artifact；
3. **P2 排名 11–20**：优先 BOFU；补查询覆盖并从相关高权重页面增加 2–3 条描述性内链；
4. **P2 高曝光低 CTR**：按数字、年份、括号、社证、50–60 字符五项检查，只改 title/meta，避免无证据重写正文；
5. **P2 落地页错配**：修内部链接与 canonical 信号，使一个关键词只归一个主页面；
6. **P2 AIO 触发未引用**：补一句话答案、Key Stats、5–8 条 FAQ、最后更新日期和可核验来源。
7. **P2 排名兜底**：只有上述四类 P2 主规则全部无候选时，才从真实 `#21–100` / `>100` 排名中选择“先到 Top 20 / Top 100”，或从 `#4–10` 中选择“冲 Top 3”；不允许用未执行的排名生成动作。

只要 P0/P1 阻塞队列非空，当天就只处理阻塞，不把证据不完整的 P2 混进执行队列。进入 P2 后按 `siteId + target` 去重：同一页面即使同时触发排名、CTR 与 AIO 缺口，也只占一个当日动作名额，其余证据保留在完整候选队列等待后续合并处理。

日报生成的动作初始状态只能是 `TODO`。只有 Agent 实际提交内容/代码并能给出 commit、PR、部署 URL 或页面证据后，后续报告才可写 `DONE`。

## 4. 页面、内容与 CTA 发布契约

自动日报负责“发现该做什么”，但不能凭 TODO 宣称页面已经优化。每次真正修改首页、文档页或新增内容时，必须把 skill 的内容与 CTA 要求落成下面的发布门禁：

1. **唯一主落地页**：一个关键词只能有一个主页面；教程/功能查询绑定 `.online` 的具体文档页，不得全部压到 `/`。
2. **直接答案**：开头先用 1–2 句回答查询，再提供 Key Stats、对比表或可核验事实；不能虚构案例数字。
3. **可引用结构**：H2/H3 清晰，包含 Key Takeaways、最后更新日期、作者/维护者、来源；适合的问题才加 5–8 条 FAQ 与对应 Schema。
4. **内链**：目标页至少获得 2–3 条来自相关高权重页面的描述性内链，并回链到支柱页；锚文本表达主题，不用“点击这里”。
5. **CTA**：正文中段和文末各有一个与上下文匹配的 Steam CTA；到 Steam 的出站链接使用站点自身作为
   `utm_source`、`utm_medium=referral`、页面级 `utm_campaign` 与位置级 `utm_content`。这是对 skill 通用博客示例
   `utm_medium=organic` 的项目适配：入站 Organic/AI 归因由 GA4 session source/medium 判定，不能用出站 UTM 冒充。
6. **事件**：Steam 跳转发送 `steam_cta_click`；`.online` 具体文档→语言主页发送 `docs_home_click`。只有用户同意 Analytics 后才发送。
7. **真实声音/E-E-A-T**：产品事实、实现限制、测试数据和团队经验必须有来源；Agent 不把通用 AI 文案当作“创始人经历”。
8. **分发**：若同步到第三方平台，必须用 canonical 指回主页面并单独记录分发 URL；当前自动日报不假定已经完成分发。

每个已执行动作至少附 `commit/PR → 部署 URL → 事件/页面验证 → 后续指标窗口` 四类证据中的适用项。没有部署证据的改动仍是“已提交”，不是“已上线”。

## 5. 日报必须回答的五组问题

1. `.cn` 的 8 个词分别排第几，Top 10 是哪些，命中哪个 URL，AIO 是否触发/引用；
2. 两站 GSC 最新完整日与 7 日趋势怎样，sitemap 覆盖是否健康；
3. 两站 GA4 的 Organic、Steam CTA、`.online` docs→home、AI 会话与 AI 转化怎样；
4. 两站 IndexNow 最近提交时间、URL 数、HTTP 状态和 artifact 在哪里；
5. DataForSEO 月搜索需求、GSC 日均曝光频率与对前 7 日变化，以及 AIO 触发/引用频率是否改善；
6. 今天基于真实证据最值得做的 1–2 个动作是什么，谁负责，如何验收。

任何一组缺失，都应在“数据可信度”和“P0/P1/P2 与负责人”中明确写出。生产门禁不仅检查顶层状态，还逐字段要求：三段固定为 `8 + 19 + 3` 行、depth 100、每段/每词采集时间属于上海时区当日日报、AIO 布尔结果与费用证据；两站必须有 GSC API 动态解析的 finalized 最新日/两个 7 日窗口/sitemap 覆盖、两个不同的 GA4 数字 Property/昨日与两个 7 日窗口，以及 IndexNow 时间/URL/响应/artifact。报告会先上传，再让 workflow 失败，避免“状态写 complete、正文却为空”的绿色空报告。

## 6. 日 / 周 / 月运营节奏

### Daily

1. 运行三段付费排名与 AIO；
2. 拉两站 GSC、GA4 和 IndexNow 证据；
3. 生成 Markdown + JSON；
4. 校验数据契约；
5. 选择并实施 1–2 个 TODO；
6. 次日依据正确延迟窗口复查，避免把日内波动当结论。

### Weekly

- 把 GSC 新查询加入候选表，先确认意图和唯一主落地页；
- 处理高曝光低 CTR 页面；
- 处理 11–20 名页面与内链；
- 对本周新增/修改页面执行直接答案、来源、Schema、内链、CTA/UTM 与事件追踪发布门禁；
- 检查 `.online` 教程/功能查询是否仍指向具体文档，而非全部回到 `/`；
- 复盘 Organic / AI → docs→home → Steam CTA 漏斗。

### Monthly

- 所有段重拉 Volume；仅支持的地区重拉 KD，中国段 KD 继续保留 `UNSUPPORTED`；
- 扩大 tracked 集，避免窄样本低估真实排名资产；
- 批量处理 11–20 名与衰退页面；
- 复盘 AIO 引用与 AI 引流；
- 识别转化最高的页面类型，优先生产同类 BOFU/MOFU 内容。

## 7. Week 0–4 在 N.E.K.O 的落地方式

| 阶段 | N.E.K.O 验收 |
|---|---|
| Week 0 地基 | 双站 robots/sitemap/canonical/GA4/GSC/IndexNow 正常；首次完整日报 artifact 通过门禁 |
| Week 1 BOFU | `.cn` 品牌/品类页与 `.online` 对比、安装、部署页拥有明确 Steam CTA |
| Week 2 内链 | 文档支柱页和功能/教程页双向互链；11–20 名页获得相关高权重内链 |
| Week 3 GEO | 高潜页面具备直接答案、Key Stats、FAQ/Schema、作者/日期和来源 |
| Week 4 CTR | 用 GSC 实际曝光筛选 title/meta 实验，并用 7 日窗口验收，不看单日噪声 |

“约 3.2 万曝光”是 skill 案例，不是 N.E.K.O 的承诺。N.E.K.O 的真实目标必须从自己的 Week 0 基线增长，不得照抄外部案例数字。

## 8. 人工与 Agent 的责任边界

### 人工一次性事项

- 为两个域名分别创建并验证 GSC 资源；
- 为两个域名分别创建 GA4 Property/Data Stream，并给服务账号 Viewer；
- 在 GA4 中把 `steam_cta_click` 作为主转化事件，并确认 `.online` 的 `docs_home_click` 可在 DebugView/实时报告观察；
- 保持 DataForSEO 余额和账号凭证有效；
- 为私有 `.cn` 仓库提供仅 `Actions: Read` 的 fine-grained `SEO_REPORTS_TOKEN`；
- 完成 DNS、部署和平台付款；
- 在 GSC 与 Bing Webmaster Tools 分别提交两站 sitemap；
- 确保 Steam CTA 目标页可访问。

### Agent 可执行事项

- 拉取、校验、解释数据并生成日报；
- 维护关键词→落地页主表；
- 修技术 SEO、内链、title/meta、FAQ/Schema 与 CTA tracking；
- 提交 IndexNow；
- 对每个动作给出代码/页面证据并跟踪结果。

## 9. 文件入口

- 日报模板：[`TEMPLATE.md`](./TEMPLATE.md)
- 当前真实验收样本：[`2026-07-28-integrated-skill-report.md`](./2026-07-28-integrated-skill-report.md)
- 部署与故障教程：[`docs/contributing/seo-geo-daily-monitoring.md`](/contributing/seo-geo-daily-monitoring)
- 双站配置：`docs/seo/monitoring.config.json`
- 生产 workflow：`.github/workflows/dataforseo.yml`

## 10. 完成定义

只有同时满足以下条件，才算“SEO/GEO 日报系统完成”：

- [ ] 三段 DataForSEO 排名均为 `COMPLETE`；
- [ ] 两站 GSC 和 GA4 均为 `COMPLETE/ok`；
- [ ] 两站 IndexNow 均有当次执行证据；无 URL 变化可为 `COMPLETE + 0`；
- [ ] 技术探针无 P0；
- [ ] Markdown 和 JSON 在同一 artifact 中；
- [ ] 报告含 Top 10、主映射表、GSC/GA4 7 日趋势、AIO、IndexNow、可信度和最多 2 个 TODO；
- [ ] 报告明确区分月搜索需求与 GSC 日均曝光频率，并分别报告 AIO 触发率、全查询引用率、触发后引用率及历史百分点变化；
- [ ] 三段逐词结果严格为 8 + 19 + 3 行，全部 observed、depth 100；两站 Google/IndexNow 字段通过逐字段门禁；
- [ ] 数据完整且存在排名/CTR/AIO/错配机会时至少选出 1 个、最多 2 个真实动作；主规则无候选时使用排名兜底，不空转；
- [ ] Top 10 净变化、新进/跌出与 sitemap 覆盖在头条可见，且比较口径一致；
- [ ] 人工 AI 抽查没有证据时显示 `NOT_RUN`，有证据时逐条可追溯；
- [ ] TODO 有 owner、证据、验收指标，执行后有 commit/PR/部署或页面证据；
- [ ] 有页面变更时通过内容、内链、来源、CTA/UTM 与事件发布门禁；
- [ ] 首次完整生产日报通过人工抽查，之后才进入常态 Daily / Weekly / Monthly 循环。

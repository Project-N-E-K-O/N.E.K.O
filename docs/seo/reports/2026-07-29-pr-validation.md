# N.E.K.O SEO / GEO 双 PR 合并前验证报告 — 2026-07-29

> 状态：`PR REVIEW FIXES VERIFIED`（主仓 [`#2541`](https://github.com/Project-N-E-K-O/N.E.K.O/pull/2541) 与源仓 [`#5`](https://github.com/Project-N-E-K-O/N.E.K.O.OfficialWebsite/pull/5) 已发布；本文已纳入审查修复后的全量回归）
> 范围：主仓统一日报改造 + `.cn` 源仓 DataForSEO / IndexNow 证据改造  
> 说明：本文是合并前测试证据，不是生产日报，也不证明搜索曝光或 AI 引用已经增长。

## 1. 结论

两个拟提交 PR 的本地验证全部通过：

| 范围 | 验证 | 结果 |
|---|---|---|
| `Project-N-E-K-O/N.E.K.O` | 完整单元测试 | **101/101 PASS** |
| `Project-N-E-K-O/N.E.K.O` | VitePress 生产构建 + SEO 产物检查 | **PASS**：299 个 indexable、37 个 noindex、1140 条 hreflang |
| `Project-N-E-K-O/N.E.K.O.OfficialWebsite` | 完整单元测试 | **19/19 PASS** |
| 两仓 | GitHub Actions YAML 解析 | **PASS** |
| 两仓 | `git diff --check` | **PASS** |
| 三段 DataForSEO | 零费用 dry-run 请求计划 | **PASS**，未发 API 请求、未消耗余额 |

这组测试证明的是：日报字段、计算公式、失败门禁、构建输出和工作流结构可运行。真正的搜索频率、AI 引用频率与转化变化，必须在代码合并后由一次完整生产运行生成，并在后续同口径窗口中比较。

### 1.1 已合并 P0 的生产正反验收

`.cn` 产物可信度修复已通过 [`N.E.K.O.OfficialWebsite#3`](https://github.com/Project-N-E-K-O/N.E.K.O.OfficialWebsite/pull/3) 合并，且不是只靠单元测试判断：

| 场景 | Actions run | 产物与结果 |
|---|---|---|
| 正常付费采集 | [`30323918621`](https://github.com/Project-N-E-K-O/N.E.K.O.OfficialWebsite/actions/runs/30323918621) | 同时上传 `dataforseo-cn-30323918621.json` 与 `dataforseo-cn-execution-30323918621.json`；8 个词、8 次请求、报告费用约 `$0.078`，状态为 `complete` |
| 缺失预期报告的故障注入 | [`30324119038`](https://github.com/Project-N-E-K-O/N.E.K.O.OfficialWebsite/actions/runs/30324119038) | workflow 失败，但仍上传执行状态；`dataReportPresent=false`，失败原因明确为预期 JSON 报告缺失 |

因此 P0 的“成功时数据+状态同时存在、缺报告时 fail closed 且保留诊断”已经完成。当前拟发布的 `.cn` PR B 是后续 P1 增强（Volume 与 IndexNow 证据），不是重复提交 P0。

## 2. 两个拟提交 PR 分别改变什么

### PR A — 主仓统一 SEO/GEO 日报

工作树：`feat/seo-daily-report-p1-p2-20260728`

核心效果：

1. 每天北京时间 08:15 运行三段付费 DataForSEO 基线；
2. 合并 `.cn`、`.online en`、`.online zh-CN` 的排名、Volume、AIO、GSC、GA4、IndexNow 和技术探针；
3. 生成同一份 Markdown + JSON artifact；
4. 增加严格生产门禁：不允许 `NOT_RUN / UNKNOWN / PARTIAL` 冒充完成；
5. 在日报头条和正文加入搜索频率、AI 引用频率及同口径变化；
6. 数据完整时只选 1–2 个有证据的 P2 动作，数据不完整时只选 P0/P1 修复动作。

当前工作区包含 21 个已跟踪文件修改，以及新增的执行状态、严格校验、双中国段配置和报告文档文件。

### PR B — `.cn` 源仓生产证据

工作树：`feat/cn-indexnow-reporting-20260728`

核心效果：

1. `.cn` 定时基线从仅 SERP 改为 **Google Ads Volume + depth-100 SERP + AIO**；
2. 明确跳过 DataForSEO Labs 不支持的 China KD，KD 继续为 `UNSUPPORTED`；
3. 保留固定名 DataForSEO artifact，供主仓统一日报复用；
4. 服务器部署后可触发 `.cn` IndexNow，并保存不含 key/payload 的执行证据；
5. 缺少产物时明确失败，不产生“绿色空报告”。

当前工作区包含 6 个已跟踪文件修改，以及新增的 IndexNow workflow 和测试。

## 3. 完整测试结果

### 3.1 主仓：101/101

| 测试组 | 数量 | 结果 | 覆盖重点 |
|---|---:|---|---|
| IndexNow | 10 | PASS | URL 规范化、key 文件、超时/限流重试、状态 artifact |
| GA4 consent / tracking | 11 | PASS | 同意前不加载、撤回、跨标签同步、Steam 与文档→主页事件 |
| Steam CTA | 4 | PASS | 所有已发布 Steam 链接的 UTM 与归因 |
| DataForSEO | 42 | PASS | 三段配置、Volume/KD、depth 100、AIO、成本、重试、workflow/artifact、空证据 fail-closed |
| SEO monitoring | 34 | PASS | GSC/GA4 窗口、技术探针、IndexNow 证据分类、日报渲染、严格门禁、搜索/引用频率 |
| **合计** | **101** | **PASS** | 0 fail / 0 skipped |

执行命令：

```bash
cd docs
npm test
npm run build:check
```

构建结果：

```text
VitePress build: PASS
SEO validation: PASS
299 indexable pages
37 noindex pages
1140 hreflang links
```

构建仍输出一个非阻断警告：部分前端 chunk 超过 500 kB。它不影响本次 SEO 日报、索引产物或测试结论，后续可以独立做代码分块优化。

### 3.2 审查修复回归

本轮针对自动 reviewer 提出的多轮可信度问题补充了代码与回归测试：

- 付费 schedule 与手动 paid dispatch 均强制 depth 100 + AIO，付费后不再因可选输入导致确定性门禁失败；
- 所有运行继续上传 `seo-geo-daily-report` 诊断 artifact，但趋势比较只读取 `main` 上通过完整付费门禁后发布的 `seo-geo-daily-paid-baseline`；
- `docs_home_click` 只计算具体文档页到任一语言首页的导航，语言首页互跳不再误报；
- 技术探针的 HTTP 与内容不变量共同决定顶层状态，`daily` 门禁还会独立复核 robots sitemap 声明、sitemap URL、验证/key 文件、`lang`、canonical、hreflang、GA4 Measurement ID 和 AI crawler 策略。
- 主仓与 `.cn` 源仓 artifact 均通过服务端精确 `name=` 查询，并只复用 `main` 证据，避免仓库 artifact 增长后静默漏掉历史数据；
- keyword metrics 缺失/空数组、三段全部无数值 Volume 均 fail-closed；证据文件缺失与 JSON 损坏被分别标记为 `NOT_RUN` 与 `UNKNOWN/unavailable`；
- 首页中的畸形 script/modulepreload URL 会被隔离丢弃，不再让已采集的 canonical、robots、sitemap 等整段证据一起丢失；历史样本已明确不定义当前字段契约。
- 旧版 artifact 缺少 AI comparison 时安全显示 `NOT_RUN`；两站 AI referral 正则由 `defaults.ga4` 单点维护且仍允许站点覆盖；GSC finalized 完整日超过 1–4 天容差即判为陈旧证据。

上述修复后重新执行 `npm test` 为 **101/101 PASS**，`npm run build:check`、workflow YAML 解析、Markdown 路径检查及 `git diff --check` 均通过。

### 3.3 `.cn` 源仓：19/19

| 测试范围 | 结果 |
|---|---|
| DataForSEO 认证/状态/成本与缺失产物门禁 | PASS |
| `.cn` 独立配置和账号安全 | PASS |
| China Volume + 跳过 Labs KD 的请求计划 | PASS |
| depth-100 SERP 与 AIO workflow | PASS |
| IndexNow URL、状态 artifact 与部署触发 | PASS |
| **总计** | **19/19 PASS** |

执行命令：

```bash
node --test scripts/*.test.mjs scripts/dataforseo/*.test.mjs
```

### 3.4 工作流与差异完整性

两仓均通过：

- 使用 `uv run --with pyyaml` 解析变更后的 GitHub Actions YAML；
- `git diff --check` 无空白错误；
- dry-run 临时文件在读取后已删除；
- 本地 dry-run 不读取 DataForSEO 凭证，也没有产生付费请求。

## 4. DataForSEO 零费用请求计划

统一日报的三段计划如下：

| 段 | 跟踪词 | Volume 请求 | KD 请求 | SERP 请求 | 请求合计 | AIO |
|---|---:|---:|---:|---:|---:|---|
| `.online en / United States` | 19 | 1 | 1 | 19 | **21** | 开启 |
| `.cn zh-CN / China` | 8 | 1 | 0 | 8 | **9** | 开启 |
| `.online zh-CN / China` | 3 | 1 | 0 | 3 | **4** | 开启 |
| **统一日报合计** | **30** | **3** | **1** | **30** | **34** | 开启 |

`.cn` 源仓自身的定时计划同样是 8 个词、1 个 Volume 请求、0 个 KD 请求、8 个 SERP 请求，共 9 个。主仓优先复用该 artifact；只有 artifact 缺失、过期或不完整时才执行 `.cn` fallback，因此正常路径不会无意义重复付费。

## 5. 日报中的“搜索频率”最终如何体现

日报不会把一个模糊的“搜索量”同时表示市场需求和本站表现，而是拆成两张表。

### 5.1 市场搜索需求

数据源：DataForSEO Google Ads monthly search volume。

| 字段 | 含义 |
|---|---|
| `reportedQueries / trackedQueries` | 有 Volume 返回值的词数 / 跟踪词数 |
| `totalMonthlySearchVolume` | 已知词的估算月搜索量合计 |
| `averageMonthlySearchVolume` | 已知词平均月搜索量 |
| `status` | 每段是否完成 Volume 采集 |

这表示配置地区和语言下的市场需求，不是 N.E.K.O 自己获得的流量。China KD 不支持不再成为跳过 China Volume 的理由。

### 5.2 N.E.K.O 实际搜索可见频率

数据源：GSC finalized impressions。

```text
近 7 日日均曝光 = 最近 7 个完整日 impressions ÷ 7
前 7 日日均曝光 = 再前 7 个完整日 impressions ÷ 7
日均变化 = 近 7 日日均曝光 - 前 7 日日均曝光
变化率 = 日均变化 ÷ 前 7 日日均曝光
```

日报按 `.cn` 和 `.online` 分别报告最新完整日曝光、近 7 日曝光、日均曝光、前 7 日日均曝光、数值变化和百分比变化。这才是“我们在 Google 搜索结果中出现得更频繁了吗”的直接答案。

## 6. 日报中的“AI 引用频率”最终如何体现

自动数据源是 30 个跟踪查询的 Google organic AI Overview，不与人工平台抽查或 GA4 referral 混算。

```text
AIO 触发率 = 触发 AIO 的查询数 ÷ 已观察查询数
全查询引用率 = 引用 project-neko 目标域的查询数 ÷ 已观察查询数
触发后引用率 = 引用目标域的查询数 ÷ 触发 AIO 的查询数
```

当没有 AIO 触发时，“触发后引用率”必须为 `N/A`，不能伪造为 0%。当存在上一份相同 segment/地区/语言/设备/depth/关键词的日报时，三项频率会额外显示百分点变化。

另外两组 AI 指标继续独立：

- **GA4 AI referral**：AI 来源会话、Steam CTA、`.online` 文档→主页和 AI/全站会话占比；
- **人工 ChatGPT/Perplexity 等引用抽查**：只有保存平台、query、回答证据和 URL/截图时才是 `COMPLETE`，否则固定 `NOT_RUN`。

## 7. 当前真实样本能说明什么

现有可审计样本是 [`2026-07-28-integrated-skill-report.md`](./2026-07-28-integrated-skill-report.md)。它来自改造前的不完整数据链路，不能当作完整基线：

| 指标 | 当前真实证据 | 可否用于趋势结论 |
|---|---|---|
| `.cn` 排名 | 8 个词已真实运行，Top 10 为 0/8 | 可作为首次排名观察，但没有上一份同口径对照 |
| `.cn` AIO | 0/8 触发，0/8 引用；触发后引用率 N/A | 可作为一次 AIO 观察，不能代表长期频率 |
| `.online en` | 19 个词中 4 个失败，AIO 未运行 | 不可用于完整基线 |
| `.online zh-CN` | artifact 缺失 | 不可用 |
| DataForSEO Volume | 历史 artifact 未完整采集 | 不可用；新 workflow 会补齐 |
| 两站 GSC 日均曝光 | 本地样本缺 Google 服务账号，值为 N/A | 不可用；生产 workflow 有权限后填充 |
| 两站 GA4 AI referral | 本地样本缺 Google 服务账号，值为 N/A | 不可用 |
| `.cn` 生产证据 | Actions run `30323918621`，费用 $0.078 | 可证明该次请求执行，不证明排名/引用增长 |

因此，当前能诚实说的是“测量系统已通过本地验收”，不能说“搜索频率或 AI 引用频率已经提高”。

## 8. 合并后的生产验收条件

第一次完整生产日报必须同时满足：

- [ ] 30/30 个跟踪查询均为上海当日、depth 100、`OBSERVED`；
- [ ] 三段 Volume 状态为 `COMPLETE`；China KD 为 `UNSUPPORTED`，US English KD 为 `COMPLETE`；
- [ ] AIO 30/30 有布尔观察结果，并输出三项频率；
- [ ] `.cn` / `.online` GSC 有最新 finalized 日和连续两个 7 日窗口；
- [ ] `.cn` / `.online` GA4 使用两个不同数字 Property ID，并有昨日与两个 7 日窗口；
- [ ] 两站 IndexNow 有本次执行 artifact；无变更可为 `COMPLETE + 0`，未执行不得写 0；
- [ ] 两站技术探针及内容不变量全部为 `ok`，不能仅凭 HTTP 200 放行；
- [ ] Markdown 与 JSON 在同一个固定名 artifact；
- [ ] 只有 `main` 上通过完整付费门禁的运行发布下一次可读取的比较基线；
- [ ] 严格门禁通过，并只选择 1–2 个真实动作。

主仓仍需要可读取私有 `.cn` 源仓 Actions artifact 的 `SEO_REPORTS_TOKEN`。若它未配置，生产日报会保留诊断 artifact 后失败，并把原因列为 P1，而不是伪造完整结果。

截至 2026-07-29 的仓库配置审计结果：

- 已配置：`DATAFORSEO_LOGIN`、`DATAFORSEO_PASSWORD`、`GOOGLE_SERVICE_ACCOUNT_JSON`；
- 已配置：两站各自的 GA4 Property ID 与 GSC resource URL；
- 未配置：`SEO_REPORTS_TOKEN`；
- 现网旧 workflow 的 `ENABLE_PAID_DATAFORSEO_SCHEDULE=false` 导致最近两次 schedule 直接为 `skipped`；PR A 会删除这条旧 kill switch，并把定时任务固定为付费 depth-100 + AIO；
- 在 `SEO_REPORTS_TOKEN` 补齐前，主仓仍可本地付费回退采集 `.cn` 排名，但无法把私有源仓的 IndexNow artifact 纳入完整日报，因此严格门禁会按设计失败。

## 9. 何时才能判断“有效果”

1. 合并后先拿到第一份 30/30 完整生产基线；
2. 根据基线执行日报选出的 1–2 个真实页面/内链/CTR/AIO 动作；
3. 次日只检查运行与事件证据，不用一天噪声宣称增长；
4. 7 天后比较 GSC 日均曝光、CTR、排名与 GA4 Organic/AI 流量；
5. 28 天后比较月搜索需求覆盖、Top 10 数量、AIO 引用频率和 Steam CTA 转化。

测试通过解决的是“以前该跑却没跑、失败和未运行混在一起”的测量问题。真实增长仍取决于后续是否按日报持续执行高价值内容、内链、snippet 和可引用结构优化。

## 10. 相关文件

- 最终日报模板：[`TEMPLATE.md`](./TEMPLATE.md)
- SEO/GEO skill 项目化说明：[`SKILL-INTEGRATION.md`](./SKILL-INTEGRATION.md)
- 当前真实但不完整的样本：[`2026-07-28-integrated-skill-report.md`](./2026-07-28-integrated-skill-report.md)
- 生产运维教程：[`docs/contributing/seo-geo-daily-monitoring.md`](/contributing/seo-geo-daily-monitoring)

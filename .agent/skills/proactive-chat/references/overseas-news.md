# 海外区新闻来源：防编造的不变式

## 来源

新闻类搭话的来源按地区分（`utils/web_scraper/trending_content.py`，由 `is_china_region()` 决定）：

- 中国区：微博、贴吧、小黑盒。
- 其它地区：Twitter/X、Google Trends（`google_trends_feed.py`，按用户语言选国家）、Steam 商店热门（`steam_store_feed.py`，按用户语言选商店语言和地区）。小黑盒是中文社区，不给海外用户。

海外这几路的共同问题：拿到手的往往只是一个光秃秃的热词或游戏名。实测弱一些的模型面对这种候选，要么没话说，要么凭空编（把一个股票代码说成「神秘数字」）。维护者对这块的要求是「最坏情况下也不能胡说」，下面每一条都是针对实测到的问题加的。动这块代码时要保持它们，并给改动补变异验证。

## 不变式

### 1. 没有配套新闻的热词直接丢

`parse_google_trends_rss`：一个热词下面如果没有任何可用的新闻条目（有标题、有 http(s) 链接、不是博彩页、不是文件名），整个热词跳过。热词条目的链接用排第一的那篇文章，不用 feed 自己的链接（那个指回 feed）。

### 2. Steam 候选必须经过核验，核验失败整个来源不用

`fetch_steam_featured` 分两步：`featuredcategories` 取热销和新品的 app id，再用 `IStoreBrowseService/GetItems` 批量核验。只留下「类型是游戏」并且「内容描述符不含被屏蔽项」的条目（`vet_steam_games`）。

- 第二步请求失败、或者核验后一个都不剩 → 返回失败，这一路不提供任何候选。不能退回「没核验但先用着」。
- 正常游戏本来就没有内容描述符字段。单个条目缺这个字段不算核验失败。只有**整批**都没有这个字段时才打告警（可能是接口改了字段名，成人内容过滤会静默失效）。评审提「缺字段应视为失败」时按这条驳回。

### 3. 海外候选带内部标记 `grounded_news`，全程按「有出处的材料」处理

`sources.py::_extract_links_from_raw` 给海外的 Twitter、Google Trends、Steam 候选打上 `grounded_news: True`，并把新闻标题或游戏简介放进 `description_hint`。这个标记决定了后面三处行为：

- **Phase 1 渲染**（`candidate_selection.py::_format_phase1_link_candidate`）：标题和简介是不可信的自由文本，按社区卡片同样的方式转义，避免里面的 `|`、`=` 破坏候选列表的结构。
- **选中之后**（`_selected_web_link_from_match`）：保留候选原始的标题和来源，不用模型复述的版本（模型可能回显转义后的标题，或者自己润色）。
- **Phase 2 上下文**（`utils/web_scraper/proactive_candidate.py::prepare_selected_web_candidate`）：不把 Phase 1 模型自己的转述交给 Phase 2，而是重新给出来源、标题、摘要这些原始事实，外加一条「不要断言材料以外的事实」的约束。

标记只在内部流转，`decisions.py::_public_web_link` 在响应边界把它和其它内部字段一起剥掉。往候选上加新的内部字段时，确认它不在 `_public_web_link` 的白名单里。

### 4. 匹配不上的选题：可能来自海外新闻就跳过，能确认来自无链接文本段的保留

Phase 1 模型返回的选题有时匹配不到任何候选链接。`_unmatched_pick_may_be_grounded_news` 的判断顺序：

1. 这一轮没有任何 `grounded_news` 候选 → 不干预，行为和以前一样。
2. 选题的来源名就是某个 `grounded_news` 来源 → 跳过。
3. 选题的来源是某个无链接文本段的标签（窗口搜索等兜底文本，它们本来就不会匹配到链接）→ 保留。
4. 选题的标题出现在无链接文本段的正文里 → 保留。
5. 来源为空 → 跳过。
6. 来源是别的带链接的非海外来源 → 保留；认不出来的来源 → 跳过。

⚠️ 这里两头都要顾：放得太松，Phase 2 拿到的只是模型自己的转述，没有出处事实也没有约束，等于绕过了第 3 条；收得太紧，窗口搜索这类合法的无链接话题会被误杀。改这个函数时两类用例都要测。

### 5. 负面新闻、博彩、垃圾标题过滤

`utils/web_scraper/news_filters.py`：

- `is_tragic_news_text`：词表是 `config/prompts/prompts_proactive.py` 的 `OVERSEAS_NEWS_BLOCKED_TERMS`（死亡、暴力、灾难类）。取舍是宁可多拦。
- 判断一个热词是否负面时，要看它下面**所有**新闻标题，包括那些因为链接不合格而被跳过的条目。
- `is_gambling_url`：博彩站。域名太通用、按子串会误伤的（比如会撞上别的单词的），按主机名后缀匹配。
- `is_junk_headline`：「标题」其实是文件名的条目。

## 加新的海外来源时

先问这个来源给的候选有没有「可以站得住的事实」。只有标题、没有上下文的条目（图片帖、视频帖的标题，光秃秃的热词）和第 1 条是同一类问题：要么带上摘要或正文，要么丢掉。新来源的候选同样要打 `grounded_news` 并提供 `description_hint`。

相关测试：`tests/unit/test_proactive_news_overseas_sources.py`。

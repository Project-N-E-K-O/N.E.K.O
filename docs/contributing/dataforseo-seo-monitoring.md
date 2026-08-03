# DataForSEO SEO Monitoring

This maintainer-only tool turns the documentation site's tracked keyword list into a sanitized JSON report containing:

- Google Ads monthly search volume;
- organic keyword difficulty from DataForSEO Labs;
- the target domain's Google organic rank and matched landing page;
- optional Google AI Overview detection and citations of the target domain.

It is not browser code and is never bundled into VitePress. DataForSEO credentials must stay in a local environment or GitHub Actions secrets.

## Safety and evidence contract

DataForSEO bills by request, but the daily baseline is deliberately **not** hidden behind a cost-saving kill switch. A skipped run and a real zero are different facts:

- pull requests run tests and three `dry-run` plans only; they receive no billing credentials;
- manual workflow dispatch defaults to `dry-run` and sends no paid request;
- the 08:15 Asia/Shanghai schedule always runs the paid baseline at SERP depth 100 with AI Overview loading enabled;
- there is no `ENABLE_PAID_DATAFORSEO_SCHEDULE` variable; an old variable with that name has no effect and should be removed from repository settings;
- SERP depth 100 may bill up to ten result pages per query;
- each SERP request sets `max_crawl_pages` from that depth, making the displayed page count a hard crawl limit;
- asynchronous AI Overview loading can add a charge to every SERP request and is intentionally included in the scheduled visibility baseline;
- one scheduled run tracks 19 `.online` English queries, 8 `.cn` Chinese queries, and 3 `.online` Chinese documentation queries;
- each segment runs Volume/KD (`--mode keywords`) and ranking/AIO (`--mode serp`) as independent workflow steps, never as one scheduled `--mode all` process;
- a failure in either step cannot prevent the other step from running or erase its report; the merger records the combined segment as `partial` and keeps separate component diagnostics;
- explicit transient SERP API failures that report zero cost retry only the failed keyword, at most three attempts with backoff;
- ambiguous network, response-body, or JSON failures are never retried automatically because the completed request may already have been billed;
- a failed response reporting any nonzero cost is never retried automatically, preventing an accidental duplicate charge;
- recoverable keyword failures do not discard successful results; the artifact records `partial` or `failed` status and per-keyword diagnostics;
- account-wide fatal failures stop the run immediately and do not produce an artifact; the sanitized fatal diagnostic includes attempts and any cost reported for the current keyword;
- generated reports live under `docs/.seo-reports/`, are ignored by Git, and are retained as workflow artifacts for 30 days;
- every segment writes an execution-status manifest even when collection fails; a missing expected report makes the workflow fail instead of silently producing a green empty run.

Each request plan states the request count, maximum SERP pages, and number of AIO-enabled calls before execution. The workflow retains both component reports plus a merged compatibility report. A completed paid merge records the combined costs returned by DataForSEO; if one component report is missing, `totalUsd` is `null` and `knownTotalUsd` contains only the cost proven by the surviving report.

::: danger Keep credentials server-side
Never add credentials to `docs/public`, Markdown, tracked JSON, browser code, or a `VITE_*` variable. Vite exposes `VITE_*` values to the client bundle. Use the separate `DATAFORSEO_LOGIN` and `DATAFORSEO_PASSWORD` values from the DataForSEO API Access page; the account password is not the API password.
:::

## Tracked keywords

The baseline uses three independent configs:

| Config | Target | Query count | Paid mode | KD |
| --- | --- | ---: | --- | --- |
| `docs/seo/dataforseo.config.json` | `.online`, US English | 19 | metrics + SERP + AIO | supported |
| `docs/seo/dataforseo.cn.config.json` | `.cn`, China zh-CN | 8 | Volume + SERP + AIO | unsupported for China |
| `docs/seo/dataforseo.online-zh.config.json` | `.online`, China zh-CN | 3 | Volume + SERP + AIO | unsupported for China |

The English config is derived from existing documentation pages and targets `project-neko.online` in US English (`locationCode` 2840).

```json
{
  "targetDomain": "project-neko.online",
  "locationCode": 2840,
  "locale": "en",
  "serpLanguageCode": "en",
  "volumeLanguageCode": "en",
  "keywordDifficultyLanguageCode": "en",
  "device": "desktop",
  "serpDepth": 100,
  "keywords": [
    {
      "keyword": "live2d ai assistant",
      "landingPage": "/frontend/live2d",
      "intent": "MOFU feature"
    }
  ]
}
```

Language configuration is provider-specific:

- `locale` labels the tracked content segment and is never sent to DataForSEO;
- `serpLanguageCode` is sent only to Google Organic SERP;
- `volumeLanguageCode` is sent only to Google Ads Search Volume; `null` omits the optional API field;
- `keywordDifficultyLanguageCode` is sent only to DataForSEO Labs; `null` disables KD collection for that segment even when the CLI default would otherwise enable it.

The two Chinese configs use `locale: "zh-CN"` and `serpLanguageCode: "zh-CN"`, but set Volume and KD language to `null`. This keeps the China location target (`2156`), avoids sending the invalid Google Ads `zh-CN` language field, and records China KD as unsupported instead of making an unsupported paid request. The legacy single `languageCode` field remains readable for external local configs, but it cannot be combined with the provider-specific fields.

Keep each keyword unique and mapped to one primary landing page. Missing Volume or KD remains `null`; the tool does not invent a replacement value.

The committed US/English baseline contains 19 phrases. Twelve are strict AI desktop-pet or desktop-companion category terms; the remaining seven measure supporting capabilities such as memory, plugins, and self-hosting. The three Chinese documentation queries are kept in their own segment and point to concrete `.online` pages. The same phrases may also appear in the `.cn` segment because the two domains are measured independently.

DataForSEO Labs does not list China location `2156` for organic keyword difficulty. China KD is therefore `UNSUPPORTED`, never `0` and never borrowed from another market. Google Ads Search Volume uses Google geographical targets and its language field is optional, so the China segments keep `locationCode: 2156` while omitting `language_code`; SERP rank, matched URL and AIO continue to use their own `zh-CN` setting.

Because the default `all` and `keywords` modes call Google Ads Search Volume, each tracked phrase is validated against that endpoint's limit of 80 characters and 10 words before any paid request is sent.

Google Ads `competition` and `competition_index` describe paid-ad competition. They are preserved as `adsCompetition*` fields but are not treated as organic KD. Organic `keywordDifficulty` comes from the separate DataForSEO Labs endpoint.

## Validate without spending

From `docs/`:

```bash
npm ci
npm run test:dataforseo
npm run seo:dataforseo -- --dry-run
```

The last command validates the config and writes a request plan to `.seo-reports/dataforseo-report.json`. It does not require credentials.

## Run locally

Set credentials only in the current shell, then select the smallest required mode:

```bash
export DATAFORSEO_LOGIN='api-login-from-dataforseo'
export DATAFORSEO_PASSWORD='api-password-from-dataforseo'

# Two paid requests: one Volume request and one bulk KD request.
npm run seo:dataforseo -- --mode keywords

# One paid Live SERP request per tracked keyword, depth 100.
npm run seo:dataforseo -- --mode serp --depth 100 --include-ai-overview

# Local convenience mode: Volume + KD + SERP in one process.
npm run seo:dataforseo -- --mode all
```

For the same failure isolation used by Actions, run the two paid groups separately and keep distinct output files:

```bash
npm run seo:dataforseo -- --mode keywords --output .seo-reports/dataforseo-online-en-metrics.json
npm run seo:dataforseo -- --mode serp --depth 100 --include-ai-overview --output .seo-reports/dataforseo-online-en-ranking.json
```

The scheduled workflow does not use the local convenience `all` mode. It merges the independent component reports only after both tasks have had a chance to run.

Use an alternate segment config explicitly when running outside Actions:

```bash
npm run seo:dataforseo -- --config seo/dataforseo.cn.config.json --mode keywords --output .seo-reports/dataforseo-cn-metrics.json
npm run seo:dataforseo -- --config seo/dataforseo.cn.config.json --mode serp --depth 100 --include-ai-overview --output .seo-reports/dataforseo-cn-ranking.json
npm run seo:dataforseo -- --config seo/dataforseo.online-zh.config.json --mode keywords --output .seo-reports/dataforseo-online-zh-metrics.json
npm run seo:dataforseo -- --config seo/dataforseo.online-zh.config.json --mode serp --depth 100 --include-ai-overview --output .seo-reports/dataforseo-online-zh-ranking.json
```

Use `--output <path>` for a different report path and `--config <path>` for an alternate untracked keyword set.

## Run in GitHub Actions

1. In the target repository, open **Settings → Secrets and variables → Actions**.
2. Add `DATAFORSEO_LOGIN` and `DATAFORSEO_PASSWORD` as secrets. Do not combine them into one public variable.
3. Remove the obsolete `ENABLE_PAID_DATAFORSEO_SCHEDULE` variable if it still exists; the current workflow does not read it.
4. Open **Actions → SEO GEO Daily Report → Run workflow**.
5. Run `dry-run` first and inspect all three request plans.
6. Run `paid`; paid dispatches always force depth 100 and AIO on, even if the dry-run-only inputs were changed.
7. Download the fixed-name `seo-geo-daily-report` diagnostic artifact. It always contains raw reports, all execution manifests, the unified JSON and the unified Markdown, including evidence from a failed gate.
8. A successful paid run on `main` also uploads `seo-geo-daily-paid-baseline`. Only this gate-verified artifact is eligible for next-run rank/AIO comparisons; dry-runs, failed paid runs, and feature-branch runs cannot replace it.
9. The daily 08:15 schedule is automatic after merge. Missing credentials, a missing core report, or a failed technical/content invariant makes the run fail after the diagnostic artifact has been uploaded.

Pull requests run the unit tests and committed-config dry-run only. They never receive DataForSEO secrets and never execute a paid request.

The workflow also writes a unified GSC/GA4 Markdown and JSON summary. See [SEO/GEO daily monitoring](./seo-geo-daily-monitoring) for its read-only Google setup and `N/A` behavior.

## Report fields

| Field | Meaning |
| --- | --- |
| `keywordMetrics[].searchVolume` | Approximate average monthly Google Ads search volume |
| `keywordMetrics[].keywordDifficulty` | Organic top-10 difficulty from DataForSEO Labs, 0-100 or `null` |
| `serp[].organicRank` | Rank among organic results (`rank_group`) |
| `serp[].absoluteRank` | Absolute position among all SERP elements (`rank_absolute`) |
| `serp[].landingPageMatched` | Whether Google ranked the configured primary page |
| `serp[].aiOverviewTriggered` | Whether an AIO item appeared |
| `serp[].matchedUrl` | The real URL that ranked for the configured target domain |
| `serp[].aiOverviewCitedTarget` | Whether AIO referenced the segment's target domain or a subdomain |
| `status` | `planned`, `complete`, `partial`, or `failed` |
| `errors[]` | Sanitized per-keyword error, attempts, incurred cost, and cost-guard decisions, including uncertain billing |
| `costs.totalUsd` | Sum of both component costs when both cost records are present; otherwise `null` |
| `costs.knownTotalUsd` | Sum proven by the component reports that are present, including partial runs |
| `components.keywordMetrics` / `components.ranking` | Independent task status and collection timestamp |

SERP crawling stops only when the target is found in an `organic` result. Appearances in other result types do not stop the crawl before the natural ranking can be recorded.

## Official API references

- [Authentication](https://docs.dataforseo.com/v3/auth/)
- [Google Ads Search Volume Live](https://docs.dataforseo.com/v3/keywords_data-google_ads-search_volume-live/)
- [Google Bulk Keyword Difficulty Live](https://docs.dataforseo.com/v3/dataforseo_labs-google-bulk_keyword_difficulty-live/)
- [Google Organic SERP Live Advanced](https://docs.dataforseo.com/v3/serp/google/organic/live/advanced/)

# DataForSEO SEO Monitoring

This maintainer-only tool turns the documentation site's tracked keyword list into a sanitized JSON report containing:

- Google Ads monthly search volume;
- organic keyword difficulty from DataForSEO Labs;
- the target domain's Google organic rank and matched landing page;
- optional Google AI Overview detection and citations of the target domain.

It is not browser code and is never bundled into VitePress. DataForSEO credentials must stay in a local environment or GitHub Actions secrets.

## Safety contract

DataForSEO bills by request. The repository therefore keeps scheduled billing behind an explicit repository variable:

- `dry-run` is the default workflow mode and sends no request;
- the 07:30 Asia/Shanghai schedule is skipped unless `ENABLE_PAID_DATAFORSEO_SCHEDULE` is exactly `true`;
- the scheduled mode is fixed to SERP depth 10 with AI Overview loading disabled;
- SERP depth defaults to 10; increasing it may bill another result page per 10 results;
- each SERP request sets `max_crawl_pages` from that depth, making the displayed page count a hard crawl limit;
- asynchronous AI Overview loading is disabled by default and can add a charge to every SERP request;
- Live SERP allows one task per request, so the committed 19-keyword set means 19 paid SERP requests per SERP run;
- transient zero-cost SERP failures retry only the failed keyword, at most three attempts with backoff;
- a failed response reporting any nonzero cost is never retried automatically, preventing an accidental duplicate charge;
- one keyword failure does not discard successful results; the artifact records `partial` or `failed` status and per-keyword diagnostics;
- generated reports live under `docs/.seo-reports/`, are ignored by Git, and are retained as workflow artifacts for 14 days.

The request plan always states the request count, maximum SERP pages, and number of AIO-enabled calls before execution. A completed paid report records the costs returned by DataForSEO.

::: danger Keep credentials server-side
Never add credentials to `docs/public`, Markdown, tracked JSON, browser code, or a `VITE_*` variable. Vite exposes `VITE_*` values to the client bundle. Use the separate `DATAFORSEO_LOGIN` and `DATAFORSEO_PASSWORD` values from the DataForSEO API Access page; the account password is not the API password.
:::

## Tracked keywords

Edit `docs/seo/dataforseo.config.json`. The committed starter set is derived from existing English documentation pages and targets `project-neko.online` in US English (`locationCode` 2840).

```json
{
  "targetDomain": "project-neko.online",
  "locationCode": 2840,
  "languageCode": "en",
  "device": "desktop",
  "serpDepth": 10,
  "keywords": [
    {
      "keyword": "live2d ai assistant",
      "landingPage": "/frontend/live2d",
      "intent": "MOFU feature"
    }
  ]
}
```

Keep each keyword unique and mapped to one primary landing page. Missing Volume or KD remains `null`; the tool does not invent a replacement value.

The committed US/English baseline contains 19 phrases. Twelve are strict AI desktop-pet or desktop-companion category terms; the remaining seven measure supporting capabilities such as memory, plugins, and self-hosting. Chinese and Japanese phrases remain locale content targets and are not mixed into this US/English API request.

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

# One paid Live SERP request per tracked keyword, depth 10.
npm run seo:dataforseo -- --mode serp

# Volume + KD + SERP.
npm run seo:dataforseo -- --mode all
```

Explicitly opt in to more SERP results or asynchronous AIO data only after reviewing account balance and current DataForSEO pricing:

```bash
npm run seo:dataforseo -- --mode serp --depth 30 --include-ai-overview
```

Use `--output <path>` for a different report path and `--config <path>` for an alternate untracked keyword set.

## Run in GitHub Actions

1. In the target repository, open **Settings → Secrets and variables → Actions**.
2. Add `DATAFORSEO_LOGIN` and `DATAFORSEO_PASSWORD` as secrets. Do not combine them into one public variable.
3. Keep the repository variable `ENABLE_PAID_DATAFORSEO_SCHEDULE` set to `false` while validating manually.
4. Open **Actions → DataForSEO SEO Report → Run workflow**.
5. Run `dry-run` first and inspect the plan artifact.
6. Choose `keywords`, `serp`, or `all`; leave depth at 10 and AIO disabled unless the extra paid data is required.
7. Download the `dataforseo-report-<run-id>` artifact.
8. Only after a successful manual SERP baseline, set `ENABLE_PAID_DATAFORSEO_SCHEDULE=true` to enable the daily paid run.

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
| `serp[].aiOverviewCitedTarget` | Whether AIO referenced `project-neko.online` or a subdomain |
| `status` | `planned`, `complete`, `partial`, or `failed` |
| `errors[]` | Sanitized per-keyword error, attempts, incurred cost, and cost-guard decision |
| `costs.totalUsd` | Sum of costs returned by the API responses |

SERP crawling stops only when the target is found in an `organic` result. Appearances in other result types do not stop the crawl before the natural ranking can be recorded.

## Official API references

- [Authentication](https://docs.dataforseo.com/v3/auth/)
- [Google Ads Search Volume Live](https://docs.dataforseo.com/v3/keywords_data-google_ads-search_volume-live/)
- [Google Bulk Keyword Difficulty Live](https://docs.dataforseo.com/v3/dataforseo_labs-google-bulk_keyword_difficulty-live/)
- [Google Organic SERP Live Advanced](https://docs.dataforseo.com/v3/serp/google/organic/live/advanced/)

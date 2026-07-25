# SEO/GEO Daily Monitoring

The `DataForSEO SEO Report` workflow produces one artifact containing a unified Markdown/JSON summary for:

- all 19 tracked keywords in one keyword → landing page → intent → Volume/KD → rank → CTA table, with the 12 strict AI desktop-pet / desktop-companion terms kept as their own segment;
- explicit DataForSEO states for ranked, outside the observed SERP depth, failed, or not queried; AIO disabled is reported as `not_audited`, not as zero citations;
- GSC dimensionless property totals, visible query-page detail, top landing pages, high-impression/low-CTR opportunities, positions 11–20, and possible query cannibalization;
- GA4 production sessions, organic landing pages, engagement, AI-referral sources, and organic-search `steam_cta_click` details by page, source/medium, CTA position, locale, and target URL;
- the public sitemap URL count.

Missing sources are written as `N/A` with a reason. They do not make an otherwise usable partial report disappear.

The scheduled workflow always performs the free GSC, GA4, and sitemap collection. `ENABLE_PAID_DATAFORSEO_SCHEDULE` controls only the paid DataForSEO step. A disabled paid step is reported as `not queried` and never as a failed or zero ranking.

## Repository configuration

Create these non-secret Actions variables:

| Variable | Value | Purpose |
| --- | --- | --- |
| `GA4_PROPERTY_ID` | `546216550` | Numeric GA4 property ID; this is not the `G-` measurement ID |
| `GSC_SITE_URL` | `https://project-neko.online/` | Exact verified URL-prefix property |
| `ENABLE_PAID_DATAFORSEO_SCHEDULE` | `false` initially | Paid DataForSEO kill switch; free Google collection still runs at 07:30 Asia/Shanghai |

Keep this credential only as an Actions secret:

| Secret | Value |
| --- | --- |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | The complete JSON key for a dedicated read-only reporting service account |

Never commit the JSON key, paste it into Markdown, put it in `docs/public`, or expose it through a `VITE_*` variable.

## One-time Google owner setup

1. In one Google Cloud project, enable the **Google Search Console API** and **Google Analytics Data API**.
2. Create a dedicated service account and JSON key.
3. In GSC, add the service-account email to the exact property `https://project-neko.online/` as a Full user (not an owner).
4. In GA4 property `546216550`, add the same email in **Property access management** with the Viewer role.
5. Save the whole JSON key as the repository secret `GOOGLE_SERVICE_ACCOUNT_JSON`.

The collector requests only `webmasters.readonly` and `analytics.readonly` OAuth scopes. Google documents OAuth 2.0 and the read-only Search Console scope in [Authorize Requests](https://developers.google.com/webmaster-tools/v1/how-tos/authorizing), and documents service-account `runReport` access in the [Google Analytics Data API quickstart](https://developers.google.com/analytics/devguides/reporting/data/v1/quickstart).

Register `cta_position`, `locale`, and `target_url` as event-scoped GA4 custom dimensions. If they are missing, total CTA events remain available and only the optional breakdown is marked `N/A`.

## Data semantics

- GSC and GA4 use the same 28-day window ending three days before the run, so the acquisition-to-conversion comparison is date-aligned with GSC final data.
- The GSC property total is queried without dimensions and can include anonymized-query traffic. Query tables are labelled as visible detail because GSC omits anonymized queries when a query dimension is requested.
- A successful DataForSEO request with no target-domain result at depth 10 is shown as `>10`; it is not a request failure.
- Volume/KD and the latest successful SERP baseline are cached separately. Their capture timestamps are always displayed when reused.
- The workflow restores `docs/.seo-history/` through GitHub Actions Cache and emits rolling daily, weekly, and monthly files. The cache contains report data only—never service-account JSON or API credentials—and the directory is ignored by Git.
- Weekly and monthly changes compare persisted rolling-window snapshots; they are not sums of daily values.

## Validation order

1. Keep `ENABLE_PAID_DATAFORSEO_SCHEDULE=false`.
2. Run the workflow in `dry-run` mode. Its artifact should contain the DataForSEO plan, free Google metrics (or explicit `N/A` reasons), and daily/weekly/monthly summaries.
3. Run one manual `serp` report with depth 10 and AI Overview disabled.
4. Confirm the report status is `complete` or review any retained per-keyword errors.
5. Only then change `ENABLE_PAID_DATAFORSEO_SCHEDULE` to `true`.

When paid scheduling is enabled, its DataForSEO run is fixed to SERP depth 10 with AI Overview disabled. A failed response that reports a nonzero DataForSEO cost is not retried automatically. The workflow never runs both `keywords` and `serp` unless the manually selected mode is `all`.

## Conversion caveat

The report reads `steam_cta_click`; it does not create that browser event. Verify separately that the deployed docs site emits the event only after Analytics consent and mark it as a GA4 key event. When the event is absent, the report correctly shows zero clicks rather than inferring a conversion from ordinary page views.

# SEO/GEO Daily Monitoring

The `DataForSEO SEO Report` workflow produces one artifact containing the paid-or-dry-run DataForSEO result plus a unified Markdown/JSON summary for:

- the 12 strict AI desktop-pet / desktop-companion keywords;
- supporting developer and capability keywords, kept as a separate count;
- GSC clicks, impressions, CTR, average position, and sitemap state;
- GA4 organic sessions, organic page views, AI referrals, and organic-search `steam_cta_click` events;
- the public sitemap URL count.

Missing sources are written as `N/A` with a reason. They do not make an otherwise usable partial report disappear.

## Repository configuration

Create these non-secret Actions variables:

| Variable | Value | Purpose |
| --- | --- | --- |
| `GA4_PROPERTY_ID` | `546216550` | Numeric GA4 property ID; this is not the `G-` measurement ID |
| `GSC_SITE_URL` | `https://project-neko.online/` | Exact verified URL-prefix property |
| `ENABLE_PAID_DATAFORSEO_SCHEDULE` | `false` initially | Paid 07:30 Asia/Shanghai schedule kill switch |

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

## Validation order

1. Keep `ENABLE_PAID_DATAFORSEO_SCHEDULE=false`.
2. Run the workflow in `dry-run` mode. Its artifact should contain the DataForSEO plan and either Google metrics or explicit `N/A` reasons.
3. Run one manual `serp` report with depth 10 and AI Overview disabled.
4. Confirm the report status is `complete` or review any retained per-keyword errors.
5. Only then change `ENABLE_PAID_DATAFORSEO_SCHEDULE` to `true`.

The scheduled run is fixed to SERP depth 10 with AI Overview disabled. A failed response that reports a nonzero DataForSEO cost is not retried automatically.

## Conversion caveat

The report reads `steam_cta_click`; it does not create that browser event. Verify separately that the deployed docs site emits the event only after Analytics consent. When the event is absent, the report correctly shows zero clicks rather than inferring a conversion from ordinary page views.

---
title: Privacy Policy
description: How the Project N.E.K.O. documentation site handles optional analytics and visitor privacy choices.
seoSchemaType: WebPage
editLink: false
lastUpdated: false
---

# Privacy Policy

This policy applies to the Project N.E.K.O. documentation site.

## Your choice

Analytics is optional. Google Analytics is not loaded until you allow it, and declining analytics does not limit access to the documentation.

Google Analytics is not loaded and the site does not send requests to Google Analytics. The consent banner stores no choice until you select **Allow** or **Decline**.

## Information used when analytics is enabled

When analytics is allowed, the site loads Google Analytics 4 using measurement ID `G-N4QZK4PHE3`. It sends page-view events so we can understand which documentation pages are useful and how visitors reach the site. When a visitor selects a link to the N.E.K.O. Steam page, it also sends a `steam_cta_click` event containing the sanitized destination URL, CTA placement, sanitized page URL, and page title.

Google Analytics may process information such as the page URL and title, referrer, browser and device information, and approximate location. We disable advertising storage, ad user data, ad personalization, Google Signals, and advertising-personalization signals in the site configuration.

The information is used for aggregate reporting and documentation improvement. Advertising tracking and advertising personalization remain disabled.

## How information is handled

Google Analytics processes analytics information on behalf of the site. The documentation site does not intentionally send account credentials, private messages, form contents, or other sensitive information through analytics.

Before analytics events are sent, page URLs retain only the approved `utm_source`, `utm_medium`, `utm_campaign`, `utm_content`, and `utm_term` campaign parameters, with each value limited to 100 characters. Other query parameters and URL fragments are removed. Steam destination URLs are sent without query parameters or fragments.

Your browser stores the choice in local storage under `neko.docs.analytics-consent.v1`. It contains only the choice, a format version, and the time it was saved. The choice expires after 180 days, after which the site asks again.

User-level and event-level data covered by GA4's Data Retention setting is kept for no longer than 14 months. Property administrators can reduce that period to 2 months. This setting does not affect aggregated standard reports. See [Google Analytics data retention](https://support.google.com/analytics/answer/7667196?hl=en).

The site may rely on external services for hosting and may open external destinations such as Steam. Those services handle information under their own policies.

## Changing or withdrawing your choice

Use the persistent **Cookie settings** control at the bottom of any documentation page to allow or decline analytics at any time. If you withdraw previously granted consent, the site changes analytics consent to denied, attempts to remove accessible `_ga` cookies, and reloads without loading the Google tag. You can also clear the site's stored data through your browser, which resets the saved choice.

## Questions

For privacy questions, contact the project through the [Project N.E.K.O. GitHub repository](https://github.com/Project-N-E-K-O/N.E.K.O/issues) without posting sensitive information publicly.

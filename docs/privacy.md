---
title: Analytics and Cookie Notice
description: How the Project N.E.K.O. documentation site uses optional Google Analytics, stores your consent choice, and lets you withdraw consent.
seoSchemaType: WebPage
---

# Analytics and Cookie Notice

Last updated: July 23, 2026.

This notice applies to the Project N.E.K.O. documentation site at `project-neko.online`.

## Before you make a choice

Google Analytics is not loaded and the site does not send requests to Google Analytics. The consent panel stores no choice until you select **Accept analytics** or **Reject**.

## If you accept analytics

The site loads Google Analytics 4 using measurement ID `G-N4QZK4PHE3`. It sends page-view events so we can understand which documentation pages are useful and how visitors find the documentation. When a visitor selects a link to the N.E.K.O. Steam page, it also sends a `steam_cta_click` event containing the destination URL, link text, CTA placement, page URL, and page title.

Google Analytics may process information such as the page URL and title, referrer, browser and device information, and approximate location. We disable advertising storage, ad user data, ad personalization, Google Signals, and advertising-personalization signals in the site configuration.

User-level and event-level data covered by GA4's Data Retention setting is kept for no longer than 14 months. Property administrators can reduce that period to 2 months. This setting does not affect aggregated standard reports. See [Google Analytics data retention](https://support.google.com/analytics/answer/7667196?hl=en).

Google Analytics may set first-party cookies such as `_ga` and `_ga_<measurement-id>` after you consent. Google describes its cookie use and data collection in:

- [Google Analytics cookie usage](https://support.google.com/analytics/answer/11397207)
- [Google Analytics data collection](https://support.google.com/analytics/answer/11593727)
- [Google Privacy Policy](https://policies.google.com/privacy)

## How your choice is stored

Your browser stores the choice in local storage under `neko.docs.analytics-consent.v1`. It contains only the choice, a format version, and the time it was saved. The choice expires after 180 days, after which the site asks again.

Rejecting analytics does not load the Google tag. If you withdraw previously granted consent, the site changes analytics consent to denied, attempts to remove accessible `_ga` cookies, and reloads without loading the Google tag.

## Change or withdraw your choice

Use the **Analytics settings** button at the bottom of any documentation page. You can accept or reject analytics at any time. The documentation remains available if you reject analytics.

For questions about this notice, contact the project through the [Project N.E.K.O. GitHub repository](https://github.com/Project-N-E-K-O/N.E.K.O/issues).

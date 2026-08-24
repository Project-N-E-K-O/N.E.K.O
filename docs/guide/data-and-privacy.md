---
title: Where does N.E.K.O send conversations and memory?
description: A technical data-flow guide to Project N.E.K.O local memory, AI providers, free API forwarding, telemetry, proactive vision, Steam Cloud, and Workshop.
seoSchemaType: WebPage
---

# Where does N.E.K.O send conversations and memory?

N.E.K.O stores character memory locally by default, but relevant content can leave the device when you use a model provider, memory-processing task, free API route, cloud speech service, Steam Cloud, Workshop, online feed, browser feature, or remote Agent channel.

Last fact review: **2026-07-23**.

::: warning Technical data-flow notice
This page explains the current implementation and official distribution statements. It is not a substitute for a jurisdiction-specific privacy policy, provider agreement, or legal review.
:::

## Data-flow overview

```text
User input
├─ local conversation and per-character memory storage
├─ selected conversation or realtime provider
├─ optional memory-processing provider
├─ free API forwarding path
├─ optional telemetry
├─ optional proactive screen context
├─ user-triggered Steam Cloud or Workshop operation
└─ online content, browser, speech, or Agent service
```

## What happens in each path

| Path | Data involved | Destination | Important boundary |
|---|---|---|---|
| Character memory storage | Recent turns, facts, reflections, persona, journals, indexes | Local configured memory directory | Local storage does not mean all processing is local |
| Conversation provider | Current prompt, conversation context, attached input | Selected model provider | Provider terms, retention, region, and account plan apply |
| Memory maintenance | Relevant conversation or memory text | Configured summary/extraction/correction provider | Runs only for the applicable task, but can contain user content |
| Explicit memory recall | Selected recalled snippets | Active conversation provider as tool output | The full memory database is not automatically sent |
| Shipped free API path | Input needed for the free request | N.E.K.O forwarding service and service partner | Current Steam EULA distinguishes this from bring-your-own paid API use |
| Telemetry | Usage and operational metadata described below | N.E.K.O telemetry service | Can be disabled with environment variables |
| Proactive vision | Screen stream or screenshots needed by the enabled feature | Local pipeline and configured vision/model path | Privacy mode stops proactive viewing; manual screenshots are separate |
| Steam Cloud | Allowlisted character settings and memory files | Steam Auto Cloud | Snapshot is not a full memory-directory backup |
| Workshop publish | User-selected card, supported model files, preview, optional reference voice | Steam Workshop | Publication and asset licensing are user decisions |
| DEBUG diagnostics | Query or tool arguments in some debug paths | Local logs unless the user shares them | Do not assume every log is content-free |

## Free API and your own provider key

The current [Steam EULA](https://store.steampowered.com/eula/4099310_eula_0) describes two relevant paths:

- with a paid provider API, input is sent from the device to the selected provider;
- with the free API service, input can be forwarded through N.E.K.O servers to service partners.

The selected provider remains an independent data processor with its own terms. A provider entry existing in the configuration does not establish the same retention or privacy behavior across all providers.

## Local memory and remote processing

The memory system keeps separate per-character layers for recent context, facts, reflections, and persona, backed by a chronological local database. Several maintenance tasks may use an LLM:

- recent-history compression and review;
- fact extraction and correction;
- reflection synthesis and promotion;
- persona merge and contradiction handling;
- explicit recall returned to the active conversation model.

Optional embedding inference is local CPU ONNX, but that does not make the LLM-based maintenance tasks local. Read [Memory System](/architecture/memory-system) for the current runtime contract.

## Telemetry

The repository README states that telemetry is enabled by default and collects operational categories such as:

- model and call type;
- token, request, and error counts;
- application version, experiment information, locale, timezone, and distribution;
- a pseudonymous device identifier and, in applicable Steam environments, a Steam numeric ID.

It states that raw conversation text, voice, images, API keys, email addresses, and phone numbers are not telemetry payloads. The implementation and the README must remain synchronized.

To opt out:

```text
DO_NOT_TRACK=1
```

or:

```text
NEKO_DO_NOT_TRACK=1
```

## Screen and proactive-vision controls

Privacy mode stops proactive vision and releases its screen stream. It does not mean that a manual screenshot or a user-initiated screen-sharing action is technically impossible. First-run behavior can differ by distribution or region, so verify the current setting instead of assuming a universal default.

Agent and plugin capabilities have separate enablement and readiness controls. See [Agent System](/architecture/agent-system) and [Task HUD System](/architecture/task-hud-system).

## Steam Cloud is a partial character snapshot

Cloud Save uploads and downloads a character unit through Steam Auto Cloud. The allowlist includes common flat files such as recent memory, facts, persona, reflections, and `time_indexed.db`, but excludes current sharded archives, some metadata, recovery journals, and SQLite sidecars.

A download can replace local same-name character data and therefore uses confirmation, active-session handling, and a local operation backup. Read [Cloud Save API](/api/rest/cloudsave) before calling it a backup or migration solution.

## Controls available today

| Control | What it does | What it does not prove |
|---|---|---|
| Choose a provider | Changes the service receiving the applicable request | That every other feature uses the same provider |
| Disable telemetry | Stops the project telemetry path | That third-party providers receive no requests |
| Enable privacy mode | Stops proactive screen viewing | That manual screenshots cannot be requested |
| Disable Agent channels | Prevents those channels from dispatching | That chat or memory providers are local |
| Avoid Cloud / Workshop | Avoids those optional transfer paths | That model APIs are offline |
| Delete a current character | Removes current runtime character memory paths | That every historical legacy directory or remote provider copy is removed |

> Comfortable with these data-flow boundaries? [View N.E.K.O. on Steam](https://store.steampowered.com/app/4099310/__NEKO/?utm_source=project-neko.online&utm_medium=referral&utm_campaign=buyer_guides&utm_content=privacy_footer_en). Review the selected AI provider's terms before sending personal content.

## Related documentation and sources

- [Memory System](/architecture/memory-system)
- [Cloud Save API](/api/rest/cloudsave)
- [Agent System](/architecture/agent-system)
- [Local and offline boundaries](./local-and-offline)
- [Cost and provider choices](./cost-and-providers)
- [Steam EULA](https://store.steampowered.com/eula/4099310_eula_0)
- [Project repository](https://github.com/Project-N-E-K-O/N.E.K.O)

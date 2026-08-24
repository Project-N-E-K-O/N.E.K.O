---
title: Can N.E.K.O run completely offline?
description: See which Project N.E.K.O components store or run locally, which features contact remote services, and why OmniOfflineClient is not a no-network mode.
seoSchemaType: WebPage
---

# Can N.E.K.O run completely offline?

N.E.K.O is not an out-of-the-box, fully offline product. Its interface and default memory storage are local, and selected components can be self-hosted, but the normal free path and many model, voice, Steam, Cloud, Workshop, feed, browser, and Agent features require a network connection.

Last fact review: **2026-07-23**.

## Four terms that should not be mixed together

| Term | Meaning in this documentation |
|---|---|
| Local storage | Data is written to files or databases on the user's device |
| Local inference | A model executes on the user's own hardware |
| Non-realtime client | Text is processed through a normal request/response API rather than a realtime session |
| Fully offline | The intended workflow continues without contacting any external endpoint |

The internal name `OmniOfflineClient` refers to the **non-realtime text Chat Completions path**. It still calls the configured model endpoint, so it is not evidence of a fully offline mode.

## Component-by-component network matrix

| Component | Default or common location | Can it contact the network? | Local option or limitation |
|---|---|---:|---|
| Main UI and avatar runtime | Local device | Sometimes | Rendering is local; connected features can still make requests |
| Character memory files | Local device | During processing | Storage is local by default, but summarization and extraction may use a configured provider |
| BM25 memory recall | Local process | No provider required for ranking | Continues when vector support is unavailable |
| Optional memory embeddings | Local CPU ONNX | Normally no | Only the embedding stage is local; it does not localize every memory LLM task |
| Core and assist models | Remote in common configurations | Yes | Some compatible self-hosted endpoints can be configured by component |
| Shipped free profiles | Project-hosted remote service | Yes | No user API key, but not offline |
| ASR, TTS, and voice registration | Depends on provider | Often | Selected local TTS or vLLM-Omni paths exist; requirements differ |
| Steam, Workshop, and Steam Cloud | Steam services | Yes | Unavailable offline |
| Browser, feeds, trends, and online content | External sources | Yes | The feature cannot fetch current external content without network access |
| Remote Agent channels | Channel-specific services | Often | Computer Use may act locally, but assessment and model calls can still be remote |

## What remains local by default

- The main web interface runs on the local main server.
- Character memory is stored under the configured per-character memory directory.
- Recent memory, facts, reflections, persona views, journals, and recovery state are local files or databases unless a user invokes a sync or export path.
- BM25 retrieval remains available without vector inference.
- Optional embedding inference uses the local CPU ONNX execution provider.
- User-provided avatar assets can be rendered locally after import.

Local storage does not by itself determine where model processing occurs.

## What commonly leaves the device

- conversation input sent to the selected chat or realtime provider;
- relevant conversation or memory text used by summary, extraction, reflection, promotion, review, or correction tasks;
- recalled memory snippets returned to the active conversation provider;
- free API requests forwarded through the current N.E.K.O service path;
- speech samples or text sent to a selected cloud ASR, TTS, or voice service;
- user-triggered Steam Cloud or Workshop content;
- online feeds, browser requests, and remote Agent work.

See [Where does N.E.K.O send conversations and memory?](./data-and-privacy) for the data-flow view.

## What a more local setup requires

A more local setup is assembled component by component:

1. choose a compatible local or self-hosted conversation endpoint;
2. verify that every required role—not only text chat—is supported;
3. configure local speech components where available;
4. keep optional embeddings local;
5. disable or avoid Steam Cloud, Workshop, online feeds, browser work, and remote Agent channels when they are outside the intended boundary;
6. test the application with outbound network access blocked and document which features degrade.

Project N.E.K.O currently does not expose a verified “one-click offline mode” that performs all of these steps.

## What to expect when offline

The exact result depends on configuration. Local rendering and stored files may remain available, while remote conversation, free profiles, online speech, Workshop, Cloud, feeds, and remote Agent channels can fail or become unavailable. Do not rely on the word “local” in one component name as a system-wide guarantee.

> Comfortable with these network boundaries? [View N.E.K.O. on Steam](https://store.steampowered.com/app/4099310/__NEKO/?utm_source=project-neko.online&utm_medium=referral&utm_campaign=buyer_guides&utm_content=offline_footer_en). For stricter local requirements, use source setup and verify each component first.

## Related technical documentation

- [Memory System](/architecture/memory-system)
- [API Providers](/config/api-providers)
- [TTS Client](/modules/tts-client)
- [TTS Pipeline](/architecture/tts-pipeline)
- [Deployment Overview](/deployment/)
- [Cost and provider choices](./cost-and-providers)

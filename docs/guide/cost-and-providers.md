---
title: Is N.E.K.O free, and what AI API costs should I expect?
description: Learn what is free in Project N.E.K.O., when an API key or paid provider may be needed, and how the free remote path differs from local or self-funded use.
seoSchemaType: WebPage
---

# Is N.E.K.O free, and what AI API costs should I expect?

The base N.E.K.O application is currently free on Steam and the project code is available under Apache License 2.0, but AI providers, speech services, and other third-party services can have separate fees, quotas, and terms.

Last fact review: **2026-07-23**. Prices, quotas, models, and provider availability are changeable service facts; check the linked provider before making a purchase decision.

## What “free” means

| Item | Current position | What may still cost money |
|---|---|---|
| Base Steam application | Free, Early Access | Future distribution terms can change; check the Steam page |
| Project source code | Apache License 2.0 | Third-party dependencies, assets, trademarks, and services retain their own terms |
| Built-in free provider path | No user-supplied API key is required for the shipped free profiles | It is a remote service with adjustable availability and quotas |
| Your own provider API key | You choose and fund the provider account | Token, realtime, speech, image, or other provider charges |
| Voice cloning and TTS | Multiple provider and local-service paths exist | Cloud providers can require a key, account, or paid quota |
| Steam Cloud and Workshop | Available through supported Steam features | Network access and the relevant Steam account are required |

“Open source” and “free application” do not mean every model, voice, character asset, or hosted service is licensed or operated by Project N.E.K.O.

## Three common ways to use AI services

### 1. Use the shipped free path

The current configuration contains free core and assist profiles that do not ask the user for an API key. These profiles connect to a Project N.E.K.O remote service; they are **not local models** and should not be presented as an offline mode.

The current [Steam EULA](https://store.steampowered.com/eula/4099310_eula_0) explains that free API requests can be forwarded through N.E.K.O servers to service partners. It also states that free-service quotas may be adjusted. For that reason, this documentation does not publish a permanent daily quota.

### 2. Bring your own API key

You can configure supported providers with your own account and credentials. In this mode:

- the provider controls pricing, rate limits, regional availability, and retention terms;
- different N.E.K.O features may use different provider roles;
- a provider that supports text chat may not support realtime voice, vision, ASR, TTS, or Agent work;
- changing the selected model can change both quality and cost.

According to the current Steam EULA, input for a paid provider API is sent from the device to the selected provider. Always review that provider's current terms.

### 3. Configure local or self-hosted components

Some components can use local or self-hosted services, including optional local embeddings and selected speech or vLLM-Omni paths. This can reduce dependence on hosted APIs, but it is not a single switch and may require hardware, model assets, and additional setup.

See [Can N.E.K.O run completely offline?](./local-and-offline) before treating local configuration as cost-free or network-free.

## Why this page does not publish a provider count

Provider definitions are data-driven and can change independently across:

- primary conversation and realtime profiles;
- assist profiles used for text, vision, summary, correction, or Agent roles;
- ASR, TTS, voice-cloning, and other feature-specific registries;
- regions, account plans, and releases.

A single number such as “14+ providers” mixes these categories and becomes stale quickly. Use the current [API Providers reference](/config/api-providers) for configuration behavior, and verify the provider visible in the version you are running.

## How to choose

| Your priority | Recommended starting point |
|---|---|
| Try N.E.K.O with minimal setup | Start with the available free profile, while accepting that it is remote and quota-limited |
| Control model choice and billing | Add your own supported provider key |
| Reduce external processing | Evaluate local/self-hosted components one by one |
| Predict monthly cost | Use your provider's usage dashboard and current price sheet; N.E.K.O does not set those rates |
| Avoid unexpected data routing | Read [Where does N.E.K.O send conversations and memory?](./data-and-privacy) |

> Want to try the free base application before choosing a provider? [View N.E.K.O. on Steam](https://store.steampowered.com/app/4099310/__NEKO/?utm_source=project-neko.online&utm_medium=referral&utm_campaign=buyer_guides&utm_content=cost_footer_en), then return here to compare API paths.

## Related technical documentation

- [API Providers](/config/api-providers)
- [Model Configuration](/config/model-config)
- [TTS Client](/modules/tts-client)
- [Local and offline boundaries](./local-and-offline)
- [Steam store page](https://store.steampowered.com/app/4099310/__NEKO/?utm_source=project-neko.online&utm_medium=referral&utm_campaign=buyer_guides&utm_content=cost_source_en)
- [Steam EULA](https://store.steampowered.com/eula/4099310_eula_0)

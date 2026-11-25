# Subscriptions & plans

The **Subscriptions & plans** section of API settings lets a group of text features use a subscription's quota: sign in or enter the subscription's key, tick the features it should take over and pick their models, then switch on **Use this subscription**. Switch it off and those features go back to their previous settings.

## Your settings are never rewritten

The subscription state is kept on this machine in `subscriptions.json` in the runtime's local state directory. It is not written into `core_config.json` and is not part of cloud saves. **Save Settings** never touches it, and the panel never touches anything under **Per-Feature Models**, so turning a subscription on or off needs no "restore" step. After moving to another machine or restoring a cloud save, sign in to the subscription and switch it on again.

## Precedence

Each text feature resolves in this order:

1. Its own setting under **Per-Feature Models**, when it is in effect: per-feature models are turned on, the provider is a named provider or a custom endpoint (not a follow option), and both address and model are filled in.
2. A subscription that takes the feature over.
3. Follow options (for example, the mini-game chat model follows the chat model by default) and the general providers above.

A feature that follows another goes where that one goes: once the chat model uses a subscription, a mini-game chat model without its own setting uses the same subscription.

Features taken over by a subscription show a **Subscription · name** tag and a note under **Per-Feature Models**. Their settings stay editable and saveable and come back when the subscription is off; meanwhile their connectivity light does not reflect what is used. A line under **General Model Provider** lists the features that currently use a subscription.

## Features a subscription can take over

Chat model, memory upkeep (large model), recognition (small model), familiar model general / small / advanced, and the mini-game chat / memory / recognition models. Which of these a subscription offers depends on the models it includes: a subscription whose models cannot read images does not offer the chat model. The decision model, realtime voice, text-to-speech, speech recognition and image generation keep their own providers.

A feature belongs to one subscription at a time. Ticking a feature another subscription has asks first, then moves it over.

## Switching on, off, and failures

| Event | Result |
| --- | --- |
| Switch a subscription on | Needs its sign-in or key and at least one ticked feature |
| Switch it off | Ticks and model choices are kept for next time; features go back to their previous settings |
| Untick one feature | Only that feature goes back to its previous setting |
| Last ChatGPT account signed out, or the plan key removed | That subscription turns off; its features go back to their previous settings |
| The key reused from elsewhere on the page cleared (no key entered in the subscription itself) | The subscription stays on, its card shows in red that no key is available, and requests report an error; no other key is used |
| Subscription expired, quota used up, or access revoked | The subscription stays on and requests report the reason; nothing silently falls back to a pay-as-you-go API key |

New requests follow the change at once; an ongoing conversation picks it up when its session ends.

## Plan keys

Keys of plans that need one are stored in `subscriptions.json` and never returned to the settings page. When the MiMo Token Plan, Kimi Code, StepFun Step Plan or Volcengine Ark Coding Plan card has no key of its own, the matching key already entered on the page is used (for MiMo the Token Plan key under **General Model Provider**, otherwise the same vendor's key in the **API Key Book**).

## Subscriptions

| Subscription | Sign-in | Features it can take over | Notes |
| --- | --- | --- | --- |
| ChatGPT | Sign in (see [ChatGPT subscription provider](/config/chatgpt)) | All 9 | Models are listed from the signed-in account |
| MiMo Token Plan | `tp-` key; the one entered under General Model Provider also works | All 9 | China, Singapore or Europe cluster |
| Kimi Code | Membership key; the Kimi Code key in the API Key Book also works | All 9 | Same connection as the Kimi Code provider; China / overseas |
| MiniMax M Plan | Plan key (not the pay-as-you-go key) | All 9 | Once the plan quota is used up, MiniMax charges the account balance by default unless turned off there |
| Alibaba Cloud Model Studio Token Plan | `sk-sp-` key | All 9 | Beijing / Singapore |
| Alibaba Cloud Model Studio Coding Plan | `sk-sp-` key | All 9 | China / international |
| Volcengine Ark Coding Plan | Ark key | All 9 | Uses `/api/coding/v3`; the plain address is billed pay-as-you-go |
| Z.ai GLM Coding Plan | Plan key | 7 (not the chat or mini-game chat model) | Images are read only through a tool, so features that must see images are not offered |
| StepFun Step Plan | Step key | All 9 | China / international |
| Baidu Qianfan Token Plan | Plan key | All 9 | |
| Tencent Cloud Token Plan | `sk-tp-` key | All 9 | Model names must be lowercase; Hy models cannot read images |
| OpenCode Go | Console key | All 9 | Models can be listed from the provider; Extra Usage charges the balance |
| Ollama Cloud | Ollama key | All 9 | Models can be listed from the provider; usage continues on purchased credit after the plan's |

Subscriptions marked **Not yet tested** have not been tried with a real subscription yet; please report problems. Each plan model's thinking switch follows the registered form of its model family; whether it turns thinking off, and whether addresses and model names are exact, is confirmed in a later round of tests with real subscriptions.

## Performance

`core_config.json` and `subscriptions.json` are cached in memory. Writes by N.E.K.O. invalidate the cache at once; other changes (a hand edit, a cloud-save restore) are noticed by a modification-time check made while reading the configuration, at most once every 2 seconds, so they take effect within 2 seconds. There is no background timer.

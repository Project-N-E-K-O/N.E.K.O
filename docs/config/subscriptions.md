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
| Subscription expired, quota used up, or access revoked | The subscription stays on and requests report the reason; nothing silently falls back to a pay-as-you-go API key |

New requests follow the change at once; an ongoing conversation picks it up when its session ends.

## Plan keys

Keys of plans that need one are stored in `subscriptions.json` and never returned to the settings page. For MiMo Token Plan, a Token Plan key already entered under **General Model Provider** is used when the card has no key of its own.

## Subscriptions

- **ChatGPT**: sign-in is described in [ChatGPT subscription provider](/config/chatgpt); models are listed from the signed-in account.
- **MiMo Token Plan**: choose the China, Singapore or Europe cluster and enter a key starting with `tp-`.

Subscriptions marked **Not yet tested** have not been tried with a real subscription yet; please report problems.

## Performance

`core_config.json` and `subscriptions.json` are cached in memory. Writes by N.E.K.O. invalidate the cache at once; other changes (a hand edit, a cloud-save restore) are noticed by a modification-time check made while reading the configuration, at most once every 2 seconds, so they take effect within 2 seconds. There is no background timer.

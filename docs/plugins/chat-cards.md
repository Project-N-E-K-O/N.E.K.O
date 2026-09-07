# HTML chat cards

Use `await ctx.create_card(...)` to post an HTML/CSS card. No TSX, JavaScript, panel declaration, or `@ui.context` provider is needed.

```python
card = await self.ctx.create_card(
    html='<p>A song is ready</p><button data-neko-action="play">Play</button>',
    css='button { padding: 8px 16px; }',
    actions={"play": {"entry": "play_track", "args": {"track_id": "123"}}},
    summary="A song is ready",
)
await card.update(summary="Playing")
await card.update(html="<p>Finished</p>", actions={})
```

`html` and `summary` are required on creation. `css`, `actions`, and `target_lanlan` are optional. Updates replace supplied fields only: omitted fields remain unchanged; `actions={}` clears bindings. `card.id` is stable. The original recipient is retained; untargeted cards are routed and pinned by the main server on first creation during its current lifetime.

When `target_lanlan` is omitted, card/view helpers use the current invocation’s `_ctx.lanlan_name`, isolated from concurrent invocations. A call without a character context leaves the target unspecified for main-server routing; it never reuses another invocation’s character. Explicit targets take precedence.

Awaiting confirms local submission, not display or delivery. Submission failures raise `CardSubmissionError`, exported from `plugin.sdk.plugin`.

## Actions

```python
from plugin.sdk.plugin import plugin_entry, ui, Ok

@ui.action(id="play_track")
@plugin_entry(id="play_track")
async def play_track(self, track_id: str, _ctx: dict):
    await self.player.play(track_id)  # Your plugin's implementation
    card = self.ctx.get_card(_ctx["card_id"], target_lanlan=_ctx["lanlan_name"])
    await card.update(html="<p>Playing</p>", summary="Playing", actions={})
    return Ok({"message": "Playback started"})
```

`data-neko-action` refers to an `actions` dictionary key. `entry` resolves to an existing UI action/entry in the same plugin. Arguments must be JSON objects. The host supplies `_ctx.card_id`, `_ctx.lanlan_name`, and `_ctx.run_id`; handlers may omit `_ctx` if unused. A returned `message` string is optional feedback. Exceptions and `Err(...)` produce error feedback.

Card/character identifiers in `_ctx` are UI request context, not authenticated identity or an authorization token. Actions are exposed at plugin scope, as with hosted panels; the card `actions` dictionary maps buttons to those actions rather than establishing a per-card access-control boundary. This follows the application’s trusted first-party UI model, not separate permissions for each character.

The parent handles clicks and pending state. Plugin scripts and inline event handlers do not execute. This is trusted installed-plugin HTML in a script-disabled iframe, not a new JavaScript application runtime. Business idempotency remains the plugin's responsibility; timeouts do not guarantee rollback.

Cards do not trigger AI replies or inject markup into model context. Copy/export and export previews use `summary` without activating buttons. Images must have browser-accessible URLs; `/media/<id>` references use the temporary plugin image cache.

Cards are online and best-effort, without persistence or offline replay. Updating an absent/evicted card does not resurrect it; create a new card to display it again.

## AgentHUD plugin content

Use `create_view()` for content outside the chat history. It uses the existing AgentHUD plugin content tab, without creating another native window or requiring a `plugin.toml` panel declaration or `@ui.context`.

```python
view = await self.ctx.create_view(
    title="Download",
    html='<p>File ready</p><button data-neko-action="download">Download</button>',
    actions={"download": {"entry": "download_file", "args": {"file_id": "123"}}},
    target_lanlan=_ctx["lanlan_name"],
)
@ui.action(id="download_file")
@plugin_entry(id="download_file")
async def download_file(self, file_id: str, _ctx: dict):
    view = self.ctx.get_view(
        _ctx["view_id"], target_lanlan=_ctx["lanlan_name"],
    )
    await view.update(summary="Downloading")
    await self.download(file_id)
    await view.update(html="<p>Download complete</p>", summary="Download complete", actions={})
    return Ok({"message": "Download complete"})
```

`create_view()` returns `PluginView`, exported from `plugin.sdk.plugin`. Creation requires `title` and `html`; `css`, `actions`, `summary`, and `target_lanlan` are optional. The initial summary defaults to the title. `update()` accepts the title and the four card content fields: omitted or `None` fields stay unchanged, and empty strings/dictionaries clear the corresponding field. `get_view()` restores a sending handle without fetching content. The host supplies `view_id`, `card_id`, `lanlan_name`, and `run_id` in action context; pass that character explicitly when restoring a handle to make the intended recipient clear.

Each plugin has one active view per character. Creating another gives it a new ID and replaces the old instance; stale updates and closes cannot affect the replacement. User close or `await view.close()` ends that display instance. Updates never reopen it or steal focus; create a new view to show content again. Closing the view does not automatically cancel plugin work. Buttons reuse the same-origin action proxy, script isolation, and error feedback.

View titles appear as tabs alongside Tasks. The first view opens the content page; later creates preserve the selected tab and collapsed state, marking unseen content with a dot. Updates keep the selection. Closing the selected page selects a neighboring view; closing the last returns to Tasks.

Views stay outside chat messages, copying/export, and AI context. Delivery follows the character's current active connection, without multi-browser synchronization, persistence, or offline replay. Awaiting still confirms local submission only; rejection raises `CardSubmissionError`.

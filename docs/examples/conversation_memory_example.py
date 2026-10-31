"""Plugin example: observe conversation records and query private memory.

Install this module as a plugin entry and replace CHARACTER with a configured
character. The host injects ctx; no separate process or service is required.
The SDK version remains 0.1.0; use a host/SDK build containing these APIs.
The version number alone does not identify their availability.
"""
from plugin.sdk.plugin import (
    Err, MemoryClient, MemoryTarget, NekoPluginBase, Ok,
    lifecycle, neko_plugin, plugin_entry,
)

CHARACTER = "YUI"


@neko_plugin
class ConversationMemoryExample(NekoPluginBase):
    def __init__(self, ctx):
        super().__init__(ctx)
        self.memory_client = MemoryClient(self.ctx)
        self.conversation_watcher = self.bus.conversations_v2.watch(
            character=CHARACTER, view_limit=20,
        )

        @self.conversation_watcher.subscribe(on="record")
        async def on_record(message):
            # Do not log message text by default; it can contain private data.
            self.logger.info("Conversation record observed: {}", message.id)

        @self.conversation_watcher.subscribe(on="change")
        async def on_change(change):
            self.logger.info("Recent window size: {}", len(change.current))

    @lifecycle(id="startup")
    async def startup(self, **_):
        await self.conversation_watcher.start()
        return Ok({"status": "ready"})

    @lifecycle(id="shutdown")
    async def shutdown(self, **_):
        await self.conversation_watcher.stop()
        return Ok({"status": "stopped"})

    @plugin_entry(
        id="search_memory", name="Search memory",
        input_schema={"type": "object", "properties": {"query": {"type": "string"}},
                      "required": ["query"]},
    )
    async def search_memory(self, query: str, **_):
        result = await self.memory_client.search(
            target=MemoryTarget.private(CHARACTER), query=query,
        )
        if isinstance(result, Err):
            return result
        return Ok({"hits": [{"kind": hit.kind, "text": hit.text} for hit in result.value.items]})

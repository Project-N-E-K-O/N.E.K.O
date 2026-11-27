from dataclasses import asdict
import json

from plugin.sdk.plugin import NekoPluginBase, lifecycle, plugin_entry


class ObservationProbe(NekoPluginBase):
    def __init__(self, ctx):
        super().__init__(ctx)
        self.events = []
        self.watcher = None

    @lifecycle(id="startup")
    async def startup(self):
        self.watcher = self.bus.conversations_v2.watch(character="YUI", view_limit=2)
        for kind in (
            "record",
            "message.updated",
            "change",
            "input.submitted",
            "reply.started",
            "reply.closed",
            "playback.started",
        ):

            def receive(value, event_kind=kind):
                self.events.append(
                    {"kind": event_kind, "value": json.loads(json.dumps(asdict(value)))}
                )

            self.watcher.subscribe(on=kind)(receive)
        await self.watcher.start()

    @plugin_entry(id="snapshot")
    async def snapshot(self):
        return self.events.copy()

    @plugin_entry(id="reply")
    async def reply(self, reply_id: str):
        value = await self.bus.conversations_v2.get_reply(reply_id)
        return json.loads(json.dumps(asdict(value))) if value is not None else None

    @plugin_entry(id="interaction")
    async def interaction(self, input_id: str):
        value = await self.bus.conversations_v2.get_interaction(input_id)
        return json.loads(json.dumps(asdict(value))) if value is not None else None

    @plugin_entry(id="stop_watch")
    async def stop_watch(self):
        await self.watcher.stop()
        return True

    @plugin_entry(id="restart_watch")
    async def restart_watch(self):
        await self.watcher.start()
        return True

    @plugin_entry(id="watch_status")
    async def watch_status(self):
        return {"active": self.watcher._active, "sub_id": self.watcher._sub_id}

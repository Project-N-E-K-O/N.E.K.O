import pytest

from plugin.plugins.neko_roast.core.module_registry import ModuleRegistry

@pytest.mark.asyncio
async def test_module_toggle_failure_keeps_previous_state_and_success_clears_degraded():
    class Module:
        id = "demo"
        title = "Demo"
        version = "1"
        enabled = False
        domain = "interaction"
        fail = True

        async def setup(self, ctx):
            return None

        async def teardown(self):
            return None

        async def on_enable(self, ctx):
            if self.fail:
                raise RuntimeError("boom")

        async def on_disable(self):
            return None

        def status(self):
            return {}

        def config_schema(self):
            return []

    module = Module()
    registry = ModuleRegistry()
    registry.register(module)

    assert await registry.enable("demo", ctx=None) is False
    assert module.enabled is False
    assert registry.is_degraded("demo") is True

    module.fail = False
    assert await registry.enable("demo", ctx=None) is True
    assert module.enabled is True
    assert registry.is_degraded("demo") is False

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

config_init = PROJECT_ROOT / "config" / "__init__.py"
if "config" not in sys.modules and config_init.exists():
    spec = importlib.util.spec_from_file_location("config", config_init)
    if spec and spec.loader:
        module = importlib.util.module_from_spec(spec)
        sys.modules["config"] = module
        spec.loader.exec_module(module)

from plugin.plugins.sts2_autoplay.tests.live_entry_smoke import LiveEntryPlugin
from plugin.sdk.shared.models.result import Err, Ok


async def unwrap(result: Any) -> dict[str, Any]:
    if isinstance(result, Ok):
        return {"kind": "ok", "payload": result.value}
    if isinstance(result, Err):
        return {"kind": "err", "error": str(result.error)}
    if isinstance(result, dict):
        return {"kind": "dict", "payload": result}
    return {"kind": type(result).__name__, "payload": repr(result)}


async def main() -> None:
    plugin = LiveEntryPlugin()
    startup = await plugin.startup()
    if isinstance(startup, Err):
        print(json.dumps({"startup": str(startup.error)}, ensure_ascii=False, indent=2))
        raise SystemExit(1)

    before = await plugin._service.get_snapshot()
    planned = await plugin.sts2_get_planned_operation()
    after_plan = await plugin._service.get_snapshot()
    executed = await plugin.sts2_execute_planned_operation()
    after_exec = await plugin._service.get_snapshot()
    await plugin.shutdown()

    output = {
        "before_screen": before.get("snapshot", {}).get("screen"),
        "before_actions": before.get("snapshot", {}).get("available_actions"),
        "planned": await unwrap(planned),
        "after_plan_screen": after_plan.get("snapshot", {}).get("screen"),
        "after_plan_actions": after_plan.get("snapshot", {}).get("available_actions"),
        "executed": await unwrap(executed),
        "after_exec_screen": after_exec.get("snapshot", {}).get("screen"),
        "after_exec_actions": after_exec.get("snapshot", {}).get("available_actions"),
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())

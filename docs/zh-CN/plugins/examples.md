# 插件示例

## 文件处理插件

一个处理文件的插件，具有生命周期管理和定时清理功能。

```python
import os
import shutil
from pathlib import Path
from typing import Any, Optional
from plugin.sdk.base import NekoPluginBase
from plugin.sdk.decorators import (
    neko_plugin, plugin_entry, lifecycle, timer_interval
)

@neko_plugin
class FileProcessorPlugin(NekoPluginBase):
    def __init__(self, ctx: Any):
        super().__init__(ctx)
        self.logger = ctx.logger
        self.work_dir = Path("/tmp/file_processor")
        self.work_dir.mkdir(exist_ok=True)
        self.processed_count = 0

    @lifecycle(id="startup")
    def startup(self, **_):
        self.logger.info("FileProcessorPlugin starting...")
        self.report_status({"status": "initialized"})
        return {"status": "ready"}

    @lifecycle(id="shutdown")
    def shutdown(self, **_):
        if self.work_dir.exists():
            shutil.rmtree(self.work_dir)
        return {"status": "stopped"}

    @plugin_entry(
        id="process_file",
        name="Process File",
        input_schema={
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
                "operation": {
                    "type": "string",
                    "enum": ["compress", "extract", "convert"]
                }
            },
            "required": ["file_path", "operation"]
        }
    )
    def process_file(self, file_path: str, operation: str, **_):
        self.report_status({"status": "processing", "file": file_path})

        try:
            # ... processing logic ...
            self.processed_count += 1
            self.ctx.push_message(
                source="file_processor",
                message_type="text",
                description="File processed",
                priority=6,
                content=f"Processed {file_path}",
            )
            return {"success": True}
        except Exception as e:
            self.logger.exception(f"Error: {e}")
            return {"success": False, "error": str(e)}

    @timer_interval(id="cleanup", seconds=3600, auto_start=True)
    def cleanup(self, **_):
        self.logger.info("Cleaning temporary files...")
        return {"cleaned": True}
```

## 异步 API 客户端插件

一个支持异步调用外部 API 和批量操作的插件。

```python
import asyncio
import aiohttp
from typing import Any, Optional, Dict
from plugin.sdk.base import NekoPluginBase
from plugin.sdk.decorators import neko_plugin, plugin_entry, lifecycle

@neko_plugin
class APIClientPlugin(NekoPluginBase):
    def __init__(self, ctx: Any):
        super().__init__(ctx)
        self.logger = ctx.logger
        self.session: Optional[aiohttp.ClientSession] = None
        self.base_url = "https://api.example.com"

    @lifecycle(id="startup")
    async def startup(self, **_):
        self.session = aiohttp.ClientSession()
        return {"status": "ready"}

    @lifecycle(id="shutdown")
    async def shutdown(self, **_):
        if self.session:
            await self.session.close()
        return {"status": "stopped"}

    @plugin_entry(
        id="fetch_data",
        name="Fetch Data",
        input_schema={
            "type": "object",
            "properties": {
                "endpoint": {"type": "string"},
                "method": {
                    "type": "string",
                    "enum": ["GET", "POST"],
                    "default": "GET"
                }
            },
            "required": ["endpoint"]
        }
    )
    async def fetch_data(self, endpoint: str, method: str = "GET", **_):
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        async with self.session.request(method, url) as response:
            data = await response.json()
            return {"success": True, "data": data}

    @plugin_entry(
        id="batch_fetch",
        input_schema={
            "type": "object",
            "properties": {
                "endpoints": {
                    "type": "array",
                    "items": {"type": "string"}
                },
                "concurrent": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 10,
                    "default": 3
                }
            },
            "required": ["endpoints"]
        }
    )
    async def batch_fetch(self, endpoints: list, concurrent: int = 3, **_):
        semaphore = asyncio.Semaphore(concurrent)

        async def fetch_one(ep):
            async with semaphore:
                return await self.fetch_data(ep)

        results = await asyncio.gather(
            *[fetch_one(ep) for ep in endpoints],
            return_exceptions=True
        )
        success = sum(1 for r in results if isinstance(r, dict) and r.get("success"))
        return {"success_count": success, "total": len(endpoints)}
```

## 带持久化的数据收集器

```python
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Dict
from plugin.sdk.base import NekoPluginBase
from plugin.sdk.decorators import (
    neko_plugin, plugin_entry, lifecycle, timer_interval
)

@neko_plugin
class DataCollectorPlugin(NekoPluginBase):
    def __init__(self, ctx: Any):
        super().__init__(ctx)
        self.logger = ctx.logger
        self.data_dir = ctx.config_path.parent / "data"
        self.data_dir.mkdir(exist_ok=True)
        self.collection_count = 0

    @plugin_entry(
        id="collect",
        input_schema={
            "type": "object",
            "properties": {
                "source": {"type": "string"}
            },
            "required": ["source"]
        }
    )
    def collect(self, source: str, **_):
        data = self._fetch_data(source)

        filename = f"{source}_{datetime.now().isoformat()}.json"
        filepath = self.data_dir / filename
        filepath.write_text(json.dumps(data, indent=2))

        self.collection_count += 1
        self.ctx.push_message(
            source="collector",
            message_type="text",
            priority=5,
            content=f"Collected {len(data)} records from {source}",
        )
        return {"count": len(data), "file": filename}

    @timer_interval(id="auto_collect", seconds=300, auto_start=True)
    def auto_collect(self, **_):
        for source in ["source1", "source2"]:
            self.collect(source=source)

    @plugin_entry(id="stats")
    def stats(self, **_):
        files = list(self.data_dir.glob("*.json"))
        return {"collection_count": self.collection_count, "files": len(files)}

    def _fetch_data(self, source):
        # Replace with actual data fetching logic
        return [{"id": 1, "source": source}]
```

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, List, Sequence

from plugin.core.state import state
from plugin.settings import PLUGIN_LOG_BUS_SDK_TIMEOUT_WARNINGS

if TYPE_CHECKING:
    from plugin.core.context import PluginContext

from .types import BusList, BusRecord


@dataclass(frozen=True)
class MemoryRecord(BusRecord):
    bucket_id: str = "default"

    @staticmethod
    def from_raw(raw: Dict[str, Any], *, bucket_id: str) -> "MemoryRecord":
        """
        Create a MemoryRecord from a raw payload, normalizing and validating common fields.
        
        Parameters:
            raw (Dict[str, Any] | any): The incoming payload to normalize. If `raw` is not a dict, it is wrapped as `{"event": raw}`.
            bucket_id (str): Identifier of the memory bucket to assign to the created record.
        
        Returns:
            MemoryRecord: A record with normalized fields:
              - `type`: string from `raw["type"]` or "UNKNOWN" if missing/falsey.
              - `timestamp`: float parsed from `raw["_ts"]`, or `None` if missing or unparsable.
              - `plugin_id`: string from `raw["plugin_id"]` or `None`.
              - `source`: string from `raw["source"]` or `None`.
              - `priority`: int parsed from `raw["priority"]`, or `0` if missing or unparsable.
              - `content`: string from `raw["content"]` or `None`.
              - `metadata`: dict from `raw["metadata"]`, or an empty dict if not a dict.
              - `raw`: the original payload dict (or wrapped dict when `raw` was not a dict).
              - `bucket_id`: the provided bucket identifier.
        """
        payload = dict(raw) if isinstance(raw, dict) else {"event": raw}
        ts = payload.get("_ts")
        timestamp = None
        try:
            if ts is not None:
                timestamp = float(ts)
        except Exception:
            timestamp = None

        typ = str(payload.get("type") or "UNKNOWN")
        plugin_id = payload.get("plugin_id")
        if plugin_id is not None:
            plugin_id = str(plugin_id)

        source = payload.get("source")
        if source is not None:
            source = str(source)

        priority = payload.get("priority", 0)
        try:
            priority = int(priority)
        except Exception:
            priority = 0

        content = payload.get("content")
        if content is not None:
            content = str(content)

        metadata = payload.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}

        return MemoryRecord(
            kind="memory",
            type=typ,
            timestamp=timestamp,
            plugin_id=plugin_id,
            source=source,
            priority=priority,
            content=content,
            metadata=metadata,
            raw=payload,
            bucket_id=bucket_id,
        )

    def dump(self) -> Dict[str, Any]:
        """
        Serialize the record to a dictionary including its bucket identifier.
        
        Returns:
            dict: A dictionary representation of the record containing the base serialized fields plus a 'bucket_id' key set to this record's bucket_id.
        """
        base = super().dump()
        base["bucket_id"] = self.bucket_id
        return base


class MemoryList(BusList[MemoryRecord]):
    def __init__(self, items: Sequence[MemoryRecord], *, bucket_id: str):
        """
        Initialize a MemoryList with given records and an associated bucket identifier.
        
        Parameters:
        	items (Sequence[MemoryRecord]): Sequence of memory records to include in the list.
        	bucket_id (str): Identifier of the memory bucket associated with these records.
        """
        super().__init__(items)
        self.bucket_id = bucket_id

    def filter(self, *args: Any, **kwargs: Any) -> "MemoryList":
        """
        Return a MemoryList containing the records that match the provided filter criteria while preserving this list's bucket_id.
        
        Parameters:
            *args: Positional filter predicates or selectors.
            **kwargs: Keyword filter options.
        
        Returns:
            MemoryList: A new MemoryList of matching MemoryRecord items with the same bucket_id as this list.
        """
        filtered = super().filter(*args, **kwargs)
        return MemoryList(filtered.dump_records(), bucket_id=self.bucket_id)

    def where(self, predicate: Any) -> "MemoryList":
        """
        Return a MemoryList containing only records that satisfy the given predicate, preserving this list's bucket_id.
        
        Parameters:
            predicate (Callable[[MemoryRecord], Any]): A callable that is invoked for each record; records for which the callable returns a truthy value are kept.
        
        Returns:
            MemoryList: A new MemoryList of records that satisfy `predicate`, with the same `bucket_id` as the original list.
        """
        filtered = super().where(predicate)
        return MemoryList(filtered.dump_records(), bucket_id=self.bucket_id)

    def limit(self, n: int) -> "MemoryList":
        """
        Return a MemoryList containing the first n records from this list.
        
        Parameters:
            n (int): Maximum number of records to include.
        
        Returns:
            MemoryList: A new MemoryList with up to `n` records from this list, preserving the original `bucket_id`.
        """
        limited = super().limit(n)
        return MemoryList(limited.dump_records(), bucket_id=self.bucket_id)


@dataclass
class MemoryClient:
    ctx: "PluginContext"

    def get(self, bucket_id: str, limit: int = 20, timeout: float = 5.0) -> MemoryList:
        """
        Retrieve memory records from the plugin bus for the given bucket.
        
        Sends a request to the plugin communication layer to fetch up to `limit` memory entries from `bucket_id`,
        waits up to `timeout` seconds for a response, normalizes each entry into a MemoryRecord, and returns them
        as a MemoryList preserving the requested bucket.
        
        Parameters:
            bucket_id (str): Identifier of the memory bucket to retrieve. Must be a non-empty string.
            limit (int): Maximum number of records to return.
            timeout (float): Maximum time in seconds to wait for a response.
        
        Returns:
            MemoryList: A list-like collection of MemoryRecord instances from the requested bucket.
        
        Raises:
            ValueError: If `bucket_id` is not a non-empty string.
            RuntimeError: If the plugin communication queue is unavailable, sending the request fails, or an error is returned.
            TimeoutError: If no valid response is received within `timeout` seconds.
        """
        if hasattr(self.ctx, "_enforce_sync_call_policy"):
            self.ctx._enforce_sync_call_policy("bus.memory.get")

        plugin_comm_queue = getattr(self.ctx, "_plugin_comm_queue", None)
        if plugin_comm_queue is None:
            raise RuntimeError(
                f"Plugin communication queue not available for plugin {getattr(self.ctx, 'plugin_id', 'unknown')}. "
                "This method can only be called from within a plugin process."
            )

        if not isinstance(bucket_id, str) or not bucket_id:
            raise ValueError("bucket_id is required")

        zmq_client = getattr(self.ctx, "_zmq_ipc_client", None)

        request_id = str(uuid.uuid4())
        request = {
            "type": "USER_CONTEXT_GET",
            "from_plugin": getattr(self.ctx, "plugin_id", ""),
            "request_id": request_id,
            "bucket_id": bucket_id,
            "limit": int(limit),
            "timeout": float(timeout),
        }
        history: List[Any] = []

        if zmq_client is not None:
            try:
                resp = zmq_client.request(request, timeout=float(timeout))
            except Exception:
                resp = None
            if not isinstance(resp, dict):
                if hasattr(self.ctx, "logger"):
                    try:
                        self.ctx.logger.warning("[bus.memory.get] ZeroMQ IPC failed; raising exception (no fallback)")
                    except Exception:
                        pass
                raise TimeoutError(f"USER_CONTEXT_GET over ZeroMQ timed out or failed after {timeout}s")

            if resp.get("error"):
                raise RuntimeError(str(resp.get("error")))

            result = resp.get("result")
            if isinstance(result, dict):
                items = result.get("history")
                if isinstance(items, list):
                    history = items
                else:
                    history = []
            elif isinstance(result, list):
                history = result
            else:
                history = []
        else:
            try:
                plugin_comm_queue.put(request, timeout=timeout)
            except Exception as e:
                raise RuntimeError(f"Failed to send USER_CONTEXT_GET request: {e}") from e

            start_time = time.time()
            check_interval = 0.01
            while time.time() - start_time < timeout:
                response = state.get_plugin_response(request_id)
                if response is None:
                    time.sleep(check_interval)
                    continue

                if not isinstance(response, dict):
                    time.sleep(check_interval)
                    continue

                if response.get("error"):
                    raise RuntimeError(str(response.get("error")))

                result = response.get("result")
                if isinstance(result, dict):
                    items = result.get("history")
                    if isinstance(items, list):
                        history = items
                    else:
                        history = []
                elif isinstance(result, list):
                    history = result
                else:
                    history = []
                break

            else:
                orphan_response = None
                try:
                    orphan_response = state.get_plugin_response(request_id)
                except Exception:
                    orphan_response = None
                if PLUGIN_LOG_BUS_SDK_TIMEOUT_WARNINGS and orphan_response is not None and hasattr(self.ctx, "logger"):
                    try:
                        self.ctx.logger.warning(
                            f"[PluginContext] Timeout reached, but response was found (likely delayed). "
                            f"Cleaned up orphan response for req_id={request_id}"
                        )
                    except Exception:
                        pass
                raise TimeoutError(f"USER_CONTEXT_GET timed out after {timeout}s")

        records: List[MemoryRecord] = []
        for item in history:
            if isinstance(item, dict):
                records.append(MemoryRecord.from_raw(item, bucket_id=bucket_id))
            else:
                records.append(MemoryRecord.from_raw({"event": item}, bucket_id=bucket_id))

        return MemoryList(records, bucket_id=bucket_id)
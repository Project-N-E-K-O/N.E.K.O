from __future__ import annotations

from typing_extensions import NotRequired, TypedDict


class RunningPluginStatus(TypedDict):
    alive: bool
    pid: int | None


class AvailableResponse(TypedDict):
    status: str
    available: bool
    plugins_count: int
    time: str


class ServerInfoSnapshot(TypedDict):
    plugins_count: int
    registered_plugins: list[str]
    running_plugins_count: int
    running_plugins: list[str]
    running_plugins_status: dict[str, RunningPluginStatus]


class ServerInfoResponse(ServerInfoSnapshot):
    sdk_version: str
    time: str


class SystemConfigResponse(TypedDict):
    config: dict[str, object]


class SerializedMessage(TypedDict):
    plugin_id: str
    source: str
    description: str
    priority: int
    message_type: str
    content: object
    binary_data: str | None
    binary_url: str
    metadata: dict[str, object]
    timestamp: str
    message_id: str


class MessageQueryResponse(TypedDict):
    messages: list[SerializedMessage]
    count: int
    time: str


MetricRecord = dict[str, object]


class GlobalMetricsSummary(TypedDict):
    total_cpu_percent: float
    total_memory_mb: float
    total_memory_percent: float
    total_threads: int
    active_plugins: int


AllPluginMetricsResponse = TypedDict(
    "AllPluginMetricsResponse",
    {
        "metrics": list[MetricRecord],
        "count": int,
        "global": GlobalMetricsSummary,
        "time": str,
    },
)


class PluginMetricsResponse(TypedDict):
    plugin_id: str
    metrics: MetricRecord | None
    time: str
    message: NotRequired[str]
    plugin_running: NotRequired[bool]
    process_alive: NotRequired[bool]


class PluginMetricsHistoryResponse(TypedDict):
    plugin_id: str
    history: list[MetricRecord]
    count: int
    time: str


class UploadSessionResponse(TypedDict):
    upload_id: str
    blob_id: str
    upload_url: str
    blob_url: str


class UploadBlobResponse(TypedDict):
    ok: bool
    upload_id: str
    blob_id: str
    size: int

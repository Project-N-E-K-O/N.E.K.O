"""Lightweight embedding contract shared by knowledge and its embedders."""


class EmbedDeferred(RuntimeError):
    """Defer background work without consuming an index attempt."""

    def __init__(self, retry_after_ms: int = 100):
        super().__init__("embedding_busy")
        self.retry_after_ms = max(1, int(retry_after_ms))

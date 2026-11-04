"""Best-effort usage counters for the watch-together scene.

Counters (not events) reach the telemetry server. Dimensions stay low-cardinality
and never include character names, video titles or IDs.
"""


def count(name: str, **dims) -> None:
    try:
        from utils.instrument import counter
        counter(name, **dims)
    except Exception:
        # Telemetry failures never affect preparation or playback.
        pass

from pathlib import Path


DIRECTOR_SCRIPT_NAMES = (
    "tutorial/yui-guide/director/foundation.js",
    "tutorial/yui-guide/director/voice-queue.js",
    "tutorial/yui-guide/director/emotion-bridge.js",
    "tutorial/yui-guide/director/cursor-anchor-store.js",
    "tutorial/yui-guide/director/director-core.js",
    "tutorial/yui-guide/director/avatar-rounds.js",
    "tutorial/yui-guide/director/page-flows.js",
    "tutorial/yui-guide/director/chat-performance.js",
    "tutorial/yui-guide/director/lifecycle.js",
    "tutorial/yui-guide/director/bootstrap.js",
)


def read_director_source(static_root: Path) -> str:
    return "\n".join(
        (static_root / relative_path).read_text(encoding="utf-8")
        for relative_path in DIRECTOR_SCRIPT_NAMES
    )

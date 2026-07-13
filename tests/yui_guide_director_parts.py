from pathlib import Path
import re


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


def read_director_source(root: Path | str = ".") -> str:
    static_root = Path(root) / "static"
    sources = {
        relative_path: (static_root / relative_path).read_text(encoding="utf-8")
        for relative_path in DIRECTOR_SCRIPT_NAMES
    }

    def class_source(relative_path: str, class_name: str) -> str:
        source = sources[relative_path]
        start = source.index(f"    class {class_name} {{")
        end = source.index(f"\n    namespace.{class_name} = {class_name};", start)
        return source[start:end]

    def method_source(relative_path: str) -> str:
        source = sources[relative_path]
        start_marker = "    namespace.extendDirector({\n"
        start = source.index(start_marker) + len(start_marker)
        end = source.rindex("\n    });")
        return re.sub(r"^        },$", "        }", source[start:end], flags=re.MULTILINE)

    core_source = class_source(
        "tutorial/yui-guide/director/director-core.js",
        "YuiGuideDirector",
    )
    core_without_closing_brace = core_source[: core_source.rindex("\n    }")]

    return "\n".join(
        [
            sources["tutorial/yui-guide/director/foundation.js"],
            class_source("tutorial/yui-guide/director/voice-queue.js", "YuiGuideVoiceQueue"),
            class_source("tutorial/yui-guide/director/emotion-bridge.js", "YuiGuideEmotionBridge"),
            class_source("tutorial/yui-guide/director/cursor-anchor-store.js", "CursorAnchorStore"),
            core_without_closing_brace,
            method_source("tutorial/yui-guide/director/avatar-rounds.js"),
            method_source("tutorial/yui-guide/director/page-flows.js"),
            method_source("tutorial/yui-guide/director/chat-performance.js"),
            method_source("tutorial/yui-guide/director/lifecycle.js"),
            "    }",
            sources["tutorial/yui-guide/director/bootstrap.js"],
        ]
    )

"""Import a user-provided Geng Guide Markdown export exactly once."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from knowledge.importers.geng_guide import load_geng_guide_markdown
from knowledge.store import KnowledgeStore


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    args = parser.parse_args()
    entries = load_geng_guide_markdown(args.input.read_bytes())
    results = KnowledgeStore(args.database).replace_source("source:geng-guide", entries)
    print(
        f"entries={len(entries)} added={sum(item.created for item in results)} "
        f"updated={sum(item.updated for item in results)} unchanged={sum(item.unchanged for item in results)}"
    )


if __name__ == "__main__":
    main()

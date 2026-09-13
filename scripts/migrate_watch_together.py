"""Import legacy caches without generation or source mutation.

uv run python scripts/migrate_watch_together.py ARCHIVE LIVE_CACHE
"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from main_logic.watch_together.library import Library, application_library


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sources", type=Path, nargs="+")
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--audio-assets", type=Path)
    args = parser.parse_args()
    library = Library(args.destination) if args.destination else application_library()
    print(json.dumps(library.import_sources(args.sources), ensure_ascii=False, indent=2))
    if args.audio_assets:
        print(json.dumps({"audio_assets":library.import_audio_assets(args.audio_assets)}, indent=2))
    print(json.dumps({"verification": library.verify()}, indent=2))

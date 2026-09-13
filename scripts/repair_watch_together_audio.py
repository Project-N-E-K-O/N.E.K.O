"""Preserve malformed legacy WAV objects; import corrected audio as new versions."""
import json
import math
from pathlib import Path
import shutil
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from main_logic.watch_together.audio import write_speech_wav
from main_logic.watch_together.engine import duration
from main_logic.watch_together.library import Library


def repair(root):
    library = Library(Path(root))
    history, offset = [], 0
    while True:
        page = library.history_page(100, offset)
        history.extend(page['analyses'])
        if page['next_offset'] is None:
            break
        offset = page['next_offset']
    repaired = set()
    for row in history:
        if row['status'] == 'ready':
            timeline = library.timeline(row['job'], row['version'])
            repaired.add((row['job'], timeline.get('audio_repair_source_version')))
    for row in history:
        if (row['job'], row['version']) in repaired:
            continue
        if row['status'] != 'ready':
            continue
        value = library.timeline(row['job'], row['version']).get('duration')
        if type(value) not in (int, float):
            continue
        try:
            total_duration = float(value)
        except OverflowError:
            continue
        if not math.isfinite(total_duration) or total_duration < 0:
            continue
        manifest = library.manifest(row['job'], row['version'])
        malformed = {}
        for name in manifest:
            if name.endswith('.wav'):
                data = library.resource(row['job'], row['version'], name).read_bytes()
                if data[:4] == b'RIFF' and data[36:40] == b'data' and data[44:48] == b'OggS':
                    malformed[name] = data[44:44 + int.from_bytes(data[40:44], 'little')]
        if not malformed:
            continue
        staging = library.root / 'audio-repairs' / uuid.uuid4().hex
        folder = staging / row['job']
        for name in manifest:
            target = folder / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(library.resource(row['job'], row['version'], name), target)
        for name, payload in malformed.items():
            write_speech_wav([payload], folder / name)
        timeline = json.loads((folder / 'timeline.json').read_text(encoding='utf-8-sig'))
        events, until = [], -1
        for event in sorted(timeline.get('events', []), key=lambda item: item['at']):
            name = (event.get('audio') or '').split(f"/media/{row['job']}/")[-1]
            if name in malformed:
                event['duration'] = duration(folder / name)
            value = event.get('duration', 0)
            try:
                event_duration = float(value) if type(value) in (int, float) else 0
            except OverflowError:
                event_duration = 0
            if not math.isfinite(event_duration) or event_duration < 0:
                event_duration = 0
            event['duration'] = event_duration
            if event['at'] < until or event['at'] + event_duration > total_duration:
                continue
            events.append(event)
            until = event['at'] + event_duration + 1.2
        timeline['events'] = events
        timeline['audio_repair_source_version'] = row['version']
        (folder / 'timeline.json').write_text(json.dumps(timeline, ensure_ascii=False), encoding='utf-8')
        library.import_sources([staging], only_job=row['job'], write_report=False)
        versions = [r for r in library.history() if r['job'] == row['job'] and r['version'] != row['version']]
        print(json.dumps({'job': row['job'], 'repaired_files': len(malformed), 'versions': versions}, ensure_ascii=False))


if __name__ == '__main__':
    repair(sys.argv[1])

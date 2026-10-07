import json
from types import SimpleNamespace
import pytest

from scripts import repair_watch_together_audio as repair


@pytest.mark.parametrize('invalid_duration', [None, 'bad', -1, True, 10**1000])
@pytest.mark.parametrize('silent_duration', [None, 'bad', -1, True, 10**1000, float('inf'), float('nan')])
def test_repair_pages_all_versions_and_preserves_silent_cues(tmp_path, monkeypatch, invalid_duration, silent_duration):
    rows = [{'job': 'job', 'version': str(i), 'status': 'incomplete'} for i in range(101)]
    rows[-1]['status'] = 'ready'
    rows[0]['status'] = 'ready'
    timeline = {'duration': 10, 'events': [{'at': 3, 'audio': '/media/job/bad.wav', 'duration': 1},
                {'at': 0, 'audio': None, 'duration': silent_duration}]}
    (tmp_path / 'timeline.json').write_text(json.dumps(timeline))
    header = bytearray(44)
    header[:4] = b'RIFF'
    header[36:40] = b'data'
    header[40:44] = (4).to_bytes(4, 'little')
    (tmp_path / 'bad.wav').write_bytes(header + b'OggS')
    offsets, imports = [], []
    def page(limit, offset):
        offsets.append(offset)
        return {'analyses': rows[offset:offset + limit],
                'next_offset': offset + limit if offset + limit < len(rows) else None}
    library = SimpleNamespace(root=tmp_path, history_page=page,
        history=lambda: [], timeline=lambda job, version: {**timeline, 'duration': invalid_duration} if version == '0' else timeline,
        manifest=lambda *args: {'timeline.json': {}, 'bad.wav': {}},
        resource=lambda job, version, name: tmp_path / name,
        import_sources=lambda paths, **kwargs: imports.extend(paths))
    monkeypatch.setattr(repair, 'Library', lambda root: library)
    monkeypatch.setattr(repair, 'write_speech_wav', lambda chunks, path: path.write_bytes(b'fixed'))
    monkeypatch.setattr(repair, 'duration', lambda path: 1)
    repair.repair(tmp_path)
    assert offsets == [0, 100]
    assert len(imports) == 1
    repaired = json.loads((imports[0] / 'job' / 'timeline.json').read_text())
    assert repaired['events'][0]['audio'] is None
    assert repaired['events'][0]['duration'] == 0
    assert [event['at'] for event in repaired['events']] == [0, 3]
    assert repaired['audio_repair_source_version'] == '100'

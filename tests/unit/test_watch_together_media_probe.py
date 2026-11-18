from unittest.mock import Mock
import pytest
from main_logic.watch_together import library, media

@pytest.mark.parametrize('role,result', [('audio', True), ('video', False)])
def test_probe_checks_role_and_caches_stat_identity(tmp_path, monkeypatch, role, result):
    library._probe_cache.clear()
    run = Mock(return_value=result)
    monkeypatch.setattr(media, 'run', run)
    args = (tmp_path / 'object', 4, 123, role)
    assert library._probe_media(*args) is result
    assert library._probe_media(*args) is result
    run.assert_called_once_with('probe', args[0], role, timeout=10)
    library._probe_media(args[0], 5, 124, role)
    assert run.call_count == 2

@pytest.mark.parametrize('error', [TimeoutError(), RuntimeError('invalid data'), OSError()])
def test_probe_failure_is_unavailable(tmp_path, monkeypatch, error):
    library._probe_cache.clear()
    monkeypatch.setattr(media, 'run', Mock(side_effect=error))
    assert not library._probe_media(tmp_path / 'object', 4, 123, 'audio')

@pytest.mark.parametrize('error', [TimeoutError(), RuntimeError('worker exited'), OSError(), ValueError()])
def test_probe_recovers_without_file_change(tmp_path, monkeypatch, error):
    library._probe_cache.clear()
    run = Mock(side_effect=[error, True])
    monkeypatch.setattr(media, 'run', run)
    args = (tmp_path / 'object', 4, 123, 'video')
    assert not library._probe_media(*args)
    assert library._probe_media(*args)
    assert library._probe_media(*args)
    assert run.call_count == 2

def test_probe_many_marks_items_that_raise(monkeypatch):
    def probe(path, role):
        if path == 'broken':
            raise ValueError('invalid data')
        return role == 'video'
    monkeypatch.setattr(media, '_probe', probe)
    assert media._probe_many([('a', 'video'), ('broken', 'video'), ('b', 'audio')]) == [True, None, False]

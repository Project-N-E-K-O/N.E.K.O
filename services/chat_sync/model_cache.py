"""Reclaim only unreferenced files in the dedicated model sync caches."""

from contextlib import closing
import json
from pathlib import Path
import re
import shutil
from urllib.parse import unquote

from filelock import FileLock

from services.chat_sync.model_protocol import decode
from services.chat_sync.writer import store_connection
from utils.config_manager import get_reserved


def collect_model_cache(sync):
    """Read fresh references after commits, serialized against model file work."""
    config = getattr(sync.adapter, 'config', None)
    if config is None or not hasattr(config, '_characters_reload_lock'):
        return False
    directory = sync.store.path.parent
    with FileLock(str(directory / 'model-cache.lock'), timeout=0), sync.runtime._commit_lock, \
            sync.runtime.writer.locked(), config._characters_reload_lock:
        sync.runtime.writer.check()
        characters = config.load_characters(require_authoritative=True)
        with closing(store_connection(sync.store.path)) as conn:
            states = [json.loads(row[0]) for row in conn.execute('SELECT state FROM model_state')]
        paths, hashes = set(), set()

        def path_reference(value):
            if not isinstance(value, str) or not value:
                return
            for prefix, root in (('/user_live2d/', getattr(config, 'readable_live2d_dir', None) or config.live2d_dir),
                                 ('/user_live2d_local/', config.live2d_dir)):
                if value.startswith(prefix):
                    value = str(Path(root) / unquote(value[len(prefix):]))
                    break
            else:
                # The model manager saves catalog paths relative to the model root;
                # a staged upload may be relative to the working directory, so both count.
                if not value.startswith('/') and not Path(value).is_absolute():
                    paths.add((Path(config.live2d_dir) / unquote(value)).resolve())
            paths.add(Path(value).resolve())

        def selection(value):
            if value:
                kind, path = json.loads(value)
                if kind == 'live2d':
                    path_reference(path)

        for raw in characters.get('猫娘', {}).values():
            if isinstance(raw, dict):
                path_reference(get_reserved(raw, 'avatar', 'live2d', 'model_path', default=''))
        for state in states:
            selection(state.get('selection'))
            for key in ('pending', 'candidate', 'conflict'):
                task = state.get(key)
                if not task:
                    continue
                selection(task.get('selection'))
                selection(task.get('expected'))
                path_reference(task.get('path'))
                if task.get('file'):
                    path_reference(task['file'].get('path'))
                model = (task.get('record') or {}).get('model')
                if model:
                    hashes.add(model['package']['sha256'])

        complete = True
        packages = directory / 'model-packages'
        if not packages.is_symlink():
            for path in packages.glob('*.zip'):
                if (not path.is_symlink() and path.is_file() and path.resolve() not in paths
                        and re.fullmatch(r'[0-9a-f]{32}|[0-9a-f]{64}', path.stem)
                        and path.stem not in hashes):
                    try:
                        path.unlink()
                    except OSError:
                        complete = False
        installed = Path(config.live2d_dir) / 'cloud-sync'
        if installed.is_symlink():
            return complete
        for root in installed.glob('*'):
            if root.is_symlink() or not root.is_dir() or not re.fullmatch(r'[0-9a-f]{64}', root.name):
                continue
            if root.name in hashes or any(path.is_relative_to(root.resolve()) for path in paths):
                continue
            receipt, archive = root / '.cloud-model.json', root / '.cloud-model.zip'
            if (receipt.is_symlink() or archive.is_symlink() or not receipt.is_file()
                    or not archive.is_file() or receipt.stat().st_size > 131072):
                continue
            try:
                record = decode(receipt.read_bytes())
            except (ValueError, OSError):
                continue
            model = record['model']
            if not model or model['package']['sha256'] != root.name:
                continue
            allowed = {row['path'] for row in model['files']} | {'.cloud-model.json', '.cloud-model.zip'}
            entries = list(root.rglob('*'))
            if any(path.is_symlink() or not path.is_dir() and path.relative_to(root).as_posix() not in allowed
                   for path in entries):
                continue
            try:
                shutil.rmtree(root)
            except OSError:
                complete = False
    return complete

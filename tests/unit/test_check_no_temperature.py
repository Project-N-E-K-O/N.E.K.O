from __future__ import annotations

import contextlib
import importlib.util
import locale
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = ROOT / "scripts" / "check_no_temperature.py"


def _load_mod():
    spec = importlib.util.spec_from_file_location("check_no_temperature_sut", SCRIPT_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


MOD = _load_mod()


def _check(src: str) -> list[tuple[int, int, str]]:
    return MOD.check_source(src)


@pytest.mark.unit
@pytest.mark.parametrize(
    "snippet",
    [
        # 1. keyword argument, whatever the callee and value
        "create_chat_llm('m', None, None, temperature=0.2)\n",
        "create_chat_llm(temperature=None)\n",
        "ChatOpenAI(model='m', temperature=cfg.temperature)\n",
        "await llm.ainvoke(messages, temperature=0)\n",
        "self.call_llm(messages, temperature=0.1, max_tokens=6000)\n",
        "functools.partial(make_llm, temperature=1.0)\n",
        "opts = dict(temperature=0.2)\n",
        "opts.update(temperature=0.2)\n",
        "class Cfg(Base, temperature=1):\n    pass\n",
        # 2. parameter
        "def make(model, *, temperature: float = 1.0):\n    return model\n",
        "def make(model, temperature=None):\n    return model\n",
        "async def make(temperature):\n    return None\n",
        "fake = lambda session, *, temperature=0.0: session\n",
        # 3. dict key, in any request body or config
        "body = {'model': 'm', 'temperature': 0.7}\n",
        "config = {'generation_config': {'temperature': 1.1}}\n",
        "create_chat_llm(**{'temperature': 0.2})\n",
        "create_chat_llm(**{**other, 'temperature': 0.2})\n",
        "opts = base | {'temperature': 0.2}\n",
        "opts = {'temperature': v for v in [0.2]}\n",
        "def make(opts={'temperature': 0.2}):\n    return opts\n",
        "log(payload={'temperature': cfg.temperature})\n",
        # 4. subscript store
        "body['temperature'] = 0.3\n",
        "body['temperature'] += 0.1\n",
        "body['temperature']: float = 0.3\n",
        "request['generation_config']['temperature'] = slot.default\n",
        "for body['temperature'] in values:\n    pass\n",
        # 5. attribute store
        "client.temperature = 0.3\n",
        "self.temperature = temperature_value\n",
        "obj.temperature = None\n",
        # 6. class-body field (schema)
        "class Cfg(BaseModel):\n    temperature: float | None = None\n",
        "class Body(TypedDict):\n    temperature: float\n",
        "class Fake:\n    temperature = 0.5\n",
        # ... also inside blocks that still run in the class's own scope
        "class Cfg:\n    if True:\n        temperature: float = 0.2\n",
        "class Cfg:\n    if flag:\n        pass\n    else:\n        temperature = 0.2\n",
        "class Cfg:\n    try:\n        temperature = 0.2\n    except Exception:\n        pass\n",
        "class Cfg:\n    try:\n        pass\n    except Exception:\n        temperature = 0.2\n",
        "class Cfg:\n    with ctx():\n        temperature = 0.2\n",
        "class Cfg:\n    match mode:\n        case 1:\n            temperature = 0.2\n",
        "class Cfg:\n    temperature, top_p = 0.2, 0.9\n",
        "class Outer:\n    class Inner:\n        temperature = 0.2\n",
        # 7. setting the key by name
        "opts.setdefault('temperature', 0.2)\n",
        "setattr(client, 'temperature', 0.2)\n",
        "operator.setitem(body, 'temperature', 0.2)\n",
        "opts = dict.fromkeys(['temperature'], 0.2)\n",
        "opts = dict([('temperature', 0.2)])\n",
        "opts = OrderedDict([('model', 'm'), ('temperature', 0.2)])\n",
        "opts.update([('temperature', 0.2)])\n",
        # ... including pairs / keys produced by a comprehension or generator
        "opts = dict(('temperature', v) for v in [0.2])\n",
        "llm.ainvoke(messages, **dict(('temperature', v) for v in [0.2]))\n",
        "opts = dict([('temperature', v) for v in [0.2]])\n",
        "opts.update({('temperature', v) for v in [0.2]})\n",
        "opts = dict.fromkeys(['temperature' for _ in range(1)], 0.2)\n",
        "opts = dict.fromkeys(('temperature' for _ in range(1)), 0.2)\n",
        # nested inside other code
        "def outer():\n    def inner():\n        return create(temperature=0.2)\n    return inner\n",
        "def f(client: type(create_chat_llm(temperature=0.2))):\n    return client\n",
        "class C:\n    def m(self):\n        return {'temperature': 1}\n",
    ],
)
def test_flags_every_way_of_setting_temperature(snippet: str):
    assert _check(snippet), snippet


@pytest.mark.unit
@pytest.mark.parametrize(
    "snippet",
    [
        # reading, testing for and removing the field
        "assert 'temperature' not in body\n",
        "has = 'temperature' in request\n",
        "value = request.get('temperature', 0)\n",
        "value = request.pop('temperature', None)\n",
        "del body['temperature']\n",
        "del client.temperature\n",
        "value = slot.defaults.temperature\n",
        "value = getattr(client, 'temperature', None)\n",
        "ok = hasattr(client, 'temperature')\n",
        "if request.get('temperature', 0) > 1:\n    raise ValueError\n",
        # constant key lists and copying through a variable key
        "ALLOWED = {'model', 'temperature', 'top_p'}\n",
        "for key in ('temperature', 'top_p'):\n    if key in request:\n        body[key] = request[key]\n",
        "for field, maximum in (('temperature', 2), ('top_p', 1)):\n    check(field, maximum)\n",
        # pattern matching reads the key
        "match body:\n    case {'temperature': t}:\n        pass\n",
        # a bare annotation stores nothing
        "body['temperature']: float\n",
        # similar names are different fields
        "result = {'temperature_c': 28}\n",
        "def convert(temp=0.3, temperature_unit='C'):\n    return temp\n",
        "create_chat_llm('m', None, None, max_completion_tokens=100, timeout=30)\n",
        "opts.setdefault('top_p', 0.9)\n",
        # text that only mentions it
        "PROMPT = 'Set {\"temperature\": 0.3} in the request.'\n",
        "temperature = slot.defaults.temperature\n",
        # a local variable in a method is not a class field
        "class C:\n    def m(self):\n        temperature = self.slot.default\n        return temperature\n",
        "class C:\n    if flag:\n        def m(self):\n            temperature = 1\n            return temperature\n",
        # opaque forwarding is not a setting by itself
        "def wrap(**kwargs):\n    return create_chat_llm(**kwargs)\n",
        "opts = dict((k, v) for k, v in pairs)\n",
        "opts = dict([(k, 0) for k in ('top_p', 'max_tokens')])\n",
        "opts = dict.fromkeys([k for k in keys], None)\n",
    ],
)
def test_allows_reading_removing_and_unrelated_code(snippet: str):
    assert _check(snippet) == [], snippet


@pytest.mark.unit
def test_reports_line_and_column_of_each_violation():
    src = "x = 1\nbody = {\n    'temperature': 0.3,\n}\nmake(temperature=1)\n"
    found = _check(src)
    assert [(line, col) for line, col, _ in found] == [(3, 4), (5, 17)]
    assert all("temperature" in msg for _, _, msg in found)


@pytest.mark.unit
def test_deep_elif_chain_does_not_hit_recursion_error():
    lines = ["def f(n):", "    opts = {}", "    if n == 0:", "        opts = {'a': 0}"]
    for idx in range(1, 2000):
        lines.append(f"    elif n == {idx}:")
        lines.append(f"        opts = {{'a': {idx}}}")
    lines.append("    return opts")
    safe_src = "\n".join(lines) + "\n"
    assert _check(safe_src) == []

    bad_src = safe_src.replace("opts = {'a': 1999}", "opts = {'temperature': 0.2}")
    assert len(_check(bad_src)) == 1


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))
    return path


@pytest.fixture
def fake_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "repo"
    _write(root / "app" / "ok.py", "create_chat_llm('m', None, None)\n")
    monkeypatch.setattr(MOD, "REPO_ROOT", root)
    monkeypatch.setattr(MOD, "EXEMPTIONS", {})
    return root


@pytest.mark.unit
def test_default_scope_is_the_whole_repository(fake_repo: Path, capsys):
    assert MOD.DEFAULT_PATHS == ["."]
    _write(fake_repo / "brand_new_package" / "deep" / "mod.py", "make(temperature=0.2)\n")
    assert MOD.main([]) == 1
    assert "brand_new_package/deep/mod.py:1:" in capsys.readouterr().out


@pytest.mark.unit
@pytest.mark.parametrize(
    "folder", [".claude/worktrees/other", ".venv/Lib", "frontend/node_modules/pkg", "build", "dist", "x/__pycache__"]
)
def test_skips_environment_dependency_and_build_folders(fake_repo: Path, folder: str):
    _write(fake_repo / folder / "mod.py", "make(temperature=0.2)\n")
    assert MOD.main([]) == 0


@pytest.mark.unit
def test_exemption_suppresses_only_its_file_or_folder(fake_repo: Path, monkeypatch: pytest.MonkeyPatch, capsys):
    _write(fake_repo / "plugin" / "gateway" / "request.py", "request['temperature'] = slot.default\n")
    _write(fake_repo / "plugin" / "tests" / "a" / "test_x.py", "body = {'temperature': 2}\n")
    monkeypatch.setattr(
        MOD,
        "EXEMPTIONS",
        {"plugin/gateway/request.py": "reason", "plugin/tests": "reason"},
    )
    assert MOD.main([]) == 0

    _write(fake_repo / "plugin" / "gateway" / "transport.py", "send(temperature=1)\n")
    capsys.readouterr()
    assert MOD.main([]) == 1
    out = capsys.readouterr().out
    assert "plugin/gateway/transport.py:1:" in out
    assert "request.py" not in out and "test_x.py" not in out


@pytest.mark.unit
def test_stale_exemption_fails_the_whole_repository_run(fake_repo: Path, monkeypatch: pytest.MonkeyPatch, capsys):
    monkeypatch.setattr(MOD, "EXEMPTIONS", {"app/ok.py": "no longer needed"})
    assert MOD.main([]) == 1
    out = capsys.readouterr().out
    assert "app/ok.py" in out and MOD.STALE_CODE in out
    # Naming the repository root explicitly is the same whole-repository run.
    assert MOD.main(["."]) == 1
    assert MOD.main([str(fake_repo)]) == 1
    # Scanning chosen paths only cannot tell whether an exemption is stale.
    assert MOD.main(["app"]) == 0


def _git(cwd: Path, *args: str) -> None:
    # Inherited GIT_DIR / GIT_INDEX_FILE (e.g. inside a git hook) would point
    # these commands at the caller's repository instead of the temporary one.
    subprocess.run(["git", *args], cwd=cwd, env=MOD._git_env(), check=True, capture_output=True)


def _git_repo_with_ignored_plugin(root: Path) -> Path:
    _write(root / "app" / "ok.py", "create_chat_llm('m', None, None)\n")
    _write(root / ".gitignore", "plugin/plugins/local_only/\nnested_checkout/\n")
    # Installed locally but not part of the repository, like a gitignored plugin.
    _write(root / "plugin" / "plugins" / "local_only" / "main.py", "make(temperature=0.8)\n")
    _write(root / "nested_checkout" / "tool.py", "make(temperature=0.8)\n")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    return root


@pytest.mark.unit
@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_repository_scan_skips_gitignored_local_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys):
    root = _git_repo_with_ignored_plugin(tmp_path / "gitrepo")
    monkeypatch.setattr(MOD, "REPO_ROOT", root)
    monkeypatch.setattr(MOD, "EXEMPTIONS", {})

    assert MOD.main([]) == 0
    assert MOD.main(["."]) == 0

    # A new file that is not ignored is scanned before it is ever `git add`ed.
    _write(root / "app" / "new.py", "make(temperature=0.2)\n")
    capsys.readouterr()
    assert MOD.main([]) == 1
    out = capsys.readouterr().out
    assert "app/new.py:1:" in out and "local_only" not in out and "nested_checkout" not in out

    # Naming an ignored folder explicitly still scans it.
    assert MOD.main([str(root / "plugin" / "plugins" / "local_only")]) == 1


@pytest.mark.unit
@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_inherited_git_location_variables_do_not_redirect_the_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    root = _git_repo_with_ignored_plugin(tmp_path / "gitrepo")
    other = tmp_path / "other"
    _write(other / "README", "x\n")
    _git(other, "init", "-q")
    monkeypatch.setattr(MOD, "REPO_ROOT", root)
    monkeypatch.setattr(MOD, "EXEMPTIONS", {})
    # As inside a git hook of another repository.
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(other))
    monkeypatch.setenv("GIT_INDEX_FILE", str(other / ".git" / "index"))

    assert MOD.main([]) == 0


@pytest.mark.unit
@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_repository_root_with_non_ascii_path_still_uses_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # Git prints paths as UTF-8; decoding them with a Windows locale code page
    # used to mismatch the root (silent fall back to walking every folder) or
    # raise UnicodeDecodeError.
    root = _git_repo_with_ignored_plugin(tmp_path / "项目" / "张三的仓库")
    monkeypatch.setattr(MOD, "REPO_ROOT", root)
    monkeypatch.setattr(MOD, "EXEMPTIONS", {})
    real_run = subprocess.run

    # A Chinese Windows without UTF-8 mode, whatever this machine uses. Two
    # parts: (1) code asking the ``locale`` module for the encoding (e.g.
    # ``encoding=locale.getpreferredencoding(False)``) gets GBK; (2) text mode
    # without an explicit encoding picks the code page in C, which no
    # monkeypatch reaches, so the ``subprocess.run`` wrapper below forces GBK
    # for it. Reading bytes, or decoding explicitly as UTF-8, still works.
    monkeypatch.setattr(locale, "getpreferredencoding", lambda do_setlocale=True: "gbk")
    if hasattr(locale, "getencoding"):
        monkeypatch.setattr(locale, "getencoding", lambda: "gbk")

    def run_with_chinese_windows_locale(*args, **kwargs):
        if (kwargs.get("text") or kwargs.get("universal_newlines")) and not kwargs.get("encoding"):
            kwargs["encoding"] = "gbk"
        return real_run(*args, **kwargs)

    monkeypatch.setattr(MOD.subprocess, "run", run_with_chinese_windows_locale)

    assert MOD._git_listed_python_files(root) is not None
    assert MOD.main([]) == 0


@pytest.mark.unit
@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_listed_file_in_a_folder_without_permission_is_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
):
    root = _git_repo_with_ignored_plugin(tmp_path / "gitrepo")
    _write(root / "locked" / "x.py", "make(temperature=0.2)\n")
    _write(root / "app" / "bad.py", "make(temperature=0.2)\n")
    monkeypatch.setattr(MOD, "REPO_ROOT", root)
    monkeypatch.setattr(MOD, "EXEMPTIONS", {})
    real_is_file = Path.is_file

    def is_file(self: Path) -> bool:
        if "locked" in self.parts:
            raise PermissionError(13, "Permission denied", str(self))
        return real_is_file(self)

    monkeypatch.setattr(Path, "is_file", is_file)

    assert MOD.main([]) == 1
    captured = capsys.readouterr()
    assert "app/bad.py:1:" in captured.out
    assert "locked/x.py" not in captured.out
    assert "x.py: skipped" in captured.err and "Permission denied" in captured.err


@pytest.mark.unit
@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
@pytest.mark.parametrize("variable", ["GIT_LITERAL_PATHSPECS", "GIT_GLOB_PATHSPECS", "GIT_NOGLOB_PATHSPECS"])
def test_pathspec_variables_do_not_shrink_the_file_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys, variable: str
):
    root = _git_repo_with_ignored_plugin(tmp_path / "gitrepo")
    _write(root / "app" / "deep" / "new.py", "make(temperature=0.2)\n")
    monkeypatch.setattr(MOD, "REPO_ROOT", root)
    monkeypatch.setattr(MOD, "EXEMPTIONS", {})
    monkeypatch.setenv(variable, "1")

    assert MOD.main([]) == 1
    out = capsys.readouterr().out
    assert "app/deep/new.py:1:" in out and "local_only" not in out


@pytest.mark.unit
def test_explicit_paths_outside_the_repository_are_scanned(tmp_path: Path, capsys):
    outside = _write(tmp_path / "elsewhere" / "nekopower" / "runtime" / "bad.py", "make(temperature=0.2)\n")
    assert MOD.main([str(outside.parent.parent)]) == 1
    assert "bad.py:1:" in capsys.readouterr().out


@pytest.mark.unit
def test_unreadable_and_broken_files_do_not_stop_the_scan(tmp_path: Path, capsys):
    folder = tmp_path / "loop_dir"
    _write(folder / "broken.py", "def (:\n")
    # A self-referencing symlink cannot be read. Creating one needs privileges
    # on Windows; without them broken.py alone still covers the skip path.
    loop_file = folder / "a.py"
    with contextlib.suppress(OSError):
        loop_file.symlink_to(loop_file)
    _write(folder / "z.py", "make(temperature=0.2)\n")
    assert MOD.main([str(folder)]) == 1
    captured = capsys.readouterr()
    assert "z.py:1:" in captured.out
    assert "broken.py" in captured.err


@pytest.mark.unit
def test_reads_files_in_their_declared_source_encoding(tmp_path: Path, capsys):
    folder = tmp_path / "enc"
    latin1 = folder / "latin1.py"
    latin1.parent.mkdir(parents=True)
    latin1.write_bytes(b"# -*- coding: latin-1 -*-\n# caf\xe9\nmake(temperature=0.2)\n")
    assert MOD.main([str(folder)]) == 1
    assert "latin1.py:3:" in capsys.readouterr().out

    # An unknown encoding declaration is skipped with a message, not a crash.
    _write(folder / "bogus.py", "# coding: no-such-codec\nx = 1\n")
    assert MOD.main([str(folder)]) == 1
    captured = capsys.readouterr()
    assert "latin1.py:3:" in captured.out
    assert "bogus.py" in captured.err


@pytest.mark.unit
def test_every_exemption_has_a_reason_and_exists():
    assert MOD.EXEMPTIONS, "the plugin model gateway still needs its exemption"
    for key, reason in MOD.EXEMPTIONS.items():
        assert (ROOT / key).exists(), key
        # Whether a reason is good enough is for review; it must not be blank.
        assert isinstance(reason, str) and reason.strip(), key


@pytest.mark.unit
def test_live_repo_passes_check_no_temperature():
    assert MOD.main([]) == 0

from __future__ import annotations

import ast
import importlib.util
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
    tree = ast.parse(src)
    return MOD.check_file(Path("dummy.py"), tree)


@pytest.mark.unit
@pytest.mark.parametrize(
    "snippet",
    [
        "create_chat_llm(temperature=0.2)\n",
        "create_chat_llm(temperature=None)\n",
        "create_chat_llm(**{'temperature': 0.2})\n",
        "create_chat_llm(**{**other, 'temperature': 0.2})\n",
        "create_chat_llm(**{**{'temperature': 0.2}})\n",
        "create_chat_llm(**({'temperature': 0.2} if cond else {}))\n",
        "create_chat_llm(**(extra or {'temperature': 0.2}))\n",
        "create_chat_llm(**dict([('temperature', 0.2)]))\n",
        "create_chat_llm(**dict({'temperature': 0.2}))\n",
        "create_chat_llm(**dict.fromkeys(['temperature'], 0.2))\n",
        "create_chat_llm(**(base | {'temperature': 0.2}))\n",
        "create_chat_llm(**{'temperature': v for v in [0.2]})\n",
        "def f():\n    kw = {'temperature': 0.2}\n    return create_chat_llm(**kw)\n",
        "def f():\n    kw = {}\n    kw['temperature'] = 0.2\n    return create_chat_llm(**kw)\n",
        "def f():\n    kw = {}\n    kw.setdefault('temperature', 0.2)\n    return create_chat_llm(**kw)\n",
        "def f():\n    kw = {}\n    kw.update({'temperature': 0.2})\n    return create_chat_llm(**kw)\n",
        "def f():\n    kw = {}\n    kw |= {'temperature': 0.2}\n    return create_chat_llm(**kw)\n",
        "def f(flag):\n    if flag:\n        kw = {'temperature': 0.2}\n    else:\n        kw = {}\n    return create_chat_llm(**kw)\n",
        "def f(items):\n    kw = {}\n    for _ in items:\n        create_chat_llm(**kw)\n        kw['temperature'] = 0.2\n",
        "MOD_KW = {'temperature': 0.2}\ndef f():\n    return create_chat_llm(**MOD_KW)\n",
    ],
)
def test_flags_direct_and_unpacked_temperature_kwargs(snippet: str):
    """B33-004: static literals, dict constructors, and local dict variables carrying temperature are flagged."""
    violations = _check(snippet)
    assert len(violations) == 1, f"expected 1 violation for:\n{snippet}\ngot: {violations}"


@pytest.mark.unit
def test_flags_unpacked_dict_kwarg_call():
    """B33-004: **dict(temperature=...) flags both the inner kwarg and outer unpack."""
    violations = _check("create_chat_llm(**dict(temperature=0.2))\n")
    assert len(violations) == 2


@pytest.mark.unit
@pytest.mark.parametrize(
    "snippet",
    [
        "def wrap(**kwargs):\n    return create_chat_llm(**kwargs)\n",
        "def f():\n    kw = {'max_tokens': 100}\n    return create_chat_llm(**kw)\n",
        "def f():\n    kw = {'temperature': 0.2}\n    kw = {}\n    return create_chat_llm(**kw)\n",
        "def f():\n    kw = {'temperature': 0.2}\n    kw.clear()\n    return create_chat_llm(**kw)\n",
    ],
)
def test_allows_legitimate_forwarding_and_cleared_dicts(snippet: str):
    """B33-004: opaque **kwargs forwarding and safe/cleared local dicts must not false-positive."""
    assert _check(snippet) == []


@pytest.mark.unit
def test_split_llm_client_package_files_are_excluded_and_ancestor_build_dir_not_excluded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """B33-004 + _is_excluded: split llm_client files stay excluded; ancestor /build/ does not disable scan."""
    for rel in (
        "utils/llm_client/factory.py",
        "utils/llm_client/openai_client.py",
        "utils/llm_client/anthropic_client.py",
    ):
        assert rel in MOD.EXCLUDE_FILES

    fake_root = tmp_path / "build" / "NEKO"
    bad = fake_root / "memory" / "bad.py"
    bad.parent.mkdir(parents=True)
    bad.write_text("create_chat_llm(**{'temperature': 0.2})\n", encoding="utf-8")
    ignored = fake_root / "dist" / "ignored.py"
    ignored.parent.mkdir(parents=True)
    ignored.write_text("create_chat_llm(temperature=0.2)\n", encoding="utf-8")

    monkeypatch.setattr(MOD, "REPO_ROOT", fake_root)
    assert MOD._is_excluded(bad) is False
    assert MOD._is_excluded(ignored) is True
    assert MOD.main(["memory"]) == 1


@pytest.mark.unit
def test_live_repo_passes_check_no_temperature():
    files = list(MOD._iter_python_files([MOD.REPO_ROOT / p for p in MOD.DEFAULT_PATHS]))
    assert len(files) > 20
    assert MOD.main([]) == 0

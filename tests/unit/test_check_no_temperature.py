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
        "def f(items):\n    a = {}\n    b = {}\n    c = {}\n    for _ in items:\n        create_chat_llm(**a)\n        a = b\n        b = c\n        c = {'temperature': 0.2}\n",
        "async def f(items):\n    a = {}\n    b = {}\n    c = {}\n    async for _ in items:\n        create_chat_llm(**a)\n        a = b\n        b = c\n        c = {'temperature': 0.2}\n",
        "def f(cond):\n    a = {}\n    b = {}\n    c = {}\n    while cond:\n        create_chat_llm(**a)\n        a = b\n        b = c\n        c = {'temperature': 0.2}\n",
        "def f():\n    d = {'temperature': 0.2}\n    d2 = d.copy()\n    return create_chat_llm(**d2)\n",
        "create_chat_llm(**{'temperature': 0.2}.copy())\n",
        "class C:\n    opts = {'temperature': 0.2}\n    client = create_chat_llm(**opts)\n",
        "def f(**kwargs):\n    kwargs['temperature'] = 0.2\n    return create_chat_llm(**kwargs)\n",
        "MOD_KW = {'temperature': 0.2}\ndef f():\n    return create_chat_llm(**MOD_KW)\n",
        "def f():\n    opts = {'temperature': 0.2}\n    opts.pop('other', None)\n    return create_chat_llm(**opts)\n",
        "def f():\n    opts = {'temperature': 0.2}\n    del opts['other']\n    return create_chat_llm(**opts)\n",
        "def make(opts={'temperature': 0.2}):\n    return create_chat_llm(**opts)\n",
        "def make(*, opts={'temperature': 0.2}):\n    return create_chat_llm(**opts)\n",
        "fn = lambda opts={'temperature': 0.2}: create_chat_llm(**opts)\n",
        "def f(client: type(create_chat_llm(temperature=0.2))):\n    return client\n",
        "def f() -> type(create_chat_llm(temperature=0.2)):\n    pass\n",
        "def f():\n    opts = {}\n    opts['temperature']: float = 0.2\n    return create_chat_llm(**opts)\n",
        "def f():\n    opts = {}\n    (opts := {'temperature': 0.2})\n    return create_chat_llm(**opts)\n",
        "opts = {}\ncreate_chat_llm(**{'max_tokens': (opts := {'temperature': 0.2}) and 100}, **opts)\n",
        "def f(flag):\n    opts = {'temperature': 0.2}\n    if flag:\n        opts.pop('temperature', None)\n        return None\n    return create_chat_llm(**opts)\n",
        "def f(items):\n    opts = {}\n    for item in items:\n        if item:\n            opts = {'temperature': 0.2}\n            break\n    create_chat_llm(**opts)\n",
        "def f(items):\n    opts = {}\n    for item in items:\n        if item:\n            opts = {'temperature': 0.2}\n            break\n    else:\n        opts = {}\n    create_chat_llm(**opts)\n",
        "def f(items):\n    opts = {}\n    for item in items:\n        create_chat_llm(**opts)\n        if item:\n            opts = {'temperature': 0.2}\n            continue\n",
        "def g(items, flag):\n    for _ in items:\n        opts = {'temperature': 0.2}\n        if flag:\n            continue\n        opts.clear()\n    create_chat_llm(**opts)\n",
        "def f():\n    opts = {}\n    try:\n        work()\n    except ValueError:\n        opts = {'temperature': 0.2}\n        raise\n    finally:\n        create_chat_llm(**opts)\n",
        "def f():\n    opts = {}\n    try:\n        work()\n    except ValueError:\n        opts = {'temperature': 0.2}\n        raise\n    else:\n        opts = {}\n    finally:\n        create_chat_llm(**opts)\n",
        "def g(flag):\n    opts = {}\n    try:\n        if flag:\n            opts = {'temperature': 0.2}\n            return 1\n    finally:\n        create_chat_llm(**opts)\n",
        "def h(flag):\n    opts = {}\n    try:\n        try:\n            if flag:\n                opts = {'temperature': 0.2}\n                return\n        finally:\n            pass\n    finally:\n        create_chat_llm(**opts)\n",
        "def f(items):\n    opts = {}\n    for item in items:\n        try:\n            if item:\n                opts = {'temperature': 0.2}\n                break\n        finally:\n            pass\n    create_chat_llm(**opts)\n",
        "def f(items):\n    opts = {}\n    for _ in items:\n        try:\n            work()\n        except ValueError:\n            opts = {'temperature': 0.2}\n            raise\n        finally:\n            break\n    create_chat_llm(**opts)\n",
        "def f():\n    opts, meta = {'temperature': 0.2}, None\n    create_chat_llm(**opts)\n",
        "def f():\n    a = {'temperature': 0.2}\n    b = {}\n    a, b = b, a\n    create_chat_llm(**b)\n",
        "def f():\n    opts, *rest = {'temperature': 0.2}, 1, 2\n    create_chat_llm(**opts)\n",
        "def f():\n    opts = {}\n    opts['temperature'], meta = 0.2, None\n    create_chat_llm(**opts)\n",
        "def f():\n    opts = {}\n    try:\n        opts = {'temperature': 0.2}\n        raise KeyError\n    except ValueError:\n        opts.clear()\n    finally:\n        create_chat_llm(**opts)\n",
        "def f():\n    opts = {}\n    try:\n        opts = {'temperature': 0.2}\n        raise ValueError\n    except ValueError:\n        raise\n    finally:\n        create_chat_llm(**opts)\n",
        "def f(flag):\n    opts = {}\n    if flag:\n        with contextlib.suppress(ValueError):\n            opts = {'temperature': 0.2}\n            raise ValueError\n    create_chat_llm(**opts)\n",
        "def f(flag):\n    opts = {}\n    if flag:\n        with contextlib.suppress(ValueError):\n            raise ValueError\n        opts = {'temperature': 0.2}\n    create_chat_llm(**opts)\n",
        "def f(flag):\n    opts = {}\n    if flag:\n        with suppress(Exception):\n            opts = {'temperature': 0.2}\n            raise ValueError\n    create_chat_llm(**opts)\n",
        "def f():\n    head, *[opts, meta] = None, {'temperature': 0.2}, None\n    create_chat_llm(**opts)\n",
        "def f():\n    opts = {'temperature': 0.2}\n    while ((saved := opts.copy()), opts.clear())[0]:\n        break\n    else:\n        saved = {}\n    create_chat_llm(**saved)\n",
        "def f(flag):\n    opts = {}\n    if flag:\n        try:\n            raise ValueError\n        except ValueError:\n            with contextlib.suppress(ValueError):\n                raise\n            opts = {'temperature': 0.2}\n    create_chat_llm(**opts)\n",
        "def f():\n    opts = {}\n    try:\n        opts = {'temperature': 0.2}\n        raise ExceptionGroup('g', [KeyError()])\n    except* ValueError:\n        opts.clear()\n    finally:\n        create_chat_llm(**opts)\n",
        "def f(excs):\n    opts = {}\n    try:\n        opts = {'temperature': 0.2}\n        raise ExceptionGroup('g', excs)\n    except* ValueError:\n        opts.clear()\n    finally:\n        create_chat_llm(**opts)\n",
        "def f():\n    opts = {}\n    try:\n        opts = {'temperature': 0.2}\n        raise ExceptionGroup('g', [ValueError(), KeyError()])\n    except* ValueError:\n        pass\n    finally:\n        create_chat_llm(**opts)\n",
        "def f():\n    try:\n        raise ExceptionGroup('g', [ValueError(), KeyError()])\n    except* ValueError:\n        opts = {'temperature': 0.2}\n    except* KeyError:\n        create_chat_llm(**opts)\n",
        "class Stop(BaseException):\n    pass\n\ndef f():\n    opts = {}\n    try:\n        opts = {'temperature': 0.2}\n        raise Stop()\n    except Exception:\n        opts.clear()\n    finally:\n        create_chat_llm(**opts)\n",
        "class Stop(BaseException):\n    pass\n\nclass SubStop(Stop):\n    pass\n\ndef f():\n    opts = {}\n    try:\n        opts = {'temperature': 0.2}\n        raise SubStop()\n    except Exception:\n        opts.clear()\n    finally:\n        create_chat_llm(**opts)\n",
        "def f():\n    opts = {}\n    try:\n        opts = {'temperature': 0.2}\n        raise asyncio.CancelledError\n    except Exception:\n        opts.clear()\n    finally:\n        create_chat_llm(**opts)\n",
        "def f():\n    opts = {}\n    try:\n        opts = {'temperature': 0.2}\n        raise KeyboardInterrupt\n    except Exception:\n        opts.clear()\n    finally:\n        create_chat_llm(**opts)\n",
        "def f(flag):\n    opts = {}\n    while True:\n        if flag:\n            opts = {'temperature': 0.2}\n            break\n    create_chat_llm(**opts)\n",
        "from asyncio import CancelledError\n\ndef f():\n    opts = {}\n    try:\n        opts = {'temperature': 0.2}\n        raise CancelledError()\n    except Exception:\n        opts.clear()\n    finally:\n        create_chat_llm(**opts)\n",
        "import asyncio as aio\n\ndef f():\n    opts = {}\n    try:\n        opts = {'temperature': 0.2}\n        raise aio.CancelledError()\n    except Exception:\n        opts.clear()\n    finally:\n        create_chat_llm(**opts)\n",
        "import asyncio\nfrom concurrent.futures import CancelledError\n\ndef f():\n    opts = {}\n    try:\n        opts = {'temperature': 0.2}\n        raise asyncio.CancelledError()\n    except CancelledError:\n        opts.clear()\n    finally:\n        create_chat_llm(**opts)\n",
        "def f():\n    opts = {'temperature': 0.2}\n    try:\n        work()\n        raise ExceptionGroup('g', [ValueError(), KeyError()])\n    except* ValueError:\n        opts.clear()\n    except* KeyError:\n        create_chat_llm(**opts)\n",
        "def f(flag):\n    opts = {'temperature': 0.2}\n    try:\n        if flag:\n            raise ExceptionGroup('g', [ValueError(), KeyError()])\n        else:\n            raise ExceptionGroup('g', [KeyError()])\n    except* ValueError:\n        opts.clear()\n    except* KeyError:\n        create_chat_llm(**opts)\n",
        "def f(flag):\n    opts = {'temperature': 0.2}\n    try:\n        raise ExceptionGroup('g', [ValueError(), KeyError()])\n    except* ValueError:\n        if flag:\n            raise RuntimeError()\n        opts.clear()\n    except* KeyError:\n        create_chat_llm(**opts)\n",
        "def f():\n    opts = {}\n    try:\n        raise ExceptionGroup('g', [ValueError(), KeyError()])\n    except* ValueError:\n        opts = {'temperature': 0.2}\n        raise RuntimeError()\n    except* KeyError:\n        create_chat_llm(**opts)\n",
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
        "kwargs = {'temperature': 0.2}\ndef forward(**kwargs):\n    return create_chat_llm(**kwargs)\n",
        "opts = {'temperature': 0.2}\ndef forward(opts, /, a, *args, kw=None, **extra):\n    return create_chat_llm(**opts)\n",
        "opts = {'temperature': 0.2}\ndef forward(*, opts):\n    return create_chat_llm(**opts)\n",
        "opts = {'temperature': 0.2}\ndef forward(*opts):\n    return create_chat_llm(**opts)\n",
        "kwargs = {'temperature': 0.2}\nfn = lambda **kwargs: create_chat_llm(**kwargs)\n",
        "kwargs = {'temperature': 0.2}\ndef f(items):\n    return [create_chat_llm(**kwargs) for kwargs in items]\n",
        "kwargs = {'temperature': 0.2}\ndef f(items):\n    return {create_chat_llm(**kwargs) for kwargs in items}\n",
        "kwargs = {'temperature': 0.2}\ndef f(items):\n    return list(create_chat_llm(**kwargs) for kwargs in items)\n",
        "kwargs = {'temperature': 0.2}\ndef f(items):\n    return {i: create_chat_llm(**kwargs) for i, kwargs in items}\n",
        "kw = {'temperature': 0.2}\ndef f(items):\n    for kw in items:\n        create_chat_llm(**kw)\n",
        "kw = {'temperature': 0.2}\ndef f(items):\n    for _, kw in items:\n        create_chat_llm(**kw)\n",
        "class C:\n    kwargs = {'temperature': 0.2}\n    def method(self, **kwargs):\n        return create_chat_llm(**kwargs)\n",
        "class C:\n    opts = {'temperature': 0.2}\n    def method(self):\n        return create_chat_llm(**opts)\n",
        "def f():\n    kw = {'max_tokens': 100}\n    return create_chat_llm(**kw)\n",
        "def f():\n    kw = {'temperature': 0.2}\n    kw = {}\n    return create_chat_llm(**kw)\n",
        "def f():\n    kw = {'temperature': 0.2}\n    kw.clear()\n    return create_chat_llm(**kw)\n",
        "def f():\n    d = {'temperature': 0.2}\n    d2 = d.copy()\n    d2.clear()\n    return create_chat_llm(**d2)\n",
        "def f():\n    opts = {'temperature': 0.2}\n    opts.pop('temperature')\n    return create_chat_llm(**opts)\n",
        "def f():\n    opts = {'temperature': 0.2}\n    opts.pop('temperature', None)\n    return create_chat_llm(**opts)\n",
        "def f():\n    opts = {'temperature': 0.2}\n    t = opts.pop('temperature', None)\n    return create_chat_llm(**opts)\n",
        "def f():\n    opts = {'temperature': 0.2}\n    del opts['temperature']\n    return create_chat_llm(**opts)\n",
        "def make(opts=None):\n    return create_chat_llm(**(opts or {}))\n",
        "def make(opts={}):\n    return create_chat_llm(**opts)\n",
        "def f():\n    opts = {}\n    opts['temperature']: float\n    return create_chat_llm(**opts)\n",
        "def f():\n    try:\n        opts = {'temperature': 0.2}\n    except Exception:\n        opts = {}\n    else:\n        opts.clear()\n    return create_chat_llm(**opts)\n",
        "def f(flag):\n    opts = {'model': 'x', 'temperature': 0.2}\n    if flag:\n        opts.pop('temperature', None)\n    else:\n        raise ValueError\n    return create_chat_llm(**opts)\n",
        "def g():\n    opts = {'temperature': 0.2}\n    try:\n        opts.pop('temperature')\n    except KeyError:\n        raise\n    return create_chat_llm(**opts)\n",
        "def h(items, cond):\n    opts = {'temperature': 0.2}\n    for _ in items:\n        if cond:\n            opts.clear()\n        else:\n            continue\n        create_chat_llm(**opts)\n",
        "def k(flag):\n    opts = {}\n    if flag:\n        opts = {'temperature': 0.2}\n        return None\n    return create_chat_llm(**opts)\n",
        "def f(items):\n    opts = {}\n    for item in items:\n        if item:\n            opts = {'temperature': 0.2}\n            break\n        create_chat_llm(**opts)\n",
        "def f(items):\n    opts = {}\n    for item in items:\n        if item:\n            opts = {'temperature': 0.2}\n            break\n    else:\n        create_chat_llm(**opts)\n",
        "def f():\n    opts = {}\n    try:\n        work()\n    except ValueError:\n        opts = {'temperature': 0.2}\n        raise\n    finally:\n        log()\n    create_chat_llm(**opts)\n",
        "def h(flag):\n    opts = {}\n    try:\n        try:\n            if flag:\n                opts = {'temperature': 0.2}\n                return\n        finally:\n            opts.clear()\n    finally:\n        create_chat_llm(**opts)\n",
        "def outer():\n    opts = {}\n    try:\n        def inner():\n            opts = {'temperature': 0.2}\n            return opts\n    finally:\n        create_chat_llm(**opts)\n",
        "def f():\n    opts = {'temperature': 0.2}\n    opts, meta = {}, None\n    create_chat_llm(**opts)\n",
        "def f():\n    opts = {'temperature': 0.2}\n    opts, meta = get_pair()\n    create_chat_llm(**opts)\n",
        "def f():\n    opts = {'temperature': 0.2}\n    [opts, _], meta = [{}, 1], None\n    create_chat_llm(**opts)\n",
        "def f():\n    a = {'temperature': 0.2}\n    b = {}\n    a, b = b, a\n    create_chat_llm(**a)\n",
        "def f():\n    opts = {'temperature': 0.2}\n    a, *opts = 1, 2, 3\n    create_chat_llm(**opts)\n",
        "def f():\n    opts = {'temperature': 0.2}\n    opts, *rest = {}, 1, 2\n    create_chat_llm(**opts)\n",
        "def f():\n    opts = {'temperature': 0.2}\n    with ctx() as opts:\n        create_chat_llm(**opts)\n",
        "def f():\n    opts = {'temperature': 0.2}\n    del (opts, meta)\n    create_chat_llm(**opts)\n",
        "def f():\n    opts = {}\n    try:\n        opts = {'temperature': 0.2}\n        raise ValueError\n    except ValueError:\n        opts.clear()\n    finally:\n        create_chat_llm(**opts)\n",
        "def f():\n    opts = {}\n    try:\n        opts['temperature'] = 0.2\n        raise ValueError\n    except:\n        opts = {}\n    finally:\n        create_chat_llm(**opts)\n",
        "def f():\n    opts = {}\n    try:\n        opts = {'temperature': 0.2}\n        raise ValueError\n    except ValueError:\n        opts.clear()\n        raise\n    finally:\n        create_chat_llm(**opts)\n",
        "def f(flag):\n    opts = {}\n    if flag:\n        with contextlib.suppress(ValueError):\n            opts = {'temperature': 0.2}\n            raise KeyError\n    create_chat_llm(**opts)\n",
        "def f(flag):\n    opts = {}\n    if flag:\n        with contextlib.suppress(ValueError):\n            opts = {'temperature': 0.2}\n            return\n    create_chat_llm(**opts)\n",
        "def f():\n    opts = {'temperature': 0.2}\n    head, *[opts, meta] = None, {}, None\n    create_chat_llm(**opts)\n",
        "def f():\n    head, *rest = None, {'temperature': 0.2}\n    create_chat_llm(**rest)\n",
        "def f(items):\n    opts = {}\n    for _ in items:\n        try:\n            break\n        except ValueError:\n            pass\n        else:\n            opts = {'temperature': 0.2}\n            break\n    create_chat_llm(**opts)\n",
        "def f(start):\n    opts = {}\n    while opts.pop('temperature', start):\n        opts['temperature'] = 0\n    create_chat_llm(**opts)\n",
        "class MyErr(Exception):\n    pass\n\ndef f():\n    opts = {'temperature': 1}\n    try:\n        try:\n            raise MyErr()\n        except Exception:\n            pass\n        opts.pop('temperature')\n    finally:\n        create_chat_llm(**opts)\n",
        "def f():\n    opts = {'temperature': 1}\n    try:\n        try:\n            raise httpx.HTTPError('x')\n        except Exception:\n            pass\n        opts.pop('temperature')\n    finally:\n        create_chat_llm(**opts)\n",
        "def f():\n    opts = {}\n    try:\n        opts = {'temperature': 0.2}\n        raise ValueError\n    except* ValueError:\n        opts.clear()\n    finally:\n        create_chat_llm(**opts)\n",
        "def f():\n    opts = {'temperature': 1}\n    try:\n        try:\n            raise ValueError\n        except* ValueError:\n            pass\n        opts = {}\n    finally:\n        create_chat_llm(**opts)\n",
        "def f(flag):\n    opts = {}\n    if flag:\n        try:\n            raise ValueError\n        except ValueError:\n            with contextlib.suppress(KeyError):\n                raise\n            opts = {'temperature': 0.2}\n    create_chat_llm(**opts)\n",
        "def f():\n    opts = {'temperature': 0.2}\n    while True:\n        opts.pop('temperature')\n        break\n    create_chat_llm(**opts)\n",
        "def f():\n    opts = {}\n    try:\n        opts = {'temperature': 0.2}\n        raise ExceptionGroup('g', [ValueError(), KeyError()])\n    except* ValueError:\n        opts.clear()\n    finally:\n        create_chat_llm(**opts)\n",
        "class Stop(BaseException):\n    pass\n\nclass SubStop(Stop):\n    pass\n\ndef f():\n    opts = {}\n    try:\n        opts = {'temperature': 0.2}\n        raise SubStop()\n    except Stop:\n        opts.clear()\n    finally:\n        create_chat_llm(**opts)\n",
        "opts = {'temperature': 0.2}\ndef f():\n    from safe_config import opts\n    return create_chat_llm(**opts)\n",
        "opts = {'temperature': 0.2}\ndef f():\n    from safe_config import default_opts as opts\n    return create_chat_llm(**opts)\n",
        "opts = {'temperature': 0.2}\ndef f():\n    import opts\n    return create_chat_llm(**opts)\n",
        "opts = {'temperature': 0.2}\ndef f():\n    import pkg.sub as opts\n    return create_chat_llm(**opts)\n",
        "import concurrent.futures\n\ndef f():\n    opts = {}\n    try:\n        opts = {'temperature': 0.2}\n        raise concurrent.futures.CancelledError()\n    except Exception:\n        opts.clear()\n    finally:\n        create_chat_llm(**opts)\n",
        "from concurrent.futures import CancelledError\n\ndef f():\n    opts = {}\n    try:\n        opts = {'temperature': 0.2}\n        raise CancelledError()\n    except Exception:\n        opts.clear()\n    finally:\n        create_chat_llm(**opts)\n",
        "from concurrent import futures\n\ndef f():\n    opts = {}\n    try:\n        opts = {'temperature': 0.2}\n        raise futures.CancelledError()\n    except Exception:\n        opts.clear()\n    finally:\n        create_chat_llm(**opts)\n",
        "import concurrent.futures as cf\n\ndef f():\n    opts = {}\n    try:\n        opts = {'temperature': 0.2}\n        raise cf.CancelledError()\n    except Exception:\n        opts.clear()\n    finally:\n        create_chat_llm(**opts)\n",
        "def f():\n    opts = {'temperature': 0.2}\n    try:\n        raise ExceptionGroup('g', [ValueError(), KeyError()])\n    except* ValueError:\n        opts.clear()\n    except* KeyError:\n        create_chat_llm(**opts)\n",
        "def f():\n    opts = {}\n    try:\n        raise ExceptionGroup('g', [ValueError(), KeyError(), TypeError()])\n    except* ValueError:\n        opts['temperature'] = 0.2\n    except* KeyError:\n        opts.clear()\n    except* TypeError:\n        create_chat_llm(**opts)\n",
        "def f():\n    opts = {'temperature': 0.2}\n    try:\n        raise ExceptionGroup('g', [ValueError(), KeyError()])\n    except* ValueError:\n        opts.clear()\n    except* KeyError:\n        pass\n    create_chat_llm(**opts)\n",
        "def f():\n    opts = {'temperature': 0.2}\n    try:\n        raise ExceptionGroup('g', [ValueError(), KeyError()])\n    except* ValueError:\n        opts.clear()\n        raise RuntimeError()\n    except* KeyError:\n        create_chat_llm(**opts)\n",
        "def f():\n    opts = {'temperature': 0.2}\n    try:\n        raise ExceptionGroup('outer', [ExceptionGroup('inner', [ValueError(), KeyError()])])\n    except* ValueError:\n        opts.clear()\n    except* KeyError:\n        create_chat_llm(**opts)\n",
    ],
)
def test_allows_legitimate_forwarding_and_cleared_dicts(snippet: str):
    """B33-004: opaque **kwargs forwarding, shadowed targets, and safe/cleared local dicts must not false-positive."""
    assert _check(snippet) == []


@pytest.mark.unit
def test_exclude_files_behavior_and_ancestor_build_dir_not_excluded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
):
    """B33-004 + _is_excluded: retained EXCLUDE_FILES skip via _iter_python_files; ancestor /build/ and '..' paths still scan."""
    for rel in ("scripts/check_no_temperature.py", "utils/llm_client.py"):
        assert rel in MOD.EXCLUDE_FILES
    for removed in (
        "utils/llm_client/factory.py",
        "utils/llm_client/openai_client.py",
        "utils/llm_client/anthropic_client.py",
    ):
        assert removed not in MOD.EXCLUDE_FILES

    fake_root = tmp_path / "build" / "NEKO"
    (fake_root / "build").mkdir(parents=True)
    for rel in (
        "utils/llm_client.py",
        "scripts/check_no_temperature.py",
        "utils/other.py",
    ):
        target = fake_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("f(temperature=0.2)\n", encoding="utf-8")

    bad = fake_root / "memory" / "bad.py"
    bad.parent.mkdir(parents=True)
    bad.write_text("create_chat_llm(**{'temperature': 0.2})\n", encoding="utf-8")
    ignored = fake_root / "dist" / "ignored.py"
    ignored.parent.mkdir(parents=True)
    ignored.write_text("create_chat_llm(temperature=0.2)\n", encoding="utf-8")

    monkeypatch.setattr(MOD, "REPO_ROOT", fake_root)
    iterated = list(
        MOD._iter_python_files([fake_root / "utils", fake_root / "scripts"])
    )
    assert iterated == [fake_root / "utils" / "other.py"]

    assert MOD._is_excluded(bad) is False
    assert MOD._is_excluded(ignored) is True
    assert MOD.main(["memory"]) == 1
    assert MOD.main(["build/../memory"]) == 1

    loop_dir = tmp_path / "loop_dir"
    loop_dir.mkdir()
    loop_file = loop_dir / "a.py"
    try:
        loop_file.symlink_to(loop_file)
    except OSError:
        pytest.skip("symlinks unavailable in this environment")
    (loop_dir / "z.py").write_text("create_chat_llm(temperature=0.2)\n", encoding="utf-8")
    capsys.readouterr()
    assert MOD.main([str(loop_dir)]) == 1
    assert "z.py" in capsys.readouterr().out


@pytest.mark.unit
def test_deep_elif_chain_does_not_hit_recursion_error():
    """Iterative elif traversal handles deep chains without hitting RecursionError."""
    lines = ["def f(n):", "    opts = {}", "    if n == 0:", "        opts = {'a': 0}"]
    for idx in range(1, 400):
        lines.append(f"    elif n == {idx}:")
        lines.append(f"        opts = {{'a': {idx}}}")
    lines.append("    create_chat_llm(**opts)")
    safe_src = "\n".join(lines) + "\n"
    assert _check(safe_src) == []

    bad_src = safe_src.replace("opts = {'a': 399}", "opts = {'temperature': 0.2}")
    assert len(_check(bad_src)) == 1


@pytest.mark.unit
def test_live_repo_passes_check_no_temperature():
    files = list(MOD._iter_python_files([MOD.REPO_ROOT / p for p in MOD.DEFAULT_PATHS]))
    assert len(files) > 20
    assert MOD.main([]) == 0

# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
N.E.K.O. unified launcher
Starts all servers, waits until they are ready, then starts the main program and monitors its state
"""
from __future__ import annotations

import sys
import os

# Preserve the historical root launcher.py path after moving this code.
_IMPLEMENTATION_FILE = __file__
__file__ = os.path.abspath(os.path.join(os.path.dirname(_IMPLEMENTATION_FILE), '..', 'launcher.py'))

def _configure_stdio_utf8() -> None:
    """Normalize stdio encoding when running the launcher on Windows.

    Prefer stream.reconfigure (keeps the stream object); fall back to swapping in a
    TextIOWrapper on failure. Keeping the original object preserves compatibility
    with pytest capture / IDE consoles / other embedded hosts — replacing sys.stdout
    would break those upstream redirectors.
    """
    if sys.platform != 'win32':
        return

    for name in ('stdout', 'stderr'):
        stream = getattr(sys, name, None)
        if stream is None:
            continue
        try:
            reconfigure = getattr(stream, 'reconfigure', None)
            if callable(reconfigure):
                reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass


# Immediately reconfigure at module level so even importing launcher / launcher_core.runtime
# as modules, e.g. ``from launcher_core import runtime`` in tests,
# prevents Chinese-log crashes on Windows. stream.reconfigure is idempotent;
# repeating it in _bootstrap_launcher_runtime is merely a no-op.
_configure_stdio_utf8()


# Detect packaged execution: PyInstaller sets sys.frozen, Nuitka __compiled__.
IS_FROZEN = getattr(sys, 'frozen', False) or '__compiled__' in globals()


def _ensure_utf8_filesystem_encoding() -> None:
    """Restart once with PYTHONUTF8=1 when Linux fs encoding is not UTF-8.

    Embedded Python builds in AppImage or minimal Linux runtimes can fall back
    to ``ascii`` under a C/POSIX locale when C.UTF-8 is unavailable and PEP 538
    locale coercion fails. Any non-ASCII path then raises UnicodeEncodeError
    during calls such as os.makedirs or open, preventing the backend from
    starting even if the host shell has a UTF-8 LANG that was not propagated.

    Filesystem encoding is fixed at interpreter startup, so the only reliable
    repair is setting PYTHONUTF8=1 (PEP 540 UTF-8 Mode) and execv-restarting
    the current process. execv preserves the PID for Electron process tracking,
    and the environment is inherited by later server subprocesses. Windows
    already defaults to a UTF-8 filesystem encoding, so it is skipped.
    """
    if sys.platform == 'win32':
        return
    enc = (sys.getfilesystemencoding() or '').lower().replace('-', '')
    if enc == 'utf8':
        return
    # Already restarted once: proceed even if still non-UTF-8, e.g. PYTHONUTF8 ignored;
    # never execv twice and risk endless restarts.
    if os.environ.get('_NEKO_FS_UTF8_REEXEC') == '1':
        return
    os.environ['PYTHONUTF8'] = '1'
    os.environ['_NEKO_FS_UTF8_REEXEC'] = '1'
    if IS_FROZEN:
        argv = [sys.executable, *sys.argv[1:]]
    else:
        argv = [sys.executable, os.path.abspath(__file__), *sys.argv[1:]]
    try:
        sys.stderr.write(
            f'[launcher] filesystem encoding is {enc!r}; '
            're-exec with PYTHONUTF8=1 to support non-ASCII paths\n'
        )
    except Exception:
        # stderr may be closed or unwritable in embedded launchers; losing this
        # diagnostic is harmless, and the re-exec attempt below is still useful.
        pass
    try:
        os.execv(argv[0], argv)
    except Exception:
        # Rare execv failures also proceed: this process remains ASCII, but PYTHONUTF8 stays
        # in os.environ, letting future Popen children use UTF-8. Better than refusing startup.
        pass


# Reexec only when running as entry point; skip test imports where __name__ !=
# '__main__', or importing under ASCII filesystems would replace pytest through execv.
# __name__ is known before module execution, so check before any Chinese-path operations.
if __name__ == '__main__':
    _ensure_utf8_filesystem_encoding()


# Handle PyInstaller/Nuitka packaged paths.
if IS_FROZEN:
    # Running packaged.
    if hasattr(sys, '_MEIPASS'):
        # PyInstaller
        bundle_dir = sys._MEIPASS
    else:
        # Nuitka or another packager.
        bundle_dir = os.path.dirname(os.path.abspath(__file__))
    # tiktoken encodings (e.g. o200k_base) load merge tables from TIKTOKEN_CACHE_DIR;
    # build_nuitka.bat pre-fetches into data/tiktoken_cache for offline use.
    _tiktoken_cache = os.path.join(bundle_dir, "data", "tiktoken_cache")
    if os.path.isdir(_tiktoken_cache):
        os.environ.setdefault("TIKTOKEN_CACHE_DIR", _tiktoken_cache)
else:
    # Running in ordinary Python.
    bundle_dir = os.path.dirname(os.path.abspath(__file__))


def _pin_project_root_first() -> None:
    """Put the bundle / repository root first on ``sys.path``.

    ``plugin/`` doubles as an import root for user-plugin processes and holds
    a sibling ``config`` package, and plugin hosts insert plugin ``vendor/``
    dirs at index 0. Without this the launcher's (or a spawned child's)
    top-level ``config`` / ``utils`` imports could resolve to one of those.
    """
    root = os.path.abspath(bundle_dir)
    while root in sys.path:
        sys.path.remove(root)
    sys.path.insert(0, root)


def _configure_ssl_cert_bundle() -> None:
    """Explicitly feed certifi's CA bundle to OpenSSL, only in frozen distributions.

    Nuitka / PyInstaller copy `libssl`, but its compile-time hard-coded OPENSSLDIR
    points at a build-machine path that doesn't exist on user machines; if the
    SSL_CERT_FILE env var isn't set either, `ssl.create_default_context()` gets no
    root certificates and all external TLS fails. build-desktop.yml already packs
    `certifi/cacert.pem` as package data; this merely points OpenSSL at it
    explicitly.

    In source mode we do **not** touch SSL_CERT_FILE: the system Python's OpenSSL
    default trust chain is whatever the OS / venv uses, possibly carrying private
    enterprise CAs (corporate TLS MITM proxies, internal PKI, etc.). The static
    certifi bundle lacks those roots, and a hard override would suddenly break
    previously working intranet HTTPS with `certificate verify failed`. Frozen
    builds don't carry that risk (libssl's OPENSSLDIR points at nothing anyway),
    so the fallback only runs in the IS_FROZEN branch.

    If the user already set either variable explicitly and the file exists, the
    original value is respected whether frozen or not; we only override variables
    that are missing or point at no-longer-existing paths (e.g. stale paths
    inherited from the packaging build machine).
    """
    var_names = ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE")

    def _existing_is_valid(name: str) -> bool:
        value = os.environ.get(name)
        if not value:
            return False
        # Each variable defines valid differently:
        # - REQUESTS_CA_BUNDLE explicitly accepts PEM files or c_rehash-processed
        #   CA directories in capath mode; replacing directories as invalid breaks enterprise PKI
        #   capath setups.
        # - SSL_CERT_FILE / CURL_CA_BUNDLE accept only PEM files;
        #   directories use SSL_CERT_DIR / CURL_CA_PATH separately.
        if name == "REQUESTS_CA_BUNDLE":
            return os.path.isfile(value) or os.path.isdir(value)
        return os.path.isfile(value)

    # All three already point to valid files -> leave completely unchanged.
    if all(_existing_is_valid(name) for name in var_names):
        return

    # Source mode preserves system trust rather than forcing certifi and breaking enterprise CAs.
    # Even invalid paths remain the user's/upstream script's responsibility in source mode; we
    # cannot distinguish deliberately invalid debugging paths from accidentally inherited ones.
    if not IS_FROZEN:
        return

    ca_path: str | None = None
    try:
        import certifi  # noqa: WPS433 — Deliberately function-local to control import overhead.
        candidate = certifi.where()
        if candidate and os.path.isfile(candidate):
            ca_path = candidate
    except Exception:
        ca_path = None

    if ca_path is None:
        # Frozen fallback: build-desktop.yml puts certifi/cacert.pem under bundle_dir;
        # PyInstaller onefile uses bundle_dir == sys._MEIPASS (the IS_FROZEN
        # branch above), covering common frozen layouts.
        candidate = os.path.join(bundle_dir, "certifi", "cacert.pem")
        if os.path.isfile(candidate):
            ca_path = candidate

    if ca_path is None:
        # No CA bundle in frozen execution means external TLS inevitably fails; give operators a clear
        # root cause rather than only downstream certificate-verify-failed symptoms.
        print(
            "[Launcher] Warning: failed to locate CA bundle in frozen build "
            f"(certifi.where() unavailable, no certifi/cacert.pem under {bundle_dir}); "
            "external HTTPS / WSS will fail with certificate verify failed.",
            flush=True,
        )
        return

    # For each invalid variable, choose the fallback order closest to its own library semantics,
    # preserving lookup behavior; use certifi only if none succeeds.
    #
    # Key case: users intentionally set SSL_CERT_FILE=/etc/openssl.pem for OpenSSL and
    # CURL_CA_BUNDLE=/etc/curl.pem for curl/requests, leaving REQUESTS_CA_BUNDLE unset
    # for documented REQUESTS -> CURL -> default fallback. If all invalid variables
    # used the first valid file in SSL -> REQUESTS -> CURL order,
    # REQUESTS_CA_BUNDLE would incorrectly receive SSL's PEM and hide
    # intended CURL_CA_BUNDLE, diverging from documented HTTPS behavior.
    #
    # Fallback-order rationale:
    # - SSL_CERT_FILE: OpenSSL documents no fallback; REQUESTS/CURL PEMs are both
    #   compatible. Prefer REQUESTS as likely application trust,
    #   then CURL for system curl configuration.
    # - REQUESTS_CA_BUNDLE: requests explicitly documents CURL_CA_BUNDLE fallback,
    #   so put CURL first, then SSL as a valid-PEM fallback.
    # - CURL_CA_BUNDLE: curl documents no fallback; follow system-wide then application
    #   trust intuition, preferring SSL then REQUESTS.
    #
    # Consider files only: REQUESTS_CA_BUNDLE's allowed capath directories cannot feed OpenSSL /
    # curl; cross-variable propagation always uses files.
    propagation_sources = {
        "SSL_CERT_FILE": ("REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"),
        "REQUESTS_CA_BUNDLE": ("CURL_CA_BUNDLE", "SSL_CERT_FILE"),
        "CURL_CA_BUNDLE": ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"),
    }

    def _pick_fallback(target: str) -> str:
        for src in propagation_sources[target]:
            value = os.environ.get(src)
            if value and os.path.isfile(value):
                return value
        return ca_path

    # Treat all three uniformly: retain valid existing values, otherwise choose a valid PEM in propagation_sources
    # order, using certifi only if none exists. setdefault is insufficient:
    # invalid inherited build-machine/legacy paths would still make requests/curl fail
    # verification, precisely the symptom this helper prevents.
    for name in var_names:
        if not _existing_is_valid(name):
            os.environ[name] = _pick_fallback(name)


# Run before any module triggering import ssl; first import makes OpenSSL fix default
# verification paths, and later environment changes cannot affect existing
# SSLContext objects. The utils imports below already pull httpx /
# OpenAI SDK dependencies, so run first.
#
# Use explicit checks rather than assert, stripped by python -O and silently bypassed.
# Operators must see regressions importing ssl before this helper.
if "ssl" in sys.modules:
    print(
        "[Launcher] Warning: `ssl` was imported before _configure_ssl_cert_bundle() ran; "
        "SSL_CERT_FILE override won't affect the already-initialized default SSLContext. "
        "Move SSL bootstrap higher in launcher.py.",
        flush=True,
    )
_configure_ssl_cert_bundle()


def _get_project_venv_python(project_dir: str) -> str | None:
    if sys.platform == 'win32':
        candidate = os.path.join(project_dir, '.venv', 'Scripts', 'python.exe')
    else:
        candidate = os.path.join(project_dir, '.venv', 'bin', 'python')

    return candidate if os.path.exists(candidate) else None


def _maybe_reexec_into_project_venv(project_dir: str) -> None:
    """Prefer the repo-local virtualenv when launching from source.

    Users often invoke ``python launcher.py`` with the system interpreter.
    When that interpreter differs from the project's managed ``.venv``,
    imports fail even though the dependency is already installed locally.
    """
    if IS_FROZEN:
        return

    # Get the expected .venv directory and current environment root.
    expected_venv_dir = os.path.abspath(os.path.join(project_dir, ".venv"))
    current_venv_dir = os.path.abspath(sys.prefix)

    # Verify this environment really is the project's .venv, ignoring case.
    # Support uv run while preventing accidental execution in unrelated virtual environments.
    if os.path.normcase(current_venv_dir) == os.path.normcase(expected_venv_dir):
        return

    # If roots differ, perform the original strict interpreter-path check.
    current_executable = os.path.abspath(sys.executable or "")
    if not current_executable:
        return

    candidate = _get_project_venv_python(project_dir)
    if not candidate:
        return

    target_executable = os.path.abspath(candidate)
    if current_executable == target_executable:
        return

    print(f"[Launcher] 当前解释器不是项目虚拟环境，正在切换到: {candidate}")
    os.execv(target_executable, [target_executable] + sys.argv)

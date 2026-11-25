"""Record a plugin process's own uses of ``system.*`` permissions.

A plugin can connect to the network, start programs and read or write files
in its own process, without going through the host. This module installs a
``sys.addaudithook`` hook in the plugin child process that notices those
uses and reports them to the host, which writes them to the call-chain audit
(``call_audit``) as ``kind: "system_use"`` entries.

What is covered (CPython audit events, see the "Audit events table" in the
Python docs):

* ``system.network:connect``: ``socket.connect`` (also ``connect_ex``) and
  ``socket.sendto``. ``socket.getaddrinfo`` / ``gethostbyname*`` are only used
  to remember the host name a following connect resolved; asyncio resolves in
  an executor thread, so that pairing is by port and time and can miss.
  Unix-domain sockets are local IPC and are not recorded.
* ``system.process:spawn``: ``subprocess.Popen``, ``os.system``, ``os.exec``,
  ``os.spawn``, ``os.posix_spawn``, ``os.fork`` / ``os.forkpty``,
  ``os.startfile``, ``pty.spawn``, ``_winapi.CreateProcess``. Only the program
  name is recorded, never its arguments.
* ``system.fs:read`` / ``system.fs:write``: ``open`` (read or write from its
  flags) and the ``os.*`` file events listed in ``_FS_EVENTS``, for paths
  outside the plugin's own directory, storage directory and shared directory
  (reads of the time zone and MIME type files the standard library loads
  itself are exempt too). A path is judged where it really lands (symlinks
  resolved). A relative path with a ``dir_fd`` is recorded as ``<dir_fd>``;
  ``os.open(..., dir_fd=)`` raises ``open`` without the descriptor, so that one
  form is judged against the current directory.

**Not covered: ``system.env:read``.** Python raises no audit event when the
environment is read (only ``os.putenv`` / ``os.unsetenv``), so reads of
``os.environ`` cannot be seen here; static analysis is the only signal.

Exempt, so the records stay about the plugin itself: reads of the interpreter,
the standard library and site-packages; the plugin's own directory and data and
the log directory; the import machinery (``open_code`` and ``.pyc`` writes), and uses
the SDK / host code makes itself (reply channel, model gateway client and so
on). Who made a use is read from the Python stack: the innermost frame that
is plugin code (records) or host code (exempt) decides; library frames in
between are transparent. A use with neither on the stack (a thread a library
started) is recorded and flagged ``unattributed``, and never refused.

Like the rest of the permission checks this only constrains honest plugins:
ctypes or a C extension can do all of the above without an audit event.

Rules inside the hook: no IPC, no locks, no blocking and no re-entry. A
record goes into a lock-free deque; a daemon thread sends batches to the host
over the plugin's authenticated message uplink.

Whether a use would be refused is judged from the grants the host sent down
(``ctx.permissions``, the same resolved states the host's own checks use).
Nothing is refused while ``REFUSES_SYSTEM_USES`` is False.
"""

from __future__ import annotations

import collections
import ipaddress
import os
import sys
import threading
import time
from collections.abc import Callable, Iterable
from typing import Any

from plugin.sdk.shared.models.exceptions import SystemPermissionDeniedError

# Whether an unauthorised use is refused (raises SystemPermissionDeniedError
# from the audited call). Observe only for now: every record says
# ``enforcing: False``. Turned on together with the host gate's REFUSES_CALLS.
REFUSES_SYSTEM_USES = False
# Known gap to close before switching it on: a use with no plugin frame on the
# stack is never refused (it may be the SDK's own work on a library thread),
# so ``asyncio.to_thread(open, path)`` / ``run_in_executor(None, open, path)``
# is recorded (flagged ``unattributed``) but would not be refused.

SYSTEM_AUDIT = "SYSTEM_AUDIT"  # message type on the plugin's message uplink

NETWORK_CONNECT = "system.network:connect"
PROCESS_SPAWN = "system.process:spawn"
FS_READ = "system.fs:read"
FS_WRITE = "system.fs:write"
AUDITED_PERMISSIONS = frozenset({NETWORK_CONNECT, PROCESS_SPAWN, FS_READ, FS_WRITE})

QUEUE_LIMIT = 1024
BATCH_SIZE = 50
FLUSH_INTERVAL = 1.0
_POLL_INTERVAL = 0.1
DEDUP_SECONDS = 60.0
_DEDUP_LIMIT = 4096
_LOOKUP_SECONDS = 10.0
_MAX_FRAMES = 128
TARGET_MAX_CHARS = 200
_PATH_DIR_DEPTH = 3

_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC

# Frame kinds.
_OTHER, _PLUGIN, _HOST = 0, 1, 2
# Project packages the SDK and host code live in (``plugin.plugins.*`` excepted).
_HOST_PACKAGES = frozenset({"plugin", "utils", "config"})
_IMPORT_MODULES = frozenset({"importlib._bootstrap", "importlib._bootstrap_external"})

# File events: event name -> (permission, paths). Each path is (index of the
# path argument, index of its dir_fd argument or None, whether the operation
# follows a symlink in the last component). Argument orders as CPython raises
# them (os.rename is also os.replace).
_FS_EVENTS: dict[str, tuple[str, tuple[tuple[int, int | None, bool], ...]]] = {
    "os.listdir": (FS_READ, ((0, None, True),)),
    "os.scandir": (FS_READ, ((0, None, True),)),
    "os.remove": (FS_WRITE, ((0, 1, False),)),
    "os.rename": (FS_WRITE, ((0, 2, False), (1, 3, False))),
    "os.mkdir": (FS_WRITE, ((0, 2, False),)),
    "os.rmdir": (FS_WRITE, ((0, 1, False),)),
    "os.chmod": (FS_WRITE, ((0, 2, True),)),
    "os.chown": (FS_WRITE, ((0, 3, True),)),
    "os.truncate": (FS_WRITE, ((0, None, True),)),
    "os.utime": (FS_WRITE, ((0, 3, True),)),
    "os.link": (FS_WRITE, ((1, 3, False),)),
    "os.symlink": (FS_WRITE, ((1, 2, False),)),
    "shutil.rmtree": (FS_WRITE, ((0, 1, False),)),
}
_DIRECTORY_EVENTS = frozenset({"os.listdir", "os.scandir", "os.mkdir", "os.rmdir", "shutil.rmtree"})
# Without a path these list the current directory; with an int, a directory fd.
_LISTING_EVENTS = frozenset({"os.listdir", "os.scandir"})
_SPAWN_EVENTS = frozenset({
    "subprocess.Popen", "os.system", "os.exec", "os.spawn", "os.posix_spawn", "os.fork",
    "os.forkpty", "os.startfile", "os.startfile/2", "pty.spawn", "_winapi.CreateProcess",
})
_LOOKUP_EVENTS = frozenset({"socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyname_ex"})
_CONNECT_EVENTS = frozenset({"socket.connect", "socket.sendto"})
_SERVICE_PORTS = {"http": 80, "https": 443}
_FROZEN = bool(getattr(sys, "frozen", False)) or "__compiled__" in globals()


def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _forms(path: str) -> set[str]:
    """A directory as written and with its symlinks resolved."""
    return {_norm(path), os.path.normcase(os.path.realpath(path))}


def _under(path: str, roots: tuple[str, ...]) -> bool:
    for root in roots:
        if path == root or path.startswith(root + os.sep):
            return True
    return False


def interpreter_directories() -> list[str]:
    """Standard library, site-packages and interpreter prefixes."""
    import site
    import sysconfig

    directories = [sys.prefix, sys.base_prefix, sys.exec_prefix, sys.base_exec_prefix]
    for key in ("stdlib", "platstdlib", "purelib", "platlib"):
        try:
            directories.append(sysconfig.get_path(key))
        except Exception:
            pass  # not available here: nothing to exempt
    try:
        directories.extend(site.getsitepackages())
    except Exception:
        pass  # not available here: nothing to exempt
    try:
        directories.append(site.getusersitepackages())
    except Exception:
        pass  # not available here: nothing to exempt
    if _FROZEN:
        # The standard library and dependencies ship next to the launcher.
        directories.append(os.path.dirname(sys.executable))
    return [directory for directory in directories if directory]


def system_read_paths() -> list[str]:
    """Files the standard library reads on its own behalf (time zones, MIME types).

    Exempt for reading only, so a plugin using ``zoneinfo`` or ``mimetypes``
    is not reported for files outside its data directory it never asked for.
    """
    paths = ["/etc/localtime", "/etc/timezone"]
    try:
        import zoneinfo

        paths.extend(zoneinfo.TZPATH)
    except Exception:
        pass  # not available here: nothing to exempt
    try:
        import mimetypes

        paths.extend(mimetypes.knownfiles)
    except Exception:
        pass  # not available here: nothing to exempt
    return paths


def redact_path(path: str, *, directory: bool = False, home: str | None = None) -> str:
    """A path for the persistent audit, with fewer private details.

    The home directory becomes ``~`` and a user name after ``Users`` / ``home``
    becomes ``<user>``; of a deeper path only the first two directory levels
    and the last one are kept; a file name is replaced by ``*`` plus its
    extension.
    """
    home = os.path.expanduser("~") if home is None else home
    text = os.path.abspath(path)
    anchor = ""
    folded, home_folded = os.path.normcase(text), os.path.normcase(home or "").rstrip(os.sep)
    if len(home_folded) > 1 and (folded == home_folded or folded.startswith(home_folded + os.sep)):
        anchor, text = "~", text[len(home_folded):]
    drive, rest = os.path.splitdrive(text)
    parts = [part for part in rest.replace("\\", "/").split("/") if part]
    if not anchor:
        anchor = drive + "/" if drive else "/"
        # Any level: /mnt/c/Users/<name>, /Volumes/Disk/Users/<name>.
        for index, part in enumerate(parts[:-1]):
            if part.lower() in ("users", "home"):
                parts[index + 1] = "<user>"
                break
    name = None
    if not directory and parts:
        base = parts.pop()
        extension = os.path.splitext(base)[1]
        if not (1 < len(extension) <= 10 and extension[1:].isalnum()):
            extension = ""
        name = "*" + extension
    if len(parts) > _PATH_DIR_DEPTH:
        # The top levels say where, the last one what for; the middle goes.
        parts = parts[:_PATH_DIR_DEPTH - 1] + ["…", parts[-1]]
    if name is not None:
        parts.append(name)
    joined = "/".join(parts)
    if anchor == "~":
        result = "~/" + joined if joined else "~"
    else:
        result = anchor + joined
    return result[:TARGET_MAX_CHARS]


def program_name(value: object, *, command_line: bool = False) -> str:
    """The program's base name only; arguments are never recorded.

    ``value`` is a path (or an argv whose first item is one) unless
    ``command_line``: only a command-line string is cut at the first blank.
    """
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ""
    if isinstance(value, os.PathLike):
        value = os.fspath(value)
    if isinstance(value, bytes):
        value = os.fsdecode(value)
    if not isinstance(value, str):
        return "<unknown>"
    text = value.strip()
    if command_line:
        if text[:1] in ('"', "'"):
            quote = text[0]
            end = text.find(quote, 1)
            text = text[1:end] if end > 0 else text[1:]
        else:
            text = text.split()[0] if text.split() else ""
    name = text.replace("\\", "/").rsplit("/", 1)[-1]
    return name[:TARGET_MAX_CHARS] or "<unknown>"


def document_name(value: object) -> str:
    """A document opened with its associated program: ``*`` plus its extension."""
    if isinstance(value, os.PathLike):
        value = os.fspath(value)
    if isinstance(value, bytes):
        value = os.fsdecode(value)
    if not isinstance(value, str):
        return "<unknown>"
    extension = os.path.splitext(value.replace("\\", "/").rsplit("/", 1)[-1])[1]
    return "*" + extension if 1 < len(extension) <= 10 and extension[1:].isalnum() else "*"


def _port(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, bytes):
        value = value.decode("ascii", "replace")
    if isinstance(value, str):
        if value.isdigit():
            return int(value)
        return _SERVICE_PORTS.get(value.lower())
    return None


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return False
    return True


def _inside_rmtree() -> bool:
    """Whether the current use comes from ``shutil.rmtree``'s fd-based walk."""
    frame = sys._getframe(1)
    for _ in range(_MAX_FRAMES):
        if frame is None:
            return False
        if frame.f_code.co_name.startswith("_rmtree") and frame.f_globals.get("__name__") == "shutil":
            return True
        frame = frame.f_back
    return False


class SystemAuditor:
    """The hook's state for one plugin process. ``hook`` is what is installed."""

    def __init__(
        self,
        *,
        plugin_dir: os.PathLike | str,
        exempt_dirs: Iterable[os.PathLike | str] = (),
        project_root: os.PathLike | str | None = None,
        permissions: Any = None,
        sender: Callable[[dict], None] | None = None,
        home: str | None = None,
    ) -> None:
        self._plugin_dir = _norm(os.fspath(plugin_dir))
        self._project_root = _norm(os.fspath(project_root)) if project_root is not None else None
        exempt = _forms(self._plugin_dir)
        for directory in exempt_dirs:
            try:
                exempt |= _forms(os.fspath(directory))
            except Exception:
                pass  # an unusable directory exempts nothing
        self._exempt_dirs = tuple(sorted(exempt))
        # The interpreter's directories are exempt for reading only: a plugin
        # writing into site-packages (or next to a frozen launcher) is recorded.
        read_exempt = set()
        for path in (*interpreter_directories(), *system_read_paths()):
            try:
                read_exempt |= _forms(path)
            except Exception:
                pass  # an unusable path exempts nothing
        self._read_exempt = tuple(sorted(read_exempt))
        self._permissions = permissions
        self._sender = sender
        self._home = os.path.expanduser("~") if home is None else home
        try:
            real_home = os.path.realpath(self._home)
        except Exception:
            real_home = self._home  # unresolvable: matched as written only
        self._real_home = real_home if os.path.normcase(real_home) != os.path.normcase(self._home) else None
        self._active = True
        self._busy: set[int] = set()
        self._internal_threads: set[int] = set()
        self._queue: collections.deque[dict] = collections.deque()
        self._dedup: dict[tuple, float] = {}
        self._frame_kinds: dict[str, int] = {}
        self._plain_dirs: set[str] = set()
        self._lookups: collections.deque[tuple[float, str, int | None]] = collections.deque(maxlen=64)
        self._thread_lookup: dict[int, tuple[float, str, int | None]] = {}
        self.dropped = 0
        self.errors = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- configuration ------------------------------------------------------

    def set_permissions(self, permissions: Any) -> None:
        """Where grants are read from: anything with ``_peek(permission)`` (PluginPermissions)."""
        self._permissions = permissions

    def deactivate(self) -> None:
        """The hook stays installed (it cannot be removed) but does nothing."""
        self._active = False

    # -- the hook -----------------------------------------------------------

    def hook(self, event: str, args: tuple) -> None:
        # Cheapest test first: most audit events are not ours.
        if event not in _HANDLED_EVENTS or not self._active:
            return
        ident = threading.get_ident()
        if ident in self._busy or ident in self._internal_threads:
            return
        self._busy.add(ident)
        refusal = None
        try:
            refusal = self._handle(event, args, ident)
        except Exception:
            self.errors += 1
        finally:
            self._busy.discard(ident)
        if refusal is not None:
            raise refusal

    def _handle(self, event: str, args: tuple, ident: int) -> SystemPermissionDeniedError | None:
        if event in _LOOKUP_EVENTS:
            # A host lookup must not lend its name to a plugin's connect.
            if self._attribution() != _HOST:
                self._remember_lookup(event, args, ident)
            return None
        uses = self._uses(event, args, ident)
        if not uses:
            return None
        attribution = self._attribution()
        if attribution == _HOST:
            return None
        refusal = None
        for permission, target, flags in uses:
            would_deny, reason = self._judge(permission)
            if attribution is None:
                flags = (*flags, "unattributed")
            self._record(permission, event, target, would_deny, reason, flags)
            # Only a use made by plugin code is refused: with no plugin frame
            # on the stack it may be the SDK's own work on a library thread.
            if REFUSES_SYSTEM_USES and would_deny and attribution == _PLUGIN and refusal is None:
                refusal = SystemPermissionDeniedError(permission=permission, reason=reason)
        return refusal

    def _uses(self, event: str, args: tuple, ident: int) -> list[tuple[str, str, tuple[str, ...]]]:
        if event == "open":
            path = args[0] if args else None
            if isinstance(path, int):
                return []
            flags = args[2] if len(args) > 2 and isinstance(args[2], int) else 0
            mode = args[1] if len(args) > 1 else None
            writes = bool(flags & _WRITE_FLAGS) or (
                isinstance(mode, str) and any(c in mode for c in "wax+")
            )
            permission = FS_WRITE if writes else FS_READ
            target = self._fs_target(path, directory=False, read=permission == FS_READ)
            return [(permission, target, ())] if target else []
        spec = _FS_EVENTS.get(event)
        if spec is not None:
            permission, paths = spec
            listing = event in _LISTING_EVENTS
            uses = []
            for index, fd_index, follow in paths:
                path = args[index] if index < len(args) else None
                dir_fd = args[fd_index] if fd_index is not None and fd_index < len(args) else None
                if (isinstance(dir_fd, int) and dir_fd >= 0 or isinstance(path, int)) and _inside_rmtree():
                    # shutil.rmtree's own descriptor walk: its root was
                    # judged by the "shutil.rmtree" event already.
                    continue
                if listing and path is None:
                    path = os.getcwd()  # raises no audit event
                elif listing and isinstance(path, int):
                    uses.append((permission, "<fd>", ()))  # a directory descriptor: unknown target
                    continue
                target = self._fs_target(
                    path, directory=event in _DIRECTORY_EVENTS, read=permission == FS_READ,
                    dir_fd=dir_fd, follow=follow,
                )
                if target:
                    uses.append((permission, target, ()))
            return uses
        if event in _SPAWN_EVENTS:
            return [(PROCESS_SPAWN, self._spawn_target(event, args), ())]
        if event in _CONNECT_EVENTS:
            return self._connect_use(args, ident)
        return []

    # -- targets ------------------------------------------------------------

    def _real(self, path: str, *, follow: bool) -> str:
        """Where ``path`` really lands (symlinks resolved; the link itself
        when the operation does not follow it), not case-normalised.

        Parent directories without any symlink are remembered, so a use
        costs a couple of ``lstat`` calls instead of one per level.
        ``realpath`` / ``lstat`` raise no audit event.
        """
        if ".." in path.replace("\\", "/").split("/"):
            # abspath folds ".." before symlinks are seen: resolve it all.
            if follow:
                return os.path.realpath(path)
            parent, name = os.path.split(path.rstrip("/\\") or path)
            if name in ("", ".", ".."):
                return os.path.realpath(path)
            return os.path.join(os.path.realpath(parent or "."), name)
        absolute = os.path.abspath(path)
        parent, name = os.path.split(absolute)
        if follow and os.path.islink(absolute):
            return os.path.realpath(absolute)
        # Only directories with no symlink anywhere on their path are cached
        # (their resolved form is themselves); a hit re-checks that the
        # directory has not become a link since. A path through a symlink is
        # resolved on every use, so retargeting the link is seen at once.
        if parent in self._plain_dirs and not os.path.islink(parent):
            return os.path.join(parent, name)
        resolved = os.path.realpath(parent)
        if os.path.normcase(resolved) == os.path.normcase(parent):
            if len(self._plain_dirs) > 4096:
                self._plain_dirs.clear()
            self._plain_dirs.add(parent)
        return os.path.join(resolved, name)

    def _exempt(self, path: str, read: bool) -> bool:
        return _under(path, self._exempt_dirs) or (read and _under(path, self._read_exempt))

    def _fs_target(self, path: object, *, directory: bool, read: bool = False,
                   dir_fd: object = None, follow: bool = True) -> str | None:
        if path is None or isinstance(path, int):
            return None
        if isinstance(path, os.PathLike):
            path = os.fspath(path)
        if isinstance(path, bytes):
            path = os.fsdecode(path)
        if not isinstance(path, str) or not path:
            return None
        if isinstance(dir_fd, int) and dir_fd >= 0 and not os.path.isabs(path):
            # Relative to a directory descriptor: where it lands is unknown,
            # so it is recorded rather than judged by the current directory.
            return "<dir_fd>"
        # Judged where it really lands, so a symlink in the data directory
        # cannot lead out of it (and an alias of it is still the data directory).
        real = self._real(path, follow=follow)
        key = os.path.normcase(real)
        if self._exempt(key, read):
            return None
        shown = real
        if self._real_home is not None:
            real_home = os.path.normcase(self._real_home).rstrip(os.sep)
            if key == real_home or key.startswith(real_home + os.sep):
                # Home is itself a link: show it under the home it was reached by.
                shown = self._home.rstrip(os.sep) + real[len(real_home):]
        return redact_path(shown, directory=directory, home=self._home)

    @staticmethod
    def _spawn_target(event: str, args: tuple) -> str:
        if event in ("os.fork", "os.forkpty"):
            return "<fork>"
        if event == "subprocess.Popen":
            executable, argv = (args + (None, None))[:2]
            if executable:
                return program_name(executable)
            # A string is a shell command (or a Windows command line).
            return program_name(argv, command_line=isinstance(argv, (str, bytes)))
        if event in ("os.startfile", "os.startfile/2"):
            return document_name(args[0] if args else None)
        if event in ("os.exec", "os.posix_spawn", "pty.spawn"):
            return program_name(args[0] if args else None)
        if event == "os.spawn":
            return program_name(args[1] if len(args) > 1 else None)
        if event == "_winapi.CreateProcess":
            application, command = (args + (None, None))[:2]
            if application:
                return program_name(application)
            return program_name(command, command_line=True)
        # os.system
        return program_name(args[0] if args else None, command_line=True)

    def _remember_lookup(self, event: str, args: tuple, ident: int) -> None:
        host = args[0] if args else None
        if isinstance(host, bytes):
            host = host.decode("idna", "replace")
        if not isinstance(host, str) or not host or _is_ip_literal(host):
            return
        port = _port(args[1]) if event == "socket.getaddrinfo" and len(args) > 1 else None
        entry = (time.monotonic(), host[:TARGET_MAX_CHARS], port)
        self._lookups.append(entry)
        if len(self._thread_lookup) > 256:
            self._thread_lookup.clear()
        self._thread_lookup[ident] = entry

    def _host_for(self, port: int | None, ident: int) -> str | None:
        now = time.monotonic()
        own = self._thread_lookup.get(ident)
        if own is not None and now - own[0] <= _LOOKUP_SECONDS and own[2] in (port, None):
            return own[1]
        try:
            recent = list(self._lookups)
        except RuntimeError:
            return None
        for when, host, lookup_port in reversed(recent):
            if now - when > _LOOKUP_SECONDS:
                break
            if lookup_port is not None and lookup_port == port:
                return host
        return None

    def _connect_use(self, args: tuple, ident: int) -> list[tuple[str, str, tuple[str, ...]]]:
        if len(args) < 2:
            return []
        address = args[1]
        if isinstance(address, (str, bytes)):
            return []  # AF_UNIX path or abstract name: local IPC
        if not isinstance(address, tuple) or len(address) < 2:
            return [(NETWORK_CONNECT, "<address>", ())]
        ip, port = address[0], _port(address[1])
        if not isinstance(ip, str):
            return [(NETWORK_CONNECT, "<address>", ())]
        flags: tuple[str, ...] = ()
        try:
            if ipaddress.ip_address(ip.split("%", 1)[0]).is_loopback:
                flags = ("loopback",)
        except ValueError:
            pass  # not an IP literal: not loopback
        # The name is paired by thread / port and time and can be wrong, so
        # the address actually connected to is always kept next to it.
        host = self._host_for(port, ident)
        address = f"[{ip}]" if ":" in ip else ip
        suffix = f":{port}" if port is not None else ""
        shown = address
        if host:
            # The address and port always fit (the host caps targets at
            # TARGET_MAX_CHARS); a long name is shortened instead.
            room = TARGET_MAX_CHARS - len(address) - len(suffix) - 2
            if room >= 8:
                name = host if len(host) <= room else host[:room - 1] + "…"
                shown = f"{name}({address})"
        return [(NETWORK_CONNECT, (shown + suffix)[:TARGET_MAX_CHARS], flags)]

    # -- attribution --------------------------------------------------------

    def _frame_kind(self, frame) -> int:
        code = frame.f_code
        filename = code.co_filename
        if code.co_name == "socketpair" and frame.f_globals.get("__name__") == "socket":
            return _HOST  # asyncio's self-pipe on Windows connects over loopback
        # Only real files are cached: every exec()/eval() shares "<string>".
        cacheable = not filename.startswith("<") or filename.startswith("<frozen ")
        kind = self._frame_kinds.get(filename) if cacheable else None
        if kind is not None:
            return kind
        module = frame.f_globals.get("__name__") or ""
        if not isinstance(module, str):
            module = ""
        if module in _IMPORT_MODULES or filename.startswith("<frozen importlib"):
            kind = _HOST  # the import machinery (open_code, .pyc writes)
        elif filename and not filename.startswith("<") and _under(_norm(filename), (self._plugin_dir,)):
            kind = _PLUGIN
        elif module.startswith("plugin.plugins."):
            kind = _PLUGIN
        elif module.split(".", 1)[0] in _HOST_PACKAGES and (
            self._project_root is None
            or _FROZEN
            or filename.startswith("<")  # exec()/eval() and generated code: the module decides
            or _under(_norm(filename), (self._project_root,))
        ):
            # A frozen build compiles every module in; its file names need
            # not lie under the project root, so the module name decides.
            kind = _HOST
        else:
            kind = _OTHER
        if cacheable:
            if len(self._frame_kinds) > 8192:
                self._frame_kinds.clear()
            self._frame_kinds[filename] = kind
        return kind

    def _attribution(self) -> int | None:
        """``_PLUGIN``, ``_HOST``, or None when neither is on the stack."""
        frame = sys._getframe(1)
        own = globals()
        while frame is not None and frame.f_globals is own:
            frame = frame.f_back  # this module's own frames
        for _ in range(_MAX_FRAMES):
            if frame is None:
                break
            kind = self._frame_kind(frame)
            if kind != _OTHER:
                return kind
            frame = frame.f_back
        return None

    # -- judging and queueing ----------------------------------------------

    def _judge(self, permission: str) -> tuple[bool, str | None]:
        """Whether the grants held would refuse this use, and why."""
        state = None
        permissions = self._permissions
        if permissions is not None:
            state = permissions._peek(permission)
        if state is None:
            # No grants received yet: enforced permissions count as denied,
            # as ctx.permissions.get() reports them (unavailable_state).
            return True, "denied"
        if not state.enforced or state.status == "granted":
            return False, None
        return True, "denied" if state.status == "denied" else "undecided"

    def _record(self, permission: str, event: str, target: str, would_deny: bool,
                reason: str | None, flags: tuple[str, ...]) -> None:
        key = (permission, target, would_deny, flags)
        now = time.monotonic()
        if now - self._dedup.get(key, -DEDUP_SECONDS) < DEDUP_SECONDS:
            return
        if len(self._queue) >= QUEUE_LIMIT:
            self.dropped += 1
            return  # not marked as recorded: the next use is tried again
        if len(self._dedup) > _DEDUP_LIMIT:
            self._dedup.clear()
        self._dedup[key] = now
        self._queue.append({
            "permission": permission,
            "event": event,
            "target": target,
            "verdict": "would_deny" if would_deny else "allow",
            "reason": reason,
            "enforcing": REFUSES_SYSTEM_USES and would_deny and "unattributed" not in flags,
            "flags": list(flags),
        })
        # No wake-up from here: Event.set takes a lock, and the hook takes none.
        # The sender polls the queue length.

    # -- sending ------------------------------------------------------------

    def take_batch(self) -> list[dict]:
        batch = []
        while len(batch) < BATCH_SIZE:
            try:
                batch.append(self._queue.popleft())
            except IndexError:
                break
        return batch

    def flush(self) -> None:
        """Send what is queued (on the sender thread, or at close)."""
        sender = self._sender
        while True:
            batch = self.take_batch()
            dropped = self.dropped
            if not batch and not dropped:
                return
            if sender is None:
                self.dropped += len(batch)
                return
            message = {"type": SYSTEM_AUDIT, "items": batch, "dropped": dropped}
            try:
                sender(message)
            except Exception:
                # Uplink busy or closed: these are lost, count them next time.
                self.dropped += len(batch)
                return
            self.dropped -= dropped
            if not batch:
                return

    def _run(self) -> None:
        ident = threading.get_ident()
        self._internal_threads.add(ident)
        last = time.monotonic()
        try:
            while not self._stop.wait(_POLL_INTERVAL):
                if len(self._queue) < BATCH_SIZE and time.monotonic() - last < FLUSH_INTERVAL:
                    continue
                last = time.monotonic()
                try:
                    self.flush()
                except Exception:
                    self.errors += 1
            try:
                self.flush()  # stopping: send the rest from this thread too
            except Exception:
                self.errors += 1
        finally:
            self._internal_threads.discard(ident)

    def start(self) -> None:
        if self._thread is None:
            thread = threading.Thread(target=self._run, name="neko-system-audit", daemon=True)
            self._thread = thread
            thread.start()

    def close(self, timeout: float = 1.0) -> None:
        """Stop recording, send what is queued, stop the sender thread."""
        self._active = False
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
        if thread is not None and thread.is_alive():
            # The sender is stuck in a send: what is still queued is lost.
            self.dropped += len(self._queue)
            self._queue.clear()
            return
        if thread is None:
            try:
                self.flush()
            except Exception:
                self.errors += 1


_HANDLED_EVENTS = frozenset({
    "open", *_FS_EVENTS, *_SPAWN_EVENTS, *_LOOKUP_EVENTS, *_CONNECT_EVENTS,
})

_installed: SystemAuditor | None = None


def install(auditor: SystemAuditor) -> SystemAuditor:
    """Install ``auditor``'s hook for the rest of this process (once).

    An audit hook cannot be removed, so a second call only replaces which
    auditor the installed hook uses.
    """
    global _installed
    first = _installed is None
    _installed = auditor
    if first:
        sys.addaudithook(_dispatch)
    auditor.start()
    return auditor


def _dispatch(event: str, args: tuple) -> None:
    if event not in _HANDLED_EVENTS:
        return
    auditor = _installed
    if auditor is not None:
        auditor.hook(event, args)


def installed() -> SystemAuditor | None:
    return _installed


__all__ = [
    "AUDITED_PERMISSIONS",
    "REFUSES_SYSTEM_USES",
    "SYSTEM_AUDIT",
    "SystemAuditor",
    "install",
    "installed",
    "interpreter_directories",
    "document_name",
    "program_name",
    "redact_path",
]

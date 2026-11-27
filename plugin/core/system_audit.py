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
between are transparent. The walk stops at the event loop: inside an asyncio
task only the task's own coroutine chain counts. It also stops at a thread
pool's work item (``concurrent.futures``): a job counts as whoever submitted
it, not whoever happened to start the pool's worker thread.

A use with neither on the stack falls back to who started the thread (or
submitted the pool job), noted when ``threading.Thread.start`` /
``ThreadPoolExecutor.submit`` ran (``install`` wraps both): one started by
plugin code (directly or through a library it called, or from a thread it
started) is flagged ``plugin_thread`` and carries the handler that was running
then (``ctx.handler_ctx``, e.g. ``plugin_entry.<id>``) as ``handler``. Anything
else (a thread the SDK / host started, the main thread's event loop, a thread
started through ``_thread`` directly) is flagged ``unattributed``. Both kinds
are recorded and never refused (they count as background uses outside any
handler).

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
import re
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
# is recorded (flagged ``plugin_thread`` when plugin code submitted it) but
# would not be refused.

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
# Attribute set on a started thread / a submitted pool job: (kind, handler).
_ORIGIN_ATTR = "_neko_audit_origin"
_HANDLER_MAX_CHARS = 128
_POOL_MODULE = "concurrent.futures.thread"
# A pool worker's own frames: below them is the job's boundary.
_POOL_FRAMES = frozenset({"run", "_worker"})
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
_FS_EVENTS: dict[str, tuple[str, tuple[tuple[int, int | None, bool | None], ...]]] = {
    "os.listdir": (FS_READ, ((0, None, True),)),
    "os.scandir": (FS_READ, ((0, None, True),)),
    "os.remove": (FS_WRITE, ((0, 1, False),)),
    "os.rename": (FS_WRITE, ((0, 2, False), (1, 3, False))),
    "os.mkdir": (FS_WRITE, ((0, 2, False),)),
    "os.rmdir": (FS_WRITE, ((0, 1, False),)),
    # chmod / chown / utime: the event does not say whether follow_symlinks
    # was False (lchown, lchmod, utime(follow_symlinks=False)), so a link is
    # judged both as itself and as its target (follow=None).
    "os.chmod": (FS_WRITE, ((0, 2, None),)),
    "os.chown": (FS_WRITE, ((0, 3, None),)),
    "os.truncate": (FS_WRITE, ((0, None, True),)),
    "os.utime": (FS_WRITE, ((0, 3, None),)),
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
# Host names kept for a connect target: letters (any script), digits, ".", "-",
# "_"; a subset of what the host accepts in a target (capability_gate._TARGET).
_HOST_NAME = re.compile(r"[\w.\-]{1,253}")
# A leading shell variable assignment (NAME=value): never a program name.
# Command-line parsing (_command_program) is by allow-list, ASCII only.
_SHELL_BLANKS = " \t"
_ASSIGNMENT_START = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
_PLAIN_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=[A-Za-z0-9_./:@%+,-]*[ \t]+")
_WORD = re.compile(r"[^ \t]*")
# Unquoted: also "\\" and "~" for Windows / home paths; never shell syntax.
_PLAIN_PROGRAM = re.compile(r"[A-Za-z0-9_./:@%+,~\\-]+")
# Quoted (e.g. "C:\\Program Files (x86)\\App\\app.exe"): blanks and brackets too.
_QUOTED_PROGRAM = re.compile(r"[A-Za-z0-9_./:@%+,~\\() -]+")
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
    if command_line:
        # A POSIX shell splits words on space and tab only; any other blank
        # (\r, \n, \f, \v, U+00A0 ...) leaves where words end unclear: "<shell>".
        if any(char.isspace() and char not in _SHELL_BLANKS for char in value):
            return "<shell>"
        return _command_program(value.strip(_SHELL_BLANKS))
    text = value.strip()
    name = text.replace("\\", "/").rsplit("/", 1)[-1]
    return name[:TARGET_MAX_CHARS] or "<unknown>"


def _command_program(text: str) -> str:
    """The program of a command line, by allow-list: "<shell>" unless clear.

    Leading ``NAME=value`` words are skipped only when the value is plain
    ASCII (no quoting, escapes, expansions, operators or other blanks) and
    an ASCII space or tab follows; their values may be secrets. The program
    word must then be plain too (quoted: without ``$``, backticks or control
    characters). Anything else could hide where a value ends, so nothing is
    guessed.
    """
    while _ASSIGNMENT_START.match(text):
        match = _PLAIN_ASSIGNMENT.match(text)
        if match is None:
            return "<shell>"
        text = text[match.end():]
    if not text:
        return "<shell>"
    if text[:1] in ('"', "'"):
        end = text.find(text[0], 1)
        program = text[1:end] if end > 0 else ""
        if not program or not _QUOTED_PROGRAM.fullmatch(program):
            return "<shell>"
    else:
        program = _WORD.match(text).group(0)
        if not _PLAIN_PROGRAM.fullmatch(program):
            return "<shell>"
    name = program.replace("\\", "/").rsplit("/", 1)[-1]
    return name[:TARGET_MAX_CHARS] or "<shell>"


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
    """Whether the use was raised by ``shutil.rmtree``'s fd-based walk itself.

    Only the frame that made the call counts, so a plugin's ``onerror`` /
    ``onexc`` callback running inside rmtree is judged like any other code.
    """
    frame = sys._getframe(1)
    own = globals()
    while frame is not None and frame.f_globals is own:
        frame = frame.f_back
    return (
        frame is not None
        and frame.f_code.co_name.startswith("_rmtree")
        and frame.f_globals.get("__name__") == "shutil"
    )


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
        own_files: tuple[os.PathLike | str, Callable[[str], bool]] | None = None,
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
        # (directory, is-ours(file name)): this process's own files in a
        # directory it shares with others (its log files among all plugins').
        self._own_files = None
        if own_files is not None:
            try:
                self._own_files = (frozenset(_forms(os.fspath(own_files[0]))), own_files[1])
            except Exception:
                self._own_files = None  # unusable: nothing extra is exempt
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
            if self._attribution()[0] != _HOST:
                self._remember_lookup(event, args, ident)
            return None
        uses = self._uses(event, args, ident)
        if not uses:
            return None
        attribution, origin = self._attribution()
        if attribution == _HOST:
            return None
        handler = None
        if attribution is None:
            if origin is not None and origin[0] == _PLUGIN:
                extra, handler = ("plugin_thread",), origin[1]
            else:
                extra = ("unattributed",)
        else:
            extra = ()
        # Only a use made by plugin code is refused: with no plugin frame on
        # the stack it may be the SDK's own work on a library thread, and a
        # use on a thread the plugin started is a background use.
        refusable = attribution == _PLUGIN
        refusal = None
        for permission, target, flags in uses:
            would_deny, reason = self._judge(permission)
            self._record(permission, event, target, would_deny, reason, (*flags, *extra),
                         handler=handler, refusable=refusable)
            if REFUSES_SYSTEM_USES and would_deny and refusable and refusal is None:
                refusal = SystemPermissionDeniedError(permission=permission, reason=reason)
        return refusal

    def _uses(self, event: str, args: tuple, ident: int) -> list[tuple[str, str, tuple[str, ...]]]:
        if event == "open":
            path = args[0] if args else None
            if isinstance(path, int):
                return []
            flags = args[2] if len(args) > 2 and isinstance(args[2], int) else 0
            mode = args[1] if len(args) > 1 else None
            access = flags & (os.O_WRONLY | os.O_RDWR)
            # A read-write open (r+, w+, a+, O_RDWR) can do both, so it is two
            # uses, each judged with its own exemptions (as the static check
            # counts it, capability_analysis._open_permissions).
            reads = access != os.O_WRONLY or (isinstance(mode, str) and "+" in mode)
            writes = bool(flags & _WRITE_FLAGS) or (
                isinstance(mode, str) and any(c in mode for c in "wax+")
            )
            if isinstance(path, (str, bytes)) and not os.path.isabs(path) and _inside_rmtree():
                # rmtree opens subdirectories by name relative to a dir_fd the
                # event does not carry; its root was judged already.
                return []
            uses = []
            for permission, wanted in ((FS_READ, reads), (FS_WRITE, writes)):
                if wanted:
                    target = self._fs_target(path, directory=False, read=permission == FS_READ)
                    if target:
                        uses.append((permission, target, ()))
            return uses
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

        Nothing is cached: a link anywhere on the path may change between
        uses. ``realpath`` / ``lstat`` raise no audit event.
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
        # Resolved on every use, all the way up: any level of the path may
        # have been switched to a link since the last use.
        return os.path.join(os.path.realpath(parent), name)

    def _own_file(self, real: str) -> bool:
        if self._own_files is None:
            return False
        directories, is_ours = self._own_files
        directory, name = os.path.split(real)
        return os.path.normcase(directory) in directories and bool(is_ours(name))

    def _exempt(self, path: str, read: bool) -> bool:
        return _under(path, self._exempt_dirs) or (read and _under(path, self._read_exempt))

    def _fs_target(self, path: object, *, directory: bool, read: bool = False,
                   dir_fd: object = None, follow: bool | None = True) -> str | None:
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
        # follow=None: unknown whether the link or its target is changed, so
        # both are judged and the use counts unless both are exempt.
        landings = [self._real(path, follow=follow is not False)]
        if follow is None:
            landings.append(self._real(path, follow=False))
        real = None
        for landing in landings:
            if not (self._exempt(os.path.normcase(landing), read) or self._own_file(landing)):
                real = landing
                break
        if real is None:
            return None
        key = os.path.normcase(real)
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
        if not _HOST_NAME.fullmatch(host):
            # Not a host name the target can carry (a failed lookup of junk):
            # the connect is then recorded by its address only.
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

    def _attribution(self) -> tuple[int | None, tuple[int | None, str | None] | None]:
        """``(kind, origin)``: kind is ``_PLUGIN``, ``_HOST``, or None when
        neither is on the stack; then origin is who started this thread or
        submitted this pool job (``(kind, handler)``, see ``note_origin``),
        None when that is not known."""
        frame = sys._getframe(1)
        own = globals()
        while frame is not None and frame.f_globals is own:
            frame = frame.f_back  # this module's own frames
        for _ in range(_MAX_FRAMES):
            if frame is None:
                # The bottom of this thread's stack: who started the thread.
                return None, self._thread_origin()
            code = frame.f_code
            module = frame.f_globals.get("__name__")
            if code.co_name == "_run" and module == "asyncio.events":
                # The event loop running a task or callback: whatever lies
                # below is whoever started the loop (the host runner), not
                # who made this use. A task with no plugin or host frame of
                # its own (one a library created) counts as the loop's thread.
                return None, self._thread_origin()
            if module == _POOL_MODULE and code.co_name in _POOL_FRAMES:
                # A pool job: whoever submitted it, never the worker thread
                # (started by an earlier submitter, maybe someone else).
                item = frame.f_locals.get("self") if code.co_name == "run" else None
                return None, getattr(item, _ORIGIN_ATTR, None)
            kind = self._frame_kind(frame)
            if kind != _OTHER:
                return kind, None
            frame = frame.f_back
        return None, None  # too deep to tell

    @staticmethod
    def _thread_origin() -> tuple[int | None, str | None] | None:
        # threading._active is a plain dict: a lock-free read, as the hook needs.
        thread = threading._active.get(threading.get_ident())
        return getattr(thread, _ORIGIN_ATTR, None)

    @staticmethod
    def _handler_label() -> str | None:
        """The handler the current context runs in (``ctx.handler_ctx``)."""
        context = sys.modules.get("plugin.core.context")
        variable = getattr(context, "_IN_HANDLER", None)
        if variable is None:
            return None
        label = variable.get()
        return label[:_HANDLER_MAX_CHARS] if isinstance(label, str) and label else None

    def note_origin(self, target: object) -> None:
        """Remember on ``target`` (a thread about to start, a pool job being
        submitted) who is starting it, for uses made there with neither
        plugin nor host code on the stack. Called by the wrappers ``install``
        sets up; never raises."""
        if not self._active:
            return
        try:
            kind, inherited = self._attribution()
            if kind == _PLUGIN:
                origin = (_PLUGIN, self._handler_label())
            elif kind == _HOST:
                origin = (_HOST, None)
            else:
                # A library starting it: whoever started the current thread
                # (or submitted the current job) started this one too.
                origin = inherited
            setattr(target, _ORIGIN_ATTR, origin)
        except Exception:
            self.errors += 1

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
                reason: str | None, flags: tuple[str, ...], *, handler: str | None = None,
                refusable: bool = True) -> None:
        key = (permission, target, would_deny, flags, handler)
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
            "enforcing": REFUSES_SYSTEM_USES and would_deny and refusable,
            "flags": list(flags),
            "handler": handler,
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


def _note_origin(target: object) -> None:
    auditor = _installed
    if auditor is not None:
        auditor.note_origin(target)


def wrap_thread_start(start):
    """``threading.Thread.start`` that first notes who starts the thread."""

    def wrapped(self, *args, **kwargs):
        _note_origin(self)
        return start(self, *args, **kwargs)

    wrapped.__wrapped__ = start
    wrapped.__name__, wrapped.__qualname__, wrapped.__doc__ = start.__name__, start.__qualname__, start.__doc__
    return wrapped


def wrap_work_item_init(init):
    """``concurrent.futures.thread._WorkItem.__init__`` that notes who submits the job."""

    def wrapped(self, *args, **kwargs):
        init(self, *args, **kwargs)
        _note_origin(self)

    wrapped.__wrapped__ = init
    wrapped.__name__, wrapped.__qualname__ = init.__name__, init.__qualname__
    return wrapped


def _wrap_thread_creation() -> None:
    """Note who starts each thread and submits each pool job (see the module
    doc). Plain attribute wrappers: a process that replaced them already
    keeps its own."""
    if not getattr(threading.Thread.start, "_neko_audit", False):
        threading.Thread.start = wrap_thread_start(threading.Thread.start)
        threading.Thread.start._neko_audit = True
    try:
        from concurrent.futures import thread as pool
    except Exception:
        return
    item = getattr(pool, "_WorkItem", None)
    if item is not None and not getattr(item.__init__, "_neko_audit", False):
        item.__init__ = wrap_work_item_init(item.__init__)
        item.__init__._neko_audit = True


def install(auditor: SystemAuditor) -> SystemAuditor:
    """Install ``auditor``'s hook for the rest of this process (once).

    An audit hook cannot be removed, so a second call only replaces which
    auditor the installed hook uses.
    """
    global _installed
    first = _installed is None
    _installed = auditor
    if first:
        try:
            _wrap_thread_creation()
        except Exception:
            auditor.errors += 1  # no thread attribution: uses there stay unattributed
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

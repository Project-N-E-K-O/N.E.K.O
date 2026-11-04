"""Plugin-owned handler installation and process inheritance policy.

The common backend only constructs sinks. Its host setup entrypoint does not
invoke this module or change its historical replacement policy.
"""
from __future__ import annotations

import logging

def all_loggers():
    yield logging.getLogger()
    for value in list(logging.Logger.manager.loggerDict.values()):
        if isinstance(value, logging.Logger):
            yield value


def replace_handlers(logger, old_handlers, new_handlers):
    """Replace a specified binding without changing any other logger's subscription."""
    retired = set(old_handlers)
    if not retired.intersection(logger.handlers):
        return
    replacement = []
    inserted = False
    for handler in logger.handlers:
        if handler in retired:
            if not inserted:
                replacement.extend(new_handlers)
                inserted = True
        else:
            replacement.append(handler)
    logger.handlers = replacement


def close_unbound_handlers(handlers):
    """Borrowed handlers remain usable until their external owners detach them."""
    attached = {h for lg in all_loggers() for h in lg.handlers}
    for handler in set(handlers) - attached:
        try:
            handler.close()
        except Exception:
            pass  # Retiring a sink must not invalidate its installed replacement.


def install_namespace_handlers(logger, handlers, level):
    """Commit a complete plugin namespace, preserving external subscriptions."""
    with logging._lock:
        old = [h for h in logger.handlers if getattr(h, "_neko_owned", False)]
        logger.handlers = [h for h in logger.handlers if h not in old] + list(handlers)
        logger.setLevel(level)
        logger.propagate = False
    close_unbound_handlers(old)


class _NamespaceFileBridge(logging.Handler):
    """Follow the namespace's current sinks without borrowing their lifetime."""

    def __init__(self, target):
        super().__init__()
        self.target = target

    def emit(self, record):
        root = logging.root
        # A containing host may install its file output after plugin startup.
        if any(isinstance(h, logging.FileHandler) for h in root.handlers):
            return
        # emit runs under this Handler's lock. Never acquire logging._lock
        # here: dictConfig holds it while waiting for handler locks.
        target = self.target
        if target.disabled or record.levelno < target.getEffectiveLevel():
            return
        for handler in target.handlers:
            if handler is not self and record.levelno >= handler.level:
                handler.handle(record)


def ensure_legacy_root_collection(base_logger):
    """Retain SDK-era file collection, yielding to a host's own file output.

    The bridge follows the namespace rather than owning a copy of its handlers,
    so even public backend reconfiguration cannot strand third-party records in
    the old directory. Existing host handlers and logger filters are untouched.
    """
    from utils.logger_config import mark_project_handler

    root = logging.getLogger()
    with logging._lock:
        previous = getattr(root, "_neko_sdk_bridge", None)
        if any(isinstance(h, logging.FileHandler) for h in root.handlers):
            if previous in root.handlers:
                root.removeHandler(previous)
                previous.close()
            root._neko_sdk_bridge = None
            return
        if previous in root.handlers:
            previous.target = logging.getLogger(base_logger.name)
        else:
            previous = _NamespaceFileBridge(logging.getLogger(base_logger.name))
            mark_project_handler(previous)
            root.addHandler(previous)
            root._neko_sdk_bridge = previous
        if root.level == logging.NOTSET or root.level > base_logger.level:
            root.setLevel(base_logger.level or logging.INFO)


def configure_uvicorn_collection():
    """Standalone compatibility: route preconfigured Uvicorn logs to root."""
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        target = logging.getLogger(name)
        target.handlers.clear()
        target.propagate = True

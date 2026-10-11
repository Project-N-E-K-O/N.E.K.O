"""Inspect effective routes across FastAPI's flat and tree router layouts."""

from fastapi import routing


def iter_routes(routes):
    iter_contexts = getattr(routing, "iter_route_contexts", None)
    if iter_contexts is not None:
        return iter_contexts(routes)
    return iter(routes)


def effective_path(route) -> str:
    """Mounted path of a route from :func:`iter_routes`, WebSocket routes included.

    In the tree layout a WebSocket route's context leaves ``path`` empty; the
    prefixed path lives on the context's ``starlette_route``.
    """
    path = getattr(route, "path", "") or ""
    if path:
        return path
    context = getattr(route, "_route_context", None)
    return getattr(getattr(context, "starlette_route", None), "path", "") or ""

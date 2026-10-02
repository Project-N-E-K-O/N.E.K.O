"""Inspect effective routes across FastAPI's flat and tree router layouts."""

from fastapi import routing


def iter_routes(routes):
    iter_contexts = getattr(routing, "iter_route_contexts", None)
    if iter_contexts is not None:
        return iter_contexts(routes)
    return iter(routes)

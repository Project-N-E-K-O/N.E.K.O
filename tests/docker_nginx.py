"""Assemble the official Docker Nginx ``server`` block the way the container does.

``docker/nginx/render.sh`` renders layered templates and Nginx stitches them
together with ``include``. Deployment-contract tests need the effective block a
variant actually loads, so this follows the same wiring: placeholder defaults
from ``docker/entrypoint.sh``, the variant's listener template as selected in
``render.sh``, and every ``include`` of a rendered file inlined at its
indentation. Full-line comments are dropped; Nginx ignores them too.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NGINX_DIR = ROOT / "docker" / "nginx"
NEKO_DIR = "/etc/nginx/neko"
VARIANTS = ("HTTP-only", "HTTP+HTTPS")


def _placeholders() -> dict[str, str]:
    entrypoint = (ROOT / "docker" / "entrypoint.sh").read_text(encoding="utf-8")
    values = dict(re.findall(r"^normalize_port (\w+) (\d+)$", entrypoint, re.MULTILINE))
    values.update({
        "NGINX_SSL_CERT": "/app/ssl/cert.pem",
        "NGINX_SSL_KEY": "/app/ssl/key.pem",
        "NGINX_NEKO_DIR": NEKO_DIR,
        "NGINX_CONF_D_DIR": "/etc/nginx/conf.d",
    })
    return values


def _rendered_sources(variant: str) -> dict[str, Path]:
    """Map each file under NEKO_DIR to the template ``render.sh`` renders into it."""
    render = (NGINX_DIR / "render.sh").read_text(encoding="utf-8")
    listeners = re.findall(
        rf'echo "🌐 Generating {re.escape(variant)} configuration[^\n]*\n'
        r'\s*render_nginx_template "\$NGINX_TEMPLATE_DIR/([^"]+)" "\$NGINX_NEKO_DIR/listener\.conf"',
        render,
    )
    assert len(listeners) == 1, f"render.sh must select exactly one listener for {variant}"
    sources = {
        dst: NGINX_DIR / src
        for src, dst in re.findall(
            r'render_nginx_template "\$NGINX_TEMPLATE_DIR/([^"]+)" "\$NGINX_NEKO_DIR/([^"]+)"', render,
        )
        if dst != "listener.conf"
    }
    sources["listener.conf"] = NGINX_DIR / listeners[0]
    for snippet in (NGINX_DIR / "snippets").glob("*.conf"):
        sources[f"snippets/{snippet.name}"] = snippet
    return sources


def _render(path: Path, sources: dict[str, Path], values: dict[str, str]) -> list[str]:
    text = re.sub(r"@@([A-Z_]+)@@", lambda m: values[m.group(1)], path.read_text(encoding="utf-8"))
    lines: list[str] = []
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        include = re.fullmatch(rf"(\s*)include {re.escape(NEKO_DIR)}/(\S+);", line)
        if include is None:
            lines.append(line)
            continue
        indent, target = include.groups()
        lines.extend(indent + inner if inner else inner for inner in _render(sources[target], sources, values))
    return lines


def official_server_block(variant: str) -> str:
    """Effective ``server`` block of ``conf.d/neko-proxy.conf`` for one variant."""
    sources = _rendered_sources(variant)
    return "\n".join(_render(NGINX_DIR / "neko-proxy.conf.template", sources, _placeholders()))

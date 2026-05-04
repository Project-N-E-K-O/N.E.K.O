from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse


SHARE_DIR = Path("/home/yun_wan/下载")
BASE_NAME = "TB322FC-Hyperos-3.0.5.0.WPYCNXM.7z"
EXPECTED_SHA256 = "69718ea58e255229c6f678e7b7f74d28f9c72b73f06d69c3c28d7e18011ddfd7"

app = FastAPI(title="Astral File Share")


def shared_files() -> list[Path]:
    files = []
    original = SHARE_DIR / BASE_NAME
    if original.exists():
        files.append(original)
    files.extend(sorted(SHARE_DIR.glob(f"{BASE_NAME}.part*")))
    return files


def resolve_shared_file(filename: str) -> Path:
    allowed = {path.name: path for path in shared_files()}
    path = allowed.get(filename)
    if path is None or not path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return path


def file_size_label(size: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}" if unit != "B" else f"{size} B"
        value /= 1024
    return f"{size} B"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    rows = []
    for path in shared_files():
        stat = path.stat()
        rows.append(
            "<tr>"
            f"<td><a href='/download/{path.name}'>{path.name}</a></td>"
            f"<td>{file_size_label(stat.st_size)}</td>"
            f"<td><code>curl -O http://HOST:8765/download/{path.name}</code></td>"
            "</tr>"
        )

    rows_html = "\n".join(rows) or "<tr><td colspan='3'>No shared files found.</td></tr>"
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Astral File Share</title>
  <style>
    body {{ font-family: system-ui, -apple-system, Segoe UI, sans-serif; margin: 32px; color: #17202a; }}
    h1 {{ font-size: 24px; margin-bottom: 8px; }}
    p {{ color: #52616b; }}
    table {{ border-collapse: collapse; width: 100%; margin-top: 24px; }}
    th, td {{ border-bottom: 1px solid #d9e2ec; padding: 12px 10px; text-align: left; }}
    th {{ background: #f4f7fb; }}
    code {{ background: #eef2f7; padding: 2px 5px; border-radius: 4px; }}
    a {{ color: #0b65c2; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
  </style>
</head>
<body>
  <h1>Astral File Share</h1>
  <p>Only the firmware archive and its split parts are shared. Use an Astral virtual LAN IP as HOST.</p>
  <p><a href="/manifest.json">manifest.json</a> | <a href="/sha256.txt">sha256.txt</a></p>
  <table>
    <thead><tr><th>File</th><th>Size</th><th>Command</th></tr></thead>
    <tbody>{rows_html}</tbody>
  </table>
</body>
</html>
"""


@app.get("/manifest.json")
async def manifest() -> JSONResponse:
    files = [
        {
            "name": path.name,
            "size": path.stat().st_size,
            "url": f"/download/{path.name}",
        }
        for path in shared_files()
    ]
    return JSONResponse(
        {
            "base_name": BASE_NAME,
            "expected_sha256": EXPECTED_SHA256,
            "files": files,
            "windows_merge": (
                "cmd /c copy /b "
                + "+".join(path.name for path in shared_files() if ".part" in path.name)
                + f" {BASE_NAME}"
            ),
        }
    )


@app.get("/sha256.txt", response_class=PlainTextResponse)
async def sha256_text() -> str:
    lines = [f"{EXPECTED_SHA256}  {BASE_NAME}"]
    for path in shared_files():
        if path.name == BASE_NAME:
            continue
        lines.append(f"{sha256_file(path)}  {path.name}")
    return "\n".join(lines) + "\n"


@app.get("/download/{filename}")
async def download(filename: str) -> FileResponse:
    path = resolve_shared_file(filename)
    return FileResponse(path, filename=path.name, media_type="application/octet-stream")


@app.head("/download/{filename}")
async def download_head(filename: str) -> FileResponse:
    path = resolve_shared_file(filename)
    return FileResponse(path, filename=path.name, media_type="application/octet-stream")

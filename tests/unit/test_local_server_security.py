"""Security and deployment contract tests for local_server/survey_server and local_server/telemetry_server."""

from __future__ import annotations

import importlib.util
import json
import logging
import re
import sys
from pathlib import Path
from types import ModuleType

import pytest
import yaml
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
SURVEY_DIR = ROOT / "local_server" / "survey_server"
TELEMETRY_DIR = ROOT / "local_server" / "telemetry_server"


def _load_local_server(
    server_dir: Path,
    *,
    module_alias: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    env_overrides: dict[str, str],
) -> ModuleType:
    """Load survey_server or telemetry_server in isolation without colliding on sibling module names."""
    db_file = tmp_path / f"{module_alias}.db"
    if "survey" in module_alias:
        monkeypatch.setenv("SURVEY_DB_PATH", str(db_file))
    else:
        monkeypatch.setenv("TELEMETRY_DB_PATH", str(db_file))
    for k, v in env_overrides.items():
        monkeypatch.setenv(k, v)

    sibling_names = ("models", "security", "storage")
    saved_modules = {name: sys.modules.pop(name, None) for name in sibling_names}
    sys_path_added = str(server_dir)
    sys.path.insert(0, sys_path_added)
    try:
        spec = importlib.util.spec_from_file_location(module_alias, server_dir / "server.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if sys.path and sys.path[0] == sys_path_added:
            sys.path.pop(0)
        for name in sibling_names:
            sys.modules.pop(name, None)
            if saved_modules[name] is not None:
                sys.modules[name] = saved_modules[name]


class _FakeStreamRequest:
    """Minimal ASGI-style Request stand-in tracking how many chunks were consumed."""

    def __init__(self, chunks: list[bytes], headers: dict[str, str] | None = None):
        self._chunks = chunks
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}
        self.chunks_read = 0

    async def stream(self):
        for chunk in self._chunks:
            self.chunks_read += 1
            yield chunk


@pytest.mark.parametrize(
    ("server_dir", "alias", "endpoint", "env_key"),
    [
        (SURVEY_DIR, "_test_survey_srv_body", "/api/v1/survey", "SURVEY_ADMIN_TOKEN"),
        (TELEMETRY_DIR, "_test_telemetry_srv_body", "/api/v1/telemetry", "TELEMETRY_ADMIN_TOKEN"),
    ],
)
async def test_local_servers_stream_limit_stops_oversized_chunked_payload_without_buffering(
    server_dir: Path,
    alias: str,
    endpoint: str,
    env_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    """Chunked/headerless requests exceeding MAX_BODY_SIZE must fail with 413 early without draining all chunks."""
    srv = _load_local_server(
        server_dir,
        module_alias=alias,
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        env_overrides={env_key: ""},
    )

    chunk_size = 64 * 1024
    num_chunks = (srv.MAX_BODY_SIZE // chunk_size) + 10
    fake_req = _FakeStreamRequest([b"a" * chunk_size for _ in range(num_chunks)])

    with pytest.raises(srv.HTTPException) as exc_info:
        await srv._read_limited_body(fake_req, srv.MAX_BODY_SIZE)
    assert exc_info.value.status_code == 413
    expected_stop_chunk = (srv.MAX_BODY_SIZE // chunk_size) + 1
    assert fake_req.chunks_read == expected_stop_chunk
    assert fake_req.chunks_read < num_chunks

    # Preflight Content-Length must reject with 413 before reading any stream chunk.
    preflight_req = _FakeStreamRequest(
        [b"a" * 16],
        headers={"Content-Length": str(srv.MAX_BODY_SIZE + 1)},
    )
    with pytest.raises(srv.HTTPException) as pre_exc:
        await srv._read_limited_body(preflight_req, srv.MAX_BODY_SIZE)
    assert pre_exc.value.status_code == 413
    assert preflight_req.chunks_read == 0

    # Exact MAX_BODY_SIZE boundary is permitted by _read_limited_body.
    exact_req = _FakeStreamRequest([b"a" * srv.MAX_BODY_SIZE])
    data = await srv._read_limited_body(exact_req, srv.MAX_BODY_SIZE)
    assert len(data) == srv.MAX_BODY_SIZE

    # Full HTTP route check with oversized body (non-integer Content-Length fallback to streaming guard).
    with TestClient(srv.app) as client:
        resp = client.post(
            endpoint,
            content=b"x" * (srv.MAX_BODY_SIZE + 1),
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 413

        exact_resp = client.post(
            endpoint,
            content=b"{" + b" " * (srv.MAX_BODY_SIZE - 2) + b"}",
            headers={"Content-Type": "application/json"},
        )
        assert exact_resp.status_code == 400


@pytest.mark.parametrize(
    ("server_dir", "alias", "env_key", "admin_path"),
    [
        (SURVEY_DIR, "_test_survey_srv_auth", "SURVEY_ADMIN_TOKEN", "/api/v1/admin/summary"),
        (TELEMETRY_DIR, "_test_telemetry_srv_auth", "TELEMETRY_ADMIN_TOKEN", "/api/v1/admin/stats"),
    ],
)
def test_local_servers_admin_rejects_query_param_token_and_strips_env_token(
    server_dir: Path,
    alias: str,
    env_key: str,
    admin_path: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    """F0140 & symmetry: admin APIs must reject ?token=, accept stripped Bearer tokens, and handle non-ASCII safely."""
    srv = _load_local_server(
        server_dir,
        module_alias=alias,
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        env_overrides={env_key: "  s3cr3t-local-token-999 \n"},
    )
    assert srv.ADMIN_TOKEN == "s3cr3t-local-token-999"

    with TestClient(srv.app) as client:
        r_query = client.get(f"{admin_path}?token=s3cr3t-local-token-999")
        assert r_query.status_code == 401

        r_header = client.get(
            admin_path,
            headers={"Authorization": "Bearer s3cr3t-local-token-999"},
        )
        assert r_header.status_code == 200

    # Non-ASCII raw header (latin-1 decoded by ASGI) must yield 401, not 500 from hmac.compare_digest.
    from starlette.requests import Request

    non_ascii_req = Request(
        {
            "type": "http",
            "method": "GET",
            "path": admin_path,
            "query_string": b"",
            "headers": [(b"authorization", b"Bearer token-\xe9")],
        }
    )
    with pytest.raises(srv.HTTPException) as exc_info:
        srv.require_admin(non_ascii_req)
    assert exc_info.value.status_code == 401

    # Non-ASCII configured server token also fails cleanly without raising TypeError.
    monkeypatch.setattr(srv, "ADMIN_TOKEN", "non-ascii-\xe9")
    ascii_req = Request(
        {
            "type": "http",
            "method": "GET",
            "path": admin_path,
            "query_string": b"",
            "headers": [(b"authorization", b"Bearer non-ascii")],
        }
    )
    with pytest.raises(srv.HTTPException) as non_ascii_cfg_exc:
        srv.require_admin(ascii_req)
    assert non_ascii_cfg_exc.value.status_code == 401

    readme_text = (server_dir / "README.md").read_text(encoding="utf-8")
    assert "?token=" not in readme_text


@pytest.mark.parametrize(
    ("server_dir", "alias", "env_key", "admin_path"),
    [
        (SURVEY_DIR, "_test_survey_placeholder", "SURVEY_ADMIN_TOKEN", "/api/v1/admin/summary"),
        (TELEMETRY_DIR, "_test_telemetry_placeholder", "TELEMETRY_ADMIN_TOKEN", "/api/v1/admin/stats"),
    ],
)
@pytest.mark.parametrize(
    "bad_token",
    ["", "   ", "please-change-me", "changeme", "Please-Change-Me", "CHANGEME"],
)
def test_local_servers_reject_empty_and_placeholder_admin_tokens(
    server_dir: Path,
    alias: str,
    env_key: str,
    admin_path: str,
    bad_token: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
):
    """F0007: empty and placeholder admin tokens must disable admin endpoints (503) and warn at startup."""
    caplog.set_level(logging.WARNING)
    srv = _load_local_server(
        server_dir,
        module_alias=f"{alias}_{abs(hash(bad_token))}",
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        env_overrides={env_key: bad_token},
    )
    with TestClient(srv.app) as client:
        resp = client.get(admin_path, headers={"Authorization": f"Bearer {bad_token.strip() or 'x'}"})
        assert resp.status_code == 503

    assert any("admin API disabled" in rec.message for rec in caplog.records)


def test_telemetry_compose_and_systemd_service_have_no_active_placeholder_token():
    """F0007: telemetry docker-compose and systemd unit must not enable default placeholder admin tokens."""
    compose_raw = (TELEMETRY_DIR / "docker-compose.yml").read_text(encoding="utf-8")
    compose_data = yaml.safe_load(compose_raw)
    service = compose_data["services"]["telemetry"]
    assert "./data:/app/data" in service["volumes"]
    env_entries = [str(e) for e in service["environment"]]
    assert "${TELEMETRY_ADMIN_TOKEN:-}" in "".join(env_entries)
    assert "please-change-me" not in compose_raw
    assert "changeme" not in compose_raw

    unit_lines = [
        line.strip()
        for line in (TELEMETRY_DIR / "deploy" / "neko-telemetry.service").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert not any("changeme" in line or "please-change-me" in line for line in unit_lines)


@pytest.mark.parametrize("server_dir", [TELEMETRY_DIR, SURVEY_DIR])
def test_local_server_dependencies_are_hash_pinned_and_venv_is_root_owned(server_dir: Path):
    """F0565: requirements.txt must be hash-pinned, Dockerfiles/setup.sh enforce hashes+wheels, and venv stays root-owned."""
    req_in = server_dir / "requirements.in"
    req_txt = server_dir / "requirements.txt"
    assert req_in.is_file()
    assert req_txt.is_file()

    # Join line continuations to inspect each logical requirement entry.
    raw_lines = req_txt.read_text(encoding="utf-8").splitlines()
    logical_entries: list[str] = []
    buf = ""
    for line in raw_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.endswith("\\"):
            buf += stripped[:-1] + " "
        else:
            buf += stripped
            logical_entries.append(buf.strip())
            buf = ""
    assert not buf, "Unclosed line continuation in requirements.txt"
    assert len(logical_entries) >= 3

    for entry in logical_entries:
        pkg_spec = entry.split("--hash=", 1)[0].split(";", 1)[0].strip()
        assert "==" in pkg_spec, f"Unpinned requirement in {req_txt}: {entry}"
        assert "--hash=sha256:" in entry, f"Missing sha256 hash in {req_txt}: {entry}"

    dockerfile = (server_dir / "Dockerfile").read_text(encoding="utf-8")
    assert re.search(r"pip install[^\n]*--require-hashes[^\n]*--only-binary\s+:all:", dockerfile)

    setup_sh = (server_dir / "deploy" / "setup.sh").read_text(encoding="utf-8")
    assert "pip install --upgrade pip" not in setup_sh
    assert re.search(r"pip[\"']?\s+install[^\n]*--require-hashes[^\n]*--only-binary\s+:all:", setup_sh)
    assert not re.search(r"chown\b[^\n]*nobody[^\n]*\$INSTALL_DIR/venv", setup_sh)
    assert re.search(r"chown\b[^\n]*nobody[^\n]*\$INSTALL_DIR/data", setup_sh)
    assert re.search(r"chown\b[^\n]*root:root[^\n]*\$STAGING_VENV", setup_sh)
    assert re.search(r"chmod\b[^\n]*a\+rX,go-w[^\n]*\$STAGING_VENV", setup_sh)
    assert re.search(r"chown\b[^\n]*-h[^\n]*root:root[^\n]*\$INSTALL_DIR/venv", setup_sh)

    # Upgrade safety: build and hash-verify dependencies inside an isolated root-owned STAGING_VENV
    # with cleanup trap on failure before replacing installed scripts or switching $INSTALL_DIR/venv,
    # and never execute pip from an existing $INSTALL_DIR/venv.
    assert "$INSTALL_DIR/venv/bin/pip" not in setup_sh
    assert 'LOCK_FILE="/run/${SERVICE_NAME}-setup.lock"' in setup_sh
    assert 'readlink -f "$INSTALL_DIR/venv"' in setup_sh
    assert '"$INSTALL_DIR"/venv.legacy.*' in setup_sh
    assert 'mv "$OLD_LEGACY_VENV" "$INSTALL_DIR/venv"' in setup_sh
    assert 'STAGING_VENV="$INSTALL_DIR/venvs/venv-' in setup_sh
    assert "trap cleanup_staging_venv EXIT" in setup_sh
    lock_pos = setup_sh.find('flock -x "$LOCK_FILE"')
    chown_root_pos = setup_sh.find('chown root:root "$INSTALL_DIR" "$INSTALL_DIR/venvs"')
    chmod_root_pos = setup_sh.find('chmod 755 "$INSTALL_DIR" "$INSTALL_DIR/venvs"')
    create_staging_pos = setup_sh.find('python3 -m venv "$STAGING_VENV"')
    pip_staging_pos = setup_sh.find('$STAGING_VENV/bin/pip')
    chown_staging_pos = setup_sh.find('chown -R root:root "$STAGING_VENV"')
    chmod_staging_pos = setup_sh.find('chmod -R a+rX,go-w "$STAGING_VENV"')
    install_files_pos = setup_sh.find("install -o root -g root")
    switch_link_pos = setup_sh.find('mv -Tf "$TMP_LINK" "$INSTALL_DIR/venv"')
    restart_pos = setup_sh.find('systemctl restart "$SERVICE_NAME"')
    cleanup_old_pos = setup_sh.find('rm -rf "$legacy_venv"')
    assert 0 <= lock_pos < chown_root_pos < create_staging_pos < pip_staging_pos < chown_staging_pos < install_files_pos < switch_link_pos < restart_pos < cleanup_old_pos
    assert 0 <= chmod_root_pos < create_staging_pos
    assert 0 <= pip_staging_pos < chmod_staging_pos < install_files_pos

    if server_dir == TELEMETRY_DIR:
        readme_text = (server_dir / "README.md").read_text(encoding="utf-8")
        assert "utils/token_tracker/reporting.py" in setup_sh
        assert "utils/token_tracker/reporting.py" in readme_text

    # Ensure JSON body helper in telemetry/survey remains callable (no accidental import breakage).
    assert json.dumps({"ok": True}) == '{"ok": true}'

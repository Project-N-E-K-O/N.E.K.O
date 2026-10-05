"""Run an isolated Chromium/mock-IdP acceptance test with production handlers.

Use uv run. Supply the compiled auth platform relay module and a Playwright
module path plus Chrome executable. No production services/credentials are used.
Self-signed TLS exceptions are confined to this fixture, not product code.
"""

import argparse
import asyncio
import base64
import datetime
import hashlib
import json
import os
import re
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import urlencode

import httpx
import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def run(args):
    """Exercise real cookies, opener isolation and TLS using disposable state."""
    with tempfile.TemporaryDirectory(prefix="neko-oauth-browser-") as directory:
        root = Path(directory)
        backend_port, auth_port = free_port(), free_port()
        backend_origin = f"https://backend.neko.test:{backend_port}"
        auth_origin = f"https://auth.neko.test:{auth_port}"
        key = "fixture-only-instance-key-" + "x" * 40
        os.environ.update(NEKO_BEHIND_PROXY="true", NEKO_INSTANCE_ACCESS_KEY=key,
                          NEKO_STORAGE_SELECTED_ROOT=str(root), NEKO_AUTH_URL=auth_origin, NEKO_SOCIAL_BASE_URL=auth_origin,
                          NEKO_TRUSTED_HOSTS="backend.neko.test")
        for name in ("NEKO_INSTANCE_PUBLIC_ORIGIN", "NEKO_COMMUNITY_WEB_REDIRECT_URI", "NEKO_COMMUNITY_WEB_CLIENT_ID"):
            os.environ.pop(name, None)
        # Set the disposable storage root before modules initialize configuration.
        from main_routers import card_drop_router as C, community_oauth as O
        from utils.instance_access import InstanceAccessMiddleware
        from utils.host_origin_guard import HostOriginGuardMiddleware
        C._auth_path = lambda: root / "community_auth.json"
        C._social_session_path = lambda: root / "social_session.json"
        C._legacy_social_session_path = C._social_session_path
        C._social_session_paths = lambda: [C._social_session_path()]
        O._oauth_pending_path = lambda: root / "community_oauth_pending.json"
        challenge = {}
        idp = FastAPI()

        @idp.get("/oauth2/auth")
        async def authorize(request: Request):
            query = request.query_params
            assert query["redirect_uri"] == auth_origin + "/oauth/callback"
            assert query["client_id"] == "neko-servers-web-prod"
            challenge.update(query)
            target = query["redirect_uri"] + "?" + urlencode({"state": query["state"], "code": "fixture-one-time-code"})
            script = "(async()=>{const had=Boolean(window.opener);if(had)window.opener.location=" + json.dumps(backend_origin + "/phishing-probe") + ";await fetch('/fixture-opener-probe?had='+had);location.replace(" + json.dumps(target) + ");})();"
            return HTMLResponse("<script>" + script + "</script>", headers={"Cross-Origin-Opener-Policy": "same-origin"})

        @idp.get("/fixture-opener-probe")
        async def opener_probe(had: str):
            challenge["had_opener"] = had == "true"
            return {"ok": True}

        @idp.get("/oauth/callback")
        async def relay(request: Request):
            # Execute the auth platform's production relay, including its CSP.
            code = "const {remoteOAuthRelayResponse}=require(process.argv[1]);const r=remoteOAuthRelayResponse(new Request(process.argv[2]));r.text().then(body=>console.log(JSON.stringify({body,status:r.status,headers:Object.fromEntries(r.headers)})));"
            output = await asyncio.to_thread(subprocess.run, ["node", "-e", code, args.auth_relay_module,
                                     auth_origin + str(request.url.path) + "?" + str(request.url.query)],
                                    check=True, capture_output=True, text=True)
            result = json.loads(output.stdout)
            return HTMLResponse(result["body"], status_code=result["status"], headers=result["headers"])

        @idp.post("/oauth2/token")
        async def exchange(request: Request):
            from urllib.parse import parse_qs

            data = {name: value[0] for name, value in parse_qs((await request.body()).decode()).items()}
            actual = base64.urlsafe_b64encode(hashlib.sha256(data["code_verifier"].encode()).digest()).decode().rstrip("=")
            assert actual == challenge["code_challenge"]
            assert data["redirect_uri"] == challenge["redirect_uri"]
            assert data["client_id"] == challenge["client_id"]
            assert data["code"] == "fixture-one-time-code" and not challenge.get("redeemed")
            challenge["redeemed"] = True
            return {"access_token": "fixture-cloud-access", "refresh_token": "fixture-cloud-refresh"}

        async def exchange_code(**fields):
            await asyncio.sleep(1)  # The popup may close while Linux redeems.
            async with httpx.AsyncClient(verify=False, trust_env=False) as client:
                result = await client.post(f"https://127.0.0.1:{auth_port}/oauth2/token", data={
                    "grant_type": "authorization_code", **{name: value for name, value in fields.items() if name != "auth_public_url"}})
                result.raise_for_status()
                return result.json()

        async def bootstrap(_base, token):
            assert token == "fixture-cloud-access"
            return {"user": {"id": "aabbccdd-1111-4222-8333-123456789abc", "email": "fixture@example.test"}}

        async def guest_bind(_base, _token):
            return {"bound": True, "error": None}

        async def saved_status():
            auth = C._read_json_dict(C._auth_path()) or {}
            return {"logged_in": bool(auth.get("access_token")), "auth": auth,
                    "snapshot": C._desktop_session_snapshot()}

        O._exchange_oauth_code, O._bootstrap_session = exchange_code, bootstrap
        O._oauth_guest_bind, O.resolve_saved_oauth_status = guest_bind, saved_status
        backend = FastAPI()
        for router in (C.router, O.router, O.callback_router):
            backend.include_router(router)
        source = (ROOT / "static/app/app-ui/surface-floating-controls.js").read_text(encoding="utf-8")
        start = source.index("if (oauthJson.relay_origin) {")
        end = source.index("if (!navigateBrowserPopup", start)
        production_listener = source[start:end]
        start = source.index("let remoteRelayChannel = null;")
        end = source.index("const oauthCompletedStates", start)
        production_navigation = source[start:end]
        start = source.index("const waitForOAuthCompletion =")
        end = source.index("const openElectronSocialWindow", start)
        production_completion = source[start:end]
        production_navigation_call = re.search(
            r"if \(!(?P<call>navigateBrowserPopup\(authUrl,[^\n]+?\))\) \{", source
        ).group("call")
        return_navigation = re.search(
            r"if \((?P<condition>popupRef \|\| \(remoteRelayChannel && remoteRelayChannel.completed\))\) \{\s*if \(!(?P<call>navigateBrowserPopup\(refreshedTargetUrl.toString\(\)\))\)", source)
        production_return_navigation = "const refreshedTargetUrl=new URL(url);if(" + return_navigation.group("condition") + ")return " + return_navigation.group("call") + ";return false;"

        @backend.get("/")
        async def home():
            script = """window.oauthCompletedStates=new Set();window.oauthPendingRelays=new Set();document.querySelector('button').onclick=async()=>{
                let popupRef=window.open('about:blank');
                const socialOpenGeneration=1, isSocialOpenRequestCurrent=()=>true;
                const attachResolvedTheme=()=>{}, registerSocialThemeTarget=()=>null, queueSocialThemeSync=()=>{};
                const forgetSocialWindow=()=>{};
                const oauthCompletedStates=window.oauthCompletedStates, oauthPendingRelays=window.oauthPendingRelays;
                const oauthJson=await (await fetch('/api/card-drop/oauth/start',{method:'POST'})).json();
                const authUrl=oauthJson.auth_url;
                window.browserOAuthState=oauthJson.state;const browserOAuthState=oauthJson.state;
                const browserOAuthTimeoutMs=30000;
                """ + production_navigation + production_completion + production_listener + production_navigation_call + ";" + """
                window.completion=await waitForOAuthCompletion(30000,browserOAuthState);
                window.navigateRemoteCommunity=(url)=>{""" + production_return_navigation + "};};"
            return HTMLResponse('<button id="login-community">OAuth fixture</button><script>' + script + '</script>')

        backend.add_middleware(InstanceAccessMiddleware, community_handoff_authorizer=C.authorize_community_handoff)
        backend.add_middleware(HostOriginGuardMiddleware)
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "NEKO isolated test")])
        now = datetime.datetime.now(datetime.timezone.utc)
        certificate = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
                       .public_key(private_key.public_key()).serial_number(x509.random_serial_number())
                       .not_valid_before(now - datetime.timedelta(minutes=1)).not_valid_after(now + datetime.timedelta(days=1))
                       .add_extension(x509.SubjectAlternativeName([x509.DNSName("backend.neko.test"), x509.DNSName("auth.neko.test")]), False)
                       .sign(private_key, hashes.SHA256()))
        cert_path, key_path = root / "cert.pem", root / "key.pem"
        cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
        key_path.write_bytes(private_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        servers = [uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, ssl_certfile=str(cert_path),
                                                ssl_keyfile=str(key_path), access_log=False, log_level="error"))
                   for app, port in ((backend, backend_port), (idp, auth_port))]
        threads = [threading.Thread(target=server.run, daemon=True) for server in servers]
        try:
            for thread in threads:
                thread.start()
            deadline = time.time() + 15
            while not all(server.started for server in servers):
                if time.time() > deadline:
                    raise TimeoutError("Fixture servers did not start")
                time.sleep(.05)
            env = {**os.environ, "NEKO_TEST_PLAYWRIGHT_MODULE": args.playwright_module,
                   "NEKO_TEST_CHROME": args.chrome, "NEKO_TEST_BACKEND_ORIGIN": backend_origin,
                   "NEKO_TEST_AUTH_ORIGIN": auth_origin, "NEKO_TEST_INSTANCE_KEY": key}
            env["NEKO_TEST_KEEP_POPUP"] = "1" if args.keep_popup else "0"
            from tests.node_harness import run_node_script
            run_node_script("node", "import(" + json.dumps((ROOT / "tests/frontend/remote_oauth_browser.mjs").as_uri()) + ");", env=env, check=True, timeout=60)
            assert challenge.get("redeemed")
            assert challenge.get("had_opener") is False
            assert C._read_json_dict(C._auth_path())["refresh_token"] == "fixture-cloud-refresh"
        finally:
            for server in servers:
                server.should_exit = True
            for thread in threads:
                thread.join(timeout=5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--auth-relay-module", required=True)
    parser.add_argument("--playwright-module", required=True)
    parser.add_argument("--chrome", required=True)
    parser.add_argument("--keep-popup", action="store_true")
    run(parser.parse_args())

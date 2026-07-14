#!/usr/bin/env python3
"""Time-gated daily token intake — paste the Upstox trading token from a browser.

Three auth modes, chosen by env:
  - PASSKEY (WebAuthn/FIDO2): set WEBAUTHN_RP_ID + WEBAUTHN_ORIGIN. Public-capable
    (behind HTTPS — WebAuthn needs a secure context). Phishing-resistant, no shared
    secret. First enrollment requires TOKEN_INTAKE_BOOTSTRAP_PIN so a stranger can't
    enroll during the public window.
  - PIN: set TOKEN_INTAKE_PIN (and no WEBAUTHN_RP_ID). Simple shared PIN.
  - LOCALHOST: neither set → binds 127.0.0.1 only (use an SSH tunnel).

The token value is never logged. One successful save shuts the server down (closes
the port). Pair with systemd RuntimeMaxSec so the port also closes if no one submits.

PUBLIC DEPLOY (passkey): run behind HTTPS. Simplest — Caddy auto-TLS:
    token.example.com { reverse_proxy 127.0.0.1:8733 }
then WEBAUTHN_RP_ID=token.example.com, WEBAUTHN_ORIGIN=https://token.example.com,
TOKEN_INTAKE_HOST=127.0.0.1 (Caddy faces the internet; this stays on localhost).

Env: TOKEN_INTAKE_HOST (default 127.0.0.1), TOKEN_INTAKE_PORT (default 8733),
     TOKEN_INTAKE_PIN, TOKEN_INTAKE_BOOTSTRAP_PIN, WEBAUTHN_* (see webauthn_gate),
     UPSTOX_TOKEN_FILE.
"""
from __future__ import annotations

import html
import json
import os
import secrets
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie
from urllib.parse import parse_qs

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from autotrader.core.token_store import write_token, token_status  # noqa: E402

HOST = os.getenv("TOKEN_INTAKE_HOST", "127.0.0.1")
PORT = int(os.getenv("TOKEN_INTAKE_PORT", "8733"))
PIN = os.getenv("TOKEN_INTAKE_PIN", "").strip()
BOOTSTRAP_PIN = os.getenv("TOKEN_INTAKE_BOOTSTRAP_PIN", "").strip()

_PASSKEY = bool(os.getenv("WEBAUTHN_RP_ID"))
if _PASSKEY:
    from autotrader.core import webauthn_gate as wa

# In-memory, single-operator morning state.
_STATE: dict = {"reg_challenge": None, "auth_challenge": None, "sessions": set()}


def _new_session() -> str:
    sid = secrets.token_urlsafe(24)
    _STATE["sessions"].add(sid)
    return sid


# ── HTML ──────────────────────────────────────────────────────────────────────

_STYLE = """<style>
body{font-family:system-ui;max-width:520px;margin:6vh auto;padding:0 18px;background:#0f1115;color:#e6e6e6}
input,button{width:100%;padding:12px;margin:8px 0;border-radius:8px;font-size:16px;box-sizing:border-box}
input{border:1px solid #333;background:#181b20;color:#e6e6e6}
button{border:0;background:#2d7;color:#04210f;font-weight:700;cursor:pointer}
.msg{padding:10px;border-radius:8px;margin:10px 0}.ok{background:#12351f}.err{background:#3a1616}
small,.muted{color:#8a8f98}.hidden{display:none}</style>"""

_PIN_BODY = """<h2>AutoTrader — paste today's trading token</h2>
<p><small>{status}</small></p>{msg}
<form method=post action="/save-token">
{pin_field}<label>Upstox access token</label>
<input name=token type=password autocomplete=off autofocus placeholder="paste token">
<button type=submit>Save token</button></form>
<p class=muted><small>Closes itself after a successful save.</small></p>"""

_PASSKEY_BODY = """<h2>AutoTrader — token intake</h2>
<p><small>%STATUS%</small></p><div id=msg></div>
<div id=enroll class="%ENROLL_CLS%">
  <p class=muted>No passkey enrolled yet. Enroll one (bootstrap PIN required).</p>
  <input id=bpin type=password autocomplete=off placeholder="bootstrap PIN">
  <button onclick="enroll()">Enroll passkey</button></div>
<div id=authbox class="%AUTH_CLS%">
  <button onclick="authenticate()">Unlock with passkey</button></div>
<div id=tokenbox class=hidden>
  <label>Upstox access token</label>
  <input id=token type=password autocomplete=off placeholder="paste token">
  <button onclick="saveToken()">Save token</button></div>
<p class=muted><small>Passkey-gated. Closes itself after a successful save.</small></p>
<script>
const b64uToBuf=s=>{s=s.replace(/-/g,'+').replace(/_/g,'/');const p='='.repeat((4-s.length%4)%4);
  const b=atob(s+p);const u=new Uint8Array(b.length);for(let i=0;i<b.length;i++)u[i]=b.charCodeAt(i);return u.buffer;};
const bufToB64u=b=>{const u=new Uint8Array(b);let s='';for(let i=0;i<u.length;i++)s+=String.fromCharCode(u[i]);
  return btoa(s).replace(/\\+/g,'-').replace(/\\//g,'_').replace(/=+$/,'');};
const msg=(t,ok)=>{document.getElementById('msg').innerHTML='<div class="msg '+(ok?'ok':'err')+'">'+t+'</div>';};
async function post(u,b){const r=await fetch(u,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b||{})});
  return {ok:r.ok,data:await r.json().catch(()=>({}))};}
async function enroll(){try{
  const pin=document.getElementById('bpin').value;
  const b=await post('/register/begin',{pin});if(!b.ok){msg(b.data.error||'begin failed',0);return;}
  const o=b.data;o.challenge=b64uToBuf(o.challenge);o.user.id=b64uToBuf(o.user.id);
  (o.excludeCredentials||[]).forEach(c=>c.id=b64uToBuf(c.id));
  const cred=await navigator.credentials.create({publicKey:o});
  const out={id:cred.id,rawId:bufToB64u(cred.rawId),type:cred.type,clientExtensionResults:{},
    response:{attestationObject:bufToB64u(cred.response.attestationObject),
    clientDataJSON:bufToB64u(cred.response.clientDataJSON)}};
  const f=await post('/register/finish',{pin,credential:out});
  if(f.ok){msg('Passkey enrolled. Unlocking…',1);location.reload();}else{msg(f.data.error||'enroll failed',0);}
}catch(e){msg('enroll error: '+e,0);}}
async function authenticate(){try{
  const b=await post('/auth/begin',{});if(!b.ok){msg(b.data.error||'begin failed',0);return;}
  const o=b.data;o.challenge=b64uToBuf(o.challenge);
  (o.allowCredentials||[]).forEach(c=>c.id=b64uToBuf(c.id));
  const cred=await navigator.credentials.get({publicKey:o});
  const out={id:cred.id,rawId:bufToB64u(cred.rawId),type:cred.type,clientExtensionResults:{},
    response:{authenticatorData:bufToB64u(cred.response.authenticatorData),
    clientDataJSON:bufToB64u(cred.response.clientDataJSON),
    signature:bufToB64u(cred.response.signature),
    userHandle:cred.response.userHandle?bufToB64u(cred.response.userHandle):null}};
  const f=await post('/auth/finish',{credential:out});
  if(f.ok){document.getElementById('authbox').classList.add('hidden');
    document.getElementById('tokenbox').classList.remove('hidden');msg('Unlocked. Paste the token.',1);}
  else{msg(f.data.error||'auth failed',0);}
}catch(e){msg('auth error: '+e,0);}}
async function saveToken(){const t=document.getElementById('token').value;
  const f=await post('/save-token',{token:t});
  if(f.ok){msg('Saved. You can close this tab.',1);}else{msg(f.data.error||'save failed',0);}}
</script>"""


def _page_body() -> str:
    st = token_status()
    status = "fresh token present ✓" if st["fresh_today"] else (
        "stale token on disk" if st["present"] else "no token yet")
    if _PASSKEY:
        registered = wa.is_registered()
        return (_PASSKEY_BODY
                .replace("%STATUS%", html.escape(status))
                .replace("%ENROLL_CLS%", "hidden" if registered else "")
                .replace("%AUTH_CLS%", "" if registered else "hidden"))
    pin_field = ('<label>PIN</label><input name=pin type=password autocomplete=off>' if PIN else "")
    return _PIN_BODY.format(status=html.escape(status), msg="", pin_field=pin_field)


def _page(body: str | None = None) -> bytes:
    return f"<!doctype html><html><head><meta name=viewport content='width=device-width,initial-scale=1'>" \
           f"<title>AutoTrader token</title>{_STYLE}</head><body>{body or _page_body()}</body></html>".encode()


class Handler(BaseHTTPRequestHandler):
    server_version = "tokenintake/2.0"

    def _send(self, code, body, ctype="text/html; charset=utf-8", cookie=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, obj, cookie=None):
        self._send(code, json.dumps(obj).encode(), "application/json", cookie)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length", 0) or 0)
        if n <= 0 or n > 65536:
            return {}
        raw = self.rfile.read(n).decode("utf-8", "replace")
        if self.headers.get("Content-Type", "").startswith("application/json"):
            try:
                return json.loads(raw)
            except Exception:
                return {}
        return {k: v[0] for k, v in parse_qs(raw).items()}

    def _session_ok(self) -> bool:
        c = SimpleCookie(self.headers.get("Cookie", ""))
        sid = c["sid"].value if "sid" in c else None
        return sid in _STATE["sessions"]

    def _shutdown_soon(self):
        import threading
        threading.Thread(target=self.server.shutdown, daemon=True).start()

    def do_GET(self):
        if self.path.split("?")[0] not in ("/", "/index.html"):
            self._send(404, b"not found")
            return
        self._send(200, _page())

    def do_POST(self):
        path = self.path.split("?")[0]
        body = self._body()

        # ── PIN / localhost mode: single form POST ──
        if not _PASSKEY and path == "/save-token":
            if PIN and (body.get("pin", "") or "").strip() != PIN:
                self._send(403, _page('<div class="msg err">Wrong PIN.</div>' + _page_body()))
                return
            return self._save(body.get("token", ""))

        if not _PASSKEY:
            self._send(404, b"not found")
            return

        # ── Passkey mode ──
        if path == "/register/begin":
            if not wa.is_registered():
                if not BOOTSTRAP_PIN or (body.get("pin", "") or "").strip() != BOOTSTRAP_PIN:
                    self._json(403, {"error": "bootstrap PIN required for first enrollment"})
                    return
            elif not BOOTSTRAP_PIN or (body.get("pin", "") or "").strip() != BOOTSTRAP_PIN:
                self._json(403, {"error": "bootstrap PIN required to add a device"})
                return
            opts_json, challenge = wa.begin_registration()
            _STATE["reg_challenge"] = challenge
            self._json(200, json.loads(opts_json))
            return

        if path == "/register/finish":
            ch = _STATE.get("reg_challenge")
            if not ch:
                self._json(400, {"error": "no active registration"})
                return
            try:
                wa.finish_registration(json.dumps(body.get("credential")), ch)
                _STATE["reg_challenge"] = None
                self._json(200, {"ok": True})
            except Exception as exc:
                self._json(400, {"error": f"registration failed: {exc}"})
            return

        if path == "/auth/begin":
            if not wa.is_registered():
                self._json(400, {"error": "no passkey enrolled"})
                return
            opts_json, challenge = wa.begin_authentication()
            _STATE["auth_challenge"] = challenge
            self._json(200, json.loads(opts_json))
            return

        if path == "/auth/finish":
            ch = _STATE.get("auth_challenge")
            if not ch:
                self._json(400, {"error": "no active authentication"})
                return
            try:
                ok = wa.finish_authentication(json.dumps(body.get("credential")), ch)
                _STATE["auth_challenge"] = None
                if not ok:
                    self._json(403, {"error": "assertion rejected"})
                    return
                sid = _new_session()
                self._json(200, {"ok": True}, cookie=f"sid={sid}; HttpOnly; Path=/; SameSite=Strict")
            except Exception as exc:
                self._json(400, {"error": f"auth failed: {exc}"})
            return

        if path == "/save-token":
            if not self._session_ok():
                self._json(403, {"error": "authenticate with your passkey first"})
                return
            return self._save(body.get("token", ""), as_json=True)

        self._send(404, b"not found")

    def _save(self, token, as_json=False):
        token = (token or "").strip()
        if not token:
            (self._json(400, {"error": "empty token"}) if as_json
             else self._send(400, _page('<div class="msg err">Empty token.</div>' + _page_body())))
            return
        try:
            write_token(token)
        except Exception as exc:
            (self._json(500, {"error": str(exc)}) if as_json
             else self._send(500, _page(f'<div class="msg err">{html.escape(str(exc))}</div>')))
            return
        # NEVER log the token — only its length.
        print(f"token accepted (len={len(token)}) — shutting down intake", flush=True)
        if as_json:
            self._json(200, {"ok": True})
        else:
            self._send(200, _page('<div class="msg ok">Saved. You can close this tab.</div>'))
        self._shutdown_soon()

    def log_message(self, *args):
        return  # silence request logging (never log form data)


def main():
    off_localhost = HOST not in ("127.0.0.1", "localhost", "::1")
    if off_localhost and not (_PASSKEY or PIN):
        print("REFUSING to bind off-localhost without a passkey (WEBAUTHN_RP_ID) or "
              "TOKEN_INTAKE_PIN. Use an SSH tunnel, or configure auth.", file=sys.stderr)
        sys.exit(2)
    mode = "PASSKEY" if _PASSKEY else ("PIN" if PIN else "LOCALHOST-only")
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"token intake on http://{HOST}:{PORT}  auth={mode} — until a token is saved "
          f"or the window ends", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Time-gated daily token intake — paste the Upstox trading token from a browser.

The Upstox OAuth trading token expires daily. This serves a tiny form; you paste
today's token once and it's written (0600, gitignored) via token_store. Designed to
be opened only for a short window each morning by a systemd timer, and to shut
itself down the moment a token is accepted — so the port is closed the rest of the day.

SECURITY MODEL (read before exposing beyond localhost):
  - Binds to TOKEN_INTAKE_HOST (default 127.0.0.1). Only set 0.0.0.0 if you accept
    the risk and rely on the time-gate + PIN + firewall. Prefer an SSH tunnel:
        ssh -L 8733:127.0.0.1:8733 user@host   then browse http://127.0.0.1:8733
  - If TOKEN_INTAKE_PIN is set, the form requires it (defense-in-depth during the
    open window). Use a real PIN if the port is reachable off-localhost.
  - The token value is never logged. Only its length is echoed.
  - Accepts exactly one successful submission, then exits (closes the port).
  - Pair with systemd RuntimeMaxSec so the port also closes if no one submits.

Env:
  TOKEN_INTAKE_HOST (default 127.0.0.1), TOKEN_INTAKE_PORT (default 8733),
  TOKEN_INTAKE_PIN (optional), UPSTOX_TOKEN_FILE (where token_store writes).
"""
from __future__ import annotations

import html
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from autotrader.core.token_store import write_token, token_status  # noqa: E402

HOST = os.getenv("TOKEN_INTAKE_HOST", "127.0.0.1")
PORT = int(os.getenv("TOKEN_INTAKE_PORT", "8733"))
PIN = os.getenv("TOKEN_INTAKE_PIN", "").strip()

_PAGE = """<!doctype html><html><head><meta name=viewport content="width=device-width,initial-scale=1">
<title>AutoTrader — daily token</title><style>
body{{font-family:system-ui;max-width:520px;margin:6vh auto;padding:0 18px;background:#0f1115;color:#e6e6e6}}
input{{width:100%;padding:12px;margin:8px 0;border-radius:8px;border:1px solid #333;background:#181b20;color:#e6e6e6;font-size:16px}}
button{{padding:12px 18px;border:0;border-radius:8px;background:#2d7;color:#04210f;font-weight:700;font-size:16px}}
.msg{{padding:10px;border-radius:8px;margin:10px 0}} .ok{{background:#12351f}} .err{{background:#3a1616}}
small{{color:#8a8f98}}</style></head><body>
<h2>AutoTrader — paste today's trading token</h2>
<p><small>Status: {status}</small></p>
{msg}
<form method=post>
{pin_field}
<label>Upstox access token</label>
<input name=token type=password autocomplete=off autofocus placeholder="paste token, then Submit">
<button type=submit>Save token</button>
</form>
<p><small>This page closes itself after a successful save.</small></p>
</body></html>"""


def _render(msg_html: str = "") -> bytes:
    st = token_status()
    status = "fresh token present ✓" if st["fresh_today"] else (
        "stale token on disk" if st["present"] else "no token yet")
    pin_field = ('<label>PIN</label><input name=pin type=password autocomplete=off>'
                 if PIN else "")
    return _PAGE.format(status=html.escape(status), msg=msg_html, pin_field=pin_field).encode()


class Handler(BaseHTTPRequestHandler):
    server_version = "tokenintake/1.0"

    def _send(self, code: int, body: bytes):
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.split("?")[0] not in ("/", "/index.html"):
            self._send(404, b"not found")
            return
        self._send(200, _render())

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length > 8192:  # a token is short; reject oversized bodies
            self._send(413, b"too large")
            return
        fields = parse_qs(self.rfile.read(length).decode("utf-8", "replace"))
        token = (fields.get("token", [""])[0] or "").strip()
        pin = (fields.get("pin", [""])[0] or "").strip()

        if PIN and pin != PIN:
            self._send(403, _render('<div class="msg err">Wrong PIN.</div>'))
            return
        if not token:
            self._send(400, _render('<div class="msg err">Empty token.</div>'))
            return
        try:
            write_token(token)
        except Exception as exc:
            self._send(500, _render(f'<div class="msg err">Save failed: {html.escape(str(exc))}</div>'))
            return
        # NEVER log the token — only its length.
        print(f"token accepted (len={len(token)}) — shutting down intake", flush=True)
        self._send(200, _render('<div class="msg ok">Saved. You can close this tab.</div>'))
        # Shut the server down from another thread so this response flushes first.
        import threading
        threading.Thread(target=self.server.shutdown, daemon=True).start()

    def log_message(self, *args):
        # Silence default request logging (avoids any chance of logging form data).
        return


def main():
    if HOST not in ("127.0.0.1", "localhost", "::1") and not PIN:
        print("REFUSING to bind off-localhost without TOKEN_INTAKE_PIN set.", file=sys.stderr)
        print("Set TOKEN_INTAKE_PIN, or use an SSH tunnel and keep HOST=127.0.0.1.", file=sys.stderr)
        sys.exit(2)
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"token intake on http://{HOST}:{PORT}  (PIN {'set' if PIN else 'OFF'}) — "
          f"serves until a token is saved or the service window ends", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()

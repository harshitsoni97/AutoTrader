"""Daily OAuth *trading* token store (separate from the analytics token).

Upstox's trading/OAuth access token (UPSTOX_ACCESS_TOKEN) expires daily and has no
refresh token, so it must be re-supplied each morning. This module is the single
read/write point for that token:

  - read_token(): env UPSTOX_ACCESS_TOKEN wins (e.g. injected by systemd); otherwise
    the gitignored on-disk token written by the intake service, but ONLY if it was
    written today (a stale token is worse than none — it 401s mid-session).
  - write_token(): persist to a 0600 file under a 0700 dir. The token value is NEVER
    logged.

Security: the file path defaults under the user's home, is created 0700/0600, and is
gitignored. Nothing here prints or logs the token itself.
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path


def _token_path() -> Path:
    p = os.getenv("UPSTOX_TOKEN_FILE")
    if p:
        return Path(p).expanduser()
    return Path.home() / ".autotrader" / "upstox_access_token"


def write_token(token: str) -> Path:
    """Persist the trading token to a 0600 file. Returns the path. Never logs the value."""
    token = (token or "").strip()
    if not token:
        raise ValueError("empty token")
    path = _token_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Write then tighten perms (umask-safe).
    with open(path, "w") as f:
        f.write(token + "\n")
    os.chmod(path, 0o600)
    return path


def read_token(require_fresh: bool = True) -> str | None:
    """Return the trading token, or None. Env var wins; else today's on-disk token.

    require_fresh: if the file was last written on a prior day, treat it as absent
    (a stale daily token only causes 401s). Set False to accept any on-disk token.
    """
    env = os.getenv("UPSTOX_ACCESS_TOKEN")
    if env:
        return env.strip()
    path = _token_path()
    if not path.exists():
        return None
    if require_fresh:
        try:
            mtime_day = date.fromtimestamp(path.stat().st_mtime)
            if mtime_day != date.today():
                return None
        except Exception:
            return None
    try:
        tok = path.read_text().strip()
        return tok or None
    except Exception:
        return None


def token_status() -> dict:
    """Non-sensitive status for health checks / the intake page. No token value."""
    path = _token_path()
    exists = path.exists()
    fresh = read_token(require_fresh=True) is not None
    return {"present": read_token(require_fresh=False) is not None,
            "fresh_today": fresh, "path": str(path), "file_exists": exists}

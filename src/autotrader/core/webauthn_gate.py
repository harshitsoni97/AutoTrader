"""Passkey (WebAuthn/FIDO2) gate for the public token-intake page.

Phishing-resistant, hardware-backed auth so the intake form can be exposed publicly
(behind HTTPS — WebAuthn requires a secure context) without a shared secret to leak.

Model:
  - First passkey enrollment is protected by a one-time bootstrap PIN
    (TOKEN_INTAKE_BOOTSTRAP_PIN) so a stranger can't enroll their OWN key during the
    public window. Once ≥1 credential exists, enrolling additional devices also
    requires that PIN.
  - Thereafter, reaching the token form requires a successful passkey assertion.
  - Credentials (id, public key, sign_count) live in a gitignored 0600 JSON file.
    No secret token is stored here — only public keys.

Config (env):
  WEBAUTHN_RP_ID     — the domain, e.g. "token.example.com" (no scheme/port)
  WEBAUTHN_ORIGIN    — full origin, e.g. "https://token.example.com"
  WEBAUTHN_RP_NAME   — display name (default "AutoTrader Token Intake")
  WEBAUTHN_CRED_FILE — credential store path (default ~/.autotrader/webauthn_credentials.json)
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from webauthn import (
    generate_registration_options, verify_registration_response,
    generate_authentication_options, verify_authentication_response, options_to_json,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria, ResidentKeyRequirement,
    UserVerificationRequirement, PublicKeyCredentialDescriptor,
)


def rp_id() -> str | None:
    return os.getenv("WEBAUTHN_RP_ID") or None


def origin() -> str | None:
    return os.getenv("WEBAUTHN_ORIGIN") or None


def _rp_name() -> str:
    return os.getenv("WEBAUTHN_RP_NAME", "AutoTrader Token Intake")


def _cred_path() -> Path:
    p = os.getenv("WEBAUTHN_CRED_FILE")
    return Path(p).expanduser() if p else Path.home() / ".autotrader" / "webauthn_credentials.json"


def _load() -> list[dict]:
    path = _cred_path()
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text()) or []
    except Exception:
        return []


def _save(creds: list[dict]) -> None:
    path = _cred_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text(json.dumps(creds, indent=2))
    os.chmod(path, 0o600)


def is_registered() -> bool:
    return len(_load()) > 0


def max_credentials() -> int:
    """Hard cap on enrolled passkeys (default 2 — e.g. two users/devices)."""
    try:
        return max(1, int(os.getenv("WEBAUTHN_MAX_CREDENTIALS", "2")))
    except ValueError:
        return 2


def credential_count() -> int:
    return len(_load())


def at_capacity() -> bool:
    return credential_count() >= max_credentials()


# ── Registration ceremony ────────────────────────────────────────────────────

def begin_registration() -> tuple[str, bytes]:
    """Return (options_json, challenge_bytes). Caller keeps the challenge for finish."""
    opts = generate_registration_options(
        rp_id=rp_id(),
        rp_name=_rp_name(),
        user_name="autotrader-operator",
        user_display_name="AutoTrader Operator",
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
        exclude_credentials=[
            PublicKeyCredentialDescriptor(id=base64url_to_bytes(c["id"])) for c in _load()
        ],
    )
    return options_to_json(opts), opts.challenge


def finish_registration(credential_json: str, challenge: bytes) -> bool:
    v = verify_registration_response(
        credential=credential_json,
        expected_challenge=challenge,
        expected_rp_id=rp_id(),
        expected_origin=origin(),
    )
    creds = _load()
    creds.append({
        "id": bytes_to_base64url(v.credential_id),
        "public_key": bytes_to_base64url(v.credential_public_key),
        "sign_count": v.sign_count,
    })
    _save(creds)
    return True


# ── Authentication ceremony ──────────────────────────────────────────────────

def begin_authentication() -> tuple[str, bytes]:
    opts = generate_authentication_options(
        rp_id=rp_id(),
        allow_credentials=[
            PublicKeyCredentialDescriptor(id=base64url_to_bytes(c["id"])) for c in _load()
        ],
        user_verification=UserVerificationRequirement.PREFERRED,
    )
    return options_to_json(opts), opts.challenge


def finish_authentication(credential_json: str, challenge: bytes) -> bool:
    """Verify an assertion against stored credentials; bump sign_count on success."""
    parsed = json.loads(credential_json)
    cred_id = parsed.get("id") or parsed.get("rawId")
    creds = _load()
    match = next((c for c in creds if c["id"] == cred_id), None)
    if not match:
        return False
    v = verify_authentication_response(
        credential=credential_json,
        expected_challenge=challenge,
        expected_rp_id=rp_id(),
        expected_origin=origin(),
        credential_public_key=base64url_to_bytes(match["public_key"]),
        credential_current_sign_count=match.get("sign_count", 0),
    )
    match["sign_count"] = v.new_sign_count
    _save(creds)
    return True

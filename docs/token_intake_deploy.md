# Daily Token Intake — Deploy & Check (Tailscale Funnel + Passkey)

Status: the intake **service + page + passkey auth are built**; this is how to
**deploy** it on OCI and make it reachable from anywhere (public, no client on the
visitor's device) via **Tailscale Funnel**, gated by a **passkey**.

All commands run on the OCI box.

---

## 1. One-time: Tailscale (gives a stable public HTTPS URL, free)

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up            # log in via the printed URL
# In the Tailscale admin console: enable MagicDNS and HTTPS Certificates (once).
tailscale status             # note this machine's name, e.g. "autotradervnic"
```

Your public hostname will be:  `<machine>.<your-tailnet>.ts.net`
e.g. `autotradervnic.tailXXXX.ts.net`  — **stable** (passkeys bind to it).

---

## 2. Configure the intake (.env on OCI)

Add to `~/AutoTrader/.env` (use YOUR ts.net hostname):

```
TOKEN_INTAKE_HOST=127.0.0.1
TOKEN_INTAKE_PORT=8733
WEBAUTHN_RP_ID=autotradervnic.tailXXXX.ts.net
WEBAUTHN_ORIGIN=https://autotradervnic.tailXXXX.ts.net
TOKEN_INTAKE_BOOTSTRAP_PIN=<a strong one-time PIN, for first passkey enrollment only>
```

The service stays on localhost; Tailscale Funnel faces the internet and terminates
TLS (WebAuthn needs HTTPS — Funnel provides it).

---

## 3. Publish the local port via Funnel

```bash
sudo tailscale funnel --bg 8733     # expose local :8733 to the public internet
tailscale funnel status             # prints the public https URL — this is your page
```

## 4. Install the timer (opens the intake each trading morning)

```bash
cd ~/AutoTrader && git pull origin claude/dazzling-mendel-pa8dtv
sudo cp systemd/autotrader-token-intake.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now autotrader-token-intake.timer
```

The service opens 08:15 IST and self-closes on token save or after ~2h.

---

## 5. First-time passkey enrollment (once)

1. Start the service now for enrollment (or wait for the timer window):
   ```bash
   sudo systemctl start autotrader-token-intake.service
   ```
2. Open the Funnel URL (from step 3) in a browser **on your phone, from anywhere**.
3. You'll see the "Enroll passkey" box → enter the `TOKEN_INTAKE_BOOTSTRAP_PIN` →
   tap **Enroll passkey** → approve with Face ID / fingerprint. Enrolled.

## 6. Daily use

Open the URL → **Unlock with passkey** → paste today's Upstox token → Save. Done;
the page closes itself. `token_store` writes it 0600 and the trading code reads it.

---

## How to CHECK it's working

```bash
# service up?
systemctl status autotrader-token-intake.service --no-pager | grep -E "Active|Main PID"

# public URL live?
tailscale funnel status

# local page responds? (should print HTML with 'AutoTrader')
curl -s http://127.0.0.1:8733/ | grep -o '<title>[^<]*</title>'

# after you save a token, confirm it landed (no token value printed):
PYTHONPATH=src python -c "from autotrader.core.token_store import token_status; print(token_status())"
# -> {'present': True, 'fresh_today': True, ...}
```

Open the Funnel URL in any browser — that IS the page. If the passkey ceremony errors
on first enroll, it's almost always a mismatch between the browser's address and
`WEBAUTHN_ORIGIN` — they must be byte-identical (scheme + host, no trailing slash).

---

## Security notes
- The page is public but **passkey-gated** — no shared secret to leak. First
  enrollment needs the bootstrap PIN so a stranger can't enroll during the window.
- The token is **never logged** (only its length). Stored 0600, gitignored.
- Funnel exposes only port 8733's HTTP to Tailscale's edge; no other inbound port.
- Alternative (no public exposure at all): skip Funnel, use `tailscale serve` — then
  the page is reachable only from devices on your tailnet.

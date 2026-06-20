"""Multi-channel outbound notifications for trades, exits, summaries and errors.

Supported channels: Telegram, Email (SMTP), WhatsApp/SMS (Twilio), Slack.

Design principles (production-grade):
  * Credentials come ONLY from environment variables — never from config or any
    committed file. The YAML config decides *whether* and *where* to notify, the
    environment supplies the secrets.
  * Every channel degrades gracefully. A missing credential or a network failure
    logs a warning and is swallowed — notifications must NEVER raise into the
    trading path or fail an order.
  * Sending happens through the same resilient HTTP primitives used elsewhere
    (timeout + bounded retry), so a slow Telegram API can't hang the graph.

Required env vars per channel:
  telegram : TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
  email    : SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, EMAIL_FROM, EMAIL_TO
  whatsapp : TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM, TWILIO_TO
  slack    : SLACK_WEBHOOK_URL
"""

from __future__ import annotations

import logging
import os
import smtplib
from email.mime.text import MIMEText
from typing import TYPE_CHECKING

import requests

if TYPE_CHECKING:
    from autotrader.core.config import NotificationConfig

logger = logging.getLogger(__name__)


def _post(url: str, *, json=None, data=None, auth=None, timeout: float = 10.0) -> bool:
    """POST with a single retry; returns True on 2xx, False otherwise (never raises)."""
    for attempt in range(2):
        try:
            resp = requests.post(url, json=json, data=data, auth=auth, timeout=timeout)
            if 200 <= resp.status_code < 300:
                return True
            logger.warning("notification POST %s -> HTTP %d: %s", url, resp.status_code, resp.text[:200])
        except Exception as exc:  # noqa: BLE001 — notifications must never crash trading
            logger.warning("notification POST failed (attempt %d): %s", attempt + 1, exc)
    return False


# --------------------------------------------------------------------------- #
# Per-channel senders
# --------------------------------------------------------------------------- #
def _send_telegram(subject: str, body: str, timeout: float) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        logger.warning("telegram channel enabled but TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID not set")
        return False
    api_base = os.getenv("TELEGRAM_API_BASE", "https://api.telegram.org").rstrip("/")
    text = f"*{subject}*\n{body}"
    return _post(
        f"{api_base}/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
        timeout=timeout,
    )


def _send_slack(subject: str, body: str, timeout: float) -> bool:
    webhook = os.getenv("SLACK_WEBHOOK_URL")
    if not webhook:
        logger.warning("slack channel enabled but SLACK_WEBHOOK_URL not set")
        return False
    return _post(webhook, json={"text": f"*{subject}*\n{body}"}, timeout=timeout)


def _send_whatsapp(subject: str, body: str, timeout: float) -> bool:
    sid = os.getenv("TWILIO_ACCOUNT_SID")
    token = os.getenv("TWILIO_AUTH_TOKEN")
    from_ = os.getenv("TWILIO_FROM")   # e.g. "whatsapp:+14155238886"
    to_ = os.getenv("TWILIO_TO")       # e.g. "whatsapp:+91XXXXXXXXXX"
    if not all([sid, token, from_, to_]):
        logger.warning("whatsapp channel enabled but Twilio env vars not fully set")
        return False
    return _post(
        f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
        data={"From": from_, "To": to_, "Body": f"{subject}\n{body}"},
        auth=(sid, token),
        timeout=timeout,
    )


def _send_email(subject: str, body: str, timeout: float) -> bool:
    host = os.getenv("SMTP_HOST")
    port = os.getenv("SMTP_PORT", "587")
    user = os.getenv("SMTP_USER")
    password = os.getenv("SMTP_PASSWORD")
    sender = os.getenv("EMAIL_FROM", user or "")
    recipients = os.getenv("EMAIL_TO", "")
    if not host or not recipients:
        logger.warning("email channel enabled but SMTP_HOST/EMAIL_TO not set")
        return False
    to_list = [r.strip() for r in recipients.split(",") if r.strip()]
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(to_list)
    try:
        with smtplib.SMTP(host, int(port), timeout=timeout) as server:
            server.ehlo()
            try:
                server.starttls()
                server.ehlo()
            except smtplib.SMTPException:
                pass  # server may not support STARTTLS
            if user and password:
                server.login(user, password)
            server.sendmail(sender, to_list, msg.as_string())
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("email notification failed: %s", exc)
        return False


_CHANNELS = {
    "telegram": _send_telegram,
    "slack": _send_slack,
    "whatsapp": _send_whatsapp,
    "email": _send_email,
}


class Notifier:
    """Fan-out notifier. Construct from a NotificationConfig and call .send()."""

    def __init__(self, cfg: "NotificationConfig") -> None:
        self.cfg = cfg

    def send(self, subject: str, body: str) -> dict[str, bool]:
        """Send to all configured channels. Returns {channel: delivered}.

        No-op (returns {}) when notifications are disabled. Never raises.
        """
        if not self.cfg.enabled or not self.cfg.channels:
            logger.warning("notifications disabled or no channels configured (enabled=%s channels=%s)", self.cfg.enabled, self.cfg.channels)
            return {}
        logger.info("sending notification channels=%s subject=%r", self.cfg.channels, subject)
        results: dict[str, bool] = {}
        for channel in self.cfg.channels:
            sender = _CHANNELS.get(channel)
            if sender is None:
                logger.warning("unknown notification channel: %s", channel)
                continue
            try:
                results[channel] = sender(subject, body, self.cfg.timeout_seconds)
            except Exception as exc:  # noqa: BLE001
                logger.warning("notification channel %s raised: %s", channel, exc)
                results[channel] = False
        return results

    # --- Convenience builders for the trading events ----------------------- #
    def notify_order(self, order: dict) -> dict[str, bool]:
        if not self.cfg.notify_on_order:
            return {}
        mode = "DRY RUN" if order.get("status") == "DRY_RUN_ASSUMED" else "LIVE"
        subject = f"[{mode}] Entry — {order.get('symbol')}"
        body = (
            f"Side: {order.get('side')}\n"
            f"Qty: {order.get('qty')}\n"
            f"Fill: ₹{order.get('fill_price')}\n"
            f"Order ID: {order.get('order_id')}"
        )
        return self.send(subject, body)

    def notify_exit(self, exit_info: dict) -> dict[str, bool]:
        if not self.cfg.notify_on_exit:
            return {}
        pnl = exit_info.get("pnl", 0.0)
        emoji = "🟢" if pnl >= 0 else "🔴"
        subject = f"{emoji} Exit ({exit_info.get('reason')}) — {exit_info.get('symbol')}"
        body = f"Reason: {exit_info.get('reason')}\nPnL: ₹{pnl}"
        return self.send(subject, body)

    def notify_daily_summary(self, summary: dict) -> dict[str, bool]:
        if not self.cfg.notify_on_daily_summary:
            return {}
        subject = f"Daily Summary — {summary.get('run_date', '')}"
        body = (
            f"Mode: {'DRY RUN' if summary.get('dry_run') else 'LIVE'}\n"
            f"Trades taken: {summary.get('trades', 0)}\n"
            f"Daily P&L: ₹{summary.get('daily_pnl', 0.0)}\n"
            f"Regime: {summary.get('regime', 'n/a')}"
        )
        return self.send(subject, body)

    def notify_pre_market_summary(self, state: dict) -> dict[str, bool]:
        if not self.cfg.notify_on_daily_summary:
            return {}
        regime = state.get("market_regime", "unknown")
        confidence = state.get("market_confidence", 0.0)
        top_sectors = state.get("top_sectors", [])
        opportunities = state.get("scored_opportunities", [])
        catalysts = state.get("catalysts", [])
        vix = state.get("india_vix", 0.0)
        pcr = state.get("options_pcr", 0.0)
        run_date = state.get("run_date", "")
        dry_run = state.get("dry_run", True)
        mode = "DRY RUN" if dry_run else "LIVE"

        regime_emoji = {
            "bullish": "🟢", "risk_on": "🟢",
            "bearish": "🔴", "risk_off": "🔴",
            "high_volatility": "🟡",
            "range_bound": "⚪",
        }.get(regime, "⚪")

        subject = f"{regime_emoji} Pre-Market [{mode}] — {run_date}"

        top_pick_line = "No eligible opportunity"
        if opportunities:
            top = opportunities[0]
            top_pick_line = f"{top['symbol']} — score {top.get('score', 0):.1f}"

        top_catalyst_line = "None"
        if catalysts:
            c = catalysts[0]
            top_catalyst_line = f"{c['symbol']}: {c.get('reason', '')[:60]}"

        body = (
            f"Regime: {regime} ({confidence*100:.0f}% confidence)\n"
            f"VIX: {vix:.1f}  |  PCR: {pcr:.2f}\n"
            f"Top sectors: {', '.join(top_sectors[:3]) or 'n/a'}\n"
            f"\nTop pick: {top_pick_line}\n"
            f"Top catalyst: {top_catalyst_line}\n"
            f"\nEligible opportunities: {len(opportunities)}"
        )
        return self.send(subject, body)

    def notify_error(self, context: str, error: str) -> dict[str, bool]:
        if not self.cfg.notify_on_error:
            return {}
        return self.send(f"⚠️ AutoTrader error — {context}", error)


def get_notifier(cfg: "NotificationConfig") -> Notifier:
    """Factory mirroring get_broker(cfg.broker)."""
    return Notifier(cfg)

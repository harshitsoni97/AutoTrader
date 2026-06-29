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

import os
import smtplib
from email.mime.text import MIMEText
from typing import TYPE_CHECKING

import structlog
import requests

if TYPE_CHECKING:
    from autotrader.core.config import NotificationConfig

logger = structlog.get_logger()


def _post(url: str, *, json=None, data=None, auth=None, timeout: float = 10.0) -> bool:
    """POST with a single retry; returns True on 2xx, False otherwise (never raises)."""
    for attempt in range(2):
        try:
            resp = requests.post(url, json=json, data=data, auth=auth, timeout=timeout)
            if 200 <= resp.status_code < 300:
                return True
            logger.warning("notification_post_failed", url=url, status=resp.status_code, body=resp.text[:200])
        except Exception as exc:  # noqa: BLE001 — notifications must never crash trading
            logger.warning("notification_post_error", attempt=attempt+1, error=str(exc))
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
            return {}
        results: dict[str, bool] = {}
        for channel in self.cfg.channels:
            sender = _CHANNELS.get(channel)
            if sender is None:
                logger.warning("unknown notification channel: %s", channel)
                continue
            try:
                ok = sender(subject, body, self.cfg.timeout_seconds)
                results[channel] = ok
                if ok:
                    logger.info("notification_sent", channel=channel, subject=subject)
                else:
                    logger.warning("notification_failed", channel=channel, subject=subject)
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
        dry_run = summary.get('dry_run', True)
        mode = "DRY RUN" if dry_run else "LIVE"
        subject = f"Daily Summary [{mode}] — {summary.get('run_date', '')}"

        pnl = summary.get('daily_pnl', 0.0)
        pnl_label = "Assumed P&L" if dry_run else "Realized P&L"
        pnl_emoji = "🟢" if pnl > 0 else ("🔴" if pnl < 0 else "⚪")

        body = (
            f"Mode: {mode}\n"
            f"Regime: {summary.get('regime', 'n/a')}\n"
            f"Trades taken: {summary.get('trades', 0)}\n"
            f"{pnl_emoji} {pnl_label}: ₹{pnl:,.0f}"
        )

        # Append per-trade breakdown when available. Show the exit that actually
        # determined the P&L per scenario — not the EOD close, which is
        # misleading for intraday stop-outs (e.g. a stock stopped on the open dip
        # but closed higher).
        outcomes = summary.get("trade_outcomes", [])
        if outcomes:
            body += "\n\n*Trade outcomes:*"
            for o in outcomes:
                sym = o.get("symbol", "?")
                sc = o.get("scenario", "")
                scenario = sc.replace("_", " ")
                o_pnl = o.get("pnl", 0)
                entry = o.get("entry")
                fill = o.get("fill_price")
                eod = o.get("eod_price")
                stop = o.get("stop")
                t1 = o.get("target1")
                t2 = o.get("target2")
                low = o.get("day_low")
                line = f"\n• {sym}: ₹{o_pnl:+,.0f} ({scenario})"
                if sc == "not_filled":
                    detail = f"limit ₹{entry:.1f} never reached"
                    if low:
                        detail += f" (day low ₹{low:.1f})"
                elif sc == "stopped_out" and fill and stop:
                    detail = f"fill ₹{fill:.1f} → stop ₹{stop:.1f}"
                elif sc == "target2_hit" and fill and t2:
                    detail = f"fill ₹{fill:.1f} → T2 ₹{t2:.1f}"
                elif sc == "target1_hit_partial" and fill and t1:
                    detail = f"fill ₹{fill:.1f} → T1 ₹{t1:.1f}, rest @ close ₹{eod:.1f}" if eod else f"fill ₹{fill:.1f} → T1 ₹{t1:.1f}"
                elif fill and eod:
                    detail = f"fill ₹{fill:.1f} → close ₹{eod:.1f}"
                elif entry and eod:
                    detail = f"entry ₹{entry:.1f} → close ₹{eod:.1f}"
                else:
                    detail = ""
                if detail:
                    line += f" | {detail}"
                body += line

        # Passive heartbeat: cumulative trade-journal size (dataset for tuning review)
        jt = summary.get("journal_total")
        if jt:
            body += f"\n\n📒 Trade journal: {jt} trades recorded so far"

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
            top_catalyst_line = f"{c['symbol']}: {c.get('reason', '')[:120]}"

        body = (
            f"Regime: {regime} ({confidence*100:.0f}% confidence)\n"
            f"VIX: {vix:.1f}  |  PCR: {pcr:.2f}\n"
            f"Top sectors: {', '.join(top_sectors[:3]) or 'n/a'}\n"
            f"\nTop pick: {top_pick_line}\n"
            f"Top catalyst: {top_catalyst_line}\n"
            f"\nEligible opportunities: {len(opportunities)}"
        )
        return self.send(subject, body)

    def notify_compete_summary(self, competitor_results: list, run_date: str = "", dry_run: bool = True, trade_plan: dict | None = None) -> dict[str, bool]:
        if not self.cfg.notify_on_daily_summary or not competitor_results:
            return {}
        mode = "DRY RUN" if dry_run else "LIVE"
        eod = any(r.get("hypothetical_pnl_pct") is not None for r in competitor_results)

        # At end-of-day, sort by P&L so winner is on top
        def _pnl_key(r):
            v = r.get("hypothetical_pnl_pct")
            return v if v is not None else float("-inf")
        results = sorted(competitor_results, key=_pnl_key, reverse=True) if eod else competitor_results

        # Joint ranking: stacks with identical P&L share the same rank/medal.
        # (standard competition ranking — tied picks are not split by list order)
        pnl_values = [_pnl_key(r) for r in results]
        ranks = [1 + sum(1 for v in pnl_values if v > pnl_values[i]) for i in range(len(results))]

        subject = f"{'🏆 Compete EOD Leaderboard' if eod else 'Compete Picks'} [{mode}] — {run_date}"
        lines = []

        # Trade plan block (ATR-based levels from trade_construction_agent)
        if trade_plan and trade_plan.get("symbol"):
            tp = trade_plan
            rr = tp.get("rr", tp.get("risk_reward", 0))
            lines.append(
                f"📊 *Trade Plan — {tp['symbol']}*\n"
                f"   Entry: ₹{tp.get('entry', 0):.2f}  |  Stop: ₹{tp.get('stop', 0):.2f}  |  "
                f"Target 1: ₹{tp.get('target1', 0):.2f}  |  Target 2: ₹{tp.get('target2', 0):.2f}\n"
                f"   Qty: {tp.get('qty', 0)}  |  Risk:Reward: {rr:.1f}R  |  "
                f"Capital: ₹{tp.get('position_size_inr', 0):,.0f}"
            )
        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        for i, r in enumerate(results):
            name = r.get("name", "?")
            pick = r.get("pick") or "—"
            score = r.get("adjusted_score")
            score_str = f"{score:.1f}" if score is not None else "n/a"
            regime = r.get("regime", "?")
            passed = r.get("pass_review", True)
            pnl = r.get("hypothetical_pnl_pct")
            pnl_str = f"{pnl:+.2f}%" if pnl is not None else "pending"
            veto = "" if passed else " [VETOED]"
            rationale = r.get("rationale", "")[:120]
            concerns = r.get("concerns", [])
            concern_str = f"\n   ⚠ {'; '.join(concerns[:2])}" if concerns else ""
            # Tie-aware medal: same P&L → same medal; mark joint placings with "=".
            if eod:
                rank = ranks[i]
                tied = pnl_values.count(pnl_values[i]) > 1
                medal = medals.get(rank, f"#{rank}")
                prefix = f"{medal}=" if tied else medal
            else:
                prefix = "•"
            lines.append(
                f"{prefix} *{name}*{veto}: {pick} (score {score_str}, regime {regime}, P&L {pnl_str})\n"
                f"   {rationale}{concern_str}"
            )
        body = "\n\n".join(lines)
        return self.send(subject, body)

    def notify_error(self, context: str, error: str) -> dict[str, bool]:
        if not self.cfg.notify_on_error:
            return {}
        return self.send(f"⚠️ AutoTrader error — {context}", error)


def get_notifier(cfg: "NotificationConfig") -> Notifier:
    """Factory mirroring get_broker(cfg.broker)."""
    return Notifier(cfg)

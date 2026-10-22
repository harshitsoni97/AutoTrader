"""LSE market calendar — the UK equivalent of the NSE time-window + holiday logic.

London Stock Exchange core hours: 08:00-16:30 Europe/London (handles BST/GMT via
zoneinfo automatically). Trading days are Mon-Fri excluding England & Wales bank
holidays. Mirrors scripts/run_intraday.is_market_open: the time window + holiday list is
AUTHORITATIVE; the swing/UK system reads this to decide whether to act today.
"""

from __future__ import annotations

from datetime import datetime, time, date
from zoneinfo import ZoneInfo

LONDON = ZoneInfo("Europe/London")
OPEN = time(8, 0)
CLOSE = time(16, 30)

# England & Wales bank holidays (LSE closed). Extend yearly. Early closes (24 & 31 Dec,
# 12:30) are treated as normal days here — intraday callers can special-case if needed.
_BANK_HOLIDAYS: set[str] = {
    # 2026
    "2026-01-01", "2026-04-03", "2026-04-06", "2026-05-04", "2026-05-25",
    "2026-08-31", "2026-12-25", "2026-12-28",
    # 2027
    "2027-01-01", "2027-03-26", "2027-03-29", "2027-05-03", "2027-05-31",
    "2027-08-30", "2027-12-27", "2027-12-28",
}


def is_holiday(day: date) -> bool:
    return day.isoformat() in _BANK_HOLIDAYS


def now_london() -> datetime:
    return datetime.now(LONDON)


def is_market_open(at: datetime | None = None) -> bool:
    """True iff `at` (default now) is within LSE core hours on a trading day."""
    dt = (at.astimezone(LONDON) if at else now_london())
    if dt.weekday() >= 5:
        return False
    if is_holiday(dt.date()):
        return False
    return OPEN <= dt.time() <= CLOSE


def is_trading_day(day: date | None = None) -> bool:
    day = day or now_london().date()
    return day.weekday() < 5 and not is_holiday(day)


def session_bounds(day: date | None = None) -> tuple[datetime, datetime]:
    """Open/close datetimes (London tz) for `day`."""
    day = day or now_london().date()
    return (datetime.combine(day, OPEN, LONDON), datetime.combine(day, CLOSE, LONDON))

"""Data-snapshot coherence helpers.

Agents fetch their market data sequentially, so the options chain might be from
09:12 while technicals are from 09:14. Full simultaneous capture is a larger
refactor; as a first step we stamp every source's fetch time and flag when two
sources drift beyond a threshold, so misaligned snapshots are visible rather
than silent.
"""

from __future__ import annotations

from datetime import datetime

# Sources drifting more than this are flagged as a potentially incoherent snapshot.
MAX_DRIFT_SECONDS = 180


def stamp(source: str) -> list[dict]:
    """Return a data_fetch_log entry for `source` at now. Merge into agent output:
        return {..., "data_fetch_log": stamp("market_regime")}
    """
    return [{"source": source, "ts": datetime.now().isoformat(timespec="seconds")}]


def check_drift(fetch_log: list[dict]) -> tuple[float, list[str]]:
    """Return (max_drift_seconds, [warnings]) across all stamped sources."""
    times = []
    for e in fetch_log or []:
        try:
            times.append((e["source"], datetime.fromisoformat(e["ts"])))
        except Exception:
            continue
    if len(times) < 2:
        return 0.0, []
    newest = max(t for _, t in times)
    oldest = min(t for _, t in times)
    drift = (newest - oldest).total_seconds()
    warnings = []
    if drift > MAX_DRIFT_SECONDS:
        oldest_src = min(times, key=lambda x: x[1])[0]
        newest_src = max(times, key=lambda x: x[1])[0]
        warnings.append(
            f"Data snapshot drift {drift:.0f}s (> {MAX_DRIFT_SECONDS}s): "
            f"'{oldest_src}' oldest vs '{newest_src}' newest"
        )
    return round(drift, 1), warnings

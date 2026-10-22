"""Mean-reversion (oversold-bounce) signal — market-agnostic, long-only, pure.

Our book is ALL momentum/breakout (ORB, composite). That bleeds on the choppy/down
days (7/23, 8/14, 8/17-19). Mean-reversion is the natural counter-weight: it buys
capitulation and sells the snap-back, and historically does BETTER exactly when
momentum does worse — genuine diversification, not another correlated long.

The setup (long only): a name that is (a) OVERSOLD — RSI low AND price stretched below
its moving average (lower band) — and (b) showing a BOUNCE TRIGGER — the latest bar
closes back UP after the washout (close > prior close, and reclaiming the band). We do
NOT buy a falling knife: without the up-bar trigger there is no signal. Stop goes below
the recent swing low; target is the mean (SMA) — classic reversion-to-mean exit.

Pure: takes OHLC bars (India: Upstox; UK: Yahoo), returns a signal dict or None. No
network, no indicators library.
"""

from __future__ import annotations

from dataclasses import dataclass


def rsi(closes: list[float], period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
    gains = losses = 0.0
    for i in range(len(closes) - period, len(closes)):
        ch = closes[i] - closes[i - 1]
        gains += max(ch, 0.0)
        losses += max(-ch, 0.0)
    avg_gain = gains / period
    avg_loss = losses / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - 100 / (1 + rs), 2)


def sma(closes: list[float], period: int) -> float | None:
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period


def _stdev(xs: list[float], mean: float) -> float:
    if len(xs) < 2:
        return 0.0
    return (sum((x - mean) ** 2 for x in xs) / (len(xs) - 1)) ** 0.5


@dataclass
class MeanRevConfig:
    rsi_period: int = 14
    rsi_oversold: float = 30.0      # must be at/below this to be "oversold"
    sma_period: int = 20            # the mean we revert TO
    band_k: float = 2.0             # lower band = SMA - band_k * stdev
    swing_lookback: int = 10        # stop below the low of the last N bars
    stop_buffer: float = 0.002      # extra cushion below the swing low (0.2%)
    min_rr: float = 1.0             # require target-to-mean to clear this R:R


def oversold_bounce_signal(bars: list[dict], cfg: MeanRevConfig | None = None) -> dict | None:
    """Return a long mean-reversion signal dict, or None if no setup on the latest bar.

    Signal: {strategy, side, entry, stop, target, rsi, reason}. entry = latest close,
    stop = recent swing low (buffered), target = SMA (the mean). Only fires on the
    bounce bar, never on the way down.
    """
    cfg = cfg or MeanRevConfig()
    closes = [float(b["close"]) for b in bars if b.get("close") and float(b["close"]) > 0]
    lows = [float(b.get("low", b.get("close", 0)) or 0) for b in bars]
    need = max(cfg.sma_period, cfg.rsi_period + 1, cfg.swing_lookback) + 1
    if len(closes) < need:
        return None

    r = rsi(closes, cfg.rsi_period)
    mean = sma(closes, cfg.sma_period)
    if r is None or mean is None:
        return None
    window = closes[-cfg.sma_period:]
    sd = _stdev(window, mean)
    lower_band = mean - cfg.band_k * sd

    last, prev = closes[-1], closes[-2]

    # (a) OVERSOLD on the prior bar's washout: RSI low and prior close under the band.
    was_oversold = (r <= cfg.rsi_oversold) and (prev < lower_band)
    # (b) BOUNCE TRIGGER: latest bar closes up and reclaims toward the band.
    bounced = (last > prev) and (last >= lower_band * 0.999)
    if not (was_oversold and bounced):
        return None

    entry = last
    swing_low = min(lows[-cfg.swing_lookback:]) if lows else prev
    stop = round(min(swing_low, prev) * (1 - cfg.stop_buffer), 2)
    target = round(mean, 2)              # revert to the mean
    if stop >= entry or target <= entry:
        return None
    rr = (target - entry) / (entry - stop)
    if rr < cfg.min_rr:
        return None

    return {
        "strategy": "MEAN_REVERSION",
        "side": "LONG",
        "entry": round(entry, 2),
        "stop": stop,
        "target1": target,
        "target": target,
        "rsi": r,
        "rr": round(rr, 2),
        "reason": f"oversold bounce: RSI {r} <= {cfg.rsi_oversold}, "
                  f"prior close {prev:.2f} < lower band {lower_band:.2f}, "
                  f"bounce to {last:.2f}; target mean {target:.2f}",
    }

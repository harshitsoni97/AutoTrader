"""Load a broker holdings export (Zerodha Console / Groww / Upstox style).

Reads .xlsx (needs openpyxl) or .csv. The header row is found by looking for a
"Symbol" cell, so title rows and summary blocks above the table are skipped.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional

# normalised header -> field. First match wins.
_ALIASES = {
    "symbol": ["symbol", "tradingsymbol", "instrument", "stock", "stock name", "scrip"],
    "isin": ["isin"],
    "sector": ["sector", "industry"],
    "qty": ["quantity available", "quantity", "qty", "qty.", "net qty", "shares"],
    "qty_lt": ["quantity long term", "long term quantity", "lt qty"],
    "avg_price": ["average price", "avg. cost", "avg cost", "average cost", "avg price", "buy avg"],
    "ltp": ["previous closing price", "ltp", "last price", "close price", "current price", "cmp"],
}


@dataclass
class Holding:
    symbol: str
    qty: float
    avg_price: float
    ltp: float
    sector: str = "UNKNOWN"
    isin: str = ""
    qty_lt: Optional[float] = None  # None: the export does not say
    # Purchases made after the export (see apply_new_lots). The export's own
    # shares keep their average in base_avg; these lots are newest, so FIFO sells them last.
    extra_lots: list = field(default_factory=list)
    base_qty: Optional[float] = None
    base_avg: Optional[float] = None

    @property
    def qty_st(self) -> float:
        return self.qty - (self.qty_lt or 0.0)

    @property
    def invested(self) -> float:
        return self.qty * self.avg_price

    @property
    def value(self) -> float:
        return self.qty * self.ltp

    @property
    def pnl(self) -> float:
        return self.value - self.invested

    @property
    def pnl_pct(self) -> float:
        return (self.pnl / self.invested * 100) if self.invested else 0.0

    def to_dict(self) -> dict:
        d = asdict(self)
        d.update(qty_st=self.qty_st, invested=round(self.invested, 2), value=round(self.value, 2),
                 pnl=round(self.pnl, 2), pnl_pct=round(self.pnl_pct, 2))
        return d


def _norm(cell) -> str:
    return str(cell).strip().lower() if cell is not None else ""


def _num(cell) -> Optional[float]:
    if cell is None or cell == "" or cell == "-":
        return None
    if isinstance(cell, (int, float)):
        return float(cell)
    try:
        return float(str(cell).replace(",", "").replace("₹", "").strip())
    except ValueError:
        return None


def _rows_from_file(path: Path) -> list[list]:
    if path.suffix.lower() in (".xlsx", ".xlsm"):
        import openpyxl  # optional dependency, only for Excel exports

        wb = openpyxl.load_workbook(path, data_only=True)
        # Prefer the equity sheet; fall back to the first sheet with a Symbol header.
        names = sorted(wb.sheetnames, key=lambda n: (n.lower() != "equity", n.lower() != "combined"))
        for name in names:
            rows = [list(r) for r in wb[name].iter_rows(values_only=True)]
            if any("symbol" in map(_norm, r) for r in rows):
                return rows
        return []
    with path.open(newline="", encoding="utf-8-sig") as fh:
        return [row for row in csv.reader(fh)]


def parse_rows(rows: list[list]) -> list[Holding]:
    header_idx = next((i for i, r in enumerate(rows) if "symbol" in map(_norm, r)
                       or "instrument" in map(_norm, r)), None)
    if header_idx is None:
        raise ValueError("no header row with a 'Symbol' or 'Instrument' column")
    header = [_norm(c) for c in rows[header_idx]]
    cols = {}
    for field, aliases in _ALIASES.items():
        for alias in aliases:
            if alias in header:
                cols[field] = header.index(alias)
                break
    missing = {"symbol", "qty", "avg_price", "ltp"} - cols.keys()
    if missing:
        raise ValueError(f"holdings file is missing columns: {sorted(missing)}")

    out = []
    for r in rows[header_idx + 1:]:
        sym = r[cols["symbol"]] if cols["symbol"] < len(r) else None
        if not sym or not str(sym).strip():
            continue
        qty, avg, ltp = (_num(r[cols[k]]) for k in ("qty", "avg_price", "ltp"))
        if not qty or avg is None or ltp is None:
            continue
        qty_lt = _num(r[cols["qty_lt"]]) if "qty_lt" in cols else None
        out.append(Holding(
            symbol=str(sym).strip().upper(),
            qty=qty, avg_price=avg, ltp=ltp,
            sector=str(r[cols["sector"]]).strip().upper() if "sector" in cols and r[cols["sector"]] else "UNKNOWN",
            isin=str(r[cols["isin"]]).strip() if "isin" in cols and r[cols["isin"]] else "",
            qty_lt=min(qty_lt, qty) if qty_lt is not None else None,
        ))
    return out


def load_holdings(path: str | Path) -> list[Holding]:
    return parse_rows(_rows_from_file(Path(path)))


def apply_new_lots(holdings: list[Holding], lots: list[dict]) -> list[str]:
    """Add purchases made after the export: [{symbol, qty, price, sector?, isin?}].

    Quantity and average become the blended totals (what the broker will show
    tomorrow); the lot itself is kept so tax uses its real cost. New shares are
    short-term. Returns one line per lot for the report.
    """
    by_sym = {h.symbol: h for h in holdings}
    notes = []
    for lot in lots or []:
        sym, q, px = str(lot["symbol"]).upper(), float(lot["qty"]), float(lot["price"])
        h = by_sym.get(sym)
        if h is None:
            h = Holding(symbol=sym, qty=0.0, avg_price=0.0, ltp=px, sector=str(lot.get("sector", "UNKNOWN")).upper(),
                        isin=str(lot.get("isin", "")), qty_lt=0.0)
            holdings.append(h)
            by_sym[sym] = h
        if h.base_qty is None:
            h.base_qty, h.base_avg = h.qty, h.avg_price
        h.extra_lots.append({"qty": q, "price": px, "date": str(lot.get("date", ""))})
        h.avg_price = (h.qty * h.avg_price + q * px) / (h.qty + q)
        h.qty += q
        notes.append(f"{sym}: +{q:g} @ {px:.2f}{' on ' + str(lot['date']) if lot.get('date') else ''} "
                     f"(now {h.qty:g} shares, average {h.avg_price:.2f})")
    return notes

"""Import a broker P&L statement (Zerodha Console style) as this FY's realised gains.

The P&L lists realised profit per symbol but not when the shares were bought,
so it cannot tell short-term from long-term. Every realised line is treated as
short-term (the conservative choice: 20% and no exemption) unless `terms`
says otherwise, e.g. {"VBL": "long"} from the tradebook or contract notes.
"""
from __future__ import annotations

from pathlib import Path

from .holdings import _norm, _num, _rows_from_file


def load_realised(path: str | Path, terms: dict[str, str] | None = None) -> dict:
    rows = _rows_from_file(Path(path))
    header_idx = next(i for i, r in enumerate(rows) if "realized p&l" in map(_norm, r) and "symbol" in map(_norm, r))
    header = [_norm(c) for c in rows[header_idx]]
    sym_col, pnl_col = header.index("symbol"), header.index("realized p&l")
    sell_col = header.index("sell value") if "sell value" in header else None
    terms = {k.upper(): v for k, v in (terms or {}).items()}

    out = {"stcg": 0.0, "stcl": 0.0, "ltcg": 0.0, "ltcl": 0.0}
    lines, assumed = [], []
    for r in rows[header_idx + 1:]:
        sym = r[sym_col] if sym_col < len(r) else None
        pnl = _num(r[pnl_col]) if pnl_col < len(r) else None
        if not sym or not pnl:
            continue
        sym = str(sym).strip().upper()
        term = terms.get(sym)
        if term not in ("short", "long"):
            term = "short"
            assumed.append(sym)
        bucket = ("ltcg" if pnl > 0 else "ltcl") if term == "long" else ("stcg" if pnl > 0 else "stcl")
        out[bucket] += abs(pnl)
        lines.append({"symbol": sym, "realised": pnl, "term": term,
                      "sell_value": _num(r[sell_col]) if sell_col is not None else None})
    return {"realised_ytd": {k: round(v, 2) for k, v in out.items()}, "lines": lines,
            "assumed_short_term": assumed}

"""Fundamentals from screener.in company pages, for `quality` (invest-research).

Fetches https://www.screener.in/company/<SYMBOL>/consolidated/ (falls back to
the standalone page when there is no consolidated P&L) and derives the metrics
QUALITY_RULES scores. Needs beautifulsoup4. Screener asks for gentle use: one
page per symbol, with a pause between symbols.
"""
from __future__ import annotations

import datetime
import re
import time
from urllib.request import Request, urlopen

BASE = "https://www.screener.in/company/{sym}/{view}"
# Metrics this module derives: a refresh replaces all of them, so a stale one never lingers.
DERIVED = {"sales_cagr_5y_pct", "profit_cagr_5y_pct", "roe_pct", "roce_pct", "ocf_to_pat",
           "debt_to_equity", "promoter_holding_pct", "other_income_to_pbt", "net_profit_latest_cr"}
UA = "Mozilla/5.0 (autotrader-longterm research)"


def _num(text: str):
    t = text.replace(",", "").replace("₹", "").replace("%", "").replace("Cr.", "").strip()
    try:
        return float(t)
    except ValueError:
        return None


def fetch_html(symbol: str, view: str = "consolidated/") -> str:
    req = Request(BASE.format(sym=symbol, view=view), headers={"User-Agent": UA})
    with urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def _table(soup, section: str) -> dict[str, list]:
    t = soup.select_one(f"#{section} table")
    if not t:
        return {}
    hdr = [" ".join(th.get_text().split()) for th in t.select("thead th")][1:]
    rows = {"_header": hdr}
    for tr in t.select("tbody tr"):
        cells = [" ".join(td.get_text().split()) for td in tr.select("td")]
        if cells:
            rows[cells[0].rstrip(" +").strip()] = [_num(c) for c in cells[1:]]
    return rows


def parse(html: str) -> dict:
    from bs4 import BeautifulSoup  # optional dependency, only for research

    soup = BeautifulSoup(html, "html.parser")
    top = {" ".join(li.select_one(".name").get_text().split()): " ".join(li.select_one(".value").get_text().split())
           for li in soup.select("#top-ratios li") if li.select_one(".name") and li.select_one(".value")}
    ranges = {}
    for t in soup.select("table.ranges-table"):
        cells = [" ".join(td.get_text().split()) for td in t.select("th,td")]
        ranges[cells[0]] = {cells[i].rstrip(":"): _num(cells[i + 1]) for i in range(1, len(cells) - 1, 2)}
    pl, bs, cf, ratios, sh = (_table(soup, s) for s in ("profit-loss", "balance-sheet", "cash-flow",
                                                         "ratios", "shareholding"))
    return {"top": top, "ranges": ranges, "pl": pl, "bs": bs, "cf": cf, "ratios": ratios, "sh": sh,
            "pros": [li.get_text(" ", strip=True) for li in soup.select(".pros li")],
            "cons": [li.get_text(" ", strip=True) for li in soup.select(".cons li")]}


def _annual(row: list, header: list) -> list:
    """Drop the TTM column so 5-year sums use whole years."""
    return [v for v, h in zip(row, header) if h != "TTM"]


def derive_metrics(p: dict) -> dict:
    top, ranges, pl, bs, cf, ratios, sh = (p[k] for k in ("top", "ranges", "pl", "bs", "cf", "ratios", "sh"))
    m: dict = {}
    financial = "Financing Profit" in pl or "Revenue" in pl and "Interest" in pl and "Sales" not in pl
    if ranges.get("Compounded Sales Growth", {}).get("5 Years") is not None:
        m["sales_cagr_5y_pct"] = ranges["Compounded Sales Growth"]["5 Years"]
    if ranges.get("Compounded Profit Growth", {}).get("5 Years") is not None:
        m["profit_cagr_5y_pct"] = ranges["Compounded Profit Growth"]["5 Years"]
    roe = ranges.get("Return on Equity", {})
    # 3-year ROE smooths one-off years better than "last year"
    if roe.get("3 Years") is not None:
        m["roe_pct"] = roe["3 Years"]
    if not financial:
        roce = [v for v in (ratios.get("ROCE %") or []) if v is not None]
        if roce:
            m["roce_pct"] = round(sum(roce[-3:]) / len(roce[-3:]), 1)  # 3-year average
        npat = _annual(pl.get("Net Profit") or [], pl.get("_header", []))[-5:]
        cfo = (cf.get("Cash from Operating Activity") or [])[-5:]
        if len(npat) == 5 and len(cfo) == 5 and None not in npat + cfo and sum(npat) > 0:
            m["ocf_to_pat"] = round(sum(cfo) / sum(npat), 2)
        borrow, cap, res = ((bs.get(k) or [None])[-1] for k in ("Borrowings", "Equity Capital", "Reserves"))
        if None not in (borrow, cap, res) and cap + res > 0:
            m["debt_to_equity"] = round(borrow / (cap + res), 2)
    promoters = [v for v in (sh.get("Promoters") or []) if v is not None]
    if promoters:
        m["promoter_holding_pct"] = promoters[-1]
    oi = _annual(pl.get("Other Income") or [], pl.get("_header", []))
    pbt = _annual(pl.get("Profit before tax") or [], pl.get("_header", []))
    if oi and pbt and oi[-1] is not None and pbt[-1] and pbt[-1] > 0 and not financial:
        m["other_income_to_pbt"] = round(oi[-1] / pbt[-1], 2)  # banks' other income is fee income
    npat = [v for v in (pl.get("Net Profit") or []) if v is not None]
    if npat:
        m["net_profit_latest_cr"] = npat[-1]
    return {"metrics": m, "financial": financial}


def snapshot(symbol: str, pause: float = 1.5, page: str | None = None) -> dict:
    """page: Screener's id when it differs from the NSE symbol (e.g. NSE's own shares: 544937)."""
    html = fetch_html(page or symbol)
    p = parse(html)
    if not p["pl"].get("_header"):
        time.sleep(pause)
        p = parse(fetch_html(page or symbol, ""))
    d = derive_metrics(p)
    top = p["top"]
    hi_lo = re.findall(r"[\d,.]+", top.get("High / Low", ""))
    return {
        "symbol": symbol,
        "source": f"screener.in, fetched {datetime.date.today():%Y-%m-%d}",
        "financial": d["financial"],
        "metrics": d["metrics"],
        "price": _num(top.get("Current Price", "")),
        "pe": _num(top.get("Stock P/E", "")),
        "market_cap_cr": _num(top.get("Market Cap", "")),
        "high_low": [_num(x) for x in hi_lo[:2]],
        "eps_ttm": ((p["pl"].get("EPS in Rs") or [None])[-1]),
        "opm_pct_last5": ((p["pl"].get("OPM %") or [])[-5:]),
        "pros": p["pros"],
        "cons": p["cons"],
    }

"""FTSE universe for the UK system — liquid large-caps, Yahoo `.L` tickers.

Yahoo Finance serves LSE data under the `.L` suffix (e.g. SHEL.L). Trading 212 maps
these to its own instrument keys at execution time (resolved via its instrument search).
This is a curated liquid subset of the FTSE 100, not the full index — enough for a
diversified long-only swing/intraday universe without illiquid names.
"""

from __future__ import annotations

# (yahoo_ticker, name, sector) — liquid FTSE 100 names.
FTSE_LIQUID: list[tuple[str, str, str]] = [
    ("SHEL.L", "Shell", "Energy"),
    ("BP.L", "BP", "Energy"),
    ("HSBA.L", "HSBC", "Banking"),
    ("BARC.L", "Barclays", "Banking"),
    ("LLOY.L", "Lloyds", "Banking"),
    ("NWG.L", "NatWest", "Banking"),
    ("AZN.L", "AstraZeneca", "Pharma"),
    ("GSK.L", "GSK", "Pharma"),
    ("ULVR.L", "Unilever", "FMCG"),
    ("DGE.L", "Diageo", "FMCG"),
    ("RKT.L", "Reckitt", "FMCG"),
    ("BATS.L", "British American Tobacco", "FMCG"),
    ("RIO.L", "Rio Tinto", "Metal"),
    ("GLEN.L", "Glencore", "Metal"),
    ("AAL.L", "Anglo American", "Metal"),
    ("VOD.L", "Vodafone", "Telecom"),
    ("BT-A.L", "BT Group", "Telecom"),
    ("NG.L", "National Grid", "Utilities"),
    ("SSE.L", "SSE", "Utilities"),
    ("REL.L", "RELX", "Capital_Goods"),
    ("EXPN.L", "Experian", "Capital_Goods"),
    ("CRH.L", "CRH", "Capital_Goods"),
    ("PRU.L", "Prudential", "Insurance"),
    ("AV.L", "Aviva", "Insurance"),
    ("LGEN.L", "Legal & General", "Insurance"),
    ("TSCO.L", "Tesco", "Retail"),
    ("SBRY.L", "Sainsbury's", "Retail"),
    ("NXT.L", "Next", "Retail"),
    ("IAG.L", "IAG", "Travel"),
    ("RR.L", "Rolls-Royce", "Capital_Goods"),
    ("BA.L", "BAE Systems", "Capital_Goods"),
    ("STAN.L", "Standard Chartered", "Banking"),
    ("LSEG.L", "London Stock Exchange", "Capital_Goods"),
    ("CPG.L", "Compass", "Services"),
    ("IHG.L", "InterContinental Hotels", "Travel"),
]

TICKERS = [t for t, _, _ in FTSE_LIQUID]
SECTOR_OF = {t: s for t, _, s in FTSE_LIQUID}
NAME_OF = {t: n for t, n, _ in FTSE_LIQUID}

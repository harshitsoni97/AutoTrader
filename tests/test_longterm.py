"""Tests for the long-term investment advisor (autotrader.longterm)."""
import pytest

from autotrader.longterm import (analyze, compute_tax, load_holdings, load_policy,
                                 plan_rebalance, quality_score, scenario_ev, tax_report)
from autotrader.longterm.holdings import parse_rows
from autotrader.longterm.report import inr
from autotrader.longterm.tax import Realised, split_sale

CSV = """Client ID,DEMO
Equity Holdings Statement,
,
Symbol,ISIN,Sector,Quantity Available,Quantity Long Term,Average Price,Previous Closing Price
AAA,INE1,BANKING,100,100,100,150
BBB,INE2,BANKING,50,20,200,100
CCC,INE3,FMCG,10,0,50,60
GOLDBEES,INF1,ETF,10,10,50,100
TINY,INE4,FMCG,1,1,10,8
"""


@pytest.fixture
def holdings(tmp_path):
    p = tmp_path / "h.csv"
    p.write_text(CSV)
    return load_holdings(p)


@pytest.fixture
def policy():
    return load_policy()


def test_load_skips_title_rows(holdings):
    assert [h.symbol for h in holdings] == ["AAA", "BBB", "CCC", "GOLDBEES", "TINY"]
    bbb = holdings[1]
    assert bbb.qty_lt == 20 and bbb.qty_st == 30
    assert bbb.pnl == pytest.approx(-5000)


def test_missing_columns_raise():
    with pytest.raises(ValueError):
        parse_rows([["Symbol", "Qty"], ["AAA", 1]])


def test_analysis_flags(holdings, policy):
    a = analyze(holdings, policy)
    kinds = {f["kind"] for f in a["flags"]}
    assert "tiny_positions" in kinds and "no_core" in kinds and "oversize" in kinds
    assert a["summary"]["holdings"] == 5
    assert sum(r["weight_pct"] for r in a["holdings"]) == pytest.approx(100, abs=0.05)
    assert a["asset_mix"]["gold"] > 0


def test_split_sale_is_fifo_long_term_first(holdings):
    bbb = holdings[1]  # 20 LT + 30 ST, loss of 100/share
    r = split_sale(bbb, 25)
    assert r.ltcl == pytest.approx(2000)
    assert r.stcl == pytest.approx(500)
    assert r.ltcg == 0 and r.stcg == 0


def test_set_off_rules(policy):
    tp = policy["tax_policy"]
    # STCL offsets STCG first, then LTCG; LTCL never touches STCG.
    t = compute_tax(Realised(stcg=10000, stcl=4000, ltcg=200000, ltcl=0), tp)
    assert t["net_stcg"] == 6000
    assert t["taxable_ltcg"] == 75000
    assert t["tax_inr"] == pytest.approx((6000 * 0.20 + 75000 * 0.125) * 1.04)
    t = compute_tax(Realised(stcg=10000, ltcl=50000), tp)
    assert t["net_stcg"] == 10000 and t["carry_forward_ltcl"] == 50000
    t = compute_tax(Realised(stcl=30000, ltcg=20000), tp)
    assert t["net_ltcg"] == 0 and t["carry_forward_stcl"] == 10000 and t["tax_inr"] == 0


def test_tax_report_skips_gold_and_plans_ltcg_harvest(holdings, policy):
    t = tax_report(holdings, policy, {"ltcg": 120000})
    assert "GOLDBEES" in t["skipped_non_equity"]
    assert t["ltcg_exemption_room"] == 5000
    assert t["ltcg_harvest_plan"][0] == {"symbol": "AAA", "lt_gain": 5000, "harvest": 5000, "fraction": 1.0}
    assert t["loss_harvest_candidates"][0]["symbol"] == "BBB"


def test_rebalance_exit_trim_and_redeploy(holdings, policy):
    policy["portfolio_policy"]["use_funds"] = True
    plan = plan_rebalance(holdings, policy, {
        "decisions": {"BBB": {"action": "exit"}, "AAA": {"action": "trim", "target_pct": 50},
                      "TINY": {"action": "exit"}},
        "prices": {"NIFTYBEES": 100},
    })
    sells = {t["symbol"]: t for t in plan["trades"] if t["side"] == "SELL"}
    assert sells["BBB"]["qty"] == 50 and sells["TINY"]["qty"] == 1
    assert "AAA" in sells and plan["post_weights_pct"]["AAA"] <= 50
    buys = {t["symbol"] for t in plan["trades"] if t["side"] == "BUY"}
    assert {"GOLDBEES", "NIFTYBEES"} <= buys
    assert plan["buys_total"] <= plan["sells_total"]
    assert "CCC" in plan["undecided"]


def test_rebalance_without_funds_buys_core_stocks_only(holdings, policy):
    pp = policy["portfolio_policy"]
    pp["use_funds"] = False
    pp["core_stocks"] = [{"symbol": "INFY", "weight": 50}, {"symbol": "LT", "weight": 50}]
    plan = plan_rebalance(holdings, policy, {"decisions": {"BBB": {"action": "exit"}},
                                              "prices": {"INFY": 100, "LT": 100}})
    buys = {t["symbol"]: t for t in plan["trades"] if t["side"] == "BUY"}
    assert set(buys) == {"INFY", "LT"}
    assert buys["INFY"]["qty"] == buys["LT"]["qty"] > 0
    assert any("gold" in n for n in plan["notes"])


def test_quality_score_and_red_flags():
    good = quality_score({"roce_pct": 25, "roe_pct": 20, "sales_cagr_5y_pct": 15, "profit_cagr_5y_pct": 18,
                          "ocf_to_pat": 1.0, "debt_to_equity": 0.1, "promoter_holding_pct": 60,
                          "promoter_pledge_pct": 0, "pe_vs_5y_median": 0.9, "peg": 1.0})
    assert good["score"] == 100 and good["verdict"] == "compounder-grade"
    bad = quality_score({"roce_pct": 6, "roe_pct": 5, "promoter_pledge_pct": 30, "ocf_to_pat": 0.3,
                         "debt_to_equity": 2.5, "profit_cagr_5y_pct": -3})
    assert len(bad["red_flags"]) == 4
    bank = quality_score({"roe_pct": 16, "gnpa_pct": 2, "car_pct": 17, "roa_pct": 1.8}, financial=True)
    assert "debt_to_equity" not in bank["missing"]


def test_scenario_ev():
    r = scenario_ev(100, [{"name": "bull", "prob": 0.25, "target": 250},
                          {"name": "base", "prob": 0.5, "target": 180},
                          {"name": "bear", "prob": 0.25, "target": 70}], 5)
    assert r["expected_price"] == 170
    assert r["worst_case_return_pct"] == -30
    with pytest.raises(ValueError):
        scenario_ev(100, [{"name": "x", "prob": 0.5, "target": 1}], 5)


def test_inr_grouping():
    assert inr(1234567) == "₹12,34,567"
    assert inr(-83854.9) == "-₹83,855"
    assert inr(999) == "₹999"


def test_upstox_key_resolution_and_parsing():
    from autotrader.longterm.market import candle_stats, parse_ltp, resolve_keys
    keys = resolve_keys({"TMPV": "INE155A01022", "NIFTYBEES": "", "Nifty 50": "", "NOPE": ""},
                        master={"NIFTYBEES": "NSE_EQ|INF204KB14I2"})
    assert keys == {"TMPV": "NSE_EQ|INE155A01022", "NIFTYBEES": "NSE_EQ|INF204KB14I2",
                    "Nifty 50": "NSE_INDEX|Nifty 50"}
    payload = {"status": "success", "data": {
        "NSE_EQ:TMPV": {"last_price": 270.5, "instrument_token": "NSE_EQ|INE155A01022"},
        "NSE_INDEX:Nifty 50": {"last_price": 22950.1, "instrument_token": "NSE_INDEX|Nifty 50"}}}
    assert parse_ltp(payload, keys) == {"TMPV": 270.5, "Nifty 50": 22950.1}
    stats = candle_stats([["t2", 0, 120, 95, 110, 0, 0], ["t1", 0, 105, 80, 100, 0, 0]])
    assert stats == {"high_52w": 120, "low_52w": 80, "return_1y_pct": 10.0, "off_high_pct": -8.3}


def test_pnl_import_defaults_to_short_term(tmp_path):
    from autotrader.longterm.pnl import load_realised
    p = tmp_path / "pnl.csv"
    p.write_text("P&L Statement,\n,\nSymbol,ISIN,Quantity,Buy Value,Sell Value,Realized P&L\n"
                 "AAA,INE1,10,100,150,50\nBBB,INE2,5,100,80,-20\nCCC,INE3,0,0,0,0\n")
    r = load_realised(p, {"BBB": "long"})
    assert r["realised_ytd"] == {"stcg": 50.0, "stcl": 0.0, "ltcg": 0.0, "ltcl": 20.0}
    assert r["assumed_short_term"] == ["AAA"]


def test_new_lot_keeps_its_own_cost_for_tax(holdings):
    from autotrader.longterm.holdings import apply_new_lots
    bbb = holdings[1]  # 50 @ 200 (20 LT), LTP 100
    apply_new_lots(holdings, [{"symbol": "BBB", "qty": 50, "price": 100}, {"symbol": "NEW", "qty": 2, "price": 10}])
    assert bbb.qty == 100 and bbb.avg_price == 150 and bbb.qty_lt == 20
    r = split_sale(bbb, 50)          # FIFO: only the export's old shares
    assert r.ltcl == pytest.approx(2000) and r.stcl == pytest.approx(3000)
    r = split_sale(bbb, 100)         # the new lot was bought at 100: no gain/loss on it
    assert r.stcl == pytest.approx(3000)
    assert holdings[-1].symbol == "NEW" and holdings[-1].qty_lt == 0


SCREENER_HTML = """
<ul id="top-ratios">
 <li><span class="name">Current Price</span><span class="value">₹ 100</span></li>
 <li><span class="name">Stock P/E</span><span class="value">20.0</span></li>
 <li><span class="name">High / Low</span><span class="value">₹ 120 / 80</span></li>
</ul>
<table class="ranges-table"><tr><th>Compounded Sales Growth</th></tr>
 <tr><td>5 Years:</td><td>14%</td></tr></table>
<table class="ranges-table"><tr><th>Compounded Profit Growth</th></tr>
 <tr><td>5 Years:</td><td>18%</td></tr></table>
<table class="ranges-table"><tr><th>Return on Equity</th></tr>
 <tr><td>3 Years:</td><td>21%</td></tr></table>
<section id="profit-loss"><table><thead><tr><th></th>
 <th>Mar 2022</th><th>Mar 2023</th><th>Mar 2024</th><th>Mar 2025</th><th>Mar 2026</th><th>TTM</th></tr></thead>
 <tbody><tr><td>Net Profit +</td><td>10</td><td>10</td><td>10</td><td>10</td><td>10</td><td>11</td></tr>
 <tr><td>Other Income +</td><td>1</td><td>1</td><td>1</td><td>1</td><td>9</td><td>9</td></tr>
 <tr><td>Profit before tax</td><td>13</td><td>13</td><td>13</td><td>13</td><td>13</td><td>14</td></tr></tbody></table></section>
<section id="balance-sheet"><table><thead><tr><th></th><th>Mar 2026</th></tr></thead>
 <tbody><tr><td>Equity Capital</td><td>10</td></tr><tr><td>Reserves</td><td>90</td></tr>
 <tr><td>Borrowings +</td><td>20</td></tr></tbody></table></section>
<section id="cash-flow"><table><thead><tr><th></th>
 <th>Mar 2022</th><th>Mar 2023</th><th>Mar 2024</th><th>Mar 2025</th><th>Mar 2026</th></tr></thead>
 <tbody><tr><td>Cash from Operating Activity +</td><td>9</td><td>9</td><td>9</td><td>9</td><td>9</td></tr></tbody></table></section>
<section id="ratios"><table><thead><tr><th></th><th>Mar 2024</th><th>Mar 2025</th><th>Mar 2026</th></tr></thead>
 <tbody><tr><td>ROCE %</td><td>18%</td><td>20%</td><td>22%</td></tr></tbody></table></section>
<section id="shareholding"><table><thead><tr><th></th><th>Sep 2026</th></tr></thead>
 <tbody><tr><td>Promoters +</td><td>55.00%</td></tr></tbody></table></section>
"""


def test_screener_parse_and_metrics():
    pytest.importorskip("bs4")
    from autotrader.longterm.screener import derive_metrics, parse
    d = derive_metrics(parse(SCREENER_HTML))
    assert d["financial"] is False
    assert d["metrics"] == {"sales_cagr_5y_pct": 14.0, "profit_cagr_5y_pct": 18.0, "roe_pct": 21.0,
                            "roce_pct": 20.0, "ocf_to_pat": 0.9, "debt_to_equity": 0.2,
                            "promoter_holding_pct": 55.0, "other_income_to_pbt": 0.69,
                            "net_profit_latest_cr": 11.0}
    q = quality_score(d["metrics"])
    assert any("other income" in f for f in q["red_flags"])


def test_uk_view_flags_indian_funds_and_converts(holdings, policy):
    from autotrader.longterm.uk import uk_plan_view
    policy["portfolio_policy"]["use_funds"] = True
    plan = plan_rebalance(holdings, policy, {"decisions": {"BBB": {"action": "exit"}}, "prices": {"NIFTYBEES": 100}})
    u = uk_plan_view(holdings, policy, plan, {"uk_tax_resident": True, "gbp_inr": 100})
    expected = sum(t["gain"] for t in plan["trades"] if t["side"] == "SELL")  # BBB loss + cap trims
    assert u["cgt_net_inr"] == pytest.approx(expected) and u["cgt_net_gbp"] == pytest.approx(expected / 100)
    assert any("non-reporting" in n for n in u["notes"])
    assert any("GOLDBEES" in n for n in u["notes"])  # held gold ETF
    assert any("Self Assessment" in n for n in u["notes"])


def test_uk_view_nri_resident_account_and_fig(holdings, policy):
    from autotrader.longterm.uk import uk_plan_view
    plan = plan_rebalance(holdings, policy, {"decisions": {"BBB": {"action": "exit"}}})
    u = uk_plan_view(holdings, policy, plan, {"uk_tax_resident": True, "gbp_inr": 100, "india_status": "NRI",
                                              "india_account": "resident", "fig_eligible_until": "2029-04-05"})
    assert u["notes"][0].startswith("ACCOUNT FIRST")
    assert any("FIG regime available until 2029-04-05" in n for n in u["notes"])

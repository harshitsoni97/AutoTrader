#!/usr/bin/env python3
"""Render the actual compiled LangGraph workflows to Mermaid (+ PNG if available).

Outputs to reports/graphs/<name>.mmd and <name>.png. The .mmd files render on
GitHub and at mermaid.live; PNG needs network access for LangGraph's renderer.

Usage:
    python3 scripts/render_graphs.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "reports", "graphs")


def _build_all():
    from autotrader.graphs.pre_market import build_pre_market_graph
    from autotrader.graphs.intraday import build_intraday_graph
    from autotrader.graphs.post_market import build_post_market_graph
    graphs = {
        "pre_market": build_pre_market_graph(),
        "intraday": build_intraday_graph(),
        "post_market": build_post_market_graph(),
    }
    try:
        from autotrader.graphs.compete import build_compete_graph
        graphs["compete"] = build_compete_graph()
    except Exception as exc:
        print(f"(compete graph skipped: {exc})")
    return graphs


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    for name, graph in _build_all().items():
        g = graph.get_graph()
        # Mermaid text always works (no network)
        try:
            mmd = g.draw_mermaid()
            with open(os.path.join(OUT_DIR, f"{name}.mmd"), "w") as f:
                f.write(mmd)
            print(f"wrote {name}.mmd")
        except Exception as exc:
            print(f"{name}: mermaid failed: {exc}")
        # PNG is best-effort (needs the mermaid.ink renderer / network)
        try:
            png = g.draw_mermaid_png()
            with open(os.path.join(OUT_DIR, f"{name}.png"), "wb") as f:
                f.write(png)
            print(f"wrote {name}.png")
        except Exception as exc:
            print(f"{name}: png skipped ({exc})")

    print(f"\nDone. See {os.path.normpath(OUT_DIR)}/")
    print("Paste any .mmd into https://mermaid.live to view, or open the .png files.")


if __name__ == "__main__":
    main()

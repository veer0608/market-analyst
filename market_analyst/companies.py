"""The five companies the filings corpus covers, and how a question names them.

One record per company ties together the three things the router needs: the NSE
symbol price_query takes, the document title the filings index stores, and the
date the quarter's results were announced.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Company:
    symbol: str
    name: str
    aliases: tuple[str, ...]
    filing_file: str
    filing_title: str
    results_date: date
    # Where results_date comes from. "filing" means it is printed in the filing in
    # the corpus; anything else names a weaker source and should be re-checked.
    results_date_source: str
    note: str = ""


# Q1 FY27: the quarter ended 30 June 2026.
COMPANIES: tuple[Company, ...] = (
    Company(
        symbol="HDFCBANK",
        name="HDFC Bank",
        aliases=("hdfc bank", "hdfcbank", "hdfc"),
        filing_file="hdfcbank_q1_fy27_press_release.pdf",
        filing_title="HDFC Bank Q1 FY27 press release",
        results_date=date(2026, 7, 18),
        results_date_source="filing",
        note="Board met on a Saturday; the first trading day after is 2026-07-20.",
    ),
    Company(
        symbol="INFY",
        name="Infosys",
        aliases=("infosys", "infy"),
        filing_file="infosys_q1_fy27_fact_sheet.pdf",
        filing_title="Infosys Q1 FY27 fact sheet",
        results_date=date(2026, 7, 23),
        results_date_source="news reports of the exchange filing (not in the fact sheet)",
        note="Announced after market hours.",
    ),
    Company(
        symbol="ITC",
        name="ITC",
        aliases=("itc",),
        filing_file="itc_q1_fy27_results.pdf",
        filing_title="ITC Q1 FY27 standalone results",
        results_date=date(2026, 7, 31),
        results_date_source="filing",
    ),
    Company(
        symbol="RELIANCE",
        name="Reliance Industries",
        aliases=("reliance industries", "reliance", "ril", "jio"),
        filing_file="reliance_q1_fy27_media_release.pdf",
        filing_title="Reliance Industries Q1 FY27 media release",
        results_date=date(2026, 7, 17),
        results_date_source="filing",
    ),
    Company(
        symbol="TCS",
        name="TCS",
        aliases=("tata consultancy services", "tata consultancy", "tcs"),
        filing_file="tcs_q1_fy27_press_release.pdf",
        filing_title="TCS Q1 FY27 press release",
        results_date=date(2026, 7, 9),
        results_date_source="filing",
    ),
)

BY_SYMBOL = {c.symbol: c for c in COMPANIES}
BY_TITLE = {c.filing_title: c for c in COMPANIES}

# Longest alias first, so "hdfc bank" wins over "hdfc" and "reliance industries"
# over "reliance".
_ALIASES = sorted(((a, c) for c in COMPANIES for a in c.aliases), key=lambda ac: -len(ac[0]))


def find_companies(text: str) -> list[Company]:
    """Companies named in `text`, in the order they first appear. Matches whole
    words only, so "itc" does not fire inside "switch"."""
    lowered = text.lower()
    hits: dict[str, int] = {}
    taken: list[tuple[int, int]] = []
    for alias, company in _ALIASES:
        for m in re.finditer(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", lowered):
            if any(s < m.end() and m.start() < e for s, e in taken):
                continue  # inside a longer alias already matched
            taken.append((m.start(), m.end()))
            hits.setdefault(company.symbol, m.start())
            hits[company.symbol] = min(hits[company.symbol], m.start())
    return [BY_SYMBOL[s] for s, _ in sorted(hits.items(), key=lambda kv: kv[1])]

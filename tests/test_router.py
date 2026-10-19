from datetime import date
from pathlib import Path

import pytest

from market_analyst.companies import BY_SYMBOL
from market_analyst.filings import Passage
from market_analyst.price_query import PriceQuery, PriceResult
from market_analyst.prices import connect, db_path, vocabulary
from market_analyst.router import Answer, Route, _excerpt, _premise_check, compose, reaction_window, route, run

needs_prices = pytest.mark.skipif(not Path(db_path()).exists(), reason="bhavcopy warehouse not present")


@pytest.fixture(scope="module")
def wh():
    con = connect()
    return con, vocabulary(con)


# --- which tools a question gets ----------------------------------------------

@needs_prices
@pytest.mark.parametrize("question, tools, symbol", [
    ("Why did TCS fall after its Q1 results?", ("price", "filings"), "TCS"),
    ("What was TCS's revenue and how did the stock move after results?", ("price", "filings"), "TCS"),
    ("How did HDFC Bank shares react to results?", ("price",), "HDFCBANK"),
    ("What was ITC's excise duty this quarter?", ("filings",), "ITC"),
    ("How many subscribers does Jio have?", ("filings",), "RELIANCE"),
    ("Infosys closing price on 2026-07-24", ("price",), "INFY"),
    ("What was the high of TCS between 2026-08-01 and 2026-08-31?", ("price",), "TCS"),
    ("return of GOODLUCK from 2026-08-01 to 2026-08-31", ("price",), None),
])
def test_tool_choice(wh, question, tools, symbol):
    con, vocab = wh
    r = route(question, vocab, con)
    assert r.refused is None, r.refused
    assert r.tools == tools
    if symbol:
        assert r.company.symbol == symbol
    if "price" in tools:
        assert r.price_query.symbol == (symbol or "GOODLUCK")


@needs_prices
@pytest.mark.parametrize("question, reason", [
    ("Should I buy Reliance?", "not what to buy"),
    ("What did Wipro report this quarter?", "only indexed for"),
    ("How did TCS do?", "share price over some dates"),
    ("How did TCS stock perform?", "give two dates"),
    ("Compare Infosys and TCS revenue", "one company per question"),
    ("Tell me something interesting", "name one company"),
])
def test_refusals(wh, question, reason):
    con, vocab = wh
    r = route(question, vocab, con)
    assert r.tools == () and reason in r.refused


@needs_prices
def test_bare_why_question_searches_for_the_headline_results(wh):
    con, vocab = wh
    r = route("Why did TCS fall after its Q1 results?", vocab, con)
    assert r.filing_query != "Why did TCS fall after its Q1 results?"
    assert "revenue" in r.filing_query
    topical = route("What was TCS's revenue?", vocab, con)
    assert topical.filing_query == "What was TCS's revenue?"


# --- the reaction window -------------------------------------------------------

def test_reaction_window_over_a_weekend():
    # HDFC Bank's board met on Saturday 2026-07-18: base is Friday's close, and the
    # five trading days start on Monday.
    days = [date(2026, 7, d) for d in (16, 17, 20, 21, 22, 23, 24, 27)]
    assert reaction_window(BY_SYMBOL["HDFCBANK"], days) == (date(2026, 7, 17), date(2026, 7, 24))


def test_reaction_window_counts_the_results_day():
    days = [date(2026, 7, d) for d in (8, 9, 10, 13, 14, 15, 16)]
    assert reaction_window(BY_SYMBOL["TCS"], days) == (date(2026, 7, 8), date(2026, 7, 15))


def test_reaction_window_needs_enough_days():
    assert reaction_window(BY_SYMBOL["TCS"], [date(2026, 7, 8), date(2026, 7, 9)]) is None


# --- composing the answer --------------------------------------------------------

def _price(value):
    q = PriceQuery("return_pct", "TCS", "NSE", date(2026, 7, 8), date(2026, 7, 15))
    return PriceResult(f"TCS returned {value}%.", value, q)


def test_premise_check():
    assert "rose 6.40%" in _premise_check("Why did TCS fall?", _price(6.4))
    assert "fell 2.00%" in _premise_check("Why did TCS jump?", _price(-2.0))
    assert _premise_check("Why did TCS fall?", _price(-2.0)) is None
    assert _premise_check("How did TCS move?", _price(6.4)) is None


def test_excerpt_shows_the_matching_line():
    text = "OTHER INCOME 2 645.33\nTOTAL INCOME 3 27588.56\nd) Excise duty 10035.63 1309.07\ne) Employee benefits"
    assert _excerpt(text, "What was ITC's excise duty?").startswith("d) Excise duty 10035.63")
    assert _excerpt(text, None).startswith("OTHER INCOME")


def test_compose_cites_and_never_claims_a_cause():
    itc = BY_SYMBOL["ITC"]
    r = Route(tools=("price", "filings"), company=itc, filing_query="excise duty")
    p = Passage(itc, itc.filing_title, 1, None, "c1", "d) Excise duty 10035.63", 1.0)
    text = compose(Answer("Why did ITC fall after results?", r, _price(-3.0), [p]))
    assert "[ITC, p.1]" in text
    assert "does not say why the share price moved" in text


@needs_prices
def test_run_uses_the_search_it_is_given(wh):
    con, vocab = wh
    calls = []

    def fake_search(query, company=None, top_k=3):
        calls.append((query, company.symbol))
        return []

    a = run("What was ITC's excise duty this quarter?", vocab, con, search=fake_search)
    assert calls == [("What was ITC's excise duty this quarter?", "ITC")]
    assert "nothing that matches" in a.text


def test_excerpt_scores_a_row_with_its_table_heading():
    text = ("1. Segment Revenue\na) FMCG - Cigarettes 15383.55 8520.04\n- Others 6481.95\n"
            "3. Segment Assets\na) FMCG - Cigarettes 13204.05 10071.82")
    out = _excerpt(text, "What was ITC's cigarettes segment revenue?")
    assert out.startswith("1. Segment Revenue: a) FMCG - Cigarettes 15383.55")
    assert _excerpt(text, "cigarettes segment assets").startswith("3. Segment Assets: a) FMCG - Cigarettes 13204.05")


def test_excerpt_heading_detection():
    from market_analyst.router import _is_heading
    for heading in ("1. Segment Revenue", "A. FINANCIAL RESULTS", "ASSET QUALITY", "Capital Adequacy:",
                    "Net Profit attributable to:"):
        assert _is_heading(heading), heading
    # Fragments the earlier, looser rule took for headings, all seen in the corpus.
    for not_heading in ("J Vs", "Ventures", "Quarter Ended", "CC CC", "###", "(%) (%)", "TV.",
                        "NSE: HDFCBANK", "NEWS RELEASE HDFC BANK LIMITED,", "Balance sheet data",
                        "Profit/(Loss) of Associates & JVs", "Rickard Gustafson, CEO, SKF",
                        "2. The continuing significant brand building costs covering a range of",
                        "a) FMCG - Cigarettes 15383.55",
                        "Gross non-performing assets were at 1.17% of gross advances"):
        assert not _is_heading(not_heading), not_heading


def test_excerpt_ignores_the_company_name():
    # "itc" in the question matched "ITC LIMITED" and lifted the page title to a
    # tie with the revenue row.
    text = ("ITC LIMITED\nSegment-wise Revenue, Results, Assets and Liabilities\n"
            "1. Segment Revenue\na) FMCG - Cigarettes 15383.55 8520.04")
    q = "What was ITC's cigarettes segment revenue?"
    assert "Segment-wise" in _excerpt(text, q)
    assert _excerpt(text, q, ignore={"itc"}).startswith("1. Segment Revenue: a) FMCG - Cigarettes 15383.55")

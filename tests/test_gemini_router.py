"""The Gemini router with a fake model: what the code does with a proposal, and
that no test spends quota."""
import json
from pathlib import Path

import pytest

from market_analyst.gemini_router import GeminiRouter, QuotaExhausted, to_route
from market_analyst.prices import connect, db_path, vocabulary

needs_prices = pytest.mark.skipif(not Path(db_path()).exists(), reason="bhavcopy warehouse not present")


@pytest.fixture(scope="module")
def wh():
    con = connect()
    return con, vocabulary(con)


@needs_prices
def test_after_results_uses_the_reaction_window(wh):
    con, vocab = wh
    r = to_route({"tools": ["price", "filings"], "symbols": ["TCS"], "window": "after_results",
                  "filing_query": "revenue and margin"}, vocab, con)
    assert r.tools == ("price", "filings") and r.company.symbol == "TCS"
    assert (str(r.price_query.start_date), str(r.price_query.end_date)) == ("2026-07-08", "2026-07-15")
    assert r.filing_query == "revenue and margin"


@needs_prices
@pytest.mark.parametrize("proposal, reason", [
    ({"tools": [], "symbols": [], "refuse_reason": "advice"}, "advice"),
    ({"tools": ["filings"], "symbols": ["WIPRO"]}, "no filings indexed"),
    ({"tools": ["filings"], "symbols": ["TCS", "INFY"]}, "one company per question"),
    ({"tools": ["price"], "symbols": ["NOTASYMBOL"], "window": "dates",
      "start_date": "2026-08-01", "end_date": "2026-08-31"}, "unknown symbol"),
    ({"tools": ["price"], "symbols": ["TCS"], "window": "dates",
      "start_date": "2025-01-01", "end_date": "2025-02-01"}, "outside the loaded data"),
    ({"tools": ["price"], "symbols": ["TCS"]}, "needs start_date and end_date"),
    ({"tools": ["price"], "symbols": ["GOODLUCK"], "window": "after_results"}, "no results date"),
])
def test_a_bad_proposal_becomes_a_refusal(wh, proposal, reason):
    con, vocab = wh
    r = to_route(proposal, vocab, con)
    assert r.tools == () and reason in r.refused


@needs_prices
def test_a_bare_why_query_is_replaced(wh):
    con, vocab = wh
    r = to_route({"tools": ["filings"], "symbols": ["ITC"], "filing_query": "why did ITC fall"}, vocab, con)
    assert "revenue" in r.filing_query


@needs_prices
def test_replies_are_cached_per_model(wh, tmp_path):
    con, vocab = wh
    calls = []

    def fake(prompt):
        calls.append(prompt)
        return {"tools": ["filings"], "symbols": ["ITC"], "filing_query": "excise duty"}

    cache = tmp_path / "c.json"
    router = GeminiRouter("m1", cache_path=cache, call=fake)
    router("ITC excise?", vocab, con)
    router("ITC excise?", vocab, con)
    assert len(calls) == 1
    assert any(k.startswith("m1|v") and k.endswith("|ITC excise?") for k in json.loads(cache.read_text()))
    # a fresh router reads the cache: an interrupted run resumes without re-asking
    GeminiRouter("m1", cache_path=cache, call=fake)("ITC excise?", vocab, con)
    assert len(calls) == 1
    # another model is another router: never reuses m1's answer
    GeminiRouter("m2", cache_path=cache, call=fake)("ITC excise?", vocab, con)
    assert len(calls) == 2


@needs_prices
def test_quota_exhaustion_propagates(wh, tmp_path):
    con, vocab = wh

    def spent(prompt):
        raise QuotaExhausted("m1: daily quota spent")

    with pytest.raises(QuotaExhausted):
        GeminiRouter("m1", cache_path=tmp_path / "c.json", call=spent)("TCS?", vocab, con)

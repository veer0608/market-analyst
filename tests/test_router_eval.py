import json

from market_analyst.companies import BY_SYMBOL
from market_analyst.price_query import PriceQuery
from market_analyst.router import Route
from market_analyst.router_eval import SET_PATH, companies_of, score, summarize


def test_set_is_well_formed():
    qs = json.loads(SET_PATH.read_text(encoding="utf-8"))["questions"]
    assert len({q["id"] for q in qs}) == len(qs) == 30
    for q in qs:
        assert q["category"] in {"price", "filings", "both", "refuse", "multi"}
        assert q["expected_tools"] in ([], ["price"], ["filings"], ["price", "filings"])
        assert (q["expected_tools"] == []) == (q["expected_companies"] == [])


def test_companies_of():
    assert companies_of(Route(tools=("filings",), company=BY_SYMBOL["ITC"])) == ["ITC"]
    assert companies_of(Route(tools=("price",), price_query=PriceQuery("return_pct", "GOODLUCK"))) == ["GOODLUCK"]
    assert companies_of(Route(tools=(), refused="no")) == []


def test_score_and_refusal_errors_are_counted_apart():
    questions = [
        {"id": "a", "category": "filings", "question": "itc", "expected_tools": ["filings"], "expected_companies": ["ITC"]},
        {"id": "b", "category": "refuse", "question": "buy?", "expected_tools": [], "expected_companies": []},
        {"id": "c", "category": "price", "question": "tcs", "expected_tools": ["price"], "expected_companies": ["TCS"]},
    ]
    answers = {
        "itc": Route(tools=("filings",), company=BY_SYMBOL["ITC"]),      # right
        "buy?": Route(tools=("filings",), company=BY_SYMBOL["ITC"]),     # should have refused
        "tcs": Route(tools=(), refused="give dates"),                     # should have answered
    }
    result = score(lambda q, vocab, con: answers[q], questions, None, None)
    s = result["summary"]
    assert (s["correct"], s["under_refused"], s["over_refused"]) == (1, 1, 1)
    assert s["by_category"] == {"filings": "1/1", "price": "0/1", "refuse": "0/1"}


def test_summarize_accuracy():
    rows = [{"category": "x", "correct": c, "expected_tools": ["price"], "got_tools": ["price"]} for c in (1, 1, 0, 1)]
    assert summarize(rows)["accuracy"] == 0.75

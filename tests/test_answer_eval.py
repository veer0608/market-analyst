import json
from datetime import date

from market_analyst.answer_eval import SET_PATH, check, summarize
from market_analyst.companies import BY_SYMBOL
from market_analyst.filings import Passage
from market_analyst.price_query import PriceQuery, PriceResult
from market_analyst.router import Answer, Route

ITC = BY_SYMBOL["ITC"]
TCS = BY_SYMBOL["TCS"]


def _price_answer(value, start="2026-07-08", end="2026-07-15", text=None):
    q = PriceQuery("return_pct", "TCS", "NSE", date.fromisoformat(start), date.fromisoformat(end))
    r = Route(tools=("price",), company=TCS, price_query=q)
    return Answer("q", r, PriceResult("", value, q), [], text if text is not None else f"TCS returned {value:.2f}%")


def _filing_answer(passages, text):
    return Answer("q", Route(tools=("filings",), company=ITC, filing_query="q"), None, passages, text)


def test_set_is_well_formed():
    qs = json.loads(SET_PATH.read_text(encoding="utf-8"))["questions"]
    assert len({q["id"] for q in qs}) == len(qs)
    for q in qs:
        assert q.get("expect_refusal") or "price" in q or "filing" in q
        if "filing" in q:
            assert q["filing"]["company"] in BY_SYMBOL


def test_price_checks():
    want = {"id": "x", "price": {"value": 6.40, "start": "2026-07-08", "end": "2026-07-15"}}
    assert all(check(want, _price_answer(6.4004)).values())
    assert not check(want, _price_answer(6.43))["price_value"]
    assert not check(want, _price_answer(6.40, end="2026-07-16"))["price_window"]
    assert not check(want, _price_answer(6.40, text="TCS moved"))["price_shown"]


def test_retrieved_is_not_shown():
    want = {"id": "x", "filing": {"company": "ITC", "substring": "10035.63"}}
    p = Passage(ITC, ITC.filing_title, 1, None, "c", "d) Excise duty 10035.63\nother", 1.0)
    c = check(want, _filing_answer([p], "From ITC's filing:\n- other [ITC, p.1]"))
    assert c["retrieved"] and not c["shown"] and c["citations"]
    assert all(check(want, _filing_answer([p], "- d) Excise duty 10035.63 [ITC, p.1]")).values())


def test_wrong_company_citation_fails():
    want = {"id": "x", "filing": {"company": "ITC", "substring": "10035.63"}}
    p = Passage(TCS, TCS.filing_title, 1, None, "c", "10035.63", 1.0)
    assert not check(want, _filing_answer([p], "10035.63 [TCS, p.1]"))["citations"]


def test_refusal_both_ways():
    refused = Answer("q", Route(tools=(), refused="advice"), None, [], "Refused: advice.")
    assert check({"id": "x", "expect_refusal": True}, refused) == {"refusal": True}
    assert check({"id": "x", "price": {"value": 1, "start": "a", "end": "b"}}, refused)["refusal"] is False


def test_summarize():
    rows = [{"category": "price", "passed": True, "checks": {"price_value": True}},
            {"category": "filings", "passed": False, "checks": {"retrieved": True, "shown": False}}]
    s = summarize(rows)
    assert s["passed"] == 1 and s["by_check"] == {"price_value": "1/1", "retrieved": "1/1", "shown": "0/1"}

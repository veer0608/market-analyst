from datetime import date
from pathlib import Path

import duckdb
import pytest

from market_analyst.price_query import (
    PriceQuery, execute_price_query, parse_price_payload, parse_question,
)
from market_analyst.prices import connect, db_path, vocabulary


@pytest.fixture
def con():
    """Tiny warehouse: SPLIT has a 5:1 split on 07-03 and a block-deal twin row,
    DEMERG has an unadjustable demerger on 07-03, and 07-04 is a weekend gap."""
    c = duckdb.connect()
    c.execute("""create table fct_daily_prices (price_key varchar, exchange varchar, trade_date date,
        symbol varchar, series varchar, segment varchar, open_price double, high_price double,
        low_price double, close_price double, volume bigint)""")
    c.execute("""create table fct_daily_returns (price_key varchar, pct_change_adj double,
        split_factor double, unadjusted_action varchar)""")
    rows = [
        # key, date, symbol, series, segment, high, low, close, vol, adj_move, factor, action
        ("a1", "2026-07-01", "SPLIT", "EQ", "mainboard", 105, 95, 100, 10, None, 1, None),
        ("a2", "2026-07-02", "SPLIT", "EQ", "mainboard", 112, 99, 110, 20, 10.0, 1, None),
        ("a3", "2026-07-03", "SPLIT", "EQ", "mainboard", 23, 21, 22, 30, 0.0, 5, None),
        ("a3b", "2026-07-03", "SPLIT", "BL", "other", 999, 1, 5, 99, 0.0, 5, None),
        ("a5", "2026-07-06", "SPLIT", "EQ", "mainboard", 25, 21, 24.2, 40, 10.0, 1, None),
        ("b1", "2026-07-01", "DEMERG", "EQ", "mainboard", 52, 48, 50, 10, None, 1, None),
        ("b2", "2026-07-02", "DEMERG", "EQ", "mainboard", 51, 29, 30, 10, -40.0, 1, "demerger"),
        ("b3", "2026-07-03", "DEMERG", "EQ", "mainboard", 33, 29, 33, 10, 10.0, 1, None),
    ]
    for key, d, sym, ser, seg, hi, lo, cl, vol, adj, fac, act in rows:
        c.execute("insert into fct_daily_prices values (?,?,?,?,?,?,?,?,?,?,?)",
                  [key, "NSE", d, sym, ser, seg, cl, hi, lo, cl, vol])
        c.execute("insert into fct_daily_returns values (?,?,?,?)", [key, adj, fac, act])
    return c


@pytest.fixture
def vocab(con):
    return vocabulary(con)


def payload(**kw):
    base = {"metric": "return_pct", "symbol": "SPLIT", "start_date": "2026-07-01", "end_date": "2026-07-06"}
    return {**base, **kw}


# --- parser rejections -------------------------------------------------------

@pytest.mark.parametrize("bad, message", [
    (payload(metric="median"), "unsupported metric"),
    (payload(symbol="NOPE"), "unknown symbol"),
    (payload(exchange="LSE"), "unknown exchange"),
    (payload(start_date="2026-07-06", end_date="2026-07-01"), "before end_date"),
    (payload(start_date="2025-01-01"), "outside the loaded data"),
    (payload(end_date="2027-01-01"), "outside the loaded data"),
    (payload(end_date=None), "needs start_date and end_date"),
    (payload(metric=""), "metric is required"),
    (payload(start_date="not-a-date"), "Invalid isoformat"),
])
def test_payload_rejections(bad, message, vocab):
    with pytest.raises(ValueError, match=message):
        parse_price_payload(bad, vocab)


def test_valid_payload(vocab):
    q = parse_price_payload(payload(), vocab)
    assert q == PriceQuery("return_pct", "SPLIT", "NSE", date(2026, 7, 1), date(2026, 7, 6))


def test_close_on_needs_one_date(vocab):
    q = parse_price_payload({"metric": "close_on", "symbol": "SPLIT", "start_date": "2026-07-02"}, vocab)
    assert q.end_date == q.start_date


# --- executor ----------------------------------------------------------------

def test_split_is_not_a_crash(con, vocab):
    # Raw: 110 -> 22 would be -80%. Adjusted: 0% on the split day, then +10%.
    r = execute_price_query(parse_price_payload(payload(), vocab), con)
    assert not r.refused
    # base is 07-01 close; moves are +10, 0, +10 -> 1.1 * 1.0 * 1.1 - 1 = 21%
    assert r.value == pytest.approx(21.0)
    assert len(r.evidence_rows) == 3


def test_block_deal_twin_row_is_dropped(con, vocab):
    r = execute_price_query(parse_price_payload(payload(metric="high", end_date="2026-07-03"), vocab), con)
    assert r.value == 112  # not the block deal's 999
    assert len(r.evidence_rows) == 3


def test_raw_metric_across_a_split_warns(con, vocab):
    r = execute_price_query(parse_price_payload(payload(metric="low"), vocab), con)
    assert r.value == 21
    assert any("split or bonus" in w for w in r.warnings)


def test_weekend_start_uses_previous_close(con, vocab):
    # start on the 5th (no trading): base is the 3rd, so only the 6th's +10% counts
    r = execute_price_query(parse_price_payload(payload(start_date="2026-07-05"), vocab), con)
    assert r.value == pytest.approx(10.0)


def test_close_on_non_trading_day_is_refused(con, vocab):
    q = parse_price_payload({"metric": "close_on", "symbol": "SPLIT", "start_date": "2026-07-04"}, vocab)
    r = execute_price_query(q, con)
    assert r.refused and "not a trading day" in r.reason


def test_first_day_in_data_cannot_chain(con, vocab):
    # base is the day before the data starts -> no prior row, so the 07-01 NULL move is in range
    q = PriceQuery("return_pct", "SPLIT", "NSE", date(2026, 7, 1), date(2026, 7, 1))
    assert execute_price_query(q, con).refused


def test_demerger_is_flagged_not_hidden(con, vocab):
    r = execute_price_query(parse_price_payload(payload(symbol="DEMERG", end_date="2026-07-03"), vocab), con)
    assert r.value == pytest.approx(-40.0 * 0 + (0.6 * 1.1 - 1) * 100)
    assert any("demerger" in w for w in r.warnings)


def test_avg_volume(con, vocab):
    r = execute_price_query(parse_price_payload(payload(metric="avg_volume"), vocab), con)
    assert r.value == pytest.approx((10 + 20 + 30 + 40) / 4)


# --- deterministic question parser -------------------------------------------

def test_question_parsing(con, vocab):
    q = parse_question("What was the return of SPLIT from 2026-07-01 to 2026-07-06?", vocab)
    assert isinstance(q, PriceQuery) and q.metric == "return_pct" and q.symbol == "SPLIT"


@pytest.mark.parametrize("text", [
    "Should I buy SPLIT?",
    "return of NOPE from 2026-07-01 to 2026-07-06",
    "return of SPLIT",
    "return of SPLIT from 2020-01-01 to 2020-02-01",
])
def test_question_refusals(text, vocab):
    assert parse_question(text, vocab).refused


# --- real warehouse smoke test -----------------------------------------------

@pytest.mark.skipif(not Path(db_path()).exists(), reason="bhavcopy warehouse not present")
def test_goodluck_bonus_is_not_a_crash():
    con = connect()
    v = vocabulary(con)
    q = parse_price_payload({"metric": "return_pct", "symbol": "GOODLUCK",
                             "start_date": "2026-08-20", "end_date": "2026-08-21"}, v)
    r = execute_price_query(q, con)
    # raw move is -65.9%; the 2:1 bonus (factor 3) makes it +2.31%
    assert r.value == pytest.approx(2.3135, abs=0.01)

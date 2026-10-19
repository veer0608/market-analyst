from datetime import timedelta
from pathlib import Path

import pytest

from market_analyst.companies import BY_SYMBOL, COMPANIES, find_companies
from market_analyst.prices import connect, db_path


@pytest.mark.parametrize("text, symbols", [
    ("Why did TCS fall after results?", ["TCS"]),
    ("Compare Infosys and Tata Consultancy Services", ["INFY", "TCS"]),
    ("HDFC Bank's net interest margin", ["HDFCBANK"]),
    ("How many subscribers did Jio add?", ["RELIANCE"]),
    ("Reliance Industries capex", ["RELIANCE"]),
    ("ITC excise duty", ["ITC"]),
    ("switch costs and itch", []),  # 'itc' only as a whole word
    ("What about Wipro?", []),
])
def test_find_companies(text, symbols):
    assert [c.symbol for c in find_companies(text)] == symbols


def test_longer_alias_wins():
    # "hdfc bank" and "hdfc" are both aliases: one company, not two hits
    assert [c.symbol for c in find_companies("hdfc bank and hdfc")] == ["HDFCBANK"]


def test_records_are_consistent():
    assert len({c.symbol for c in COMPANIES}) == len({c.filing_title for c in COMPANIES}) == 5
    for c in COMPANIES:
        assert BY_SYMBOL[c.symbol] is c
        assert c.filing_file.endswith(".pdf")


@pytest.mark.skipif(not Path(db_path()).exists(), reason="bhavcopy warehouse not present")
def test_symbols_and_results_dates_against_prices():
    con = connect()
    for c in COMPANIES:
        days = [d for (d,) in con.execute(
            "select trade_date from fct_daily_prices where exchange = 'NSE' and symbol = ? "
            "and trade_date between ? and ? order by trade_date",
            [c.symbol, c.results_date - timedelta(days=7), c.results_date + timedelta(days=7)]).fetchall()]
        assert days, f"{c.symbol} has no NSE prices around {c.results_date}"
        # a reaction can be measured: a trading day before and one after the announcement
        assert any(d < c.results_date for d in days) and any(d > c.results_date for d in days)

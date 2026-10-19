"""price_query: a validated, closed set of price questions.

A model (or the deterministic parser) may only fill in a PriceQuery. Validation
checks it against what is really in the warehouse, and DuckDB holds the data,
so a symbol or date that does not exist is refused rather than answered with a
plausible zero.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import duckdb

from .prices import Vocabulary

METRICS = {"return_pct", "high", "low", "avg_volume", "close_on"}
DEFAULT_EXCHANGE = "NSE"


@dataclass(frozen=True)
class PriceQuery:
    metric: str
    symbol: str
    exchange: str = DEFAULT_EXCHANGE
    start_date: date | None = None
    end_date: date | None = None


@dataclass(frozen=True)
class PriceResult:
    answer: str
    value: float | None
    query: PriceQuery | None
    evidence_rows: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    refused: bool = False
    reason: str | None = None


def parse_price_payload(payload: dict[str, Any], vocab: Vocabulary) -> PriceQuery:
    metric = _required_str(payload, "metric")
    if metric not in METRICS:
        raise ValueError(f"unsupported metric: {metric}")

    exchange = _optional_str(payload, "exchange") or DEFAULT_EXCHANGE
    if exchange not in vocab.exchanges:
        raise ValueError(f"unknown exchange: {exchange}")

    symbol = _required_str(payload, "symbol")
    if symbol not in vocab.symbols[exchange]:
        raise ValueError(f"unknown symbol on {exchange}: {symbol}")

    start = _optional_date(payload, "start_date")
    end = _optional_date(payload, "end_date")
    if metric == "close_on":
        if start is None:
            raise ValueError("close_on needs start_date")
        end = start
    elif start is None or end is None:
        raise ValueError(f"{metric} needs start_date and end_date")
    if start > end:
        raise ValueError("start_date must be before end_date")
    for d in (start, end):
        if not vocab.first_date <= d <= vocab.last_date:
            raise ValueError(f"{d} is outside the loaded data ({vocab.first_date} to {vocab.last_date})")
    return PriceQuery(metric, symbol, exchange, start, end)


# One row per trading day for the symbol. NSE files block-deal series (BL, BT)
# next to EQ for the same day, so keep the main-board row and drop the rest.
_DAILY = """
    select p.trade_date, p.series, p.open_price, p.high_price, p.low_price, p.close_price,
           p.volume, r.pct_change_adj, r.split_factor, r.unadjusted_action
    from fct_daily_prices p
    join fct_daily_returns r using (price_key)
    where p.exchange = ? and p.symbol = ?
    qualify row_number() over (partition by p.trade_date order by (p.segment = 'other'), p.series) = 1
"""


def execute_price_query(query: PriceQuery, con: duckdb.DuckDBPyConnection) -> PriceResult:
    daily = f"({_DAILY})"
    args = [query.exchange, query.symbol]
    label = f"{query.symbol} ({query.exchange})"

    if query.metric == "close_on":
        rows = _rows(con, f"select * from {daily} where trade_date = ?", args + [query.start_date])
        if not rows:
            return refusal(f"{query.start_date} is not a trading day for {label}")
        return PriceResult(f"{label} closed at {rows[0]['close_price']:.2f} on {query.start_date}.",
                           rows[0]["close_price"], query, rows)

    if query.metric == "return_pct":
        return _return_pct(query, con, daily, args, label)

    rows = _rows(con, f"select * from {daily} where trade_date between ? and ? order by trade_date",
                 args + [query.start_date, query.end_date])
    if not rows:
        return refusal(f"no trading days for {label} between {query.start_date} and {query.end_date}")
    warnings = [f"raw prices span a split or bonus (factor {r['split_factor']:.2f}) on {r['trade_date']}; "
                f"levels before it are in old shares" for r in rows if r["split_factor"] != 1]
    span = f"between {query.start_date} and {query.end_date} ({len(rows)} trading days)"
    if query.metric == "high":
        value = max(r["high_price"] for r in rows)
        answer = f"{label} high {span} was {value:.2f}."
    elif query.metric == "low":
        value = min(r["low_price"] for r in rows)
        answer = f"{label} low {span} was {value:.2f}."
    else:
        value = sum(r["volume"] for r in rows) / len(rows)
        answer = f"{label} average daily volume {span} was {value:,.0f} shares."
    return PriceResult(answer, value, query, rows, warnings)


def _return_pct(query, con, daily, args, label) -> PriceResult:
    # The base is the last close on or before start_date, so "1 Aug to 31 Aug"
    # is close-to-close and 1 Aug's own move is not counted.
    base = con.execute(f"select max(trade_date) from {daily} where trade_date <= ?",
                       args + [query.start_date]).fetchone()[0]
    if base is None:
        return refusal(f"no trading day for {label} on or before {query.start_date}")
    rows = _rows(con, f"select * from {daily} where trade_date > ? and trade_date <= ? order by trade_date",
                 args + [base, query.end_date])
    if not rows:
        return refusal(f"no trading days for {label} after {base} up to {query.end_date}")
    missing = sum(r["pct_change_adj"] is None for r in rows)
    if missing:
        return refusal(f"{missing} day(s) for {label} have no previous close, so the return cannot be chained")
    growth = 1.0
    for r in rows:
        growth *= 1 + r["pct_change_adj"] / 100
    value = round((growth - 1) * 100, 4)
    warnings = [f"{r['trade_date']}: {r['unadjusted_action']} cannot be adjusted, the raw move is included"
                for r in rows if r["unadjusted_action"]]
    answer = (f"{label} returned {value:.2f}% from the close of {base} to the close of {rows[-1]['trade_date']}, "
              f"adjusted for splits and bonuses ({len(rows)} trading days).")
    return PriceResult(answer, value, query, rows, warnings)


def refusal(reason: str) -> PriceResult:
    return PriceResult(f"Refused: {reason}", None, None, [], [], True, reason)


def _rows(con, sql: str, args: list) -> list[dict[str, Any]]:
    cur = con.execute(sql, args)
    cols = [c[0] for c in cur.description]
    return [{k: (float(v) if hasattr(v, "as_tuple") else v) for k, v in zip(cols, row)} for row in cur.fetchall()]


def _required_str(payload, key) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} is required")
    return value


def _optional_str(payload, key) -> str | None:
    value = payload.get(key)
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    return value


def _optional_date(payload, key) -> date | None:
    value = payload.get(key)
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ValueError(f"{key} must be an ISO date string")
    return date.fromisoformat(value)


_ISO = re.compile(r"\d{4}-\d{2}-\d{2}")
_TOKEN = re.compile(r"[A-Za-z0-9&-]+")


def parse_question(question: str, vocab: Vocabulary) -> PriceQuery | PriceResult:
    """Fixed-phrase parser so tests and evals need no API key.

    Returns a PriceQuery, or a refusal when the question is outside the phrases.
    """
    q = question.lower()
    if "average volume" in q or "avg volume" in q:
        metric = "avg_volume"
    elif "return" in q:
        metric = "return_pct"
    elif "high" in q:
        metric = "high"
    elif "low" in q:
        metric = "low"
    elif "close" in q or "closing" in q:
        metric = "close_on"
    else:
        return refusal("only return, high, low, average volume and close questions are supported")

    exchange = "BSE" if re.search(r"\bBSE\b", question) else DEFAULT_EXCHANGE
    known = vocab.symbols.get(exchange, frozenset())
    # Symbols are written in capitals; requiring that keeps words like ON or TO out.
    symbol = next((t for t in _TOKEN.findall(question) if t.isupper() and t in known), None)
    if symbol is None:
        return refusal(f"no known {exchange} symbol found in the question")
    dates = _ISO.findall(question)
    payload = {"metric": metric, "symbol": symbol, "exchange": exchange,
               "start_date": dates[0] if dates else None,
               "end_date": dates[1] if len(dates) > 1 else None}
    try:
        return parse_price_payload(payload, vocab)
    except ValueError as e:
        return refusal(str(e))

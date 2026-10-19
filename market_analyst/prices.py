"""The one price source: bhavcopy-pipeline's DuckDB warehouse, opened read-only.

The agent never writes to it and never keeps its own copy of prices. Split and
bonus adjustment already happens in bhavcopy-pipeline's fct_daily_returns, so
this module only finds the file and reports what is in it.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import duckdb

DEFAULT_DB = Path(__file__).resolve().parents[2] / "bhavcopy-pipeline" / "warehouse.duckdb"


@dataclass(frozen=True)
class Vocabulary:
    symbols: dict[str, frozenset[str]]  # exchange -> symbols
    first_date: date
    last_date: date

    @property
    def exchanges(self) -> frozenset[str]:
        return frozenset(self.symbols)


def db_path() -> Path:
    return Path(os.environ.get("BHAVCOPY_DB", DEFAULT_DB))


def connect(path: Path | str | None = None) -> duckdb.DuckDBPyConnection:
    path = Path(path) if path else db_path()
    if not path.exists():
        raise FileNotFoundError(f"price warehouse not found at {path}; set BHAVCOPY_DB")
    return duckdb.connect(str(path), read_only=True)


def vocabulary(con: duckdb.DuckDBPyConnection) -> Vocabulary:
    symbols: dict[str, set[str]] = {}
    for exchange, symbol in con.execute("select distinct exchange, symbol from fct_daily_prices").fetchall():
        symbols.setdefault(exchange, set()).add(symbol)
    first, last = con.execute("select min(trade_date), max(trade_date) from fct_daily_prices").fetchone()
    if first is None:
        raise ValueError("fct_daily_prices is empty")
    return Vocabulary({k: frozenset(v) for k, v in symbols.items()}, first, last)

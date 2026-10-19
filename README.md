# market-analyst

Answers price questions about NSE/BSE stocks from the
[bhavcopy-pipeline](https://github.com/veer0608/bhavcopy-pipeline) warehouse.
The first half of a market analyst agent: the price tool. A filings tool comes later.

A question is turned into a small fixed query (`PriceQuery`), validated against what
is actually in the warehouse, and run in DuckDB. An unknown symbol, a date outside the
data or an unsupported question is refused, never answered with a plausible zero.
Every answer carries the daily rows it was computed from.

```bash
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
set BHAVCOPY_DB=..\bhavcopy-pipeline\warehouse.duckdb
python -m market_analyst ask "return of GOODLUCK from 2026-08-01 to 2026-08-31"
```

Metrics: `return_pct`, `high`, `low`, `avg_volume`, `close_on`.

- Returns are close-to-close and adjusted for splits and bonuses, using
  `fct_daily_returns` from bhavcopy-pipeline. GOODLUCK's 2:1 bonus on 2026-08-21 is a
  +2.31% day here, not the raw -65.9%.
- High, low and volume are raw. If a split falls in the range the answer says so.
- Demergers cannot be adjusted. They are included raw and flagged in the answer.
- The question parser is a fixed set of phrases, so tests need no API key.

```bash
pytest
```

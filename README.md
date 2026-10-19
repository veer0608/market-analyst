# market-analyst

A market analyst agent for NSE stocks, built from two tools: prices from the
[bhavcopy-pipeline](https://github.com/veer0608/bhavcopy-pipeline) warehouse, and
cited passages from company results via [citerag](https://github.com/veer0608/citerag).
A rule-based router decides which tools a question needs and builds the answer from
their outputs only.

```bash
python -m market_analyst ask "Why did TCS fall after its Q1 results?"
```

```
[tools: price, filings]
TCS (NSE) returned 6.40% from the close of 2026-07-08 to the close of 2026-07-15, ...
The question says it fell, but it rose 6.40% over this window.
From TCS's Q1 FY27 filing:
- TCS begins FY27 with continued growth; ... Q1 Revenue at ₹72,275 crore ... [TCS, p.1]
The filing reports results; it does not say why the share price moved.
```

## Router

`router.route()` turns a question into a `Route`: which tools to run, for which company,
with which `PriceQuery` and which search text. It needs no API key, so it is the
baseline a model-based router will have to beat.

- Advice ("should I buy") is refused, and so is a question naming no company or symbol.
- Price words send it to `price_query`, filing words to `filings.search`, both to both.
- "After results" becomes a reaction window: the close before the results date to the
  close five trading days later, counting the results day. Starting before it covers
  both intraday and after-hours announcements, and HDFC Bank's Saturday board meeting.
- A bare "why did it fall" has no topic to search for, so it searches for the headline
  results instead of the question text.
- The answer puts the move next to what the company reported and never claims one
  caused the other. When the question assumes a direction the price did not take, it
  says so.
- Filing excerpts show the lines that match the question, so a table row such as
  "Excise duty 10035.63" is shown rather than the passage's first line.

## Price tool

A question is turned into a small fixed query (`PriceQuery`), validated against what
is actually in the warehouse, and run in DuckDB. An unknown symbol, a date outside the
data or an unsupported question is refused, never answered with a plausible zero.
Every answer carries the daily rows it was computed from.

```bash
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

## Filings tool

`filings.search(question, company=...)` returns passages, each with a citation such as
`[ITC, p.1]`, from five companies' results for the quarter ended 30 June 2026: HDFC
Bank, Infosys, ITC, Reliance Industries and TCS. It uses citerag's retrieval (hybrid
dense + BM25, then a cross-encoder) as a library. citerag's own `/query` would also have
an LLM write an answer; here the agent writes the one answer.

The index is citerag's five-company corpus, with each chunk's text prefixed by its
document title. That lifted recall@5 there from 0.52 to 0.80, because a table row such as
"Excise duty 10035.63" never names its company. `filings.py` refuses to open citerag's
Berkshire or web databases, whose eval numbers are published.

`companies.py` ties each company to its NSE symbol, its filing title and its results
date, so a question about "Infosys after results" can be turned into a price window. Four
results dates are printed in the filings. Infosys's (2026-07-23, after market hours) is
from news reports of the exchange filing, and the record says so.

Setup: the PDFs are not in git. Put them in `citerag/backend/data/corpus_nse/` with the
file names in `companies.py`, then build the index:

```bash
python -c "from market_analyst import filings; print(*filings.build_index(), sep='\n')"
```

The filings tool needs citerag's dependencies (sentence-transformers, sqlite-vec,
pdfplumber). If those are installed system-wide, a venv made with
`python -m venv --system-site-packages .venv` sees them, and only this repo's
`requirements.txt` needs installing into it.

## Tests

```bash
pytest
```

Tests that need the price warehouse or the filings index skip when the file is absent.

# market-analyst

A market analyst agent for NSE stocks, built from two tools: prices from the
[bhavcopy-pipeline](https://github.com/veer0608/bhavcopy-pipeline) warehouse, and
cited passages from company results via [citerag](https://github.com/veer0608/citerag).
The router that combines them comes next.

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

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

### Router eval

`evals/router_set.json` has 30 questions labelled with what an ideal router should do:
which tools, for which company, or a refusal. Only the routing decision is scored, so
a run needs the price warehouse but no filings index and no API key.

```bash
python -m market_analyst.router_eval --out evals/runs/rule_based.json
```

| Router | Correct | price | filings | both | refuse | multi | over-refused | under-refused |
|---|---|---|---|---|---|---|---|---|
| rule-based | **26/30 (0.867)** | 8/8 | 7/8 | 5/7 | 6/6 | 0/1 | 3 | 0 |
| `gemini-3.1-flash-lite`, schema v1 | 22/30 (0.733) | 1/8 | 8/8 | 7/7 | 6/6 | 0/1 | 6 | 0 |
| `gemini-3.1-flash-lite`, schema v2 | **26/30 (0.867)** | 6/8 | 7/8 | 7/7 | 6/6 | 0/1 | 2 | 0 |

```bash
python -m market_analyst.router_eval --router gemini --model gemini-3.1-flash-lite --out evals/runs/gemini.json
```

Neither router answered a question it should have refused.

**The two tie, and miss different questions.** The rule-based router refused three it
should have answered: "How many people does TCS employ?" ("employ" is not a filing
word), "Did Reliance's results justify its share price move?" (no dates and no "after
results"), and the two-company comparison. It also treated "What drove the Reliance
share price after its Q1 results?" as price only. Gemini got every "both" question,
which is where wording varies most. Two of its misses add work rather than refuse: it
also searched the filing for "how did the stock do after earnings". The third is a gap
in its prompt: it was never told Jio belongs to Reliance, which the rule-based router
knows as an alias. One router or the other gets 29 of 30 right.

**Schema v1 was a bug in the harness, not the model.** It required only `tools` and
`symbols`, so flash-lite left out `metric` and `end_date`: "ITC's highest price between
2026-07-01 and 2026-07-31" came back with neither, defaulted to a return and was
rejected for a missing date. v2 requires every field (each may be null). The change was
made after seeing v1's misses, so v2 is not blind to this set; v1 stays in the table.

Neither router can answer q27, which compares two companies: a `Route` holds one.

**Caveats.** The same person wrote the routers and the questions, so this is not an
independent test. Each Gemini score is one run at temperature 0. The rules were not
tuned to their misses. Every Gemini reply is cached by model, schema version and
question in `evals/cache/`, so a run can be re-scored without spending quota, and a run
stopped by the daily limit resumes where it stopped. Models are pinned ids, never a
`-latest` alias, and kept off the ones another project on this machine runs daily.

### Answer eval

`evals/answer_set.json` has 23 questions run end to end: route, run both tools, compose
the answer, then check it. Price expectations were computed from raw closes in
`fct_daily_prices`, not through `price_query`, so a bug there cannot hide by agreeing
with itself. Filing facts are the ones verified for citerag's NSE golden set.

```bash
python -m market_analyst.answer_eval --out evals/runs/answers_rule_based.json
```

Rule-based router: **16/23 at baseline, 18/23 after the heading-aware excerpt.**

| Check | Baseline | + headings | What it asks |
|---|---|---|---|
| price window | 12/12 | 12/12 | the expected dates were used |
| price value | 12/12 | 12/12 | the number matches raw closes, to 0.01 |
| price shown | 12/12 | 12/12 | that number is in the answer |
| retrieved | 8/12 | 8/12 | a returned passage holds the filing fact |
| shown | 5/12 | **7/12** | the fact is visible in the answer, not only retrieved |
| citations | 12/12 | 12/12 | every citation is the right company |
| refusal | 23/23 | 23/23 | refused exactly the three it should |

Prices are right everywhere, including GOODLUCK's August return across its 2:1 bonus.
The weak step is the last one: three answers retrieved the fact and then did not show
it, because the excerpt picks lines by shared words. "Employees" never matches
"Workforce strength: 593,798"; "NPA" never matches "non-performing assets"; and for
ITC's cigarettes revenue it picked the cigarettes row from **Segment Assets**, since a
line scored alone has lost its table heading.

The fix scores each row together with the heading above it and shows the heading
("1. Segment Revenue: a) FMCG - Cigarettes 15383.55"), so a reader can also tell which
table a figure is from. It fixed the ITC question, as expected, and broke nothing. It
also fixed the HDFC NPA question, which was not expected: that one is a tie-break win,
not understanding. Two lines scored the same, and the new rule prefers the one sitting
under a heading ("ASSET QUALITY"). Count it as fragile. "Employees" against "Workforce
strength" is still a miss; a synonym list would fix it, and would also be tuning to
this set.

Four more were not retrieved: Reliance's
net debt (a known citerag miss), and three "why did it move" questions, where the
fixed headline search text did not reach the headline numbers.


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

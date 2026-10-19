"""Rule-based router: decide which tools a question needs, run them, and put the
answer together from their outputs only.

The same Route shape will be filled by a model later; this version needs no API
key, so it is the baseline the model has to beat and what tests run against.

Decisions, in order:
1. Advice ("should I buy") is refused.
2. A company from companies.py, or a capitalised NSE symbol, must be named.
3. Price words ask for price_query, filing words for filings.search, both for both.
4. A price question needs a window: two ISO dates, one date for a close, or
   "after results", which becomes the reaction window around the results date.

The answer never says why a stock moved. It puts the move next to what the
company reported and leaves the link to the reader.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

import duckdb

from .companies import Company, find_companies
from .price_query import (
    PriceQuery, PriceResult, execute_price_query, parse_price_payload, parse_question, refusal,
)
from .prices import Vocabulary

REACTION_DAYS = 5  # trading days from the results date, inclusive

_ADVICE = re.compile(r"\b(should i|buy|sell|invest(?:ing)?|target price|recommend\w*|good stock)\b", re.I)
_PRICE = re.compile(
    r"\b(returns?|move[sd]?|moving|fall|fell|fallen|drop(?:ped)?|rise|rose|risen|gain(?:ed)?|"
    r"jump(?:ed)?|rall(?:y|ied)|slump(?:ed)?|crash(?:ed)?|price|stock|shares|close[sd]?|closing|"
    r"high|low|volume|traded|react(?:ed|ion)?|perform(?:ed|ance)?)\b", re.I)
_FILING = re.compile(
    r"\b(why|reasons?|revenues?|profits?|margins?|ebitda|npa|deposits?|advances|dividends?|"
    r"guidance|segments?|excise|capex|capital expenditure|debt|subscribers?|headcount|employees|"
    r"eps|tcv|deals?|report(?:ed)?|said|say|explain\w*|outlook)\b", re.I)
_DOWN = re.compile(r"\b(fall|fell|fallen|drop(?:ped)?|slump(?:ed)?|crash(?:ed)?|decline[sd]?|lose|lost)\b", re.I)
_UP = re.compile(r"\b(rise|rose|risen|gain(?:ed)?|jump(?:ed)?|rall(?:y|ied)|surge[sd]?)\b", re.I)
# Filing words that name an actual topic. "why"/"explain" alone do not: a bare
# "why did it fall" retrieves boilerplate unless the search asks for the results.
_FILING_TOPIC = re.compile(
    r"\b(revenues?|profits?|margins?|ebitda|npa|deposits?|advances|dividends?|guidance|segments?|"
    r"excise|capex|capital expenditure|debt|subscribers?|headcount|employees|eps|tcv|deals?|outlook)\b", re.I)
_STOP = {"what", "which", "when", "does", "did", "this", "that", "with", "from", "after", "were",
         "was", "the", "and", "for", "its", "quarter", "much", "many", "how", "have", "q1", "fy27"}
_AFTER_RESULTS = re.compile(r"\b(after|around|on|following)\b[^.?]*\b(results?|earnings|q1)\b", re.I)
_ISO = re.compile(r"\d{4}-\d{2}-\d{2}")


@dataclass(frozen=True)
class Route:
    tools: tuple[str, ...]  # subset of ("price", "filings"), in that order
    company: Company | None = None
    price_query: PriceQuery | None = None
    filing_query: str | None = None
    refused: str | None = None
    notes: tuple[str, ...] = ()


@dataclass
class Answer:
    question: str
    route: Route
    price: PriceResult | None = None
    passages: list = field(default_factory=list)
    text: str = ""


def _refuse(reason: str) -> Route:
    return Route(tools=(), refused=reason)


def trading_days(con: duckdb.DuckDBPyConnection, symbol: str, exchange: str = "NSE") -> list[date]:
    return [d for (d,) in con.execute(
        "select distinct trade_date from fct_daily_prices where exchange = ? and symbol = ? order by 1",
        [exchange, symbol]).fetchall()]


def reaction_window(company: Company, days: list[date]) -> tuple[date, date] | None:
    """Close before the results date to the close REACTION_DAYS trading days later,
    counting the results date itself when it is a trading day. Starting before it
    covers both an intraday and an after-hours announcement."""
    before = [d for d in days if d < company.results_date]
    after = [d for d in days if d >= company.results_date]
    if not before or len(after) < REACTION_DAYS:
        return None
    return before[-1], after[REACTION_DAYS - 1]


def route(question: str, vocab: Vocabulary, con: duckdb.DuckDBPyConnection) -> Route:
    if _ADVICE.search(question):
        return _refuse("this answers questions about prices and reported results, not what to buy or sell")

    companies = find_companies(question)
    if len(companies) > 1:
        return _refuse("one company per question for now: " + ", ".join(c.name for c in companies))
    company = companies[0] if companies else None

    wants_price = bool(_PRICE.search(question))
    wants_filing = bool(_FILING.search(question))

    if company is None:
        # Not one of the five: only a price question about a named symbol can work.
        parsed = parse_question(question, vocab) if wants_price else None
        if isinstance(parsed, PriceQuery):
            return Route(tools=("price",), price_query=parsed)
        if wants_filing:
            return _refuse("filings are only indexed for HDFC Bank, Infosys, ITC, Reliance Industries and TCS")
        return _refuse(parsed.reason if parsed is not None else "name one company or NSE symbol")

    if not wants_price and not wants_filing:
        return _refuse(f"ask about {company.name}'s share price over some dates, or about its Q1 FY27 results")

    tools: list[str] = []
    price_query = None
    notes: list[str] = []
    if wants_price:
        price_query, note = _price_query_for(question, company, vocab, con)
        if price_query is not None:
            tools.append("price")
        if note:
            notes.append(note)
        if price_query is None and not wants_filing:
            return _refuse(note)
    filing_query = None
    if wants_filing:
        tools.append("filings")
        filing_query = question
        if not _FILING_TOPIC.search(question):
            # "Why did it fall?" has no topic to search for; ask for the headline results.
            filing_query = f"{company.name} quarter highlights revenue profit margin growth"
    return Route(tools=tuple(tools), company=company, price_query=price_query,
                 filing_query=filing_query, notes=tuple(notes))


def _price_query_for(question: str, company: Company, vocab: Vocabulary,
                     con: duckdb.DuckDBPyConnection) -> tuple[PriceQuery | None, str | None]:
    q = question.lower()
    dates = _ISO.findall(question)
    if "volume" in q:
        metric = "avg_volume"
    elif re.search(r"\bhigh(est)?\b", q):
        metric = "high"
    elif re.search(r"\blow(est)?\b", q):
        metric = "low"
    elif len(dates) == 1 and re.search(r"\bclos(e|ed|ing)\b", q):
        metric = "close_on"
    else:
        metric = "return_pct"

    note = None
    if len(dates) >= 2 or (metric == "close_on" and dates):
        start, end = dates[0], dates[1] if len(dates) > 1 else None
    elif _AFTER_RESULTS.search(question) or re.search(r"\b(react|reaction)\b", q):
        window = reaction_window(company, trading_days(con, company.symbol))
        if window is None:
            return None, f"no price data around {company.name}'s results on {company.results_date}"
        start, end = (d.isoformat() for d in window)
        note = (f"Window: close on {start} (before results on {company.results_date}) "
                f"to close on {end}, {REACTION_DAYS} trading days.")
    else:
        return None, (f"for {company.name}'s price, give two dates (YYYY-MM-DD) "
                      f"or ask about the move after its results")
    try:
        return parse_price_payload({"metric": metric, "symbol": company.symbol,
                                    "start_date": start, "end_date": end}, vocab), note
    except ValueError as e:
        return None, str(e)


def run(question: str, vocab: Vocabulary, con: duckdb.DuckDBPyConnection, *,
        search=None, top_k: int = 3, router=None) -> Answer:
    """Route, run the tools, compose. `router` defaults to the rule-based route();
    `search` defaults to filings.search, and tests pass a stand-in so they need no
    index."""
    r = (router or route)(question, vocab, con)
    answer = Answer(question, r)
    if r.refused:
        answer.price = refusal(r.refused)
        answer.text = f"Refused: {r.refused}."
        return answer
    if "price" in r.tools:
        answer.price = execute_price_query(r.price_query, con)
    if "filings" in r.tools:
        if search is None:
            from .filings import search
        answer.passages = search(r.filing_query, company=r.company, top_k=top_k)
    answer.text = compose(answer)
    return answer


def compose(a: Answer) -> str:
    parts: list[str] = []
    r = a.route
    if a.price is not None:
        parts.append(a.price.answer)
        parts += [f"Note: {w}" for w in a.price.warnings]
        premise = _premise_check(a.question, a.price)
        if premise:
            parts.append(premise)
    parts += list(r.notes)
    if "filings" in r.tools:
        if not a.passages:
            parts.append(f"{r.company.name}'s Q1 FY27 filing has nothing that matches this question.")
        else:
            parts.append(f"From {r.company.name}'s Q1 FY27 filing:")
            name_words = set(re.findall(r"[a-z]{3,}", " ".join((r.company.name, *r.company.aliases)).lower()))
            parts += [f"- {_excerpt(p.text, r.filing_query, ignore=name_words)} {p.citation()}"
                      for p in a.passages]
    if "price" in r.tools and "filings" in r.tools:
        parts.append("The filing reports results; it does not say why the share price moved.")
    return "\n".join(parts)


def _premise_check(question: str, price: PriceResult) -> str | None:
    """Say so when the question assumes a direction the price did not take."""
    if price.refused or price.query is None or price.query.metric != "return_pct" or price.value is None:
        return None
    if _DOWN.search(question) and price.value > 0:
        return f"The question says it fell, but it rose {price.value:.2f}% over this window."
    if _UP.search(question) and price.value < 0:
        return f"The question says it rose, but it fell {abs(price.value):.2f}% over this window."
    return None


# A section heading inside a passage, one of:
#   numbered and short     "1. Segment Revenue", "A. FINANCIAL RESULTS"
#   ALL CAPS and short     "ASSET QUALITY", "KEY SUBSIDIARIES"
#   a short label ending ":"  "Capital Adequacy:", "Net Profit attributable to:"
# An earlier rule also took any short capitalised line without figures. Across the
# corpus that matched more fragments than headings: the wrapped second half of a
# table label ("J Vs", "Ventures"), column headers ("Quarter Ended", "CC CC"),
# people's names. A wrong label misleads; a missing one only loses context.
_NUMBER_PREFIX = re.compile(r"^(?:\d{1,2}|[A-Z])\.\s+")
_MAX_HEADING_WORDS = 6


def _is_heading(line: str) -> bool:
    body = _NUMBER_PREFIX.sub("", line)
    numbered = body != line
    core = body.rstrip(": ")
    words = core.split()
    if not words or len(words) > _MAX_HEADING_WORDS or re.search(r"\d", body):
        return False
    if ":" in core:  # "NSE: HDFCBANK" is a key and a value
        return False
    if not any(sum(ch.isalpha() for ch in w) >= 3 for w in words):  # "CC CC", "J Vs", "TV.", "###"
        return False
    if body.endswith(","):  # a running page header, "NEWS RELEASE HDFC BANK LIMITED,"
        return False
    letters = [ch for ch in core if ch.isalpha()]
    all_caps = all(ch.isupper() for ch in letters)
    labelled = body.endswith(":") and "," not in body
    return numbered or all_caps or labelled


def _excerpt(text: str, query: str | None = None, limit: int = 220, ignore: set[str] = frozenset()) -> str:
    """The lines of a passage that best match the query, shown under their table
    heading. A row is scored together with the heading above it, so for "cigarettes
    segment revenue" the Cigarettes row under "1. Segment Revenue" beats the one
    under "3. Segment Assets", and the heading is shown with it so the reader can
    tell which table a figure is from. Falls back to the start of the passage.

    `ignore` drops words from the query: the company's own name, which every
    passage of its filing shares, so it says nothing about which line answers.
    Matched against "ITC LIMITED" it once lifted a page title to a tie with the
    cigarettes revenue row."""
    lines = [" ".join(line.split()) for line in text.splitlines() if line.strip()]
    words = {w for w in re.findall(r"[a-z]{3,}", (query or "").lower()) if w not in _STOP and w not in ignore}
    if words and lines:
        heads: list[str | None] = []
        current = None
        for line in lines:
            if _is_heading(line):
                heads.append(None)
                current = line
            else:
                heads.append(current)

        def score(i: int) -> int:
            context = f"{lines[i]} {heads[i] or ''}".lower()
            return sum(w in context for w in words)

        # Highest score; on a tie a row beats a heading, then the earlier line.
        i = max(range(len(lines)), key=lambda i: (score(i), heads[i] is not None, -i))
        if score(i):
            flat = " ".join(lines[i:i + 2])
            if heads[i]:
                flat = f"{heads[i]}: {flat}"
            return flat if len(flat) <= limit else flat[:limit].rsplit(" ", 1)[0] + " ..."
    flat = " ".join(lines)
    return flat if len(flat) <= limit else flat[:limit].rsplit(" ", 1)[0] + " ..."

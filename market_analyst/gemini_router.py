"""Gemini router: the model fills in the same Route the rule-based router does.

The model only proposes. Its JSON is checked the same way any input is: the
symbol must be in the warehouse, dates must parse and fall inside the data,
filings are only offered for the five indexed companies. Anything that fails
becomes a refusal, never a guess.

Quota, from what this machine's free tier has done before:
- Model ids are pinned, never a -latest alias, which can repoint overnight to a
  model with a 20-request day.
- One model per run. A run that switched models halfway would report one score
  for two routers.
- A per-day 429 stops the run (QuotaExhausted); a per-minute one is waited out.
  The two are told apart by the quotaId in the body, not by the status code.
- Every raw reply is cached by (model, question), so re-scoring costs nothing
  and an interrupted run resumes where it stopped.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

import duckdb

from .companies import BY_SYMBOL, COMPANIES
from .price_query import METRICS, parse_price_payload
from .prices import Vocabulary
from .router import Route, _FILING_TOPIC, reaction_window, trading_days

# Kept off the models reruns' daily job uses (3.7/3.6/3.5 flash, 3.5-flash-lite).
LADDER = ("gemini-3.1-flash-lite", "gemini-3-flash-preview")
API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
MIN_INTERVAL = 4.0  # seconds; 15 requests/minute, published in no header
CACHE_PATH = Path(__file__).resolve().parents[1] / "evals" / "cache" / "gemini_routes.json"
# Part of every cache key, so a changed prompt or schema never reuses old replies.
# v1: only tools and symbols required; flash-lite then dropped metric and end_date
#     ("highest price between X and Y" came back with neither), scored 22/30.
# v2: every field required (nullable), so the model has to answer each one.
PROMPT_VERSION = 2


class QuotaExhausted(RuntimeError):
    """The model's daily allowance is gone. Waiting inside this run will not help."""


SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "tools": {"type": "ARRAY", "items": {"type": "STRING", "enum": ["price", "filings"]}},
        "symbols": {"type": "ARRAY", "items": {"type": "STRING"}},
        "metric": {"type": "STRING", "enum": sorted(METRICS), "nullable": True},
        "window": {"type": "STRING", "enum": ["dates", "after_results"], "nullable": True},
        "start_date": {"type": "STRING", "nullable": True},
        "end_date": {"type": "STRING", "nullable": True},
        "filing_query": {"type": "STRING", "nullable": True},
        "refuse_reason": {"type": "STRING", "nullable": True},
    },
    "required": ["tools", "symbols", "metric", "window", "start_date", "end_date",
                 "filing_query", "refuse_reason"],
}


def prompt(question: str, vocab: Vocabulary) -> str:
    companies = "\n".join(
        f"- {c.name} (NSE symbol {c.symbol}; Q1 FY27 results announced {c.results_date})" for c in COMPANIES)
    return f"""You route questions for a market analyst agent. Decide which tools a question needs.
Do not answer the question.

Tools:
- "price": NSE daily prices for any NSE symbol, {vocab.first_date} to {vocab.last_date}.
  Metrics: return_pct (split-adjusted return over a window), high, low, avg_volume, close_on (one date).
  A window is either two dates (window "dates", start_date and end_date as YYYY-MM-DD; close_on needs
  only start_date) or the days around a company's results (window "after_results").
- "filings": passages from Q1 FY27 results (quarter ended 30 June 2026), only for:
{companies}
  Use it for anything the company reported: revenue, profit, margins, deposits, NPAs, dividends,
  headcount, deals, segments, and for "why" or "what drove" questions about a results period.

Rules:
- A question about why a stock moved after results needs both tools.
- Return tools [] and a short refuse_reason for: buy/sell/invest advice, price targets, predictions,
  companies outside the list when filings are needed, questions naming no company or symbol, or a
  price question with no dates and no link to results.
- symbols: the NSE symbols the question is about, in order.
- filing_query: what to search the filing for, or null.

Question: {question}"""


def _api_key() -> str:
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        env = Path(__file__).resolve().parents[1] / ".env"
        if env.exists():
            for line in env.read_text(encoding="utf-8-sig").splitlines():
                if line.startswith("GEMINI_API_KEY="):
                    key = line.split("=", 1)[1].strip().strip('"')
    if not key:
        raise RuntimeError("set GEMINI_API_KEY (or put it in market-analyst/.env)")
    return key


class GeminiRouter:
    def __init__(self, model: str = LADDER[0], *, cache_path: Path = CACHE_PATH, call=None):
        self.model = model
        self.cache_path = cache_path
        self.cache: dict[str, dict] = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
        self._call = call or self._http
        self._last = 0.0

    def __call__(self, question: str, vocab: Vocabulary, con: duckdb.DuckDBPyConnection) -> Route:
        return to_route(self.propose(question, vocab), vocab, con)

    def propose(self, question: str, vocab: Vocabulary) -> dict:
        key = f"{self.model}|v{PROMPT_VERSION}|{question}"
        if key not in self.cache:
            self.cache[key] = self._call(prompt(question, vocab))
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(json.dumps(self.cache, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        return self.cache[key]

    def _http(self, text: str) -> dict:
        body = json.dumps({
            "contents": [{"role": "user", "parts": [{"text": text}]}],
            "generationConfig": {"temperature": 0, "responseMimeType": "application/json",
                                 "responseSchema": SCHEMA},
        }).encode()
        for attempt in range(4):
            wait = MIN_INTERVAL - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            req = urllib.request.Request(
                API.format(model=self.model), data=body, method="POST",
                headers={"Content-Type": "application/json", "x-goog-api-key": _api_key(),
                         "User-Agent": "market-analyst/0.1"})
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    out = json.loads(resp.read())
                return json.loads(out["candidates"][0]["content"]["parts"][0]["text"])
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "replace")
                if e.code == 429 and re.search(r"per[ _-]?day", detail, re.I):
                    raise QuotaExhausted(f"{self.model}: daily quota spent") from None
                if e.code in (429, 500, 503) and attempt < 3:
                    delay = re.search(r'"retryDelay":\s*"(\d+(?:\.\d+)?)s"', detail)
                    time.sleep(float(delay.group(1)) + 1 if delay else 10 * (attempt + 1))
                    continue
                raise RuntimeError(f"{self.model}: HTTP {e.code}: {detail[:300]}") from None
        raise RuntimeError(f"{self.model}: gave up after retries")


def to_route(p: dict, vocab: Vocabulary, con: duckdb.DuckDBPyConnection) -> Route:
    """Check a proposal against the data and turn it into a Route, or a refusal."""
    tools = tuple(t for t in ("price", "filings") if t in (p.get("tools") or []))
    if not tools:
        return Route(tools=(), refused=p.get("refuse_reason") or "the model refused")
    symbols = [s.upper() for s in p.get("symbols") or []]
    if len(symbols) != 1:
        return Route(tools=(), refused=f"one company per question for now (model gave {symbols or 'none'})")
    symbol = symbols[0]
    company = BY_SYMBOL.get(symbol)
    if "filings" in tools and company is None:
        return Route(tools=(), refused=f"no filings indexed for {symbol}")

    price_query, notes = None, []
    if "price" in tools:
        payload = {"metric": p.get("metric") or "return_pct", "symbol": symbol,
                   "start_date": p.get("start_date"), "end_date": p.get("end_date")}
        if p.get("window") == "after_results":
            window = reaction_window(company, trading_days(con, symbol)) if company else None
            if window is None:
                return Route(tools=(), refused=f"no results date or price data around results for {symbol}")
            payload.update(metric="return_pct", start_date=window[0].isoformat(), end_date=window[1].isoformat())
            notes.append(f"Window: close on {window[0]} (before results on {company.results_date}) "
                         f"to close on {window[1]}.")
        try:
            price_query = parse_price_payload(payload, vocab)
        except ValueError as e:
            return Route(tools=(), refused=f"price query rejected: {e}")

    filing_query = None
    if "filings" in tools:
        filing_query = p.get("filing_query") or f"{company.name} quarter highlights revenue profit margin growth"
        if not _FILING_TOPIC.search(filing_query) and "why" in filing_query.lower():
            filing_query = f"{company.name} quarter highlights revenue profit margin growth"
    return Route(tools=tools, company=company, price_query=price_query,
                 filing_query=filing_query, notes=tuple(notes))

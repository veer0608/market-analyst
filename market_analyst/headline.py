"""A filing's headline figures: the quarter's revenue and profit, read off the page.

"Why did it move" has no topic to search for, and both search and word-matched
excerpts lose the headline to tables that mention the same words more often
(segment tables, subsidiaries, ratio rows). Results releases lead with their
headline, so instead of searching, this reads the whole filing in page order and
takes the first revenue figure and the first profit figure it finds.

A figure counts when a headline term is followed closely by an amount:
  "Consolidated Revenue at ₹ 340,257 crore"        revenue
  "Profit after tax (PAT) for the quarter was at ₹ 190.6 billion"   profit
  "Revenues 48,211 42,279 14.0%"                   revenue
Not: ratios ("Profit after tax (PAT) / revenue 16.1%": a "/" before or a "%"
after), "Profit before tax", or a date ("June 30, 2026").

Written against the five filings in the corpus; how it does on other companies'
releases is unmeasured.
"""
from __future__ import annotations

import re
from dataclasses import replace

from .companies import Company
from .filings import Passage, document_passages

_TERMS = {
    "revenue": r"(?:net |gross |total |consolidated )?revenues?(?: from (?:operations|sale of products and services))?",
    "profit": r"(?:profit after tax|net profit|net income|profit for the period|(?<![a-z])pat(?![a-z]))",
}
# An amount: currency-marked, or digits grouped with commas, or a decimal. A bare
# "30" or "2026" is not one, so "June 30, 2026" never reads as a figure.
_AMOUNT = r"(?:(?:₹|rs\.?|us\$|\$)\s?\d[\d,]*(?:\.\d+)?|\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+\.\d+)"
# Between term and amount: at most 60 characters and no "/" (a ratio).
_PATTERNS = {
    kind: re.compile(rf"\b{term}(?P<gap>[^/]{{0,60}}?)(?P<amount>{_AMOUNT})(?!\s?%)(?P<tail>[^;]{{0,70}})", re.I)
    for kind, term in _TERMS.items()
}


_TAIL_CHARS = 45


def _clean_gap(gap: str) -> bool:
    # A sentence break, or a percentage, between term and amount means they are not
    # one statement ("revenue. In 2026 we ...", "revenue 21.1% 20.8% 1,234"). The
    # exception is a change leading into the amount: "Net profit rose 12% to ₹890 crore".
    if ". " in gap:
        return False
    return "%" not in gap or gap.rstrip().endswith(" to")


def find_headlines(text: str) -> dict[str, str]:
    """{kind: snippet} for the first headline figure of each kind in `text`."""
    flat = " ".join(text.split())
    found: dict[str, str] = {}
    for kind, pattern in _PATTERNS.items():
        for m in pattern.finditer(flat):
            if flat[:m.start()].rstrip().endswith("/"):  # "Market capitalization / revenue"
                continue
            if _clean_gap(m.group("gap")):
                tail = re.split(r"(?<=[a-z%)])\.\s|\s\|\s", m.group("tail"))[0]
                if len(tail) > _TAIL_CHARS:
                    tail = tail[:_TAIL_CHARS].rsplit(" ", 1)[0] + " ..."
                found[kind] = (flat[m.start():m.end("amount")] + tail).strip()
                break
    return found


def headline_passages(company: Company) -> list[Passage]:
    """The first revenue and first profit figure in the company's filing, in page
    order, each as a passage holding just that statement, cited to its page."""
    out: dict[str, Passage] = {}
    for p in document_passages(company):
        for kind, snippet in find_headlines(p.text).items():
            if kind not in out:
                out[kind] = replace(p, text=snippet)
        if len(out) == len(_PATTERNS):
            break
    return [out[k] for k in _PATTERNS if k in out]

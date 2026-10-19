import pytest

from market_analyst import filings
from market_analyst.companies import BY_SYMBOL, COMPANIES
from market_analyst.headline import find_headlines, headline_passages


@pytest.mark.parametrize("text, kind, starts", [
    # Phrasings from outside the corpus.
    ("Revenue for the quarter stood at Rs. 1,234 crore, up 5% YoY", "revenue", "Revenue for the quarter stood at Rs. 1,234"),
    ("Net profit rose 12% to ₹ 890.5 crore on lower costs", "profit", "Net profit rose 12% to ₹ 890.5"),
    ("Total revenues were $ 2,345 million.", "revenue", "Total revenues were $ 2,345"),
    ("PAT for Q1 came in at 4,120.75 crore", "profit", "PAT for Q1 came in at 4,120.75"),
    # Written across a PDF line break.
    ("Profit before tax was 251.1 billion. Profit\nafter tax was at ₹ 190.6 billion", "profit", "Profit after tax was at ₹ 190.6"),
])
def test_finds_headline_phrasings(text, kind, starts):
    assert find_headlines(text)[kind].startswith(starts)


@pytest.mark.parametrize("text", [
    "Profit after tax (PAT) / revenue 16.1% 16.4% 16.5%",          # a ratio
    "Market capitalization / revenue, end of the period 2.2 4.0",  # term after a slash
    "Revenue growth was 14.0% YoY",                                 # a percentage, no amount
    "Profit before tax was ₹ 500 crore",                           # not a headline profit
    "Revenue recognition. In June 30, 2026 we changed policy",     # a date is not an amount
    "Operating profit 10,163 8,803",                                # operating profit is not net profit
])
def test_rejects_non_headlines(text):
    found = find_headlines(text)
    assert "profit" not in found
    assert "revenue" not in found


def test_first_figure_wins():
    text = "Revenue at ₹100 crore. Segment revenue from operations 79,745"
    assert find_headlines(text)["revenue"].startswith("Revenue at ₹100")


needs_index = pytest.mark.skipif(not filings.db_path().exists(), reason="filings index not built")


@needs_index
@pytest.mark.parametrize("symbol, revenue, profit", [
    ("HDFCBANK", "463.6 billion", "190.6 billion"),
    ("INFY", "48,211", "7,775"),
    ("ITC", "26794.47", "3578.82"),
    ("RELIANCE", "340,257", "23,196"),
    ("TCS", "72,275", "13,849"),
])
def test_headlines_of_the_corpus(symbol, revenue, profit):
    company = BY_SYMBOL[symbol]
    rev, prof = headline_passages(company)
    assert revenue in rev.text and profit in prof.text
    assert rev.company is company and prof.company is company

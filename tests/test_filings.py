import sqlite3

import pytest

from market_analyst import filings
from market_analyst.companies import BY_SYMBOL, COMPANIES


@pytest.mark.parametrize("name", sorted(filings.PROTECTED_DBS))
def test_refuses_published_databases(monkeypatch, tmp_path, name):
    monkeypatch.setenv("FILINGS_DB", str(tmp_path / name))
    with pytest.raises(ValueError, match="published eval number"):
        filings._env()


def test_env_turns_on_title_prefix(monkeypatch, tmp_path):
    monkeypatch.setenv("FILINGS_DB", str(tmp_path / "x.db"))
    env = filings._env()
    assert env["CHUNK_TITLE_PREFIX_ENABLED"] == "true"
    assert env["DATABASE_URL"].endswith("/x.db")


def test_citation_format():
    p = filings.Passage(BY_SYMBOL["ITC"], "ITC Q1 FY27 standalone results", 2, None, "c1", "t", 0.0)
    assert p.citation() == "[ITC, p.2]"


needs_index = pytest.mark.skipif(not filings.db_path().exists(), reason="filings index not built")


@needs_index
def test_index_titles_match_companies():
    con = sqlite3.connect(filings.db_path())
    titles = {t for (t,) in con.execute("select title from documents")}
    assert titles == {c.filing_title for c in COMPANIES}


@needs_index
def test_company_filter_returns_only_that_company():
    itc = BY_SYMBOL["ITC"]
    passages = filings.search("How much excise duty was paid this quarter?", company=itc, top_k=3)
    assert passages and all(p.company is itc for p in passages)
    assert any("Excise duty 10035.63" in p.text for p in passages)

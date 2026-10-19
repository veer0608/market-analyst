"""filing_search: cited passages from the companies' Q1 FY27 results.

Uses citerag's retrieval (hybrid dense + BM25, then a cross-encoder) as a library,
not its /query endpoint, which would also have its own LLM write an answer. Here
the agent writes the one answer, from these passages.

citerag reads its settings once, at import, from the environment. So this module
sets them before the first import and checks they took:

- the filings database (never citerag.db or citerag_web.db, whose eval numbers
  are published), and
- chunk_title_prefix_enabled, which the database was built with. Querying a
  title-prefixed index without it scores the re-ranker on different text.
"""
from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace

from .companies import BY_TITLE, COMPANIES, Company

DEFAULT_BACKEND = Path(__file__).resolve().parents[2] / "citerag" / "backend"
PROTECTED_DBS = {"citerag.db", "citerag_web.db"}
# The cross-encoder re-ranks this many candidates; asking for all of them lets a
# company filter keep its best passages instead of whatever survived a top 5.
POOL = 20


@dataclass(frozen=True)
class Passage:
    company: Company | None
    title: str
    page: int | None
    page_label: str | None
    chunk_id: str
    text: str
    score: float

    def citation(self) -> str:
        page = self.page_label or self.page
        return f"[{self.company.name if self.company else self.title}, p.{page}]"


def backend_dir() -> Path:
    return Path(os.environ.get("CITERAG_BACKEND", DEFAULT_BACKEND)).resolve()


def db_path() -> Path:
    default = backend_dir() / "data" / "citerag_nse_titled.db"
    return Path(os.environ.get("FILINGS_DB", default)).resolve()


def corpus_dir() -> Path:
    return Path(os.environ.get("FILINGS_CORPUS", backend_dir() / "data" / "corpus_nse")).resolve()


def _env() -> dict[str, str]:
    path = db_path()
    if path.name in PROTECTED_DBS:
        raise ValueError(f"refusing to use {path.name}: it holds a published eval number")
    return {
        "DATABASE_URL": f"sqlite:///{path.as_posix()}",
        "CORPUS_DIR": corpus_dir().as_posix(),
        "CHUNK_TITLE_PREFIX_ENABLED": "true",
    }


@lru_cache
def _citerag() -> SimpleNamespace:
    backend = backend_dir()
    loaded = sys.modules.get("app")
    if loaded is not None and Path(loaded.__file__).resolve().parent.parent != backend:
        raise RuntimeError(f"another package named 'app' is already imported: {loaded.__file__}")
    os.environ.update(_env())
    if str(backend) not in sys.path:
        sys.path.insert(0, str(backend))

    from app.config import settings
    from app.db import SessionLocal
    from app.ingest import ingest_pdf
    from app.retrieval import retrieve
    from app.vectorstore import document_titles

    want = _env()["DATABASE_URL"]
    if settings.database_url != want or not settings.chunk_title_prefix_enabled:
        raise RuntimeError(
            f"citerag was configured before this module set it up "
            f"(database_url={settings.database_url}, "
            f"title prefix={settings.chunk_title_prefix_enabled}); import filings first"
        )
    return SimpleNamespace(SessionLocal=SessionLocal, retrieve=retrieve,
                           document_titles=document_titles, ingest_pdf=ingest_pdf)


def search(question: str, *, company: Company | None = None, top_k: int = 5) -> list[Passage]:
    """Best `top_k` passages for `question`, optionally from one company's filing."""
    if not db_path().exists():
        raise FileNotFoundError(f"filings index not found at {db_path()}; run build_index() first")
    c = _citerag()
    with c.SessionLocal() as session:
        hits = c.retrieve(session, question, top_k=POOL)
        titles = c.document_titles(session, {str(h.document_id) for h in hits})
    passages = []
    for h in hits:
        title = titles.get(str(h.document_id), "")
        passages.append(Passage(BY_TITLE.get(title), title, h.page_number, h.page_label,
                                str(h.chunk_id), h.content, h.score))
    if company is not None:
        passages = [p for p in passages if p.company is company]
    return passages[:top_k]


def build_index() -> list[str]:
    """(Re)build the filings index from corpus_dir(), titled from companies.py.

    Creates the schema with citerag's own migrations, then ingests each company's
    PDF. Ingest is idempotent, so a re-run replaces rather than duplicates.
    """
    env = {**os.environ, **_env()}
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=backend_dir(),
                   env=env, check=True, capture_output=True)
    c = _citerag()
    report = []
    with c.SessionLocal() as session:
        for company in COMPANIES:
            pdf = corpus_dir() / company.filing_file
            if not pdf.exists():
                report.append(f"{company.name}: MISSING {pdf.name}")
                continue
            r = c.ingest_pdf(session, pdf, title=company.filing_title)
            report.append(f"{company.name}: pages={r.n_pages} chunks={r.n_chunks} empty={r.n_empty_pages}")
    return report

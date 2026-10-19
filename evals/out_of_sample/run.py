"""Run the frozen headline extractor on raw out-of-sample PDFs, page by page in order.

usage (from the repo root): python -m evals.out_of_sample.run <folder holding the PDFs>

The PDFs are not in git; labels.json names each file and where to get it.
"""
import json, sys
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")
repo, pdf_dir = Path(__file__).resolve().parents[2], Path(sys.argv[1])
sys.path.insert(0, str(repo))
import pdfplumber
from market_analyst.headline import find_headlines

labels = json.loads((repo / "evals/out_of_sample/labels.json").read_text(encoding="utf-8"))["releases"]
results = []
for rel in labels:
    path = pdf_dir / rel["file"]
    if not path.exists():
        print(f"{rel['company']}: PDF not present, skipped"); continue
    found = {}
    with pdfplumber.open(str(path)) as pdf:
        for i, page in enumerate(pdf.pages, 1):
            for kind, snip in find_headlines(page.extract_text() or "").items():
                found.setdefault(kind, (i, snip))
            if len(found) == 2:
                break
    row = {"company": rel["company"]}
    for kind in ("revenue", "profit"):
        page, snip = found.get(kind, (None, ""))
        hit = rel[kind] in snip.replace(" ", "") or rel[kind] in snip
        row[kind] = {"want": rel[kind], "page": page, "got": snip, "hit": hit}
        print(f"{rel['company']:10} {kind:8} {'HIT ' if hit else 'MISS'} want {rel[kind]:>8}  p{page}: {snip[:110]}")
    results.append(row)
hits = sum(r[k]["hit"] for r in results for k in ("revenue", "profit"))
print(f"\n{hits}/{2 * len(results)} headline figures found")
(repo / "evals/out_of_sample/results.json").write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

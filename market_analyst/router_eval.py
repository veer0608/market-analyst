"""Score a router on evals/router_set.json: did it pick the right tools for the
right company, and did it refuse exactly the questions it should?

Only the routing decision is scored; no tool runs, so a run needs the price
warehouse (for symbols and trading days) but no filings index and no API key.

    python -m market_analyst.router_eval                 # rule_based
    python -m market_analyst.router_eval --out evals/runs/rule_based.json
    python -m market_analyst.router_eval --router gemini --model gemini-3.1-flash-lite --out evals/runs/gemini.json

A gemini run caches every reply, so if the daily quota runs out partway it stops
and the same command resumes from where it stopped.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Callable

from .prices import connect, vocabulary
from .router import Route, route

SET_PATH = Path(__file__).resolve().parents[1] / "evals" / "router_set.json"
ROUTERS = ("rule_based", "gemini")


def make_router(name: str, model: str | None = None) -> Callable:
    if name == "rule_based":
        return route
    from .gemini_router import LADDER, GeminiRouter
    return GeminiRouter(model or LADDER[0])


def companies_of(r: Route) -> list[str]:
    if r.company is not None:
        return [r.company.symbol]
    if r.price_query is not None:
        return [r.price_query.symbol]
    return []


def score(router: Callable, questions: list[dict], vocab, con) -> dict:
    rows = []
    for q in questions:
        r = router(q["question"], vocab, con)
        got_tools, got_cos = list(r.tools), companies_of(r)
        tools_ok = got_tools == q["expected_tools"]
        cos_ok = got_cos == q["expected_companies"]
        rows.append({
            "id": q["id"], "category": q["category"], "question": q["question"],
            "expected_tools": q["expected_tools"], "got_tools": got_tools,
            "expected_companies": q["expected_companies"], "got_companies": got_cos,
            "refused": r.refused, "correct": tools_ok and cos_ok,
        })
    return {"summary": summarize(rows), "questions": rows}


def summarize(rows: list[dict]) -> dict:
    by_cat: dict[str, list[bool]] = defaultdict(list)
    for r in rows:
        by_cat[r["category"]].append(r["correct"])
    should_refuse = [r for r in rows if not r["expected_tools"]]
    should_answer = [r for r in rows if r["expected_tools"]]
    return {
        "n": len(rows),
        "accuracy": round(sum(r["correct"] for r in rows) / len(rows), 3),
        "correct": sum(r["correct"] for r in rows),
        "by_category": {c: f"{sum(v)}/{len(v)}" for c, v in sorted(by_cat.items())},
        # The two errors that matter most, kept apart from "wrong tool":
        "over_refused": sum(1 for r in should_answer if not r["got_tools"]),
        "under_refused": sum(1 for r in should_refuse if r["got_tools"]),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--router", default="rule_based", choices=ROUTERS)
    ap.add_argument("--model", help="gemini model id (pinned, one per run)")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")

    questions = json.loads(SET_PATH.read_text(encoding="utf-8"))["questions"]
    con = connect()
    router = make_router(args.router, args.model)
    name = args.router if args.router == "rule_based" else router.model
    try:
        result = {"router": name, **score(router, questions, vocabulary(con), con)}
    except Exception as e:  # QuotaExhausted, or a model that will not answer
        print(f"{name}: stopped: {e}. Replies so far are cached; re-run to resume.")
        return 1
    s = result["summary"]
    print(f"{name}: {s['correct']}/{s['n']} correct ({s['accuracy']:.3f})")
    print("by category:", ", ".join(f"{c} {v}" for c, v in s["by_category"].items()))
    print(f"over-refused (answerable, refused): {s['over_refused']}   "
          f"under-refused (should refuse, answered): {s['under_refused']}")
    for r in result["questions"]:
        if not r["correct"]:
            print(f"  MISS {r['id']} [{r['category']}] {r['question']}")
            print(f"       want {r['expected_tools']} {r['expected_companies']}, "
                  f"got {r['got_tools']} {r['got_companies']}"
                  + (f" (refused: {r['refused']})" if r["refused"] else ""))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())

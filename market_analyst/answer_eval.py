"""End-to-end answer eval: route, run both tools, compose, then check the answer.

Checks, each scored on its own so a failure says where it happened:
- price_window  the price query used the expected dates
- price_value   the number matches one computed from raw closes (to 0.01)
- price_shown   that number appears in the answer text
- retrieved     a returned passage contains the expected filing fact
- shown         the fact is visible in the answer text, not only retrieved
- citations     every passage and every [..] citation is the right company
- refusal       a question that should be refused was, and none other was

A question passes when every check that applies to it passes.

    python -m market_analyst.answer_eval --out evals/runs/answers_rule_based.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

from .companies import BY_SYMBOL
from .prices import connect, vocabulary
from .router import run
from .router_eval import ROUTERS, make_router

SET_PATH = Path(__file__).resolve().parents[1] / "evals" / "answer_set.json"
CHECKS = ("price_window", "price_value", "price_shown", "retrieved", "shown", "citations", "refusal")


def _flat(text: str) -> str:
    return " ".join(text.split())


def check(q: dict, a) -> dict[str, bool]:
    out: dict[str, bool] = {}
    refused = a.route.refused is not None
    out["refusal"] = refused == bool(q.get("expect_refusal"))
    if q.get("expect_refusal"):
        return out

    if "price" in q:
        want = q["price"]
        pq = a.route.price_query
        got = a.price.value if a.price is not None and not a.price.refused else None
        out["price_window"] = pq is not None and (str(pq.start_date), str(pq.end_date)) == (want["start"], want["end"])
        out["price_value"] = got is not None and abs(got - want["value"]) <= 0.01
        out["price_shown"] = f"{want['value']:.2f}" in a.text or f"{abs(want['value']):.2f}" in a.text

    if "filing" in q:
        want = q["filing"]
        name = BY_SYMBOL[want["company"]].name
        needle = _flat(want["substring"])
        out["retrieved"] = any(needle in _flat(p.text) for p in a.passages)
        out["shown"] = needle in _flat(a.text)
        cited = re.findall(r"\[([^\[\],]+), p\.[^\]]*\]", a.text)
        out["citations"] = bool(a.passages) and all(
            p.company is not None and p.company.symbol == want["company"] for p in a.passages
        ) and all(c == name for c in cited)
    return out


def summarize(rows: list[dict]) -> dict:
    per_check = {c: [r["checks"][c] for r in rows if c in r["checks"]] for c in CHECKS}
    by_cat: dict[str, list[bool]] = defaultdict(list)
    for r in rows:
        by_cat[r["category"]].append(r["passed"])
    return {
        "n": len(rows),
        "passed": sum(r["passed"] for r in rows),
        "by_category": {c: f"{sum(v)}/{len(v)}" for c, v in sorted(by_cat.items())},
        "by_check": {c: f"{sum(v)}/{len(v)}" for c, v in per_check.items() if v},
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--router", default="rule_based", choices=ROUTERS)
    ap.add_argument("--model")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")

    questions = json.loads(SET_PATH.read_text(encoding="utf-8"))["questions"]
    con = connect()
    vocab = vocabulary(con)
    router = make_router(args.router, args.model)
    name = args.router if args.router == "rule_based" else router.model
    rows = []
    for q in questions:
        a = run(q["question"], vocab, con, router=router)
        checks = check(q, a)
        rows.append({"id": q["id"], "category": q["category"], "question": q["question"],
                     "tools": list(a.route.tools), "refused": a.route.refused,
                     "checks": checks, "passed": all(checks.values()), "answer": a.text})
    s = summarize(rows)
    print(f"{name}: {s['passed']}/{s['n']} answers pass every check")
    print("by category:", ", ".join(f"{c} {v}" for c, v in s["by_category"].items()))
    print("by check:   ", ", ".join(f"{c} {v}" for c, v in s["by_check"].items()))
    for r in rows:
        if not r["passed"]:
            failed = [c for c, ok in r["checks"].items() if not ok]
            print(f"  FAIL {r['id']} [{r['category']}] {r['question']}  -> {', '.join(failed)}"
                  + (f" (refused: {r['refused']})" if r["refused"] else ""))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps({"router": name, "summary": s, "questions": rows}, indent=2,
                                       ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())

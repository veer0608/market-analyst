"""Usage: python -m market_analyst ask "Why did TCS fall after its Q1 results?" """
import sys

from .prices import connect, vocabulary
from .router import run


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[0] != "ask":
        print(__doc__)
        return 2
    sys.stdout.reconfigure(encoding="utf-8")
    con = connect()
    answer = run(argv[1], vocabulary(con), con)
    print(f"[tools: {', '.join(answer.route.tools) or 'none'}]")
    print(answer.text)
    rows = answer.price.evidence_rows if answer.price else []
    if rows:
        print(f"evidence: {len(rows)} price row(s)")
        for r in rows[:3] + (rows[-1:] if len(rows) > 3 else []):
            print(f"  {r['trade_date']} close={r['close_price']} adj_move={r['pct_change_adj']}")
    return 1 if answer.route.refused else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

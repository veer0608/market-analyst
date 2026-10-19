"""Usage: python -m market_analyst ask "return of GOODLUCK from 2026-08-01 to 2026-08-31" """
import sys

from .price_query import PriceQuery, execute_price_query, parse_question
from .prices import connect, vocabulary


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[0] != "ask":
        print(__doc__)
        return 2
    con = connect()
    parsed = parse_question(argv[1], vocabulary(con))
    result = execute_price_query(parsed, con) if isinstance(parsed, PriceQuery) else parsed
    print(result.answer)
    for w in result.warnings:
        print(f"warning: {w}")
    rows = result.evidence_rows
    if rows:
        print(f"evidence: {len(rows)} row(s)")
        for r in rows[:3] + (rows[-1:] if len(rows) > 3 else []):
            print(f"  {r['trade_date']} close={r['close_price']} adj_move={r['pct_change_adj']}")
    return 1 if result.refused else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

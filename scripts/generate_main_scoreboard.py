#!/usr/bin/env python3
"""Regenerate the top-10 leaderboard block in the root README.md.

Reads the featured challenge's `SCOREBOARD.md`, takes the top entries,
and rewrites the section between the `<!-- BEGIN_MAIN_LEADERBOARD -->`
and `<!-- END_MAIN_LEADERBOARD -->` markers.

Run by .github/workflows/update-scoreboards.yml after a merge.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"
BEGIN = "<!-- BEGIN_MAIN_LEADERBOARD -->"
END = "<!-- END_MAIN_LEADERBOARD -->"
FEATURED_CHALLENGE = "challenge-1"
TOP_N = 10


def parse_scoreboard(path: Path) -> list[dict]:
    """Read a per-challenge SCOREBOARD.md and return its data rows.

    The expected table shape is:

    | Rank | Username | Coverage | RPS | p99 (ms) | Errors |
    """
    rows: list[dict] = []
    if not path.exists():
        return rows
    for line in path.read_text().splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 6:
            continue
        if cells[0].lower() == "rank" or set(cells[0]) <= {":", "-"}:
            continue
        if "no submissions" in line.lower():
            continue
        try:
            rank = int(cells[0])
        except ValueError:
            continue
        rows.append(
            {
                "rank": rank,
                "username": cells[1].strip("` "),
                "coverage": cells[2],
                "rps": cells[3],
                "p99": cells[4],
                "errors": cells[5],
            }
        )
    return rows


def render_block(rows: list[dict]) -> str:
    header = (
        "| Rank | Developer | Coverage | RPS | p99 (ms) |\n"
        "|:---:|:---|:---:|---:|---:|"
    )
    if not rows:
        body = "| — | _no submissions yet_ | — | — | — |"
    else:
        body = "\n".join(
            f"| {row['rank']} | [{row['username']}](https://github.com/{row['username']}) | "
            f"{row['coverage']} | {row['rps']} | {row['p99']} |"
            for row in rows[:TOP_N]
        )
    return f"{BEGIN}\n{header}\n{body}\n{END}"


def main() -> int:
    if not README.exists():
        print(f"README not found at {README}", file=sys.stderr)
        return 1

    scoreboard = ROOT / FEATURED_CHALLENGE / "SCOREBOARD.md"
    rows = parse_scoreboard(scoreboard)

    content = README.read_text()
    block = render_block(rows)
    pattern = re.compile(
        re.escape(BEGIN) + r".*?" + re.escape(END),
        flags=re.DOTALL,
    )

    if pattern.search(content):
        new_content = pattern.sub(block, content)
    else:
        print(
            f"Markers '{BEGIN}'/'{END}' not found in README. Skipping.",
            file=sys.stderr,
        )
        return 0

    if new_content != content:
        README.write_text(new_content)
        print("Updated main leaderboard in README.md")
    else:
        print("No changes needed for README.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())

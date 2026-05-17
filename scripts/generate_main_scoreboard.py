#!/usr/bin/env python3
"""Regenerate the top-N leaderboard block in the root README.md.

Discovers every `challenge-*/SCOREBOARD.md`, takes the top entries from
each, and rewrites the section between the `<!-- BEGIN_MAIN_LEADERBOARD -->`
and `<!-- END_MAIN_LEADERBOARD -->` markers as one sub-section per
challenge.

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
TOP_N = 10

CHALLENGE_DIR_RE = re.compile(r"^challenge-(\d+)$")
CHALLENGE_TITLE_RE = re.compile(r"^#\s+(Challenge\s+\d+\s*[—–-]\s*.+?)\s*$")


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


def challenge_title(challenge_dir: Path) -> str:
    """Return the H1 title of the challenge README, or a fallback."""
    readme = challenge_dir / "README.md"
    if readme.exists():
        for line in readme.read_text().splitlines():
            match = CHALLENGE_TITLE_RE.match(line)
            if match:
                return match.group(1).strip()
    fallback = challenge_dir.name.replace("-", " ").title()
    return fallback


def discover_challenges() -> list[Path]:
    """Return challenge directories sorted by their numeric suffix."""
    discovered: list[tuple[int, Path]] = []
    for path in ROOT.iterdir():
        if not path.is_dir():
            continue
        match = CHALLENGE_DIR_RE.match(path.name)
        if not match:
            continue
        discovered.append((int(match.group(1)), path))
    discovered.sort(key=lambda item: item[0])
    return [path for _, path in discovered]


def render_table(rows: list[dict]) -> str:
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
    return f"{header}\n{body}"


def render_block(challenges: list[Path]) -> str:
    if not challenges:
        return f"{BEGIN}\n_no challenges yet_\n{END}"

    sections: list[str] = []
    for challenge_dir in challenges:
        title = challenge_title(challenge_dir)
        scoreboard = challenge_dir / "SCOREBOARD.md"
        rows = parse_scoreboard(scoreboard)
        sections.append(f"### {title}\n\n{render_table(rows)}")

    body = "\n\n".join(sections)
    return f"{BEGIN}\n{body}\n{END}"


def main() -> int:
    if not README.exists():
        print(f"README not found at {README}", file=sys.stderr)
        return 1

    challenges = discover_challenges()
    block = render_block(challenges)

    content = README.read_text()
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

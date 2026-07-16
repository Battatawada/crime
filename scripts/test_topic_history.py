#!/usr/bin/env python3
"""Quick checks for case-level topic dedupe."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from common import filter_topics_against_history, load_topic_history, topic_overlaps_history  # noqa: E402


def main() -> None:
    history = load_topic_history()
    assert history, "topic_history.json must list published cases"

    blocked = [
        "She Never Left the House. The JonBenét Note.",
        "JonBenét Ramsey Boulder investigation",
        "Why the Ramsey ransom note was three pages",
    ]
    for t in blocked:
        assert topic_overlaps_history(t, history), f"expected block: {t}"

    assert topic_overlaps_history("Ted Bundy Florida Chi Omega", history) is None
    assert topic_overlaps_history("Unrelated cold case in Boulder Colorado 1998", history) is None

    kept, rejected = filter_topics_against_history(
        blocked + ["Isabella Stewart Gardner Museum heist Boston 1990"],
        history,
    )
    assert kept == ["Isabella Stewart Gardner Museum heist Boston 1990"]
    assert len(rejected) == 3
    print("OK topic history hard-block checks passed")


if __name__ == "__main__":
    main()

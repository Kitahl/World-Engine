#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


def wilson_interval(successes: int, total: int, z: float = 1.959963984540054) -> list[float] | None:
    if total <= 0:
        return None
    p = successes / total
    denominator = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    radius = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total) / denominator
    return [round(max(0.0, center - radius), 6), round(min(1.0, center + radius), 6)]


def fleiss_kappa(groups: Iterable[Counter[str]], categories: list[str]) -> float | None:
    rows = [g for g in groups if sum(g.values()) >= 2]
    if not rows:
        return None
    agreements: list[float] = []
    totals = Counter()
    rating_total = 0
    for row in rows:
        n = sum(row.values())
        agreements.append((sum(row[c] ** 2 for c in categories) - n) / (n * (n - 1)))
        totals.update(row)
        rating_total += n
    observed = sum(agreements) / len(agreements)
    expected = sum((totals[c] / rating_total) ** 2 for c in categories)
    if math.isclose(expected, 1.0):
        return 1.0 if math.isclose(observed, 1.0) else None
    return round((observed - expected) / (1 - expected), 6)


def _normalized_row(row: dict[str, Any]) -> dict[str, str]:
    required = ["case_id", "category", "reviewer_id", "system_a", "system_b", "winner"]
    missing = [key for key in required if not str(row.get(key, "")).strip()]
    if missing:
        raise ValueError(f"rating row missing fields: {missing}")
    a = str(row["system_a"]).strip()
    b = str(row["system_b"]).strip()
    if a == b:
        raise ValueError("system_a and system_b must differ")
    winner = str(row["winner"]).strip()
    if winner not in {"A", "B", "TIE"}:
        raise ValueError("winner must be A, B, or TIE")
    systems = sorted([a, b])
    actual = "TIE" if winner == "TIE" else (a if winner == "A" else b)
    return {
        "case_id": str(row["case_id"]).strip(),
        "category": str(row["category"]).strip(),
        "reviewer_id": str(row["reviewer_id"]).strip(),
        "system_1": systems[0],
        "system_2": systems[1],
        "winner": actual,
    }


def aggregate(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    normalized = [_normalized_row(row) for row in rows]
    if not normalized:
        raise ValueError("at least one rating row is required")
    seen = set()
    for row in normalized:
        key = (row["case_id"], row["reviewer_id"], row["system_1"], row["system_2"])
        if key in seen:
            raise ValueError(f"duplicate reviewer/case/pair rating: {key}")
        seen.add(key)

    pair_rows: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in normalized:
        pair_rows[(row["system_1"], row["system_2"])].append(row)

    pairs = []
    for pair in sorted(pair_rows):
        items = pair_rows[pair]
        counts = Counter(item["winner"] for item in items)
        decisive = counts[pair[0]] + counts[pair[1]]
        group_counts: dict[str, Counter[str]] = defaultdict(Counter)
        for item in items:
            group_counts[item["case_id"]][item["winner"]] += 1
        per_category = []
        categories = sorted({item["category"] for item in items})
        for category in categories:
            subset = [item for item in items if item["category"] == category]
            cc = Counter(item["winner"] for item in subset)
            per_category.append({
                "category": category,
                "ratings": len(subset),
                "system_1_wins": cc[pair[0]],
                "system_2_wins": cc[pair[1]],
                "ties": cc["TIE"],
            })
        pairs.append({
            "system_1": pair[0],
            "system_2": pair[1],
            "ratings": len(items),
            "unique_cases": len(group_counts),
            "system_1_wins": counts[pair[0]],
            "system_2_wins": counts[pair[1]],
            "ties": counts["TIE"],
            "system_1_unconditional_win_rate": round(counts[pair[0]] / len(items), 6),
            "system_2_unconditional_win_rate": round(counts[pair[1]] / len(items), 6),
            "tie_rate": round(counts["TIE"] / len(items), 6),
            "system_1_decisive_win_rate": round(counts[pair[0]] / decisive, 6) if decisive else None,
            "system_2_decisive_win_rate": round(counts[pair[1]] / decisive, 6) if decisive else None,
            "system_1_decisive_wilson_95": wilson_interval(counts[pair[0]], decisive),
            "system_2_decisive_wilson_95": wilson_interval(counts[pair[1]], decisive),
            "fleiss_kappa_A_B_TIE": fleiss_kappa(group_counts.values(), [pair[0], pair[1], "TIE"]),
            "per_category": per_category,
        })
    return {
        "report_version": "WE-NARRATIVE-EVAL-0.1",
        "rating_rows": len(normalized),
        "reviewers": len({x["reviewer_id"] for x in normalized}),
        "cases": len({x["case_id"] for x in normalized}),
        "pair_count": len(pairs),
        "pairs": pairs,
        "interpretation_boundary": "Pairwise preference and agreement only; hard correctness must be reported separately and must remain zero for promotion.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Aggregate blinded World Engine narrative ratings JSONL.")
    parser.add_argument("ratings", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    rows = []
    for line_number, line in enumerate(args.ratings.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise SystemExit(f"invalid JSON on line {line_number}: {exc}") from exc
    report = aggregate(rows)
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

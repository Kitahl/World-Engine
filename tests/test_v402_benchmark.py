from __future__ import annotations

import unittest

from scripts.narrative_benchmark_aggregate import aggregate, fleiss_kappa, wilson_interval


class NarrativeBenchmarkV402Tests(unittest.TestCase):
    def test_wilson_interval_is_bounded(self):
        low, high = wilson_interval(7, 10)
        self.assertGreaterEqual(low, 0)
        self.assertLessEqual(high, 1)
        self.assertLess(low, 0.7)
        self.assertGreater(high, 0.7)

    def test_fleiss_kappa_perfect_agreement(self):
        from collections import Counter
        value = fleiss_kappa([Counter({"A": 3}), Counter({"B": 3})], ["A", "B", "TIE"])
        self.assertEqual(1.0, value)

    def test_aggregate_normalizes_left_right_and_reports_ties(self):
        rows = [
            {"case_id": "c1", "category": "dialogue", "reviewer_id": "r1", "system_a": "candidate", "system_b": "baseline", "winner": "A"},
            {"case_id": "c1", "category": "dialogue", "reviewer_id": "r2", "system_a": "baseline", "system_b": "candidate", "winner": "B"},
            {"case_id": "c1", "category": "dialogue", "reviewer_id": "r3", "system_a": "candidate", "system_b": "baseline", "winner": "TIE"},
        ]
        report = aggregate(rows)
        pair = report["pairs"][0]
        self.assertEqual("baseline", pair["system_1"])
        self.assertEqual("candidate", pair["system_2"])
        self.assertEqual(2, pair["system_2_wins"])
        self.assertEqual(1, pair["ties"])
        self.assertEqual(1.0, pair["system_2_decisive_win_rate"])


if __name__ == "__main__":
    unittest.main()

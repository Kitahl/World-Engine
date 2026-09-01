"""World Engine 5.1.0 — additive projection contract and snapshot consistency.

The 5.1.0 projection adds three fields (``projection_sequence``,
``terrain_seed``, ``notification_summary``) without removing or renaming
anything. These tests pin the additions, prove the existing visibility rules
still hold, and check that a snapshot is assembled from one consistent read
rather than several independent ones.
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from world_engine import WorldEngine  # noqa: E402
from world_engine.desktop import (  # noqa: E402
    DESKTOP_PROJECTION_VERSION,
    LOW_HP_WARNING_FRACTION,
    SUPPORTED_PROJECTION_VERSIONS,
    DesktopProjectionKernel,
    _notification_summary,
    _stable_campaign_seed,
)


class ProjectionContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = WorldEngine(Path(self.tmp.name) / "proj.sqlite3")
        self.engine.ensure_campaign("c", "Projection")
        self.engine.upsert_location(
            "c", "home", "Hearth", region="Vale", x=1.0, y=2.0,
            description="Home", tags=["public_map"], state={"population": 400},
        )
        self.engine.upsert_character("c", "hero", "Hero", hp=30, max_hp=30, location="home")

    def tearDown(self) -> None:
        try:
            self.tmp.cleanup()
        except OSError:
            pass

    def _snap(self):
        return DesktopProjectionKernel(self.engine, "c", "hero").snapshot()

    def test_schema_is_bumped_and_supported(self) -> None:
        self.assertEqual("WE-DESKTOP-5.1.0", DESKTOP_PROJECTION_VERSION)
        self.assertIn(DESKTOP_PROJECTION_VERSION, SUPPORTED_PROJECTION_VERSIONS)
        self.assertEqual(DESKTOP_PROJECTION_VERSION, self._snap()["schema"])

    def test_existing_fields_remain_present(self) -> None:
        snapshot = self._snap()
        for field in (
            "campaign_id", "campaign", "mode", "presentation", "player", "location",
            "environment", "economy", "population", "world_map", "combat", "quests",
            "executable_quests", "inventory", "balances", "known_npcs", "known_factions",
            "known_relationships", "agency", "politics", "journal", "investigation",
            "projection_sha256",
        ):
            self.assertIn(field, snapshot, f"5.1.0 must not drop {field}")

    def test_projection_sequence_equals_campaign_revision(self) -> None:
        snapshot = self._snap()
        self.assertEqual(snapshot["campaign"]["revision"], snapshot["projection_sequence"])
        self.assertIsInstance(snapshot["projection_sequence"], int)

    def test_projection_sequence_advances_with_state(self) -> None:
        before = self._snap()["projection_sequence"]
        self.engine.apply_hp_delta("c", "character", "hero", -1, "scratch")
        self.assertGreater(self._snap()["projection_sequence"], before)

    def test_terrain_seed_is_stable_non_negative_int(self) -> None:
        first = self._snap()["terrain_seed"]
        second = self._snap()["terrain_seed"]
        self.assertEqual(first, second)
        self.assertIsInstance(first, int)
        self.assertGreaterEqual(first, 0)

    def test_terrain_seed_prefers_the_simulation_seed(self) -> None:
        self.engine.set_simulation_seed("c", 4242)
        self.assertEqual(4242, self._snap()["terrain_seed"])

    def test_stable_fallback_seed_is_deterministic_and_bounded(self) -> None:
        a = _stable_campaign_seed("default")
        self.assertEqual(a, _stable_campaign_seed("default"))
        self.assertNotEqual(a, _stable_campaign_seed("other"))
        self.assertTrue(0 <= a <= 0x7FFFFFFF)

    def test_notification_tier_normal_when_healthy(self) -> None:
        summary = self._snap()["notification_summary"]
        self.assertEqual("normal", summary["tier"])
        self.assertEqual(0, summary["critical"])

    def test_notification_tier_warns_below_threshold(self) -> None:
        # Drop to just under the documented warning fraction.
        target = int(30 * LOW_HP_WARNING_FRACTION) - 1
        self.engine.apply_hp_delta("c", "character", "hero", target - 30, "wounded")
        summary = self._snap()["notification_summary"]
        self.assertEqual("warning", summary["tier"])
        self.assertGreaterEqual(summary["warning"], 1)

    def test_notification_tier_critical_at_zero_hp(self) -> None:
        self.engine.apply_hp_delta("c", "character", "hero", -30, "down")
        summary = self._snap()["notification_summary"]
        self.assertEqual("critical", summary["tier"])
        self.assertGreaterEqual(summary["critical"], 1)

    def test_notification_summary_is_pure_and_bounded(self) -> None:
        summary = _notification_summary(
            {"hp": 1, "max_hp": 100, "status": "alive"},
            {"id": "fight"},
            {"alerts": list(range(50))},
            [{"id": i} for i in range(50)],
        )
        self.assertEqual("warning", summary["tier"])
        self.assertLessEqual(summary["warning"], 1 + 1 + 10)
        self.assertLessEqual(summary["informational"], 10)

    def test_additive_fields_are_json_serializable(self) -> None:
        snapshot = self._snap()
        json.dumps(
            {
                "projection_sequence": snapshot["projection_sequence"],
                "terrain_seed": snapshot["terrain_seed"],
                "notification_summary": snapshot["notification_summary"],
            }
        )


class VisibilityPreservedTests(unittest.TestCase):
    """5.1.0 is additive: none of the 5.0.x visibility rules may loosen."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = WorldEngine(Path(self.tmp.name) / "vis.sqlite3")
        self.engine.ensure_campaign("c", "Visibility")
        self.engine.upsert_location(
            "c", "home", "Hearth", region="Vale", x=1.0, y=2.0, tags=["public_map"]
        )
        self.engine.upsert_location(
            "c", "secret", "HIDDEN_KEEP_CANARY", region="Nowhere", x=9.0, y=9.0
        )
        self.engine.upsert_character("c", "hero", "Hero", hp=10, max_hp=10, location="home")
        self.engine.upsert_npc(
            "c", "ghost", "Faraway Ivo", location="secret",
            beliefs=["BELIEF_CANARY"], goals=["GOAL_CANARY"],
            memory=[{"note": "MEMORY_CANARY"}],
        )
        self.engine.commit_event("c", "gm_note", "RAW_EVENT_CANARY")

    def tearDown(self) -> None:
        try:
            self.tmp.cleanup()
        except OSError:
            pass

    def test_hidden_locations_stay_hidden_despite_coordinates(self) -> None:
        blob = json.dumps(DesktopProjectionKernel(self.engine, "c", "hero").snapshot(), default=str)
        self.assertNotIn("HIDDEN_KEEP_CANARY", blob)

    def test_private_cognition_and_raw_events_stay_out(self) -> None:
        blob = json.dumps(DesktopProjectionKernel(self.engine, "c", "hero").snapshot(), default=str)
        for canary in ("BELIEF_CANARY", "GOAL_CANARY", "MEMORY_CANARY", "RAW_EVENT_CANARY"):
            self.assertNotIn(canary, blob)

    def test_remote_npcs_are_not_projected(self) -> None:
        snapshot = DesktopProjectionKernel(self.engine, "c", "hero").snapshot()
        self.assertNotIn("ghost", [n.get("id") for n in snapshot["known_npcs"]])


class SnapshotConsistencyTests(unittest.TestCase):
    """A snapshot must not mix pre-commit and post-commit state."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = WorldEngine(Path(self.tmp.name) / "consistency.sqlite3")
        self.engine.ensure_campaign("c", "Consistency")
        self.engine.upsert_location("c", "home", "Hearth", region="Vale", x=1.0, y=2.0, tags=["public_map"])
        self.engine.upsert_character("c", "hero", "Hero", hp=50, max_hp=50, location="home")

    def tearDown(self) -> None:
        try:
            self.tmp.cleanup()
        except OSError:
            pass

    def test_sequence_matches_reported_revision_under_concurrent_writes(self) -> None:
        """Whatever revision the snapshot reports, its sequence must agree.

        A torn read would surface as a snapshot whose campaign revision and
        projection_sequence disagree, or whose sequence never appears in the
        real revision history.
        """
        stop = threading.Event()
        seen: list[tuple[int, int]] = []
        errors: list[BaseException] = []

        def writer() -> None:
            try:
                while not stop.is_set():
                    self.engine.apply_hp_delta("c", "character", "hero", 0, "tick")
            except BaseException as exc:  # pragma: no cover - surfaced below
                errors.append(exc)

        thread = threading.Thread(target=writer, daemon=True)
        thread.start()
        try:
            kernel = DesktopProjectionKernel(self.engine, "c", "hero")
            for _ in range(12):
                snapshot = kernel.snapshot()
                seen.append((snapshot["campaign"]["revision"], snapshot["projection_sequence"]))
        finally:
            stop.set()
            thread.join(timeout=10)

        self.assertEqual([], errors, f"writer thread failed: {errors[:1]}")
        for revision, sequence in seen:
            self.assertEqual(revision, sequence, "campaign revision and projection_sequence disagreed")

    def test_repeated_snapshots_are_individually_coherent(self) -> None:
        kernel = DesktopProjectionKernel(self.engine, "c", "hero")
        for _ in range(5):
            snapshot = kernel.snapshot()
            self.assertEqual(snapshot["campaign_id"], "c")
            self.assertIsNotNone(snapshot["player"])
            self.assertEqual(snapshot["player"]["location_id"], "home")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

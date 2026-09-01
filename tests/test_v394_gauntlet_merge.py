from __future__ import annotations

import tempfile
import unittest
from unittest import mock
from pathlib import Path

from world_engine import WorldEngine
from world_engine.music import MusicResolver, normalize_music_catalog, youtube_video_id
from music_player import PlayerApi, player_html


CONTROL_ID = "M7lc1UVf-VE"
FALLBACK_ID = "dQw4w9WgXcQ"


class V394GauntletMergeTests(unittest.TestCase):
    def test_youtube_parser_accepts_supported_forms_and_rejects_ambiguous_urls(self):
        accepted = (
            CONTROL_ID,
            f"https://youtu.be/{CONTROL_ID}",
            f"https://www.youtube.com/watch?v={CONTROL_ID}",
            f"https://m.youtube.com/watch?v={CONTROL_ID}&feature=share",
            f"https://music.youtube.com/watch?v={CONTROL_ID}",
            f"https://www.youtube.com/embed/{CONTROL_ID}",
            f"https://www.youtube.com/shorts/{CONTROL_ID}",
            f"https://www.youtube.com/live/{CONTROL_ID}",
            f"https://www.youtube-nocookie.com/embed/{CONTROL_ID}",
        )
        for value in accepted:
            self.assertEqual(CONTROL_ID, youtube_video_id(value))
        rejected = (
            f"ftp://www.youtube.com/watch?v={CONTROL_ID}",
            f"https://youtu.be/{CONTROL_ID}/extra",
            f"https://www.youtube.com/embed/{CONTROL_ID}/extra",
            f"https://www.youtube.com/watch?v={CONTROL_ID}&v={FALLBACK_ID}",
            f"https://www.youtube.com.evil.invalid/watch?v={CONTROL_ID}",
            f"https://user@example.com/watch?v={CONTROL_ID}",
            f"https://www.youtube.com/watch?v={CONTROL_ID}#fragment",
            "not-a-video!",
            "",
        )
        for value in rejected:
            with self.assertRaises(ValueError, msg=value):
                youtube_video_id(value)

    def test_catalog_normalization_adds_provenance_and_drops_malformed_track(self):
        payload = {
            "tracks": [
                {
                    "id": "control",
                    "youtube": f"https://youtu.be/{CONTROL_ID}",
                    "priority": "not-an-int",
                    "volume": 9,
                    "match": {"scene_types": ["travel"]},
                },
                {"id": "bad", "youtube": "invalid"},
            ]
        }
        normalized = normalize_music_catalog(payload)
        self.assertEqual(1, len(normalized["tracks"]))
        track = normalized["tracks"][0]
        self.assertEqual(CONTROL_ID, track["youtube"])
        self.assertEqual(f"https://youtu.be/{CONTROL_ID}", track["source_url"])
        self.assertEqual({"scene_types": ["travel"]}, track["binding_tags"])
        self.assertEqual(0, track["priority"])
        self.assertEqual(9.0, track["volume"])
        self.assertEqual("unverified", track["validation_status"])
        self.assertIsNone(track["last_validation_result"])

    def _resolver(self, root: Path) -> MusicResolver:
        e = WorldEngine(root / "world.sqlite3")
        e.ensure_campaign("c")
        e.upsert_location("c", "l", "L", region="r")
        e.upsert_character("c", "hero", "Hero", location="l", hp=10, max_hp=10, ac=10)
        resolver = MusicResolver(e, root / "music.json")
        resolver.save_catalog({
            "version": 1,
            "defaults": {},
            "tracks": [
                {"id": "first", "youtube": CONTROL_ID, "priority": 2, "match": {}},
                {"id": "second", "youtube": FALLBACK_ID, "priority": 1, "match": {}},
            ],
        })
        return resolver

    def test_music_failure_cooldown_excludes_candidate_and_persists_validation_result(self):
        with tempfile.TemporaryDirectory() as td:
            resolver = self._resolver(Path(td))
            self.assertEqual("first", resolver.resolve("c").track["id"])
            receipt = resolver.record_player_error(CONTROL_ID, 100, message="unavailable")
            self.assertTrue(receipt["fallback_requested"])
            self.assertEqual(900, receipt["cooldown_seconds"])
            self.assertEqual("second", resolver.resolve("c").track["id"])
            catalog = resolver.load_catalog()
            first = next(x for x in catalog["tracks"] if x["id"] == "first")
            self.assertEqual("error", first["validation_status"])
            self.assertEqual(100, first["last_validation_result"]["error_code"])
            resolver.clear_player_failures()
            self.assertEqual("first", resolver.resolve("c").track["id"])

    def test_player_error_returns_immediate_next_decision_and_153_does_not_blacklist(self):
        with tempfile.TemporaryDirectory() as td:
            resolver = self._resolver(Path(td))
            api = PlayerApi(resolver, "c")
            receipt = api.report_player_error(100, "unavailable", CONTROL_ID)
            self.assertTrue(receipt["fallback"])
            self.assertEqual("second", receipt["next_decision"]["track"]["id"])
            self.assertIn(CONTROL_ID, receipt["excluded_video_ids"])
            origin = api.report_player_error(153, "origin", FALLBACK_ID)
            self.assertFalse(origin["fallback"])
            self.assertNotIn(FALLBACK_ID, origin["excluded_video_ids"])
            html = player_html("http://127.0.0.1:9999")
            self.assertIn("receipt.next_decision", html)
            self.assertIn("v5.0.0", html)


    def test_public_health_checks_exact_gpt_action_endpoint(self):
        import launcher
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): return False
        with mock.patch.object(launcher.urllib.request, "urlopen", return_value=Response()) as opened:
            self.assertTrue(launcher.public_health("https://example.invalid/"))
        self.assertEqual("https://example.invalid/health", opened.call_args.args[0])

    def test_next_turn_automatically_resolves_dying_player_character_death_save(self):
        with tempfile.TemporaryDirectory() as td:
            e = WorldEngine(Path(td) / "world.sqlite3")
            e.ensure_campaign("c")
            e.set_simulation_seed("c", 99)
            e.upsert_location("c", "arena", "Arena", region="r")
            e.upsert_character("c", "hero", "Hero", location="arena", hp=10, max_hp=10, ac=10)
            e.upsert_npc("c", "foe", "Foe", location="arena", hp=10, max_hp=10, ac=10)
            e.rules_dispatch("set_actor_profile", "c", {"actor_kind": "character", "actor_id": "hero"})
            combat = e.start_combat("c", "fight", "arena", [
                {"kind": "character", "id": "hero"},
                {"kind": "npc", "id": "foe"},
            ])
            e.apply_hp_delta("c", "character", "hero", -10, "test injury")
            result = None
            # Advance at most one complete initiative cycle until the dying hero's
            # turn becomes active. The backend must resolve the save automatically.
            for _ in range(len(combat["initiative"]) + 1):
                result = e.next_turn("c", "fight")
                if "automatic_death_save" in result:
                    break
            self.assertIsNotNone(result)
            self.assertIn("automatic_death_save", result)
            save = result["automatic_death_save"]
            self.assertIn(save["outcome"], {"success", "failure", "critical_failure", "critical_success_revived", "stable", "dead"})
            with e._db() as db:
                events = db.execute("SELECT event_type FROM events WHERE campaign_id='c' ORDER BY revision").fetchall()
            self.assertIn("death_save", [row[0] for row in events])


if __name__ == "__main__":
    unittest.main()

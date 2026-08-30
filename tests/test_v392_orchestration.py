from pathlib import Path
import tempfile
import unittest

from world_engine import WorldEngine
from world_engine.turn_policy import select_reasoning_profile, image_directive
from world_engine.music import MusicResolver, youtube_video_id
from music_player import PlayerApi


class V392OrchestrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.engine = WorldEngine(root / "v392.sqlite3")
        self.engine.ensure_campaign("c", "V392")
        self.engine.upsert_location("c", "gate", "North Gate", region="frontier", description="A basalt gate under storm clouds.")
        self.engine.upsert_character("c", "hero", "Hero", hp=20, max_hp=20, ac=15, location="gate")
        self.catalog = root / "music.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_reasoning_profiles_use_current_slider_names(self):
        routine = select_reasoning_profile(task="routine")
        scene = select_reasoning_profile(task="routine", trigger_type="scene_start")
        world = select_reasoning_profile(task="world_generation")
        choice = select_reasoning_profile(task="quest_branch", trigger_type="event_choice", choice_options=["a", "b", "c", "d"], major_consequence=True)
        self.assertEqual("Instant", routine["recommended_reasoning_level"])
        self.assertEqual("Medium", scene["recommended_reasoning_level"])
        self.assertEqual("High", world["recommended_reasoning_level"])
        self.assertEqual("High", choice["recommended_reasoning_level"])
        self.assertNotIn("Thinking", {routine["recommended_reasoning_level"], scene["recommended_reasoning_level"], world["recommended_reasoning_level"]})

    def test_image_directive_is_mandatory_when_cue_requires_generation(self):
        directive = image_directive({"should_generate": True, "prompt": "scene"})
        self.assertTrue(directive["required"])
        self.assertEqual("before_narration", directive["order"])
        self.assertTrue(directive["record_after_generation"])
        self.assertIn("Image generation", directive["capability_requirement"])

    def test_scene_location_battle_and_choice_cues_default_on(self):
        for trigger, kwargs in [
            ("scene_start", {"location_id": "gate", "scene_key": "scene:s1"}),
            ("new_location", {"location_id": "gate", "scene_key": "location:gate:r1"}),
        ]:
            cue = self.engine.build_image_cue("c", trigger_type=trigger, **kwargs)
            self.assertTrue(cue["should_generate"], cue)

        self.engine.upsert_npc("c", "wolf", "Wolf", hp=7, max_hp=7, ac=12, location="gate")
        self.engine.start_scene("c", "s1", "gate", entities=[{"kind":"character","id":"hero"},{"kind":"npc","id":"wolf"}])
        self.engine.start_combat("c", "cmb", "gate", [{"kind":"character","id":"hero"},{"kind":"npc","id":"wolf"}], scene_id="s1")
        battle = self.engine.build_image_cue("c", trigger_type="battle_start", location_id="gate", combat_id="cmb")
        self.assertTrue(battle["should_generate"], battle)

        choice = self.engine.build_image_cue(
            "c", trigger_type="event_choice", location_id="gate", scene_key="decision:1",
            summary="The player must choose whether to open the sealed gate.", choice_options=["Open it", "Leave"], decision_phase="before"
        )
        self.assertTrue(choice["should_generate"], choice)
        self.assertEqual("before", choice["visual_context"]["decision_phase"])

        after = self.engine.build_image_cue(
            "c", trigger_type="event_choice", location_id="gate", scene_key="decision:2",
            summary="The player opened the sealed gate and cold mist spills out.", choice_options=["Opened the gate"], decision_phase="after"
        )
        self.assertTrue(after["should_generate"], after)
        self.assertEqual("after", after["visual_context"]["decision_phase"])
        self.assertIn("immediate visible state after", after["prompt"])

    def test_failed_image_record_does_not_suppress_retry(self):
        cue = self.engine.build_image_cue("c", trigger_type="new_location", location_id="gate", scene_key="arrive:gate")
        self.engine.record_image_generation(
            "c", "new_location", cue["scene_key"], title=cue["title"], prompt=cue["prompt"],
            location_id="gate", status="failed"
        )
        retry = self.engine.build_image_cue("c", trigger_type="new_location", location_id="gate", scene_key="arrive:gate")
        self.assertTrue(retry["should_generate"], retry)

        self.engine.record_image_generation(
            "c", "new_location", cue["scene_key"], title=cue["title"], prompt=cue["prompt"],
            location_id="gate", status="generated"
        )
        done = self.engine.build_image_cue("c", trigger_type="new_location", location_id="gate", scene_key="arrive:gate")
        self.assertFalse(done["should_generate"], done)

    def test_music_parser_and_failed_track_fallback(self):
        vid = "M7lc1UVf-VE"
        self.assertEqual(vid, youtube_video_id(f"https://music.youtube.com/watch?v={vid}"))
        with self.assertRaises(ValueError):
            youtube_video_id("https://www.youtube.com/playlist?list=PL123")

        resolver = MusicResolver(self.engine, self.catalog)
        resolver.save_catalog({
            "version": 1,
            "defaults": {"volume": 55},
            "tracks": [
                {"id":"first","name":"First","youtube":"M7lc1UVf-VE","priority":100,"match":{}},
                {"id":"second","name":"Second","youtube":"dQw4w9WgXcQ","priority":90,"match":{}},
            ],
        })
        self.assertEqual("first", resolver.resolve("c").track["id"])
        self.assertEqual("second", resolver.resolve("c", exclude_video_ids={"M7lc1UVf-VE"}).track["id"])

        api = PlayerApi(resolver, "c")
        result = api.report_player_error(2, "invalid video id", "M7lc1UVf-VE")
        self.assertTrue(result["fallback"])
        self.assertIn("M7lc1UVf-VE", api.failed_video_ids)
        # 153 is an origin/client-identity problem, not a reason to blacklist a track.
        api.report_player_error(153, "referrer rejected", "dQw4w9WgXcQ")
        self.assertNotIn("dQw4w9WgXcQ", api.failed_video_ids)


if __name__ == "__main__":
    unittest.main()

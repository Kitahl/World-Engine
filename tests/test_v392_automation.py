from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from world_engine import WorldEngine
from world_engine.openapi_compat import mark_actions_non_consequential
from world_engine.turn_policy import image_directive, select_reasoning_profile
from world_engine.music import MusicResolver
from music_player import PlayerApi


class V392AutomationTests(unittest.TestCase):
    def setUp(self):
        self._old_admin_key = os.environ.get("WORLD_ENGINE_ADMIN_KEY")
        os.environ["WORLD_ENGINE_ADMIN_KEY"] = "operator-secret-9876543210-abcdef"

    def tearDown(self):
        if self._old_admin_key is None:
            os.environ.pop("WORLD_ENGINE_ADMIN_KEY", None)
        else:
            os.environ["WORLD_ENGINE_ADMIN_KEY"] = self._old_admin_key

    def test_reasoning_policy_is_fast_for_backend_mechanics_and_deep_for_world_synthesis(self):
        fast = select_reasoning_profile(task="combat")
        self.assertEqual("fast", fast["profile"])
        self.assertEqual("Instant", fast["recommended_chatgpt_mode"])
        deep = select_reasoning_profile(task="world_generation")
        self.assertEqual("deep", deep["profile"])
        self.assertEqual("High", deep["recommended_chatgpt_mode"])
        choice = select_reasoning_profile(task="quest_branch", trigger_type="event_choice", choice_options=["a", "b", "c", "d"], major_consequence=True)
        self.assertEqual("deep", choice["profile"])

    def test_image_directive_is_mandatory_when_cue_says_generate(self):
        d = image_directive({"should_generate": True, "prompt": "scene", "scene_key": "s"})
        self.assertTrue(d["required"])
        self.assertEqual("before_narration", d["order"])
        self.assertIn("MANDATORY", d["instruction"])

    def test_setting_neutral_image_prompt(self):
        with tempfile.TemporaryDirectory() as td:
            e = WorldEngine(Path(td) / "x.sqlite3")
            e.ensure_campaign("c")
            e.upsert_location("c", "city", "Neon City", region="arcology", description="A rain-soaked megacity", tags=["cyberpunk"])
            cue = e.build_image_cue("c", trigger_type="new_location", location_id="city")
            self.assertTrue(cue["should_generate"])
            self.assertIn("current campaign setting", cue["prompt"])
            self.assertNotIn("Create one fantasy scene", cue["prompt"])
            self.assertNotIn("Avoid: modern objects", cue["prompt"])

    def test_openapi_actions_can_be_marked_non_consequential(self):
        schema = {"paths": {"/x": {"post": {"operationId": "x"}, "get": {"operationId": "y"}}}}
        mark_actions_non_consequential(schema)
        self.assertFalse(schema["paths"]["/x"]["post"]["x-openai-isConsequential"])
        self.assertFalse(schema["paths"]["/x"]["get"]["x-openai-isConsequential"])

    def test_music_resolver_excludes_failed_candidate(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            e = WorldEngine(root / "x.sqlite3")
            e.ensure_campaign("c")
            e.upsert_location("c", "l", "L", region="r")
            e.upsert_character("c", "p", "P", location="l", hp=10, max_hp=10, ac=10)
            r = MusicResolver(e, root / "music.json")
            r.save_catalog({"version": 1, "defaults": {}, "tracks": [
                {"id": "a", "youtube": "M7lc1UVf-VE", "priority": 100, "match": {}},
                {"id": "b", "youtube": "dQw4w9WgXcQ", "priority": 90, "match": {}},
            ]})
            self.assertEqual("a", r.resolve("c").track["id"])
            self.assertEqual("b", r.resolve("c", exclude_video_ids={"M7lc1UVf-VE"}).track["id"])

    def test_music_player_errors_blacklist_track_except_153(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            e = WorldEngine(root / "x.sqlite3")
            e.ensure_campaign("c")
            api = PlayerApi(MusicResolver(e, root / "music.json"), "c")
            res = api.report_player_error(2, "bad", "M7lc1UVf-VE")
            self.assertTrue(res["fallback"])
            self.assertIn("M7lc1UVf-VE", api.failed_video_ids)
            api.report_player_error(153, "origin", "dQw4w9WgXcQ")
            self.assertNotIn("dQw4w9WgXcQ", api.failed_video_ids)

    def test_api_scene_start_carries_required_image_and_receipt(self):
        # app has a module-global engine; replace it only within this test and restore.
        import app as api
        old_engine = api.engine
        old_key = os.environ.get("WORLD_ENGINE_API_KEY")
        try:
            os.environ["WORLD_ENGINE_API_KEY"] = "test-secret-0123456789-abcdef"
            with tempfile.TemporaryDirectory() as td:
                api.engine = WorldEngine(Path(td) / "api.sqlite3")
                api.engine.ensure_campaign("c")
                api.engine.upsert_location("c", "dock", "Orbital Dock", region="ring", description="A crowded orbital terminal")
                api.engine.upsert_character("c", "hero", "Hero", location="dock", hp=10, max_hp=10, ac=10)
                client = TestClient(api.app)
                headers = {"Authorization": "Bearer test-secret-0123456789-abcdef", "X-World-Engine-Operator-Key": "operator-secret-9876543210-abcdef"}
                res = client.post("/api/sim/configure", headers=headers, json={
                    "campaign_id": "c", "kind": "scene", "scene_id": "intro", "location_id": "dock", "scene_type": "exploration",
                    "params": {"action": "start", "entities": [{"kind": "character", "id": "hero"}]}
                })
                self.assertEqual(200, res.status_code, res.text)
                body = res.json()
                self.assertTrue(body["_turn_directives"]["image"]["required"])
                self.assertEqual("scene:intro", body["_turn_directives"]["image"]["cue"]["scene_key"])
                self.assertEqual("4.5.0", body["_engine_receipt"]["engine_version"])
        finally:
            api.engine = old_engine
            if old_key is None:
                os.environ.pop("WORLD_ENGINE_API_KEY", None)
            else:
                os.environ["WORLD_ENGINE_API_KEY"] = old_key

    def test_api_character_move_carries_new_location_image(self):
        import app as api
        old_engine = api.engine
        old_key = os.environ.get("WORLD_ENGINE_API_KEY")
        try:
            os.environ["WORLD_ENGINE_API_KEY"] = "test-secret-0123456789-abcdef"
            with tempfile.TemporaryDirectory() as td:
                api.engine = WorldEngine(Path(td) / "api.sqlite3")
                api.engine.ensure_campaign("c")
                api.engine.upsert_location("c", "a", "A", region="r")
                api.engine.upsert_location("c", "b", "B", region="r")
                api.engine.upsert_character("c", "hero", "Hero", location="a", hp=10, max_hp=10, ac=10)
                client = TestClient(api.app)
                headers = {"Authorization": "Bearer test-secret-0123456789-abcdef", "X-World-Engine-Operator-Key": "operator-secret-9876543210-abcdef"}
                res = client.post("/api/gameplay/move", headers=headers, json={"campaign_id":"c","kind":"character","actor_id":"hero","location":"b","reason":"travel"})
                self.assertEqual(200, res.status_code, res.text)
                body = res.json()
                self.assertTrue(body["_turn_directives"]["image"]["required"])
                self.assertEqual("new_location", body["_turn_directives"]["image"]["cue"]["trigger_type"])
                self.assertEqual("b", body["_turn_directives"]["image"]["cue"]["location_id"])
        finally:
            api.engine = old_engine
            if old_key is None:
                os.environ.pop("WORLD_ENGINE_API_KEY", None)
            else:
                os.environ["WORLD_ENGINE_API_KEY"] = old_key

    def test_api_event_choice_requires_image_and_deep_reasoning(self):
        import app as api
        old_engine = api.engine
        old_key = os.environ.get("WORLD_ENGINE_API_KEY")
        try:
            os.environ["WORLD_ENGINE_API_KEY"] = "test-secret-0123456789-abcdef"
            with tempfile.TemporaryDirectory() as td:
                api.engine = WorldEngine(Path(td) / "api.sqlite3")
                api.engine.ensure_campaign("c")
                api.engine.upsert_location("c", "l", "L", region="r")
                client = TestClient(api.app)
                headers = {"Authorization": "Bearer test-secret-0123456789-abcdef", "X-World-Engine-Operator-Key": "operator-secret-9876543210-abcdef"}
                res = client.post("/api/visual/cue", headers=headers, json={
                    "campaign_id":"c", "trigger_type":"event_choice", "location_id":"l",
                    "scene_key":"choice:gate", "summary":"Choose which faction controls the gate.",
                    "choice_options":["Crown","Guild","Rebels","Destroy it"]
                })
                self.assertEqual(200, res.status_code, res.text)
                body = res.json()
                self.assertTrue(body["_turn_directives"]["image"]["required"])
                self.assertEqual("deep", body["_turn_directives"]["reasoning"]["profile"])
                self.assertEqual("High", body["_turn_directives"]["reasoning"]["recommended_reasoning_level"])
        finally:
            api.engine = old_engine
            if old_key is None:
                os.environ.pop("WORLD_ENGINE_API_KEY", None)
            else:
                os.environ["WORLD_ENGINE_API_KEY"] = old_key

    def test_api_combat_start_carries_battle_image(self):
        import app as api
        old_engine = api.engine
        old_key = os.environ.get("WORLD_ENGINE_API_KEY")
        try:
            os.environ["WORLD_ENGINE_API_KEY"] = "test-secret-0123456789-abcdef"
            with tempfile.TemporaryDirectory() as td:
                api.engine = WorldEngine(Path(td) / "api.sqlite3")
                api.engine.ensure_campaign("c")
                api.engine.upsert_location("c", "arena", "Arena", region="r")
                api.engine.upsert_character("c", "hero", "Hero", location="arena", hp=10, max_hp=10, ac=10)
                api.engine.upsert_npc("c", "foe", "Foe", location="arena", hp=8, max_hp=8, ac=10)
                client = TestClient(api.app)
                headers = {"Authorization": "Bearer test-secret-0123456789-abcdef", "X-World-Engine-Operator-Key": "operator-secret-9876543210-abcdef"}
                res = client.post("/api/combat/start", headers=headers, json={
                    "campaign_id":"c","combat_id":"fight","location":"arena",
                    "participants":[{"kind":"character","id":"hero"},{"kind":"npc","id":"foe"}]
                })
                self.assertEqual(200, res.status_code, res.text)
                body = res.json()
                self.assertTrue(body["_turn_directives"]["image"]["required"])
                self.assertEqual("battle_start", body["_turn_directives"]["image"]["cue"]["trigger_type"])
                self.assertEqual("standard", body["_turn_directives"]["reasoning"]["profile"])
                self.assertEqual("Medium", body["_turn_directives"]["reasoning"]["recommended_reasoning_level"])
        finally:
            api.engine = old_engine
            if old_key is None:
                os.environ.pop("WORLD_ENGINE_API_KEY", None)
            else:
                os.environ["WORLD_ENGINE_API_KEY"] = old_key


if __name__ == "__main__":
    unittest.main()

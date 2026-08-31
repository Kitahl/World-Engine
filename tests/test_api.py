import os
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        os.environ["WORLD_ENGINE_DB"] = str(Path(cls.tmp.name) / "api.sqlite3")
        os.environ["WORLD_ENGINE_API_KEY"] = "test-secret-0123456789-abcdef"
        os.environ["WORLD_ENGINE_ADMIN_KEY"] = "operator-secret-0123456789-abcdef"
        import importlib
        cls.api = importlib.import_module("app")
        cls.client = TestClient(cls.api.app)
        cls.headers = {
            "Authorization": "Bearer test-secret-0123456789-abcdef",
            "X-World-Engine-Operator-Key": "operator-secret-0123456789-abcdef",
        }

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_openapi_has_action_operation_ids(self):
        schema = self.client.get("/openapi.json").json()
        operation_ids = {op.get("operationId") for path in schema["paths"].values() for op in path.values() if isinstance(op, dict)}
        self.assertNotIn("getWorldContext", operation_ids)
        self.assertNotIn("getEntity", operation_ids)
        self.assertIn("resolveTurn", operation_ids)
        self.assertNotIn("resolveAttack", operation_ids)
        self.assertNotIn("runRulesKernel", operation_ids)
        self.assertNotIn("advanceWorld", operation_ids)
        for operation_id in {
            "saveNpc", "saveFaction", "updateNpcState", "adjustFaction", "setWorldState",
            "configureSimulation", "authorWorldContent",
        }:
            self.assertNotIn(operation_id, operation_ids)
        self.assertIn("buildImageCue", operation_ids)
        self.assertIn("recordImageGeneration", operation_ids)
        self.assertNotIn("getInternalStateBlock", operation_ids)
        self.assertIn("saveVisualProfile", operation_ids)
        self.assertNotIn("getVisualProfile", operation_ids)
        self.assertNotIn("saveVisualState", operation_ids)
        self.assertNotIn("getVisualState", operation_ids)
        self.assertNotIn("getRecentImageContext", operation_ids)
        self.assertNotIn("getVisualPreferences", operation_ids)
        self.assertNotIn("setVisualPreferences", operation_ids)
        self.assertNotIn("generateNpcPortrait", operation_ids)

    def test_publish_presentation_rejects_model_owned_extension_fields(self):
        response = self.client.post(
            "/api/presentation",
            headers=self.headers,
            json={
                "campaign_id": "default",
                "presentation_id": "pres-closed-request",
                "packet_id": "packet-closed-request",
                "turn_id": "turn-closed-request",
                "expected_revision": 0,
                "narration": "A lantern burns beside the closed gate.",
                "choices": [],
                "presentation": {"unexpected": True},
            },
        )
        self.assertEqual(422, response.status_code, response.text)
        rejected_fields = {
            str(item["loc"][-1]) for item in response.json().get("detail", [])
        }
        self.assertIn("presentation", rejected_fields)

    def test_publish_presentation_rejects_choice_over_500_characters(self):
        response = self.client.post(
            "/api/presentation",
            headers=self.headers,
            json={
                "campaign_id": "default",
                "presentation_id": "pres-choice-boundary",
                "packet_id": "packet-choice-boundary",
                "turn_id": "turn-choice-boundary",
                "expected_revision": 0,
                "narration": "A lantern burns beside the closed gate.",
                "choices": ["x" * 501],
            },
        )
        self.assertEqual(422, response.status_code, response.text)
        errors = response.json().get("detail", [])
        self.assertTrue(
            any(item.get("loc", [])[-2:] == ["choices", 0] for item in errors),
            errors,
        )


    def test_public_turn_rejects_npc_actor_impersonation(self):
        response = self.client.post(
            "/api/turn",
            headers=self.headers,
            json={
                "campaign_id": "default",
                "actor_kind": "npc",
                "actor_id": "private-npc",
                "mode": "context_only",
            },
        )
        self.assertEqual(422, response.status_code, response.text)

    def test_public_turn_rejects_private_read_capability(self):
        response = self.client.post(
            "/api/turn",
            headers=self.headers,
            json={
                "campaign_id": "default",
                "actor_kind": "character",
                "actor_id": "hero",
                "mode": "context_only",
                "intents": [{"capability": "knowledge.read", "parameters": {}}],
            },
        )
        self.assertEqual(403, response.status_code, response.text)
        self.assertEqual("PUBLIC_TURN_CAPABILITY_NOT_ALLOWED", response.json()["detail"])

    def test_public_turn_rejects_knowledge_transfer_and_unknown_capabilities(self):
        campaign = "public-turn-capability-boundary"
        for intent in (
            {"capability": "knowledge.transfer", "parameters": {}},
            {"type": "inform", "parameters": {}},
            {"capability": "future.unreviewed", "parameters": {}},
        ):
            response = self.client.post(
                "/api/turn",
                headers=self.headers,
                json={
                    "campaign_id": campaign,
                    "actor_kind": "character",
                    "actor_id": "hero",
                    "mode": "context_only",
                    "intents": [intent],
                },
            )
            self.assertEqual(403, response.status_code, response.text)
            self.assertEqual("PUBLIC_TURN_CAPABILITY_NOT_ALLOWED", response.json()["detail"])

    def test_enforce_mode_rejects_caller_downgrade_before_turn(self):
        campaign = "api-enforce-no-downgrade"
        self.api.engine.ensure_campaign(campaign, "Enforce")
        self.api.engine.configure_narrative(campaign, mode="enforce")
        response = self.client.post(
            "/api/turn",
            headers=self.headers,
            json={"campaign_id": campaign, "mode": "context_only", "narrative_mode_override": "off"},
        )
        self.assertEqual(409, response.status_code, response.text)
        self.assertEqual("NARRATIVE_ENFORCE_DOWNGRADE_REJECTED", response.json()["detail"])

    def test_enforce_mode_requires_execute_without_override_before_turn(self):
        campaign = "api-enforce-execute-required"
        self.api.engine.ensure_campaign(campaign, "Enforce execute only")
        self.api.engine.configure_narrative(campaign, mode="enforce")
        revision_before = self.api.engine.get_campaign(campaign)["revision"]
        response = self.client.post(
            "/api/turn",
            headers=self.headers,
            json={"campaign_id": campaign, "mode": "context_only"},
        )
        self.assertEqual(409, response.status_code, response.text)
        self.assertEqual("NARRATIVE_ENFORCE_EXECUTE_REQUIRED", response.json()["detail"])
        self.assertEqual(revision_before, self.api.engine.get_campaign(campaign)["revision"])

    def test_enforce_http_body_excludes_canonical_fact_and_event_canaries(self):
        from world_engine.turn_router import TurnRouter

        campaign = "api-enforce-whole-response"
        canonical_marker = "HTTP-CANONICAL-SECRET-CANARY"
        belief_marker = "HTTP-FALSE-BELIEF-CANARY"
        event_marker = "HTTP-SECRET-EVENT-CANARY"
        self.api.engine.ensure_campaign(campaign, "Whole Response")
        self.api.engine.upsert_location(campaign, "road", "Road")
        self.api.engine.upsert_character(
            campaign, "hero", "Hero", location="road", hp=10, max_hp=10, ac=12,
        )
        router = TurnRouter(self.api.engine)
        fact = router.assert_fact(
            campaign, "location:road", "witness.identity", canonical_marker,
        )
        router.set_belief(
            campaign, "character:hero", fact["fact_id"], belief_value=belief_marker,
        )
        self.api.engine.commit_event(
            campaign, "secret_test_event", event_marker, payload={"marker": event_marker},
        )
        self.api.engine.configure_narrative(campaign, mode="enforce")

        response = self.client.post(
            "/api/turn",
            headers=self.headers,
            json={
                "campaign_id": campaign,
                "actor_kind": "character",
                "actor_id": "hero",
                "player_text": "Check the empty road.",
                "mode": "execute",
                "idempotency_key": "http-whole-response-canary",
                "intents": [{"type": "check", "parameters": {"modifier": 0, "dc": 10}}],
            },
        )
        self.assertEqual(200, response.status_code, response.text)
        self.assertNotIn(canonical_marker, response.text)
        self.assertNotIn(event_marker, response.text)
        self.assertNotIn("context_packet", response.text)
        body = response.json()
        self.assertIn("_narrative_render_packet", body)
    def test_key_error_http_body_does_not_reflect_resource_identifier(self):
        marker = "PRIVATE-MISSING-RESOURCE-CANARY"
        response = self.client.get(
            f"/api/entity/npc/{marker}",
            headers=self.headers,
            params={"campaign_id": "default"},
        )
        self.assertEqual(404, response.status_code, response.text)
        self.assertEqual("RESOURCE_NOT_FOUND", response.json()["detail"])
        self.assertNotIn(marker, response.text)

    def test_auth_required(self):
        r = self.client.get("/api/context")
        self.assertEqual(401, r.status_code)


    def test_auth_fails_closed_when_key_unset(self):
        old = os.environ.pop("WORLD_ENGINE_API_KEY", None)
        old_admin = os.environ.pop("WORLD_ENGINE_ADMIN_KEY", None)
        try:
            missing_config = Path(self.tmp.name) / "missing-launcher-config.json"
            with patch.object(self.api, "PERSISTENT_CONFIG_PATH", missing_config):
                read = self.client.get("/api/context")
                self.assertEqual(503, read.status_code)
                write = self.client.post("/api/setup/character", json={
                    "campaign_id": "closed", "character_id": "hero", "name": "Hero",
                    "level": 1, "hp": 10, "max_hp": 10, "ac": 10, "location": "Gate"
                })
                self.assertEqual(503, write.status_code)
        finally:
            if old is not None:
                os.environ["WORLD_ENGINE_API_KEY"] = old
            if old_admin is not None:
                os.environ["WORLD_ENGINE_ADMIN_KEY"] = old_admin


    def test_auth_rejects_placeholder_key(self):
        old = os.environ.get("WORLD_ENGINE_API_KEY")
        os.environ["WORLD_ENGINE_API_KEY"] = "change-me-before-public-use"
        try:
            r = self.client.get("/api/context", headers={"Authorization": "Bearer change-me-before-public-use"})
            self.assertEqual(503, r.status_code)
        finally:
            if old is None:
                os.environ.pop("WORLD_ENGINE_API_KEY", None)
            else:
                os.environ["WORLD_ENGINE_API_KEY"] = old


    def test_campaign_bootstrap_via_api_with_minimal_and_explicit_world_time(self):
        minimal = self.client.post(
            "/api/campaign", headers=self.headers,
            json={"campaign_id": "qa-bootstrap-minimal", "name": "QA Minimal"},
        )
        self.assertEqual(200, minimal.status_code, minimal.text)
        self.assertEqual("qa-bootstrap-minimal", minimal.json()["id"])
        self.assertEqual(0, minimal.json()["revision"])
        explicit = self.client.post(
            "/api/campaign", headers=self.headers,
            json={"campaign_id": "qa-bootstrap-explicit", "name": "QA Explicit", "world_time": "1492-06-01T10:30:00+00:00"},
        )
        self.assertEqual(200, explicit.status_code, explicit.text)
        self.assertEqual("1492-06-01T10:30:00+00:00", explicit.json()["world_time"])

    def test_campaign_bootstrap_wrong_bearer_is_401_not_backend_failure(self):
        r = self.client.post(
            "/api/campaign",
            headers={"Authorization": "Bearer wrong-key-value-that-is-long-enough"},
            json={"campaign_id": "qa-bootstrap-wrong-key", "name": "QA"},
        )
        self.assertEqual(401, r.status_code)
        self.assertIn("Invalid World Engine operator key", r.text)

    def test_state_roundtrip_via_api(self):
        c = {
            "campaign_id": "api-campaign", "character_id": "hero", "name": "Hero",
            "level": 1, "hp": 10, "max_hp": 10, "ac": 14, "location": "Gate",
            "abilities": {"dex": 2}, "resources": {}, "inventory": []
        }
        r = self.client.post("/api/setup/character", headers=self.headers, json=c)
        self.assertEqual(200, r.status_code, r.text)
        ctx = self.client.get("/api/context", headers=self.headers, params={"campaign_id": "api-campaign", "location": "Gate"})
        self.assertEqual(200, ctx.status_code)
        self.assertEqual("hero", ctx.json()["characters"][0]["id"])


    def test_image_cue_roundtrip_via_api(self):
        loc = {"campaign_id": "api-campaign", "location_id": "Gate", "name": "North Gate", "region": "Frontier", "description": "A storm-beaten stone gate.", "tags": ["fortified"]}
        self.client.post("/api/setup/location", headers=self.headers, json=loc)
        cue = self.client.post("/api/visual/cue", headers=self.headers, json={"campaign_id": "api-campaign", "trigger_type": "new_location", "location_id": "Gate", "scene_key": "api:test:image-cue-roundtrip:gate", "force": True})
        self.assertEqual(200, cue.status_code, cue.text)
        body = cue.json()
        self.assertTrue(body["should_generate"])
        rec = self.client.post("/api/visual/record", headers=self.headers, json={
            "campaign_id": "api-campaign", "trigger_type": "new_location", "scene_key": body["scene_key"], "title": body["title"], "prompt": body["prompt"], "aspect_ratio": body["aspect_ratio"], "location_id": "Gate"
        })
        self.assertEqual(200, rec.status_code, rec.text)
        again = self.client.post("/api/visual/cue", headers=self.headers, json={"campaign_id": "api-campaign", "trigger_type": "new_location", "location_id": "Gate", "scene_key": "api:test:image-cue-roundtrip:gate"})
        self.assertFalse(again.json()["should_generate"])


    def test_visual_profile_and_internal_state_api(self):
        profile = self.client.post("/api/visual/profile", headers=self.headers, json={
            "campaign_id": "api-campaign", "entity_kind": "character", "entity_id": "hero",
            "profile": {"hair": "black", "coat": "weathered green"}
        })
        self.assertEqual(200, profile.status_code, profile.text)
        fetched = self.client.get("/api/visual/profile/character/hero", headers=self.headers, params={"campaign_id": "api-campaign"})
        self.assertEqual("weathered green", fetched.json()["profile"]["coat"])
        internal = self.client.get("/api/internal/state", headers=self.headers, params={"campaign_id": "api-campaign", "location": "Gate"})
        self.assertEqual(404, internal.status_code, internal.text)

    def test_spatial_and_curve_configuration_via_single_action(self):
        cid="api-spatial"
        for loc in (
            {"campaign_id":cid,"location_id":"a","name":"A","region":"r","x":0,"y":0},
            {"campaign_id":cid,"location_id":"b","name":"B","region":"r","x":1,"y":0},
        ):
            r=self.client.post("/api/setup/location",headers=self.headers,json=loc)
            self.assertEqual(200,r.status_code,r.text)
        link=self.client.post("/api/sim/configure",headers=self.headers,json={"campaign_id":cid,"kind":"link","from_id":"a","to_id":"b","travel_hours":2})
        self.assertEqual(200,link.status_code,link.text)
        npc=self.client.post("/api/setup/npc",headers=self.headers,json={"campaign_id":cid,"npc_id":"n","name":"N","hp":5,"max_hp":5,"ac":10,"location":"a"})
        self.assertEqual(200,npc.status_code,npc.text)
        need=self.client.post("/api/sim/configure",headers=self.headers,json={"campaign_id":cid,"kind":"need","npc_id":"n","need":"hunger","value":95,"curve":"threshold"})
        self.assertEqual(200,need.status_code,need.text)
        ctx=self.client.get("/api/context",headers=self.headers,params={"campaign_id":cid,"location":"a","destination":"b"})
        self.assertEqual(["a","b"],ctx.json()["world_graph"]["route_to_destination"]["path"])

    def test_scene_director_and_lifecycle_configuration_share_existing_action(self):
        cid = "api-v34"
        self.client.post("/api/setup/location", headers=self.headers, json={"campaign_id":cid,"location_id":"city","name":"City","region":"north","realm_id":"realm"})
        self.client.post("/api/setup/npc", headers=self.headers, json={"campaign_id":cid,"npc_id":"mayor","name":"Mayor","hp":5,"max_hp":5,"ac":10,"location":"city"})
        scene = self.client.post("/api/sim/configure", headers=self.headers, json={"campaign_id":cid,"kind":"scene","scene_id":"scene1","location_id":"city","scene_type":"social"})
        self.assertEqual(200, scene.status_code, scene.text)
        director = self.client.post("/api/sim/configure", headers=self.headers, json={"campaign_id":cid,"kind":"director","object_id":"mayor_director","name":"Mayor Office","director_kind":"civic","scope_type":"location","scope_id":"city","source_kind":"npc","source_id":"mayor","weights":{"crime":0.7},"policies":{"curfew":"dusk"}})
        self.assertEqual(200, director.status_code, director.text)
        lifecycle = self.client.post("/api/sim/configure", headers=self.headers, json={"campaign_id":cid,"kind":"lifecycle","npc_id":"mayor","birth_year":1450,"mortality":{"enabled":False},"fertility":{"enabled":False}})
        self.assertEqual(200, lifecycle.status_code, lifecycle.text)
        ctx = self.client.get("/api/context", headers=self.headers, params={"campaign_id":cid,"location":"city","entity_limit":10}).json()
        self.assertEqual("scene1", ctx["active_scene"]["id"])
        self.assertEqual("mayor_director", ctx["directors"]["stack"][0]["id"])

    def test_configure_simulation_action_and_advance(self):
        self.client.post("/api/setup/faction", headers=self.headers, json={
            "campaign_id": "api-sim", "faction_id": "guild", "name": "Guild", "reputation": 5
        })
        cfg = self.client.post("/api/sim/configure", headers=self.headers, json={
            "campaign_id": "api-sim", "kind": "rule", "object_id": "rep_fade",
            "archetype": "drift", "cadence": "day", "target": "factions.reputation",
            "params": {"k": 0.5, "baseline": 0, "cause": "absence"}
        })
        self.assertEqual(200, cfg.status_code, cfg.text)
        advanced = self.client.post("/api/world/advance", headers=self.headers, json={
            "campaign_id": "api-sim", "minutes": 1440
        })
        self.assertEqual(200, advanced.status_code, advanced.text)
        self.assertIn("simulation", advanced.json())

    def test_authoring_action_stage_validate_dry_run_promote(self):
        campaign = "api-author"
        self.client.post("/api/campaign", headers=self.headers, json={"campaign_id": campaign, "name": "Author"})
        self.client.post("/api/setup/location", headers=self.headers, json={"campaign_id": campaign, "location_id": "hamlet", "name": "Hamlet", "region": "Vale", "state": {"pop": 50}})
        payload = {"world_bible": {"tone": "grounded"}, "archetypes": [{"id": "villager", "name": "Villager"}], "npcs": [{"id": "n1", "name": "Nell", "archetype_id": "villager", "location": "hamlet"}]}
        stage = self.client.post("/api/authoring", headers=self.headers, json={"campaign_id": campaign, "action": "stage", "batch_id": "b1", "payload": payload})
        self.assertEqual(200, stage.status_code, stage.text)
        val = self.client.post("/api/authoring", headers=self.headers, json={"campaign_id": campaign, "action": "validate", "batch_id": "b1"})
        self.assertTrue(val.json()["valid"], val.text)
        dry = self.client.post("/api/authoring", headers=self.headers, json={"campaign_id": campaign, "action": "dry_run", "batch_id": "b1", "days": 1})
        self.assertTrue(dry.json()["passed"], dry.text)
        promote = self.client.post("/api/authoring", headers=self.headers, json={"campaign_id": campaign, "action": "promote", "batch_id": "b1"})
        self.assertEqual("promoted", promote.json()["status"])
        entity = self.client.get("/api/entity/npc/n1", headers=self.headers, params={"campaign_id": campaign})
        self.assertEqual("Nell", entity.json()["name"])

    def test_rules_kernel_endpoint_roundtrip(self):
        campaign = "api-rules"
        self.client.post("/api/campaign", headers=self.headers, json={"campaign_id":campaign,"name":"Rules"})
        self.client.post("/api/setup/location", headers=self.headers, json={"campaign_id":campaign,"location_id":"lab","name":"Lab"})
        self.client.post("/api/setup/character", headers=self.headers, json={"campaign_id":campaign,"character_id":"mage","name":"Mage","level":3,"hp":12,"max_hp":12,"ac":12,"location":"lab","abilities":{"int":3},"proficiency_bonus":2})
        configured=self.client.post("/api/rules/admin",headers=self.headers,json={"campaign_id":campaign,"operation":"configure","payload":{"rules_version":"2024"}})
        self.assertEqual(200,configured.status_code,configured.text)
        defined=self.client.post("/api/rules/admin",headers=self.headers,json={"campaign_id":campaign,"operation":"define_activity","payload":{"activity_id":"ward","name":"Ward","activity_type":"utility","targeting":{"mode":"self"},"effects":[{"name":"Ward","modifiers":{"ac_bonus":2},"duration":{"unit":"hour","value":1}}]}})
        self.assertEqual(200,defined.status_code,defined.text)
        resolved=self.client.post("/api/rules",headers=self.headers,json={"campaign_id":campaign,"operation":"resolve_activity","payload":{"activity_id":"ward","actor_kind":"character","actor_id":"mage"}})
        self.assertEqual(200,resolved.status_code,resolved.text)
        self.assertEqual("Ward",resolved.json()["results"][0]["effects"][0]["name"])

    def test_public_rules_routes_reject_authoring_before_mutation(self):
        campaign = "api-public-rules-closed"
        self.api.engine.ensure_campaign(campaign, "Closed Rules")
        revision_before = self.api.engine.get_campaign(campaign)["revision"]
        for operation, payload in (
            ("configure", {"rules_version": "2024"}),
            ("define_activity", {"activity_id": "forbidden", "name": "Forbidden", "activity_type": "utility"}),
            ("define_reaction", {"reaction_id": "forbidden", "name": "Forbidden", "trigger": "after_hit"}),
        ):
            direct = self.client.post(
                "/api/rules",
                headers=self.headers,
                json={"campaign_id": campaign, "operation": operation, "payload": payload},
            )
            self.assertEqual(422, direct.status_code, direct.text)
            turn = self.client.post(
                "/api/turn",
                headers=self.headers,
                json={
                    "campaign_id": campaign,
                    "actor_kind": "character",
                    "actor_id": "hero",
                    "intents": [{
                        "capability": "rules.generic",
                        "parameters": {"operation": operation, "payload": payload},
                    }],
                },
            )
            self.assertEqual(403, turn.status_code, turn.text)
            self.assertEqual("PUBLIC_RULES_OPERATION_NOT_ALLOWED", turn.json()["detail"])
        self.assertEqual(revision_before, self.api.engine.get_campaign(campaign)["revision"])
        with self.api.engine._db() as db:
            self.assertEqual(0, db.execute("SELECT COUNT(*) FROM rule_activities WHERE campaign_id=?", (campaign,)).fetchone()[0])
            self.assertEqual(0, db.execute("SELECT COUNT(*) FROM rule_reactions WHERE campaign_id=?", (campaign,)).fetchone()[0])



if __name__ == "__main__":
    unittest.main()

# v3.9.2 orchestration tests are kept in this file so they exercise the same TestClient/API-key fixture.
def _v392_test_auto_directives_and_receipts(self):
    cid = "api-v392-auto"
    self.client.post("/api/setup/location", headers=self.headers, json={"campaign_id":cid,"location_id":"a","name":"A","region":"r","description":"A stone square."})
    self.client.post("/api/setup/location", headers=self.headers, json={"campaign_id":cid,"location_id":"b","name":"B","region":"r","description":"A cliffside observatory."})
    self.client.post("/api/setup/character", headers=self.headers, json={"campaign_id":cid,"character_id":"hero","name":"Hero","hp":20,"max_hp":20,"ac":15,"location":"a"})
    moved = self.client.post("/api/gameplay/move", headers=self.headers, json={"campaign_id":cid,"kind":"character","actor_id":"hero","location":"b","reason":"travel"})
    self.assertEqual(200, moved.status_code, moved.text)
    body = moved.json()
    self.assertTrue(body["_turn_directives"]["image"]["required"])
    self.assertEqual("Medium", body["_turn_directives"]["reasoning"]["recommended_reasoning_level"])
    self.assertEqual("moveActor", body["_engine_receipt"]["operation"])

    scene = self.client.post("/api/sim/configure", headers=self.headers, json={"campaign_id":cid,"kind":"scene","scene_id":"s1","location_id":"b","scene_type":"exploration"})
    self.assertEqual(200, scene.status_code, scene.text)
    self.assertTrue(scene.json()["_turn_directives"]["image"]["required"])

    self.client.post("/api/setup/npc", headers=self.headers, json={"campaign_id":cid,"npc_id":"foe","name":"Foe","hp":8,"max_hp":8,"ac":12,"location":"b"})
    combat = self.client.post("/api/combat/start", headers=self.headers, json={"campaign_id":cid,"combat_id":"c1","location":"b","participants":[{"kind":"character","id":"hero"},{"kind":"npc","id":"foe"}],"scene_id":"s1"})
    self.assertEqual(200, combat.status_code, combat.text)
    self.assertTrue(combat.json()["_turn_directives"]["image"]["required"])
    self.assertEqual("Medium", combat.json()["_turn_directives"]["reasoning"]["recommended_reasoning_level"])


def _v392_test_decision_after_image_api(self):
    cid = "api-v392-choice"
    self.client.post("/api/setup/location", headers=self.headers, json={"campaign_id":cid,"location_id":"gate","name":"Gate","region":"r","description":"A sealed gate."})
    cue = self.client.post("/api/visual/cue", headers=self.headers, json={
        "campaign_id":cid,"trigger_type":"event_choice","location_id":"gate","scene_key":"decision:rev1",
        "summary":"The gate has opened and mist pours into the hall.","choice_options":["Opened the gate"],"decision_phase":"after"
    })
    self.assertEqual(200, cue.status_code, cue.text)
    body = cue.json()
    self.assertTrue(body["_turn_directives"]["image"]["required"])
    self.assertEqual("after", body["visual_context"]["decision_phase"])
    self.assertEqual("High", body["_turn_directives"]["reasoning"]["recommended_reasoning_level"])


ApiTests.test_v392_auto_directives_and_receipts = _v392_test_auto_directives_and_receipts
ApiTests.test_v392_decision_after_image_api = _v392_test_decision_after_image_api

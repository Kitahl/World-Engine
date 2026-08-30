from pathlib import Path
import tempfile
import unittest

from world_engine import WorldEngine


class WorldActivitySimulationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "sim.sqlite3"
        self.e = WorldEngine(self.path)
        self.e.ensure_campaign("sim", "Simulation")

    def tearDown(self):
        self.tmp.cleanup()

    def test_fractional_drift_accumulates_instead_of_permanent_integer_noop(self):
        self.e.upsert_faction("sim", "guild", "Guild", reputation=1)
        self.e.save_simulation_rule(
            "sim", "rep_decay", "drift", cadence="day", target="factions.reputation",
            params={"k": 0.1, "baseline": 0, "cause": "reputation fades without contact"},
        )
        # One-day rounding would stay at 1 forever without a hidden floating accumulator.
        for _ in range(7):
            self.e.advance_world("sim", 1440)
        self.assertEqual(0, self.e.get_faction("sim", "guild")["reputation"])

    def test_stock_catchup_is_exact_over_one_year(self):
        self.e.save_resource_node("sim", "berries", "forest", "berry", qty=1, qty_max=1000, regen_per_day=2)
        self.e.save_simulation_rule("sim", "berry_growth", "stock", target="resource_nodes.qty", params={"item_id": "berry"})
        self.e.advance_world("sim", 365 * 1440)
        node = self.e.save_resource_node  # ensure method still exists after advance
        from world_engine.simulation import SimulationKernel
        result = SimulationKernel(self.e).get_resource_node("sim", "berries")
        self.assertAlmostEqual(731.0, result["qty"], places=7)

    def test_chance_is_reproducible_from_seed_and_configuration(self):
        def run(db_path: Path):
            e = WorldEngine(db_path)
            e.ensure_campaign("same", "Same")
            e.set_simulation_seed("same", 123456)
            e.save_simulation_rule(
                "same", "storm_roll", "chance", cadence="day",
                params={"p": 0.2, "event_type": "storm", "summary": "A storm formed."},
            )
            e.advance_world("same", 100 * 1440)
            return [x["payload"]["trial_index"] for x in reversed(e.recent_events("same", 100)) if x["event_type"] == "storm"]

        p2 = Path(self.tmp.name) / "sim2.sqlite3"
        self.assertEqual(run(self.path), run(p2))


    def test_chance_is_invariant_to_time_chunking(self):
        def setup(db_path: Path):
            e = WorldEngine(db_path)
            e.ensure_campaign("chunk", "Chunk")
            e.set_simulation_seed("chunk", 987654321)
            e.save_simulation_rule(
                "chunk", "storm_roll", "chance", cadence="day",
                params={"p": 0.2, "event_type": "storm", "summary": "A storm formed."},
            )
            return e

        whole = setup(Path(self.tmp.name) / "whole.sqlite3")
        chunked = setup(Path(self.tmp.name) / "chunked.sqlite3")
        whole.advance_world("chunk", 100 * 1440)
        for _ in range(100):
            chunked.advance_world("chunk", 1440)

        def storms(e):
            with e._db() as db:
                rows = db.execute("SELECT world_time,payload_json FROM events WHERE campaign_id='chunk' AND event_type='storm' ORDER BY world_time,id").fetchall()
            return [(r["world_time"], e._loads(r["payload_json"])["trial_index"]) for r in rows]

        self.assertEqual(storms(whole), storms(chunked))
        self.assertEqual(whole.get_campaign("chunk")["world_time"], chunked.get_campaign("chunk")["world_time"])

    def test_chance_stream_is_independent_of_unrelated_random_rule(self):
        def run(db_path: Path, with_extra: bool):
            e = WorldEngine(db_path)
            e.ensure_campaign("iso", "Isolation")
            e.set_simulation_seed("iso", 777)
            e.save_simulation_rule("iso", "storm", "chance", cadence="day", params={"p": 0.25, "event_type": "storm", "summary": "storm"})
            if with_extra:
                e.save_simulation_rule("iso", "omens", "chance", cadence="day", params={"p": 0.9, "event_type": "omen", "summary": "omen"}, priority=5)
            e.advance_world("iso", 60 * 1440)
            return [(x["world_time"], x["payload"]["trial_index"]) for x in reversed(e.recent_events("iso", 100)) if x["event_type"] == "storm"]

        self.assertEqual(run(Path(self.tmp.name) / "iso1.sqlite3", False), run(Path(self.tmp.name) / "iso2.sqlite3", True))

    def test_decide_changes_npc_plan_when_resource_scarcity_changes(self):
        self.e.upsert_npc("sim", "baker", "Baker", hp=5, max_hp=5, ac=10, location="bakery")
        self.e.save_resource_node("sim", "flour_bin", "bakery", "flour", qty=0, qty_max=10, regen_per_day=0)
        self.e.save_npc_action(
            "sim", "baker", "buy_flour", location="market",
            considerations=[{"type": "resource", "item_id": "flour", "location_id": "bakery", "invert": True, "weight": 1.0}],
        )
        self.e.save_npc_action(
            "sim", "baker", "bake_bread", location="bakery",
            considerations=[{"type": "resource", "item_id": "flour", "location_id": "bakery", "weight": 1.0}],
        )
        self.e.save_simulation_rule("sim", "daily_decisions", "decide", cadence="day")
        self.e.advance_world("sim", 1440)
        self.assertEqual("market", self.e.get_npc("sim", "baker")["location"])

        # Refill the same stock; next decision should reverse naturally.
        self.e.save_resource_node("sim", "flour_bin", "bakery", "flour", qty=10, qty_max=10, regen_per_day=0)
        self.e.advance_world("sim", 1440)
        self.assertEqual("bakery", self.e.get_npc("sim", "baker")["location"])

    def test_cascade_and_relationship_reason_are_persisted(self):
        self.e.upsert_npc("sim", "elira", "Elira", hp=5, max_hp=5, ac=10)
        self.e.save_simulation_rule(
            "sim", "death_event", "chance", cadence="day",
            params={"p": 1.0, "event_type": "friend_died", "summary": "Elira's brother died at the ford."},
        )
        self.e.save_simulation_reaction(
            "sim", "grief_reaction", "friend_died",
            [{"type": "emit", "event_type": "grief", "summary": "Elira grieves."}], priority=10,
        )
        self.e.save_simulation_reaction(
            "sim", "trust_reaction", "grief",
            [{"type": "relationship", "source_id": "elira", "target_id": "hero", "trust_delta": -7,
              "cause": "Hero failed to save Elira's brother at the ford."}], priority=20,
        )
        out = self.e.advance_world("sim", 1440)
        self.assertGreaterEqual(out["simulation"]["cascade"], 2)
        self.assertEqual(-7, self.e.get_relationship("sim", "elira", "hero")["trust"])
        reasons = self.e.get_relationship_events("sim", "elira", "hero")
        self.assertEqual("Hero failed to save Elira's brother at the ford.", reasons[0]["cause"])
        self.assertEqual("cascade", reasons[0]["event_type"])

    def test_direct_relationship_change_stores_reason(self):
        self.e.adjust_relationship("sim", "elira", "hero", trust_delta=5, reason="Hero kept a dangerous promise.")
        reason = self.e.get_relationship_events("sim", "elira", "hero")[0]
        self.assertEqual(5, reason["trust_delta"])
        self.assertEqual("Hero kept a dangerous promise.", reason["cause"])
        self.assertEqual("direct", reason["event_type"])


    def test_world_context_surfaces_relationship_causes_without_extra_action(self):
        self.e.upsert_character("sim", "hero", "Hero", hp=10, max_hp=10, ac=14, location="ford")
        self.e.upsert_npc("sim", "elira", "Elira", hp=5, max_hp=5, ac=10, location="ford")
        self.e.adjust_relationship("sim", "elira", "hero", trust_delta=-8, reason="Hero abandoned Elira's brother at the ford.")
        ctx = self.e.get_world_context("sim", "ford")
        self.assertTrue(ctx["recent_social_history"])
        self.assertEqual("Hero abandoned Elira's brother at the ford.", ctx["recent_social_history"][0]["cause"])

    def test_relationship_drift_stores_reason(self):
        self.e.adjust_relationship("sim", "elira", "hero", trust_delta=10, reason="initial trust")
        self.e.save_simulation_rule(
            "sim", "trust_fade", "drift", cadence="day", target="relationships.trust",
            params={"k": 0.2, "baseline": 0, "cause": "trust fades during long separation"},
        )
        self.e.advance_world("sim", 5 * 1440)
        reasons = self.e.get_relationship_events("sim", "elira", "hero")
        self.assertTrue(any(r["event_type"] == "drift" and r["cause"] == "trust fades during long separation" for r in reasons))

    def test_fixed_schedule_jumps_to_final_posting(self):
        self.e.upsert_npc("sim", "guard", "Guard", hp=5, max_hp=5, ac=12, location="gate", routine={"08:00": "gate", "18:00": "tavern"})
        self.e.save_simulation_rule("sim", "routine", "schedule", cadence="hour")
        self.e.advance_world("sim", 11 * 60)  # 08:00 -> 19:00
        self.assertEqual("tavern", self.e.get_npc("sim", "guard")["location"])

    def test_simulation_schema_forward_migrates_existing_database(self):
        # Fresh engines created before v3.2 simply lack the new tables. Reopen after dropping them simulates that shape.
        with self.e._write_db() as db:
            for table in ("relationship_events", "sim_reactions", "sim_agent_state", "npc_actions", "npc_needs", "resource_nodes", "sim_accumulators", "sim_rules", "sim_config"):
                db.execute(f"DROP TABLE IF EXISTS {table}")
            db.execute("PRAGMA user_version = 4")
        reopened = WorldEngine(self.path)
        reopened.save_simulation_rule("sim", "r", "chance", params={"p": 0})
        self.assertEqual("chance", reopened.list_simulation_rules("sim")[0]["archetype"])

    def test_boundary_aware_catchup_applies_event_before_later_drift(self):
        self.e.upsert_npc("sim", "elira", "Elira", hp=5, max_hp=5, ac=10)
        self.e.save_npc_action("sim", "elira", "remember", base_utility=1.0)
        self.e.save_simulation_rule("sim", "decision", "decide", cadence="day", priority=10)
        self.e.save_simulation_reaction(
            "sim", "decision_trust", "sim_decision",
            [{"type": "relationship", "source_id": "elira", "target_id": "hero", "trust_delta": 100, "cause": "a decisive shared event"}],
            priority=10,
        )
        self.e.save_simulation_rule(
            "sim", "trust_decay", "drift", cadence="day", target="relationships.trust",
            params={"k": 0.1, "baseline": 0, "cause": "time apart"}, priority=20,
        )
        self.e.advance_world("sim", 10 * 1440)
        trust = self.e.get_relationship("sim", "elira", "hero")["trust"]
        # Decision occurs at the first daily boundary, then the remaining nine
        # daily drift steps act on it. A non-boundary-aware bulk update would leave 100.
        self.assertGreaterEqual(trust, 35)
        self.assertLessEqual(trust, 45)

    def test_campaign_year_1492_boundary_math_does_not_use_platform_timestamps(self):
        from datetime import datetime, timezone
        from world_engine.simulation import SimulationKernel
        start = datetime(1492, 1, 1, 8, 0, tzinfo=timezone.utc)
        end = datetime(1492, 1, 2, 8, 0, tzinfo=timezone.utc)
        self.assertEqual(1, SimulationKernel._boundaries(start, end, "day"))
        boundary = SimulationKernel._first_boundary(start, "day")
        self.assertEqual(1492, boundary.year)

    def test_one_shot_catchup_matches_daily_chunked_final_state(self):
        def setup(path: Path):
            e = WorldEngine(path)
            e.ensure_campaign("eq", "Equivalence")
            e.set_simulation_seed("eq", 2468)
            e.upsert_npc("eq", "baker", "Baker", hp=5, max_hp=5, ac=10, location="bakery")
            e.save_resource_node("eq", "flour", "bakery", "flour", qty=3, qty_max=20, regen_per_day=1)
            e.save_npc_need("eq", "baker", "hunger", 30, baseline=80, drift_per_day=0.05)
            e.save_npc_action("eq", "baker", "eat", location="bakery", base_utility=0.0, considerations=[{"type":"need","key":"hunger","weight":1.0}], effects=[{"type":"need","need":"hunger","delta":-25}])
            e.save_npc_action("eq", "baker", "work", location="market", base_utility=0.6, considerations=[{"type":"need","key":"hunger","weight":-0.2}])
            e.adjust_relationship("eq", "baker", "hero", trust_delta=50, reason="initial")
            e.save_simulation_rule("eq", "trust_fade", "drift", cadence="day", target="relationships.trust", params={"k":0.05,"baseline":0,"cause":"time"}, priority=30)
            e.save_simulation_rule("eq", "stock", "stock", cadence="day", target="resource_nodes.qty", params={"item_id":"flour"}, priority=10)
            e.save_simulation_rule("eq", "decide", "decide", cadence="day", priority=20)
            e.save_simulation_rule("eq", "chance", "chance", cadence="day", params={"p":0.15,"event_type":"market_news","summary":"news"}, priority=40)
            return e

        whole = setup(Path(self.tmp.name) / "eq_whole.sqlite3")
        daily = setup(Path(self.tmp.name) / "eq_daily.sqlite3")
        whole.advance_world("eq", 30 * 1440)
        for _ in range(30):
            daily.advance_world("eq", 1440)

        from world_engine.simulation import SimulationKernel
        sw, sd = SimulationKernel(whole), SimulationKernel(daily)
        self.assertEqual(whole.get_npc("eq", "baker")["location"], daily.get_npc("eq", "baker")["location"])
        self.assertEqual(whole.get_relationship("eq", "baker", "hero")["trust"], daily.get_relationship("eq", "baker", "hero")["trust"])
        self.assertAlmostEqual(sw.get_resource_node("eq", "flour")["qty"], sd.get_resource_node("eq", "flour")["qty"], places=9)
        self.assertAlmostEqual(sw.get_need("eq", "baker", "hunger")["value"], sd.get_need("eq", "baker", "hunger")["value"], places=9)

    def test_schedule_wraps_to_previous_day_last_posting_before_first_slot(self):
        self.e.upsert_npc("sim", "guard2", "Guard 2", hp=5, max_hp=5, ac=12, location="gate", routine={"08:00": "gate", "18:00": "tavern"})
        self.e.save_simulation_rule("sim", "routine2", "schedule", cadence="hour")
        # Advance to 02:00 the following day from the default 08:00.
        self.e.advance_world("sim", 18 * 60)
        self.assertEqual("tavern", self.e.get_npc("sim", "guard2")["location"])


    def test_per_need_curve_threshold_overrides_global_quadratic_failure(self):
        from world_engine.simulation import SimulationKernel
        self.e.upsert_npc("sim", "guard_curve", "Guard", hp=10, max_hp=10, ac=14, location="gate")
        self.e.save_npc_need("sim", "guard_curve", "hunger", 95, baseline=95, drift_per_day=0, curve="threshold")
        self.e.save_npc_need("sim", "guard_curve", "duty", 80, baseline=80, drift_per_day=0, curve="urgent")
        self.e.save_npc_action("sim", "guard_curve", "eat", base_utility=0, considerations=[{"type":"need","key":"hunger","weight":0.7}])
        self.e.save_npc_action("sim", "guard_curve", "patrol", base_utility=0, considerations=[{"type":"need","key":"duty","weight":0.43}])
        kernel=SimulationKernel(self.e)
        with self.e._write_db() as db:
            npc=db.execute("SELECT * FROM npcs WHERE campaign_id='sim' AND id='guard_curve'").fetchone()
            ranked=kernel._score_actions(db,"sim",npc,None,0)
        self.assertEqual("eat", ranked[0][1])
        self.assertGreater(ranked[0][0], ranked[1][0])

    def test_softmax_top_k_is_seeded_and_not_argmax_only(self):
        from datetime import datetime, timezone
        from world_engine.simulation import SimulationKernel
        def pick(path, seed):
            e=WorldEngine(path); e.ensure_campaign("d","D"); e.set_simulation_seed("d",seed)
            e.upsert_npc("d","n","N",hp=5,max_hp=5,ac=10,location="x")
            for aid,score in (("a",1.00),("b",0.99),("c",0.98)):
                e.save_npc_action("d","n",aid,base_utility=score,cost_hours=0)
            e.save_simulation_rule("d","dec","decide",cadence="day",params={"top_k":3,"temperature":1.0})
            k=SimulationKernel(e)
            with e._write_db() as db:
                npc=db.execute("SELECT * FROM npcs WHERE campaign_id='d' AND id='n'").fetchone()
                ranked=k._score_actions(db,"d",npc,None,0)
                return k._select_action(db,"d","dec","n",datetime(1492,1,2,tzinfo=timezone.utc),ranked,top_k=3,temperature=1.0)[1]
        a=pick(Path(self.tmp.name)/"soft1.sqlite3",11)
        b=pick(Path(self.tmp.name)/"soft2.sqlite3",11)
        self.assertEqual(a,b)
        # Verify that at least one seed can select a non-max action, proving this is not argmax.
        picks={pick(Path(self.tmp.name)/f"soft{s}.sqlite3",s) for s in range(1,20)}
        self.assertTrue(any(x != "a" for x in picks))

    def test_action_feasibility_inventory_gate(self):
        from world_engine.simulation import SimulationKernel
        self.e.upsert_npc("sim","baker2","Baker 2",hp=5,max_hp=5,ac=10,location="bakery")
        self.e.save_item_def("sim","flour","Flour")
        self.e.set_inventory_item("sim","npc","baker2","flour",0)
        self.e.save_npc_action("sim","baker2","bake",base_utility=10,requirements={"item":{"flour":1}})
        self.e.save_npc_action("sim","baker2","market",base_utility=1)
        k=SimulationKernel(self.e)
        with self.e._write_db() as db:
            npc=db.execute("SELECT * FROM npcs WHERE campaign_id='sim' AND id='baker2'").fetchone()
            ranked=k._score_actions(db,"sim",npc,None,0)
        self.assertEqual(["market"],[x[1] for x in ranked])

    def test_action_commitment_prevents_hourly_thrashing(self):
        self.e.upsert_npc("sim","worker","Worker",hp=5,max_hp=5,ac=10,location="shop")
        self.e.save_npc_action("sim","worker","work",location="shop",base_utility=2,cost_hours=8)
        self.e.save_npc_action("sim","worker","tavern",location="tavern",base_utility=1,cost_hours=1)
        self.e.save_simulation_rule("sim","hourly_decide","decide",cadence="hour",params={"top_k":1})
        self.e.advance_world("sim",60)
        with self.e._db() as db:
            first=db.execute("SELECT last_action,committed_until FROM sim_agent_state WHERE campaign_id='sim' AND npc_id='worker'").fetchone()
        self.assertEqual("work",first["last_action"])
        # Make tavern overwhelmingly attractive, but commitment still holds for next hour.
        self.e.save_npc_action("sim","worker","tavern",location="tavern",base_utility=100,cost_hours=1)
        self.e.advance_world("sim",60)
        with self.e._db() as db:
            second=db.execute("SELECT last_action FROM sim_agent_state WHERE campaign_id='sim' AND npc_id='worker'").fetchone()
        self.assertEqual("work",second["last_action"])
        self.assertEqual("shop",self.e.get_npc("sim","worker")["location"])

    def test_cascade_same_location_excludes_dead_target(self):
        from collections import deque
        from world_engine.simulation import SimulationKernel
        self.e.upsert_npc("sim","dead","Dead",hp=0,max_hp=5,ac=10,location="ford")
        self.e.upsert_npc("sim","alive","Alive",hp=5,max_hp=5,ac=10,location="ford")
        self.e.save_npc_need("sim","dead","fear",0)
        self.e.save_npc_need("sim","alive","fear",0)
        self.e.save_simulation_reaction("sim","fear_here","tantrum",[{"type":"need","npc_id":"$actor","need":"fear","delta":10}],selector={"same_location":"$region"})
        k=SimulationKernel(self.e)
        with self.e._write_db() as db:
            rev=self.e._next_revision(db,"sim")
            q=deque([{"event_type":"tantrum","summary":"rage","payload":{},"region":"ford","actor_id":"alive","target_id":"dead","world_time":"1492-01-02T00:00:00+00:00","depth":0}])
            k._drain_reactions(db,"sim",rev,q)
        self.assertEqual(0, k.get_need("sim", "dead", "fear")["value"])
        self.assertEqual(10, k.get_need("sim", "alive", "fear")["value"])

    def test_cascade_repeat_policy_can_allow_multiple_damage_like_effects(self):
        from collections import deque
        from world_engine.simulation import SimulationKernel
        self.e.upsert_npc("sim","x","X",hp=5,max_hp=5,ac=10,location="room")
        self.e.save_npc_need("sim","x","stress",0)
        self.e.save_simulation_reaction("sim","repeat","spark",[{"type":"need","npc_id":"$actor","need":"stress","delta":5}],selector={"who":"x"},repeat_policy="count_limited",repeat_limit=2)
        k=SimulationKernel(self.e)
        with self.e._write_db() as db:
            rev=self.e._next_revision(db,"sim")
            q=deque([
                {"event_type":"spark","summary":"1","payload":{},"region":"room","actor_id":"x","target_id":"door","world_time":"1492-01-02T00:00:00+00:00","depth":0},
                {"event_type":"spark","summary":"2","payload":{},"region":"room","actor_id":"x","target_id":"door","world_time":"1492-01-02T00:00:01+00:00","depth":0},
                {"event_type":"spark","summary":"3","payload":{},"region":"room","actor_id":"x","target_id":"door","world_time":"1492-01-02T00:00:02+00:00","depth":0},
            ])
            k._drain_reactions(db,"sim",rev,q)
            val=db.execute("SELECT value FROM npc_needs WHERE campaign_id='sim' AND npc_id='x' AND need='stress'").fetchone()["value"]
        self.assertEqual(10,val)

    def test_road_spread_uses_world_graph(self):
        self.e.upsert_location("sim","a","A",x=0,y=0)
        self.e.upsert_location("sim","b","B",x=1,y=0)
        self.e.save_location_link("sim","a","b",0,bidirectional=True)
        self.e.upsert_npc("sim","src","Source",hp=5,max_hp=5,ac=10,location="a")
        self.e.upsert_npc("sim","dst","Dest",hp=5,max_hp=5,ac=10,location="b")
        self.e.set_world_state("sim","npc","src","rumor",True)
        self.e.save_simulation_rule("sim","rumor_road","spread",cadence="day",params={"state_key":"rumor","mode":"road","p_road":1.0,"road_decay_hours":24})
        self.e.advance_world("sim",1440)
        vals=self.e.get_world_state("sim","npc","dst")
        self.assertTrue(any(v["key"]=="rumor" and v["value"] is True for v in vals))

    def test_drama_manager_weights_threats_against_player_hardship(self):
        from datetime import datetime, timezone
        from world_engine.simulation import SimulationKernel
        self.e.upsert_character("sim","hero","Hero",hp=2,max_hp=10,ac=12)
        self.e.set_drama_config("sim",enabled=True,low_hp_threshold=0.35,hardship_suppression=0.2,relief_boost=2.0)
        k=SimulationKernel(self.e)
        with self.e._write_db() as db:
            threat=k._drama_multiplier(db,"sim","threat",datetime(1492,1,2,tzinfo=timezone.utc))
            relief=k._drama_multiplier(db,"sim","relief",datetime(1492,1,2,tzinfo=timezone.utc))
        self.assertEqual(0.2,threat)
        self.assertEqual(2.0,relief)

    def test_thin_lifecycle_can_resolve_seeded_age_death(self):
        self.e.upsert_npc("sim","elder","Elder",hp=5,max_hp=5,ac=10,location="village")
        self.e.save_npc_lifecycle("sim","elder",birth_year=1400,mortality={"enabled":True,"makeham":1e9,"gompertz_b":0,"gompertz_c":0})
        self.e.advance_world("sim",1440)
        self.assertEqual(0,self.e.get_npc("sim","elder")["hp"])
        self.assertFalse(self.e.get_npc_lifecycle("sim","elder")["alive"])
        self.assertTrue(any(e["event_type"]=="death" for e in self.e.recent_events("sim",20)))


    def test_thin_market_price_tracks_stock_scarcity(self):
        self.e.upsert_location("sim", "market", "Market")
        self.e.save_item_def("sim", "grain", "Grain", base_price=10)
        self.e.save_resource_node("sim", "grain_stock", "market", "grain", qty=10, qty_max=10, regen_per_day=0)
        full = self.e.get_market_prices("sim", "market")[0]
        self.assertEqual(10.0, full["price"])
        self.e.save_resource_node("sim", "grain_stock", "market", "grain", qty=0, qty_max=10, regen_per_day=0)
        scarce = self.e.get_market_prices("sim", "market")[0]
        self.assertEqual(20.0, scarce["price"])
        self.assertEqual(1.0, scarce["scarcity"])
        ctx = self.e.get_world_context("sim", "market")
        self.assertEqual(20.0, ctx["market_prices"][0]["price"])


    def test_thin_item_inventory_can_track_quality_and_wear_metadata(self):
        self.e.save_item_def("sim", "sword", "Iron Sword", base_price=15, metadata={"material":"iron"})
        self.e.set_inventory_item("sim", "npc", "smith", "sword", 1, metadata={"quality": 3, "wear": 0.25})
        row = self.e.get_inventory_items("sim", "npc", "smith")[0]
        self.assertEqual({"material":"iron"}, row["metadata"])
        self.assertEqual(3, row["inventory_metadata"]["quality"])
        self.assertEqual(0.25, row["inventory_metadata"]["wear"])


if __name__ == "__main__":
    unittest.main()

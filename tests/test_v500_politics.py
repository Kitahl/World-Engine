from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from world_engine import WorldEngine
from world_engine.economy import EconomyKernel
from world_engine.politics import POLITICS_SCHEMA, PoliticsKernel
from world_engine.population import PopulationKernel

UTC = timezone.utc


class PoliticsV500Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.e, self.k = self._new_engine(Path(self.tmp.name) / "world.sqlite3")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _new_engine(self, path: Path) -> tuple[WorldEngine, PoliticsKernel]:
        e = WorldEngine(path)
        e.ensure_campaign("c", "Politics", "1492-01-01T00:00:00+00:00")
        e.upsert_location("c", "a", "A")
        e.upsert_location("c", "b", "B")
        e.upsert_location("c", "c-town", "C Town")
        e.upsert_faction("c", "fa", "Faction A")
        e.upsert_faction("c", "fb", "Faction B")
        e.upsert_faction("c", "fc", "Faction C")
        e.save_item_def("c", "wood", "Wood", base_price=2)
        e.save_item_def("c", "rations", "Rations", base_price=1)
        k = PoliticsKernel(e)
        with e._write_db() as db:
            k.install_schema_db(db)
            k.seed_defaults_db(db, "c")
            now = e._now()
            db.execute(
                """INSERT INTO population_cohorts(
                       campaign_id,id,location_id,species,culture,faction_id,age_band,
                       livelihood,count,birth_rate_annual,death_rate_annual,
                       labor_participation,migration_affinity,health,wealth,
                       transition_rate_annual,state_json,last_processed_world_time,updated_at)
                   VALUES('c','cohort-a','a','human','a','fa','adult','mixed',100,
                          0,0,.5,1,.8,.5,0,'{}','1492-01-01T00:00:00+00:00',?)""",
                (now,),
            )
            db.execute(
                """INSERT INTO settlement_labor(
                       campaign_id,location_id,occupation,demand,supply,filled,
                       productivity,wage_index,state_json,updated_world_time,updated_at)
                   VALUES('c','a','builders',0,20,0,1,1,'{}','1492-01-01T00:00:00+00:00',?)""",
                (now,),
            )
            db.execute(
                """INSERT INTO owner_balances(
                       campaign_id,owner_kind,owner_id,currency_key,amount,updated_at)
                   VALUES('c','faction','fa','gp',100,?)""",
                (now,),
            )
            for item_id, qty in (("wood", 50), ("rations", 100)):
                db.execute(
                    """INSERT INTO inventories(
                           campaign_id,owner_kind,owner_id,item_id,qty,metadata_json,updated_at)
                       VALUES('c','faction','fa',?,?,'{}',?)""",
                    (item_id, qty, now),
                )
        return e, k

    def _dispatch(
        self,
        operation: str,
        *,
        principal: str = "system",
        key: str,
        kernel: PoliticsKernel | None = None,
        **payload,
    ):
        kernel = kernel or self.k
        kind = "system" if principal == "system" else "faction"
        return kernel.dispatch(
            operation,
            "c",
            {
                "principal_kind": kind,
                "principal_id": principal,
                "request_key": key,
                **payload,
            },
        )

    def _route(
        self, e: WorldEngine, route_id: str = "ab", capacity: float = 200
    ) -> None:
        with e._write_db() as db:
            db.execute(
                """INSERT INTO economy_routes(
                       campaign_id,id,from_location_id,to_location_id,travel_hours,
                       capacity_qty_per_day,risk,cost_per_qty,active,state_json,updated_at)
                   VALUES('c',?,'a','b',4,?,0,0,1,'{}',?)""",
                (route_id, capacity, e._now()),
            )

    def _project(self, kernel: PoliticsKernel | None = None, suffix: str = "") -> None:
        kernel = kernel or self.k
        self._dispatch(
            "create_project",
            principal="fa",
            key=f"project-create{suffix}",
            kernel=kernel,
            project_id=f"bridge{suffix}",
            owner_faction_id="fa",
            location_id="a",
            project_kind="bridge",
            name="Bridge",
            work_required=8,
            requirements=[
                {"kind": "inventory", "key": "wood", "amount": 10},
                {
                    "kind": "labor",
                    "key": "builders",
                    "amount": 4,
                    "location_id": "a",
                },
            ],
        )
        self._dispatch(
            "start_project",
            principal="fa",
            key=f"project-start{suffix}",
            kernel=kernel,
            project_id=f"bridge{suffix}",
        )

    def _mobilize(
        self,
        force_id: str = "force-a",
        *,
        manpower: float = 20,
        supply_qty: float = 5,
    ) -> dict:
        return self._dispatch(
            "mobilize",
            principal="fa",
            key=f"mobilize:{force_id}",
            force_id=force_id,
            faction_id="fa",
            name=force_id,
            location_id="a",
            source_cohort_id="cohort-a",
            manpower=manpower,
            currency_key="gp",
            currency_cost=5,
            supply_item_id="rations",
            supply_qty=supply_qty,
        )

    def test_schema_is_additive_stage_22_without_claiming_user_version(self) -> None:
        with self.e._write_db() as db:
            before = int(db.execute("PRAGMA user_version").fetchone()[0])
            db.executescript(POLITICS_SCHEMA)
            self.k.install_schema_db(db)
            after = int(db.execute("PRAGMA user_version").fetchone()[0])
            names = {
                row["name"]
                for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'politics_%'"
                ).fetchall()
            }
            violations = db.execute("PRAGMA foreign_key_check").fetchall()
        self.assertEqual(before, after)
        self.assertFalse(violations)
        self.assertTrue(
            {
                "politics_commitments",
                "politics_projects",
                "politics_claims",
                "politics_grievances",
                "politics_territorial_control",
                "politics_proposals",
                "politics_treaties",
                "politics_treaty_clauses",
                "politics_obligations",
                "politics_treaty_violations",
                "politics_forces",
                "politics_raids",
                "politics_wars",
                "politics_jurisdictions",
                "politics_action_receipts",
                "politics_daily_steps",
            }
            <= names
        )

    def test_schema_install_participates_in_caller_rollback(self) -> None:
        path = Path(self.tmp.name) / "rollback.sqlite3"
        engine = WorldEngine(path)
        engine.ensure_campaign("c", "Rollback", "1492-01-01T00:00:00+00:00")
        kernel = PoliticsKernel(engine)
        with engine._db() as db:
            before = {
                row["name"]
                for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'politics_%'"
                ).fetchall()
            }
        with (
            self.assertRaisesRegex(RuntimeError, "force rollback"),
            engine._write_db() as db,
        ):
            kernel.install_schema_db(db)
            raise RuntimeError("force rollback")
        with engine._db() as db:
            after = {
                row["name"]
                for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'politics_%'"
                ).fetchall()
            }
        self.assertEqual(before, after)

    def test_atomic_project_reservation_rollback_and_actor_idempotency(self) -> None:
        with self.e._db() as db:
            revision = db.execute(
                "SELECT revision FROM campaigns WHERE id='c'"
            ).fetchone()[0]
        bad = {
            "project_id": "bad",
            "owner_faction_id": "fa",
            "location_id": "a",
            "project_kind": "fort",
            "name": "Bad",
            "work_required": 10,
            "requirements": [
                {"kind": "currency", "key": "gp", "amount": 10},
                {"kind": "inventory", "key": "wood", "amount": 999},
            ],
        }
        with self.assertRaisesRegex(ValueError, "insufficient uncommitted inventory"):
            self._dispatch("create_project", principal="fa", key="bad", **bad)
        with self.e._db() as db:
            self.assertEqual(
                revision,
                db.execute("SELECT revision FROM campaigns WHERE id='c'").fetchone()[0],
            )
            self.assertEqual(
                100,
                db.execute(
                    "SELECT amount FROM owner_balances WHERE campaign_id='c' AND owner_id='fa'"
                ).fetchone()[0],
            )
            self.assertEqual(
                0,
                db.execute(
                    "SELECT COUNT(*) FROM politics_projects WHERE id='bad'"
                ).fetchone()[0],
            )
            self.assertEqual(
                0,
                db.execute(
                    "SELECT COUNT(*) FROM politics_commitments WHERE purpose_id='bad'"
                ).fetchone()[0],
            )
        good = {
            **bad,
            "project_id": "good",
            "name": "Good",
            "requirements": [{"kind": "currency", "key": "gp", "amount": 10}],
        }
        first = self._dispatch("create_project", principal="fa", key="good", **good)
        with self.e._db() as db:
            after_first = db.execute(
                "SELECT revision FROM campaigns WHERE id='c'"
            ).fetchone()[0]
        replay = self._dispatch("create_project", principal="fa", key="good", **good)
        self.assertFalse(first["idempotent_replay"])
        self.assertTrue(replay["idempotent_replay"])
        with self.e._db() as db:
            self.assertEqual(
                after_first,
                db.execute("SELECT revision FROM campaigns WHERE id='c'").fetchone()[0],
            )
        with self.assertRaisesRegex(ValueError, "POLITICS_IDEMPOTENCY_CONFLICT"):
            self._dispatch(
                "create_project",
                principal="fa",
                key="good",
                **{**good, "name": "Different"},
            )
        cancelled = self._dispatch(
            "cancel_project", principal="fa", key="cancel-good", project_id="good"
        )
        self.assertEqual("cancelled", cancelled["status"])
        with self.e._db() as db:
            self.assertEqual(
                100,
                db.execute(
                    "SELECT amount FROM owner_balances WHERE campaign_id='c' AND owner_id='fa'"
                ).fetchone()[0],
            )

    def test_strategy_reads_only_faction_beliefs_and_private_projection_is_scoped(
        self,
    ) -> None:
        with self.e._write_db() as db:
            now = self.e._now()
            for faction_id in ("fa", "fb"):
                db.execute(
                    """INSERT OR IGNORE INTO we4_entities(
                           campaign_id,entity_key,entity_type,entity_id,canonical_name,status,
                           source_table,components_json,created_at,updated_at)
                       VALUES('c',?,'faction',?,?,'active','factions','{}',?,?)""",
                    (f"faction:{faction_id}", faction_id, faction_id, now, now),
                )
            for fact_id, predicate in (
                ("known", "border_open"),
                ("secret", "hidden_army"),
                ("unknown", "rumor"),
            ):
                db.execute(
                    """INSERT INTO we4_facts(
                           campaign_id,fact_id,subject_key,predicate,object_type,
                           object_value_json,confidence,status,provenance_json,created_at,updated_at)
                       VALUES('c',?,'faction:fb',?,'literal','true',1,'active','{}',?,?)""",
                    (fact_id, predicate, now, now),
                )
            for fact_id, status in (("known", "believes"), ("unknown", "unknown")):
                db.execute(
                    """INSERT INTO we4_beliefs(
                           campaign_id,believer_key,fact_id,belief_value_json,confidence,
                           acquired_world_time,status,provenance_json,updated_at)
                       VALUES('c','faction:fa',?,'true',.9,'1492-01-01T00:00:00+00:00',?,'{}',?)""",
                    (fact_id, status, now),
                )
        view = self.k.dispatch(
            "strategy_view",
            "c",
            {"actor_kind": "faction", "actor_id": "fa", "faction_id": "fa"},
        )
        self.assertEqual(["known"], [item["fact_id"] for item in view["beliefs"]])
        with (
            self.e._write_db() as db,
            self.assertRaisesRegex(ValueError, "not known"),
        ):
            self.k.add_claim_db(
                db,
                "c",
                "claim-secret",
                claimant_faction_id="fa",
                target_kind="faction",
                target_id="fb",
                claim_type="threat",
                strength=0.5,
                source_fact_id="secret",
                visibility="private",
                revision=1,
                when=datetime(1492, 1, 1, tzinfo=UTC),
            )
        self._dispatch(
            "add_claim",
            principal="fa",
            key="private-claim",
            claim_id="claim-known",
            claimant_faction_id="fa",
            target_kind="faction",
            target_id="fb",
            claim_type="threat",
            strength=0.5,
            source_fact_id="known",
            visibility="private",
        )
        own = self.k.public_snapshot("c", actor_kind="faction", actor_id="fa")
        other = self.k.public_snapshot("c", actor_kind="faction", actor_id="fb")
        self.assertEqual(["claim-known"], [item["id"] for item in own["claims"]])
        self.assertEqual([], other["claims"])
        self.assertEqual([], other["beliefs"])

    def test_proposal_acceptance_and_treaty_violation_create_grievance(self) -> None:
        terms = {
            "name": "Grain Accord",
            "clauses": [
                {"id": "delivery", "type": "delivery", "terms": {"item": "wood"}}
            ],
            "obligations": [
                {
                    "id": "deliver-wood",
                    "clause_id": "delivery",
                    "type": "deliver",
                    "debtor_faction_id": "fa",
                    "beneficiary_faction_id": "fb",
                    "due_world_time": "1492-01-02T00:00:00+00:00",
                    "resource_kind": "inventory",
                    "resource_key": "wood",
                    "amount": 5,
                }
            ],
        }
        self._dispatch(
            "create_proposal",
            principal="fa",
            key="proposal",
            proposal_id="p1",
            proposer_faction_id="fa",
            recipient_faction_id="fb",
            proposal_type="trade",
            terms=terms,
        )
        accepted = self._dispatch(
            "respond_proposal",
            principal="fb",
            key="accept",
            proposal_id="p1",
            responder_faction_id="fb",
            response="accept",
            treaty_id="t1",
        )
        self.assertEqual("accepted", accepted["proposal"]["status"])
        self.assertEqual("t1", accepted["result"]["id"])
        violation = self._dispatch(
            "record_violation",
            key="violate",
            violation_id="v1",
            treaty_id="t1",
            obligation_id="deliver-wood",
            violator_faction_id="fa",
            harmed_faction_id="fb",
            violation_type="non_delivery",
            severity=0.8,
        )
        self.assertEqual("grievance:v1", violation["grievance_id"])
        with self.e._db() as db:
            self.assertEqual(
                "violated",
                db.execute(
                    "SELECT status FROM politics_obligations WHERE id='deliver-wood'"
                ).fetchone()[0],
            )
            grievance = db.execute(
                "SELECT * FROM politics_grievances WHERE id='grievance:v1'"
            ).fetchone()
        self.assertEqual("fb", grievance["aggrieved_faction_id"])
        self.assertEqual("fa", grievance["against_faction_id"])

    def test_proposal_counter_and_reject_are_party_scoped(self) -> None:
        self._dispatch(
            "create_proposal",
            principal="fa",
            key="counter-origin",
            proposal_id="counter-origin",
            proposer_faction_id="fa",
            recipient_faction_id="fb",
            proposal_type="alliance",
            terms={"name": "First terms"},
        )
        counter = self._dispatch(
            "respond_proposal",
            principal="fb",
            key="counter-response",
            proposal_id="counter-origin",
            responder_faction_id="fb",
            response="counter",
            counter_id="counter-child",
            counter_terms={"name": "Second terms"},
        )
        self.assertEqual("countered", counter["proposal"]["status"])
        self.assertEqual("pending", counter["result"]["status"])
        with self.assertRaisesRegex(ValueError, "does not own this action"):
            self._dispatch(
                "respond_proposal",
                principal="fb",
                key="wrong-party",
                proposal_id="counter-child",
                responder_faction_id="fa",
                response="reject",
            )
        rejected = self._dispatch(
            "respond_proposal",
            principal="fa",
            key="reject-counter",
            proposal_id="counter-child",
            responder_faction_id="fa",
            response="reject",
        )
        self.assertEqual("rejected", rejected["proposal"]["status"])

    def test_private_politics_events_never_enter_public_world_context(self) -> None:
        marker = "never-public-council-password"
        proposal = self._dispatch(
            "create_proposal",
            principal="fa",
            key="private-event-proposal",
            proposal_id="private-event-proposal",
            proposer_faction_id="fa",
            recipient_faction_id="fb",
            proposal_type="secret_alliance",
            terms={"password": marker},
            visibility="private",
        )
        with self.e._db() as db:
            audit = db.execute(
                """SELECT sensitivity,scope_type,principal_kind,principal_id,payload_json
                   FROM events WHERE campaign_id='c' AND event_type='politics_action'
                   ORDER BY id DESC LIMIT 1"""
            ).fetchone()
        self.assertIsNotNone(audit)
        self.assertEqual(("PRIVATE", "ENTITY", "faction", "fa"), tuple(audit)[:4])
        self.assertEqual(
            "create_proposal", json.loads(audit["payload_json"])["operation"]
        )
        public_events = json.dumps(
            self.e.get_world_context("c", event_limit=50)["recent_events"],
            sort_keys=True,
        )
        self.assertNotIn("create_proposal", public_events)
        self.assertNotIn("secret_alliance", public_events)
        self.assertNotIn(marker, public_events)
        self.assertNotIn(proposal["id"], public_events)

    def test_no_free_mobilization_casualties_reduce_population_and_demobilize(
        self,
    ) -> None:
        with self.assertRaisesRegex(ValueError, "insufficient uncommitted currency"):
            self._dispatch(
                "mobilize",
                principal="fa",
                key="too-costly",
                force_id="bad-force",
                faction_id="fa",
                name="Bad",
                location_id="a",
                source_cohort_id="cohort-a",
                manpower=10,
                currency_key="gp",
                currency_cost=101,
            )
        force = self._mobilize(manpower=60, supply_qty=5)
        self.assertEqual(60, force["manpower"])
        with self.assertRaisesRegex(ValueError, "insufficient uncommitted manpower"):
            self._mobilize("force-b", manpower=41, supply_qty=0)
        loss = self._dispatch(
            "apply_force_losses",
            key="losses",
            force_id="force-a",
            count=10,
            loss_kind="casualty",
            cause_kind="war",
            cause_id="test-war",
        )
        self.assertEqual(50, loss["manpower"])
        with self.e._db() as db:
            self.assertEqual(
                90,
                db.execute(
                    "SELECT count FROM population_cohorts WHERE id='cohort-a'"
                ).fetchone()[0],
            )
            self.assertEqual(
                40,
                self.k.available_resource_db(
                    db, "c", "manpower", "cohort-a", "faction", "fa", location_id="a"
                ),
            )
        ended = self._dispatch(
            "demobilize",
            principal="fa",
            key="demobilize",
            force_id="force-a",
        )
        self.assertEqual("demobilized", ended["status"])
        with self.e._db() as db:
            self.assertEqual(
                90,
                self.k.available_resource_db(
                    db, "c", "manpower", "cohort-a", "faction", "fa", location_id="a"
                ),
            )

    def test_raid_requires_real_route_and_committed_supply(self) -> None:
        self._mobilize(manpower=20)
        payload = {
            "raid_id": "raid-1",
            "attacker_faction_id": "fa",
            "target_faction_id": "fb",
            "force_id": "force-a",
            "target_location_id": "b",
            "route_id": "missing",
            "supply_item_id": "rations",
            "supply_qty": 2,
        }
        with self.assertRaisesRegex(ValueError, "requires an active route"):
            self._dispatch("plan_raid", principal="fa", key="bad-raid", **payload)
        with self.e._db() as db:
            before = db.execute(
                "SELECT qty FROM inventories WHERE owner_id='fa' AND item_id='rations'"
            ).fetchone()[0]
            self.assertEqual(
                0, db.execute("SELECT COUNT(*) FROM politics_raids").fetchone()[0]
            )
        self._route(self.e)
        planned = self._dispatch(
            "plan_raid", principal="fa", key="raid", **{**payload, "route_id": "ab"}
        )
        self.assertEqual("planned", planned["status"])
        with self.e._db() as db:
            after = db.execute(
                "SELECT qty FROM inventories WHERE owner_id='fa' AND item_id='rations'"
            ).fetchone()[0]
        self.assertEqual(2, before - after)
        resolved = self._dispatch(
            "resolve_raid",
            key="resolve-raid",
            raid_id="raid-1",
            success=True,
            attacker_losses=1,
        )
        self.assertEqual("resolved", resolved["status"])
        with self.e._db() as db:
            route_commitment = db.execute(
                "SELECT status FROM politics_commitments WHERE id='raid:raid-1:route'"
            ).fetchone()[0]
            supply_commitment = db.execute(
                "SELECT status FROM politics_commitments WHERE id='raid:raid-1:supply'"
            ).fetchone()[0]
        self.assertEqual("released", route_commitment)
        self.assertEqual("consumed", supply_commitment)

    def test_route_capacity_reservation_is_global_across_factions(self) -> None:
        self._route(self.e, capacity=10)
        self._dispatch(
            "reserve",
            principal="system",
            key="fa-route",
            commitment_id="fa-route",
            actor_kind="faction",
            actor_id="fa",
            resource_kind="route_capacity",
            resource_key="ab",
            amount=6,
            purpose_kind="movement",
            purpose_id="fa-move",
        )
        with self.assertRaisesRegex(
            ValueError, "insufficient uncommitted route_capacity"
        ):
            self._dispatch(
                "reserve",
                principal="system",
                key="fb-route",
                commitment_id="fb-route",
                actor_kind="faction",
                actor_id="fb",
                resource_kind="route_capacity",
                resource_key="ab",
                amount=5,
                purpose_kind="movement",
                purpose_id="fb-move",
            )
        with self.e._db() as db:
            self.assertEqual(
                1,
                db.execute(
                    "SELECT COUNT(*) FROM politics_commitments WHERE resource_kind='route_capacity'"
                ).fetchone()[0],
            )

    def test_population_labor_refresh_subtracts_project_commitments(self) -> None:
        with self.e._write_db() as db:
            db.execute(
                """INSERT INTO recipes(
                       campaign_id,id,kind,inputs_json,output_item_id,output_qty,skill,
                       dc,hours,station_tag,metadata_json,updated_at)
                   VALUES('c','building-work','craft','{}','wood',1,NULL,10,1,NULL,'{}',?)""",
                (self.e._now(),),
            )
        EconomyKernel(self.e).save_producer(
            "c",
            "building-yard",
            "a",
            "location",
            "a",
            "building-work",
            state={"workers_required": 50, "occupation": "builders"},
        )
        self._project()
        with self.e._write_db() as db:
            factors = PopulationKernel(self.e).refresh_labor_db(
                db, "c", "a", datetime(1492, 1, 1, tzinfo=UTC)
            )
            row = db.execute(
                """SELECT productivity,state_json FROM settlement_labor
                   WHERE campaign_id='c' AND location_id='a' AND occupation='builders'"""
            ).fetchone()
        self.assertAlmostEqual(0.92, factors["builders"])
        self.assertAlmostEqual(0.92, float(row["productivity"]))
        self.assertEqual(4.0, json.loads(row["state_json"])["politics_labor_reserved"])

    def test_economy_shipments_respect_politics_route_commitments(self) -> None:
        self._route(self.e, capacity=10)
        self.e.set_inventory_item("c", "location", "a", "wood", 10)
        self._dispatch(
            "reserve",
            key="economy-route-reservation",
            commitment_id="economy-route-reservation",
            actor_kind="faction",
            actor_id="fa",
            resource_kind="route_capacity",
            resource_key="ab",
            amount=8,
            purpose_kind="project",
            purpose_id="convoy-priority",
        )
        economy = EconomyKernel(self.e)
        shipment = {
            "from_owner_kind": "location",
            "from_owner_id": "a",
            "to_owner_kind": "location",
            "to_owner_id": "b",
            "from_location_id": "a",
            "to_location_id": "b",
            "item_id": "wood",
            "route_id": "ab",
        }
        with self.assertRaisesRegex(ValueError, "route capacity exceeded"):
            economy.create_shipment("c", "too-large", qty=3, **shipment)
        accepted = economy.create_shipment("c", "fits", qty=2, **shipment)
        self.assertEqual(2.0, accepted["qty"])

    def test_war_occupation_and_peace_are_linked(self) -> None:
        self._route(self.e)
        self._dispatch(
            "set_control",
            key="control-b",
            location_id="b",
            controller_faction_id="fb",
            reason="initial control",
        )
        self._dispatch(
            "add_grievance",
            principal="system",
            key="grievance",
            grievance_id="g1",
            aggrieved_faction_id="fa",
            against_faction_id="fb",
            grievance_type="border_raid",
            severity=0.7,
            source_kind="raid",
            source_id="prior-raid",
        )
        war = self._dispatch(
            "declare_war",
            principal="fa",
            key="war",
            war_id="w1",
            attacker_faction_id="fa",
            defender_faction_id="fb",
            casus_belli_kind="grievance",
            casus_belli_id="g1",
            goals=[{"kind": "control", "location_id": "b"}],
        )
        self.assertEqual("active", war["status"])
        self._mobilize(manpower=20, supply_qty=5)
        deployed = self._dispatch(
            "deploy_force",
            principal="fa",
            key="deploy",
            force_id="force-a",
            destination_location_id="b",
            route_id="ab",
            supply_cost=1,
        )
        self.assertEqual("b", deployed["location_id"])
        occupied = self._dispatch(
            "occupy",
            principal="fa",
            key="occupy",
            war_id="w1",
            force_id="force-a",
            location_id="b",
            control=0.75,
        )
        self.assertEqual("fa", occupied["controller_faction_id"])
        self.assertEqual("occupied", occupied["occupation_state"])
        peace = self._dispatch(
            "make_peace",
            key="peace",
            war_id="w1",
            treaty_id="peace-w1",
            clauses=[{"id": "ceasefire", "type": "ceasefire", "terms": {}}],
            obligations=[],
        )
        self.assertEqual("ended", peace["war"]["status"])
        self.assertEqual("peace", peace["treaty"]["treaty_type"])

    def test_construction_daily_catchup_is_chunk_invariant_and_replay_safe(
        self,
    ) -> None:
        other_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(other_tmp.cleanup)
        other_e, other_k = self._new_engine(Path(other_tmp.name) / "other.sqlite3")
        self._project(self.k)
        self._project(other_k)
        with self.e._write_db() as db:
            r1 = self.e._next_revision(db, "c")
            self.k.step_db(db, "c", r1, datetime(1492, 1, 2, tzinfo=UTC))
        with self.e._write_db() as db:
            r2 = self.e._next_revision(db, "c")
            tally_a = self.k.step_db(db, "c", r2, datetime(1492, 1, 3, tzinfo=UTC))
        with other_e._write_db() as db:
            rb = other_e._next_revision(db, "c")
            tally_b = other_k.step_db(db, "c", rb, datetime(1492, 1, 3, tzinfo=UTC))
            replay = other_k.step_db(db, "c", rb, datetime(1492, 1, 3, tzinfo=UTC))
        self.assertEqual(tally_b, replay)
        self.assertEqual(1, tally_a["projects_completed"])
        self.assertEqual(1, tally_b["projects_completed"])
        with self.e._db() as db_a, other_e._db() as db_b:
            project_a = db_a.execute(
                "SELECT status,progress FROM politics_projects WHERE id='bridge'"
            ).fetchone()
            project_b = db_b.execute(
                "SELECT status,progress FROM politics_projects WHERE id='bridge'"
            ).fetchone()
            commitments_a = db_a.execute(
                "SELECT resource_kind,status FROM politics_commitments WHERE purpose_id='bridge' ORDER BY resource_kind"
            ).fetchall()
            commitments_b = db_b.execute(
                "SELECT resource_kind,status FROM politics_commitments WHERE purpose_id='bridge' ORDER BY resource_kind"
            ).fetchall()
        self.assertEqual(tuple(project_a), tuple(project_b))
        self.assertEqual(
            [tuple(row) for row in commitments_a], [tuple(row) for row in commitments_b]
        )
        self.assertEqual(("completed", 8.0), tuple(project_a))

    def test_daily_overdue_obligation_is_deterministic_and_one_shot(self) -> None:
        self._dispatch(
            "create_treaty",
            key="daily-treaty",
            treaty_id="daily-treaty",
            treaty_type="tribute",
            name="Daily treaty",
            parties=["fa", "fb"],
            clauses=[{"id": "pay", "type": "payment", "terms": {}}],
            obligations=[
                {
                    "id": "daily-obligation",
                    "clause_id": "pay",
                    "type": "payment",
                    "debtor_faction_id": "fa",
                    "beneficiary_faction_id": "fb",
                    "due_world_time": "1492-01-02T00:00:00+00:00",
                }
            ],
        )
        boundary = datetime(1492, 1, 2, tzinfo=UTC)
        with self.e._write_db() as db:
            revision = self.e._next_revision(db, "c")
            first = self.k.step_db(db, "c", revision, boundary)
            replay = self.k.step_db(db, "c", revision, boundary)
        self.assertEqual(first, replay)
        self.assertEqual(1, first["obligations_violated"])
        with self.e._db() as db:
            self.assertEqual(
                1,
                db.execute(
                    "SELECT COUNT(*) FROM politics_treaty_violations WHERE obligation_id='daily-obligation'"
                ).fetchone()[0],
            )
            self.assertEqual(
                1,
                db.execute(
                    "SELECT COUNT(*) FROM politics_grievances WHERE source_kind='treaty_violation'"
                ).fetchone()[0],
            )
        with (
            self.e._write_db() as db,
            self.assertRaisesRegex(ValueError, "chronological order"),
        ):
            self.k.step_db(
                db,
                "c",
                revision,
                datetime(1492, 1, 1, tzinfo=UTC),
            )

    def test_law_hook_requires_jurisdiction_and_real_offender(self) -> None:
        with self.assertRaisesRegex(ValueError, "no configured jurisdiction"):
            self._dispatch(
                "open_legal_case",
                key="no-law",
                case_id="case-1",
                location_id="a",
                offender_kind="faction",
                offender_id="fb",
                offense="trespass",
                severity=0.2,
            )
        self._dispatch(
            "set_jurisdiction",
            key="jurisdiction",
            location_id="a",
            authority_faction_id="fa",
            law_profile={"trespass": "fine"},
            enforcement_capacity=10,
        )
        case = self._dispatch(
            "open_legal_case",
            key="case",
            case_id="case-1",
            location_id="a",
            offender_kind="faction",
            offender_id="fb",
            offense="trespass",
            severity=0.2,
        )
        self.assertEqual("fa", case["authority_faction_id"])
        self.assertEqual("open", case["status"])


if __name__ == "__main__":
    unittest.main()

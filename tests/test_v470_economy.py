from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from world_engine import WorldEngine
from world_engine.economy import (
    ECONOMY_SCHEMA,
    EconomyKernel,
    migrate_economy_schema_db,
)


class EconomyV470Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "world.sqlite3"
        self.e = WorldEngine(self.db_path)
        with self.e._write_db() as db:
            db.executescript(ECONOMY_SCHEMA)
        self.k = EconomyKernel(self.e)
        self.e.ensure_campaign("c", "Economy", "1492-07-01T00:00:00+00:00")
        self.e.upsert_location("c", "farm", "Farm")
        self.e.upsert_location("c", "town", "Town")
        self.e.save_location_link("c", "farm", "town", 4, bidirectional=True)
        self.e.upsert_character("c", "hero", "Hero", hp=20, max_hp=20, location="town")
        self.e.upsert_character("c", "rival", "Rival", hp=20, max_hp=20, location="town")
        self.e.set_simulation_seed("c", 470)
        for item_id, name, price in (
            ("grain", "Grain", 2),
            ("flour", "Flour", 5),
            ("bread", "Bread", 10),
            ("ore", "Ore", 8),
        ):
            self.e.save_item_def("c", item_id, name, base_price=price)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _dispatch(self, operation: str, **payload):
        return self.k.dispatch(operation, "c", payload)

    def _balance(self, kind: str, owner_id: str, amount: float) -> None:
        with self.e._write_db() as db:
            db.execute(
                """INSERT INTO owner_balances(
                       campaign_id,owner_kind,owner_id,currency_key,amount,updated_at)
                   VALUES('c',?,?, 'gp',?,?)
                   ON CONFLICT(campaign_id,owner_kind,owner_id,currency_key)
                   DO UPDATE SET amount=excluded.amount,updated_at=excluded.updated_at""",
                (kind, owner_id, amount, self.e._now()),
            )

    def _market(
        self, market_id: str = "shop", *, visibility: str = "public", item: str = "bread",
        demand_per_day: float = 0,
    ) -> None:
        self._dispatch(
            "save_market",
            market_id=market_id,
            location_id="town",
            name=market_id,
            visibility=visibility,
        )
        self._dispatch(
            "set_market_item",
            market_id=market_id,
            item_id=item,
            target_stock=10,
            demand_per_day=demand_per_day,
        )

    def _step(self, when: datetime) -> dict[str, int]:
        with self.e._write_db() as db:
            revision = self.e._next_revision(db, "c")
            return self.k.step_db(db, "c", revision, when)

    def test_schema_is_owned_without_claiming_shared_user_version(self) -> None:
        with self.e._db() as db:
            before = int(db.execute("PRAGMA user_version").fetchone()[0])
            migrate_economy_schema_db(db)
            after = int(db.execute("PRAGMA user_version").fetchone()[0])
            columns = {
                row["name"] for row in db.execute("PRAGMA table_info(economy_transactions)")
            }
            market_columns = {
                row["name"] for row in db.execute("PRAGMA table_info(economy_markets)")
            }
        self.assertEqual(before, after)
        self.assertTrue({"actor_kind", "actor_id", "request_fingerprint"} <= columns)
        self.assertIn("visibility", market_columns)

    def test_actor_scoped_idempotency_and_payload_conflicts(self) -> None:
        self._market()
        self.e.set_inventory_item("c", "location", "town", "bread", 5)
        self._balance("character", "hero", 100)
        self._balance("character", "rival", 100)
        self._balance("location", "town", 0)
        base = dict(
            action="buy", actor_kind="character", actor_id="hero",
            market_id="shop", item_id="bread", qty=1, transaction_key="turn-1",
        )
        first = self._dispatch("interact", **base)
        self.assertEqual(first, self._dispatch("interact", **base))
        with self.assertRaisesRegex(ValueError, "ECONOMY_IDEMPOTENCY_CONFLICT"):
            self._dispatch("interact", **{**base, "qty": 2})
        with self.assertRaisesRegex(ValueError, "ECONOMY_IDEMPOTENCY_CONFLICT"):
            self._dispatch("interact", **{**base, "reason": "different reason"})
        rival = self._dispatch("interact", **{**base, "actor_id": "rival"})
        self.assertEqual("rival", rival["actor_id"])
        with self.e._db() as db:
            rows = db.execute(
                """SELECT actor_id,request_fingerprint FROM economy_transactions
                   WHERE campaign_id='c' AND tx_key='turn-1' ORDER BY actor_id"""
            ).fetchall()
        self.assertEqual(["hero", "rival"], [row["actor_id"] for row in rows])
        self.assertTrue(all(len(row["request_fingerprint"]) == 64 for row in rows))

    def test_public_turn_market_is_actor_bound_and_uses_server_replay_key(self) -> None:
        self._market()
        self.e.set_inventory_item("c", "location", "town", "bread", 5)
        self._balance("character", "hero", 100)
        self._balance("location", "town", 0)
        intents = [{
            "intent_id": "purchase",
            "type": "buy",
            "parameters": {
                "market_id": "shop",
                "item_id": "bread",
                "qty": 1,
                "actor_id": "hero",
                "transaction_key": "CALLER_MUST_NOT_CONTROL_THIS",
            },
        }]
        result = self.e.resolve_turn(
            "c",
            actor_kind="character",
            actor_id="hero",
            intents=intents,
            idempotency_key="market-turn",
            enforce_pbem=True,
        )
        step = result["steps"][0]
        self.assertEqual("completed", step["status"])
        self.assertEqual("hero", step["result"]["actor_id"])
        with self.e._db() as db:
            transaction = db.execute(
                "SELECT tx_key,actor_kind,actor_id FROM economy_transactions WHERE campaign_id='c'"
            ).fetchone()
        self.assertEqual(f"wetp:{result['turn_id']}:purchase", transaction["tx_key"])
        self.assertNotEqual("CALLER_MUST_NOT_CONTROL_THIS", transaction["tx_key"])
        self.assertEqual(("character", "hero"), (transaction["actor_kind"], transaction["actor_id"]))

    def test_concurrent_last_stock_never_oversells(self) -> None:
        self._market()
        self.e.set_inventory_item("c", "location", "town", "bread", 1)
        self._balance("character", "hero", 100)
        self._balance("character", "rival", 100)
        self._balance("location", "town", 0)
        kernels = {
            actor_id: EconomyKernel(WorldEngine(self.db_path))
            for actor_id in ("hero", "rival")
        }

        def buy(actor_id: str):
            try:
                return kernels[actor_id].interact(
                    "c", action="buy", actor_kind="character", actor_id=actor_id,
                    market_id="shop", item_id="bread", qty=1,
                    transaction_key="last-stock",
                )
            except ValueError as exc:
                return str(exc)

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(buy, ("hero", "rival")))
        self.assertEqual(1, sum(isinstance(value, dict) for value in outcomes))
        self.assertEqual(1, sum("insufficient market stock" in str(value) for value in outcomes))
        with self.e._db() as db:
            qty = db.execute(
                """SELECT qty FROM inventories WHERE campaign_id='c'
                   AND owner_kind='location' AND owner_id='town' AND item_id='bread'"""
            ).fetchone()["qty"]
            tx_count = db.execute(
                "SELECT COUNT(*) AS n FROM economy_transactions WHERE campaign_id='c'"
            ).fetchone()["n"]
        self.assertEqual(0.0, float(qty))
        self.assertEqual(1, tx_count)

    def test_numeric_writes_reject_bool_nan_infinity_and_range_overflow(self) -> None:
        bad_values = (True, float("nan"), float("inf"), float("-inf"), 1e40)
        for index, value in enumerate(bad_values):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    self._dispatch(
                        "save_market", market_id=f"bad{index}", location_id="town",
                        name="bad", buy_markup=value,
                    )
        self._market()
        for value in bad_values:
            with self.subTest(target_stock=value):
                with self.assertRaises(ValueError):
                    self._dispatch(
                        "set_market_item", market_id="shop", item_id="bread",
                        target_stock=value,
                    )

    def test_defensive_read_rejects_nonfinite_and_public_json_is_strict(self) -> None:
        self._market()
        self.e.set_inventory_item("c", "location", "town", "bread", 1)
        payload = self.k.public_snapshot("c")
        json.dumps(payload, allow_nan=False)
        with self.e._write_db() as db:
            db.execute(
                "UPDATE economy_markets SET buy_markup=? WHERE campaign_id='c' AND id='shop'",
                (float("inf"),),
            )
        with self.assertRaisesRegex(ValueError, "finite"):
            self.k.public_snapshot("c")

    def test_private_and_undiscovered_markets_are_not_public_or_interactable(self) -> None:
        self._market("public")
        self._market("private", visibility="private")
        self._market("hidden", visibility="undiscovered")
        visible = self.k.public_snapshot("c")
        self.assertEqual(["public"], [row["id"] for row in visible["markets"]])
        for market_id in ("private", "hidden"):
            with self.assertRaisesRegex(KeyError, "inaccessible"):
                self.k.interact(
                    "c", action="inspect", actor_kind="character", actor_id="hero",
                    market_id=market_id,
                )

    def test_public_projection_has_total_quote_bound(self) -> None:
        self._market(item="bread")
        for item_id in ("grain", "flour", "ore"):
            self._dispatch("set_market_item", market_id="shop", item_id=item_id)
        view = self.k.public_snapshot("c", quote_limit=2)
        self.assertEqual(2, len(view["quotes"]))
        self.assertTrue(view["quotes_truncated"])
        self.assertEqual(4, view["markets"][0]["item_count"])
        with self.assertRaises(ValueError):
            self.k.public_snapshot("c", quote_limit=501)

    def test_same_boundary_delivery_precedes_demand(self) -> None:
        self._market(item="grain", demand_per_day=24)
        self.e.set_inventory_item("c", "location", "farm", "grain", 1)
        self._dispatch(
            "save_route", route_id="road", from_location_id="farm",
            to_location_id="town", travel_hours=1, capacity_qty_per_day=10,
        )
        self._dispatch(
            "create_shipment", shipment_id="due", from_owner_kind="location",
            from_owner_id="farm", to_owner_kind="location", to_owner_id="town",
            from_location_id="farm", to_location_id="town", item_id="grain",
            qty=1, route_id="road",
        )
        result = self._step(datetime(1492, 7, 1, 1, tzinfo=timezone.utc))
        self.assertEqual(1, result["shipments_delivered"])
        with self.e._db() as db:
            qty = db.execute(
                """SELECT qty FROM inventories WHERE campaign_id='c'
                   AND owner_kind='location' AND owner_id='town' AND item_id='grain'"""
            ).fetchone()["qty"]
            pressure = db.execute(
                """SELECT demand_pressure FROM economy_market_items
                   WHERE campaign_id='c' AND market_id='shop' AND item_id='grain'"""
            ).fetchone()["demand_pressure"]
        self.assertEqual(0.0, float(qty))
        self.assertEqual(0.0, float(pressure))

    def test_step_rejects_noncanonical_tail(self) -> None:
        with self.e._write_db() as db:
            with self.assertRaisesRegex(ValueError, "canonical UTC hour"):
                self.k.step_db(
                    db, "c", 1, datetime(1492, 7, 1, 0, 30, tzinfo=timezone.utc)
                )

    def test_world_scheduler_uses_hours_and_never_request_tails(self) -> None:
        self.e.save_resource_node(
            "c", "hourly-node", "farm", "grain", qty=10, qty_max=10,
            regen_per_day=0,
        )
        self._dispatch(
            "save_extractor", extractor_id="hourly", location_id="farm",
            owner_kind="location", owner_id="farm",
            resource_node_id="hourly-node", units_per_day=24,
        )
        first = self.e.advance_world("c", 30, "half hour")
        second = self.e.advance_world("c", 30, "complete hour")
        self.assertEqual(0, first["simulation"]["economy_extraction"])
        self.assertEqual(1, second["simulation"]["economy_extraction"])
        with self.e._db() as db:
            qty = db.execute(
                """SELECT qty FROM inventories WHERE campaign_id='c'
                   AND owner_kind='location' AND owner_id='farm' AND item_id='grain'"""
            ).fetchone()["qty"]
        self.assertEqual(1.0, float(qty))

    def test_duplicate_shipment_rolls_back_reserved_stock(self) -> None:
        self.e.set_inventory_item("c", "location", "farm", "grain", 5)
        self._dispatch(
            "save_route", route_id="road", from_location_id="farm",
            to_location_id="town", travel_hours=1, capacity_qty_per_day=10,
        )
        shipment = dict(
            shipment_id="same", from_owner_kind="location", from_owner_id="farm",
            to_owner_kind="location", to_owner_id="town", from_location_id="farm",
            to_location_id="town", item_id="grain", qty=1, route_id="road",
        )
        self._dispatch("create_shipment", **shipment)
        with self.assertRaises(sqlite3.IntegrityError):
            self._dispatch("create_shipment", **shipment)
        with self.e._db() as db:
            qty = db.execute(
                """SELECT qty FROM inventories WHERE campaign_id='c'
                   AND owner_kind='location' AND owner_id='farm' AND item_id='grain'"""
            ).fetchone()["qty"]
        self.assertEqual(4.0, float(qty))

    def test_authoring_promote_is_atomic_idempotent_and_conflict_closed(self) -> None:
        sections = {
            "economy_markets": [
                {"id": "auth", "location_id": "town", "name": "Auth Market"}
            ],
            "economy_market_items": [
                {"market_id": "auth", "item_id": "bread", "target_stock": 4}
            ],
            "economy_inventories": [
                {
                    "owner_kind": "location", "owner_id": "town",
                    "item_id": "bread", "qty": 4,
                }
            ],
            "economy_balances": [
                {
                    "owner_kind": "location", "owner_id": "town",
                    "currency_key": "gp", "amount": 20,
                }
            ],
        }
        with self.e._write_db() as db:
            first = self.k.promote_records_db(db, "c", sections)
        self.assertEqual(1, first["sections"]["economy_markets"]["inserted"])
        with self.e._write_db() as db:
            second = self.k.promote_records_db(db, "c", sections)
        self.assertEqual(1, second["sections"]["economy_markets"]["unchanged"])

        conflicting = {
            "economy_markets": [
                {"id": "temporary", "location_id": "town", "name": "Temporary"},
                {"id": "auth", "location_id": "town", "name": "Changed"},
            ]
        }
        with self.assertRaisesRegex(ValueError, "ECONOMY_AUTHORING_CONFLICT"):
            with self.e._write_db() as db:
                self.k.promote_records_db(db, "c", conflicting)
        with self.e._db() as db:
            self.assertIsNone(
                db.execute(
                    "SELECT 1 FROM economy_markets WHERE campaign_id='c' AND id='temporary'"
                ).fetchone()
            )

    def test_authoring_rejects_nonfinite_state_and_values(self) -> None:
        with self.assertRaises(ValueError):
            with self.e._write_db() as db:
                self.k.promote_records_db(
                    db,
                    "c",
                    {
                        "economy_markets": [
                            {
                                "id": "bad", "location_id": "town", "name": "Bad",
                                "state": {"workers_required": float("nan")},
                            }
                        ]
                    },
                )
        self.e.save_resource_node(
            "c", "labor-node", "farm", "grain", qty=10, qty_max=10,
            regen_per_day=0,
        )
        with self.assertRaisesRegex(ValueError, "workers_required"):
            with self.e._write_db() as db:
                self.k.promote_records_db(
                    db,
                    "c",
                    {
                        "economy_extractors": [
                            {
                                "id": "bad-labor", "location_id": "farm",
                                "owner_kind": "location", "owner_id": "farm",
                                "resource_node_id": "labor-node",
                                "state": {"workers_required": 1e40},
                            }
                        ]
                    },
                )
        with self.assertRaises(ValueError):
            with self.e._write_db() as db:
                self.k.promote_records_db(
                    db,
                    "c",
                    {
                        "economy_balances": [
                            {
                                "owner_kind": "location", "owner_id": "town",
                                "currency_key": "gp", "amount": True,
                            }
                        ]
                    },
                )

    def test_corrupt_route_numeric_fails_step_closed(self) -> None:
        self._dispatch(
            "save_market", market_id="source", location_id="farm", name="source"
        )
        self._dispatch("set_market_item", market_id="source", item_id="grain")
        self._market("dest", item="grain")
        self.e.set_inventory_item("c", "location", "farm", "grain", 10)
        self._dispatch(
            "save_route", route_id="road", from_location_id="farm",
            to_location_id="town", travel_hours=1, capacity_qty_per_day=10,
        )
        self._dispatch(
            "save_supply_link", link_id="link", source_market_id="source",
            dest_market_id="dest", item_id="grain", reorder_point=20,
            reorder_qty=1, source_reserve=0, route_id="road", settle=False,
        )
        with self.e._write_db() as db:
            db.execute("PRAGMA ignore_check_constraints=ON")
            db.execute(
                "UPDATE economy_routes SET risk=? WHERE campaign_id='c' AND id='road'",
                (float("inf"),),
            )
        with self.assertRaisesRegex(ValueError, "finite"):
            self._step(datetime(1492, 7, 1, 1, tzinfo=timezone.utc))

    def test_legacy_global_keys_are_retired_without_replay_disclosure(self) -> None:
        path = Path(self.tmp.name) / "legacy.sqlite3"
        db = sqlite3.connect(path)
        try:
            db.row_factory = sqlite3.Row
            db.executescript(
                """
                CREATE TABLE campaigns(id TEXT PRIMARY KEY);
                INSERT INTO campaigns VALUES('c');
                CREATE TABLE economy_markets(
                    campaign_id TEXT,id TEXT,location_id TEXT,active INTEGER,
                    PRIMARY KEY(campaign_id,id));
                CREATE TABLE economy_transactions(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    campaign_id TEXT NOT NULL,tx_key TEXT,kind TEXT NOT NULL,
                    market_id TEXT,buyer_kind TEXT,buyer_id TEXT,seller_kind TEXT,
                    seller_id TEXT,item_id TEXT,qty REAL NOT NULL DEFAULT 0,
                    unit_price REAL NOT NULL DEFAULT 0,total REAL NOT NULL DEFAULT 0,
                    currency_key TEXT,world_time TEXT NOT NULL,revision INTEGER NOT NULL,
                    result_json TEXT NOT NULL DEFAULT '{}',
                    metadata_json TEXT NOT NULL DEFAULT '{}',created_at TEXT NOT NULL,
                    UNIQUE(campaign_id,tx_key));
                INSERT INTO economy_transactions(
                    campaign_id,tx_key,kind,qty,unit_price,total,world_time,revision,
                    result_json,metadata_json,created_at)
                VALUES('c','secret-key','market_buy',1,2,2,'1492-01-01T00:00:00+00:00',
                       1,'{"actor_balance":98}','{}','now');
                PRAGMA user_version=91;
                """
            )
            migrate_economy_schema_db(db)
            migrated = db.execute(
                "SELECT tx_key,actor_kind,actor_id,request_fingerprint,result_json "
                "FROM economy_transactions"
            ).fetchone()
            self.assertIsNone(migrated["tx_key"])
            self.assertIsNone(migrated["request_fingerprint"])
            self.assertEqual(91, db.execute("PRAGMA user_version").fetchone()[0])
            columns = {
                row["name"] for row in db.execute("PRAGMA table_info(economy_markets)")
            }
            self.assertIn("visibility", columns)
        finally:
            db.close()

    def test_canonical_boundary_chunking_is_deterministic(self) -> None:
        def run(path: Path, boundaries: list[int]):
            engine = WorldEngine(path)
            with engine._write_db() as db:
                db.executescript(ECONOMY_SCHEMA)
            kernel = EconomyKernel(engine)
            engine.ensure_campaign("x", "X", "1492-07-01T00:00:00+00:00")
            engine.upsert_location("x", "farm", "Farm")
            engine.save_item_def("x", "grain", "Grain", base_price=2)
            engine.save_resource_node(
                "x", "field", "farm", "grain", qty=20, qty_max=20, regen_per_day=0
            )
            kernel.save_extractor(
                "x", "harvest", "farm", "location", "farm", "field",
                units_per_day=8,
            )
            start = datetime(1492, 7, 1, tzinfo=timezone.utc)
            for hours in boundaries:
                with engine._write_db() as db:
                    kernel.step_db(db, "x", 1, start + timedelta(hours=hours))
            with engine._db() as db:
                node = float(
                    db.execute(
                        "SELECT qty FROM resource_nodes WHERE campaign_id='x' AND id='field'"
                    ).fetchone()["qty"]
                )
                inventory = float(
                    db.execute(
                        """SELECT qty FROM inventories WHERE campaign_id='x'
                           AND owner_kind='location' AND owner_id='farm'
                           AND item_id='grain'"""
                    ).fetchone()["qty"]
                )
            return round(node, 9), round(inventory, 9)

        once = run(Path(self.tmp.name) / "once.sqlite3", [24])
        chunked = run(Path(self.tmp.name) / "chunked.sqlite3", [6, 12, 18, 24])
        self.assertEqual(once, chunked)


if __name__ == "__main__":
    unittest.main()

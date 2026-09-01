from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from .engine import WorldEngine


ECONOMY_SCHEMA = r'''
CREATE TABLE IF NOT EXISTS economy_config (
    campaign_id TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 1,
    default_currency TEXT NOT NULL DEFAULT 'gp',
    price_floor_mult REAL NOT NULL DEFAULT 0.25 CHECK(price_floor_mult > 0),
    price_ceiling_mult REAL NOT NULL DEFAULT 4.0 CHECK(price_ceiling_mult >= price_floor_mult),
    production_enabled INTEGER NOT NULL DEFAULT 1,
    logistics_enabled INTEGER NOT NULL DEFAULT 1,
    consumption_enabled INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS economy_markets (
    campaign_id TEXT NOT NULL,
    id TEXT NOT NULL,
    location_id TEXT NOT NULL,
    name TEXT NOT NULL,
    owner_kind TEXT NOT NULL CHECK(owner_kind IN ('character','npc','faction','location')),
    owner_id TEXT NOT NULL,
    currency_key TEXT NOT NULL DEFAULT 'gp',
    buy_markup REAL NOT NULL DEFAULT 1.0 CHECK(buy_markup > 0),
    sell_discount REAL NOT NULL DEFAULT 0.5 CHECK(sell_discount >= 0),
    visibility TEXT NOT NULL DEFAULT 'public' CHECK(visibility IN ('public','private','undiscovered')),
    active INTEGER NOT NULL DEFAULT 1,
    state_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,id),
    FOREIGN KEY(campaign_id,location_id) REFERENCES locations(campaign_id,id) ON DELETE CASCADE,
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS economy_market_items (
    campaign_id TEXT NOT NULL,
    market_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    target_stock REAL NOT NULL DEFAULT 10 CHECK(target_stock >= 0),
    reorder_point REAL NOT NULL DEFAULT 0 CHECK(reorder_point >= 0),
    demand_per_day REAL NOT NULL DEFAULT 0 CHECK(demand_per_day >= 0),
    demand_pressure REAL NOT NULL DEFAULT 0 CHECK(demand_pressure BETWEEN -1 AND 1),
    floor_mult REAL NOT NULL DEFAULT 0.25 CHECK(floor_mult > 0),
    ceiling_mult REAL NOT NULL DEFAULT 4.0 CHECK(ceiling_mult >= floor_mult),
    enabled INTEGER NOT NULL DEFAULT 1,
    last_demand_world_time TEXT,
    state_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,market_id,item_id),
    FOREIGN KEY(campaign_id,market_id) REFERENCES economy_markets(campaign_id,id) ON DELETE CASCADE,
    FOREIGN KEY(campaign_id,item_id) REFERENCES item_defs(campaign_id,id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS economy_extractors (
    campaign_id TEXT NOT NULL,
    id TEXT NOT NULL,
    location_id TEXT NOT NULL,
    owner_kind TEXT NOT NULL CHECK(owner_kind IN ('character','npc','faction','location')),
    owner_id TEXT NOT NULL,
    resource_node_id TEXT NOT NULL,
    units_per_day REAL NOT NULL DEFAULT 1 CHECK(units_per_day >= 0),
    max_units_per_step REAL NOT NULL DEFAULT 100 CHECK(max_units_per_step > 0),
    active INTEGER NOT NULL DEFAULT 1,
    last_processed_world_time TEXT NOT NULL,
    state_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,id),
    FOREIGN KEY(campaign_id,location_id) REFERENCES locations(campaign_id,id) ON DELETE CASCADE,
    FOREIGN KEY(campaign_id,resource_node_id) REFERENCES resource_nodes(campaign_id,id) ON DELETE CASCADE,
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS economy_producers (
    campaign_id TEXT NOT NULL,
    id TEXT NOT NULL,
    location_id TEXT NOT NULL,
    owner_kind TEXT NOT NULL CHECK(owner_kind IN ('character','npc','faction','location')),
    owner_id TEXT NOT NULL,
    recipe_id TEXT NOT NULL,
    batches_per_day REAL NOT NULL DEFAULT 1 CHECK(batches_per_day >= 0),
    work_credit REAL NOT NULL DEFAULT 0 CHECK(work_credit >= 0),
    max_batches_per_step INTEGER NOT NULL DEFAULT 24 CHECK(max_batches_per_step BETWEEN 1 AND 1000),
    active INTEGER NOT NULL DEFAULT 1,
    last_processed_world_time TEXT NOT NULL,
    state_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,id),
    FOREIGN KEY(campaign_id,location_id) REFERENCES locations(campaign_id,id) ON DELETE CASCADE,
    FOREIGN KEY(campaign_id,recipe_id) REFERENCES recipes(campaign_id,id) ON DELETE CASCADE,
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS economy_routes (
    campaign_id TEXT NOT NULL,
    id TEXT NOT NULL,
    from_location_id TEXT NOT NULL,
    to_location_id TEXT NOT NULL,
    travel_hours REAL NOT NULL CHECK(travel_hours >= 0),
    capacity_qty_per_day REAL NOT NULL DEFAULT 100 CHECK(capacity_qty_per_day >= 0),
    risk REAL NOT NULL DEFAULT 0 CHECK(risk BETWEEN 0 AND 1),
    cost_per_qty REAL NOT NULL DEFAULT 0 CHECK(cost_per_qty >= 0),
    carrier_owner_kind TEXT CHECK(carrier_owner_kind IS NULL OR carrier_owner_kind IN ('character','npc','faction','location')),
    carrier_owner_id TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    state_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,id),
    FOREIGN KEY(campaign_id,from_location_id) REFERENCES locations(campaign_id,id) ON DELETE CASCADE,
    FOREIGN KEY(campaign_id,to_location_id) REFERENCES locations(campaign_id,id) ON DELETE CASCADE,
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS economy_shipments (
    campaign_id TEXT NOT NULL,
    id TEXT NOT NULL,
    route_id TEXT,
    supply_link_id TEXT,
    from_owner_kind TEXT NOT NULL CHECK(from_owner_kind IN ('character','npc','faction','location')),
    from_owner_id TEXT NOT NULL,
    to_owner_kind TEXT NOT NULL CHECK(to_owner_kind IN ('character','npc','faction','location')),
    to_owner_id TEXT NOT NULL,
    from_location_id TEXT NOT NULL,
    to_location_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    qty REAL NOT NULL CHECK(qty > 0),
    currency_key TEXT NOT NULL DEFAULT 'gp',
    goods_value REAL NOT NULL DEFAULT 0 CHECK(goods_value >= 0),
    shipping_cost REAL NOT NULL DEFAULT 0 CHECK(shipping_cost >= 0),
    risk REAL NOT NULL DEFAULT 0 CHECK(risk BETWEEN 0 AND 1),
    status TEXT NOT NULL DEFAULT 'in_transit' CHECK(status IN ('in_transit','delivered','lost','cancelled')),
    depart_world_time TEXT NOT NULL,
    eta_world_time TEXT NOT NULL,
    state_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,id),
    FOREIGN KEY(campaign_id,item_id) REFERENCES item_defs(campaign_id,id) ON DELETE CASCADE,
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS economy_supply_links (
    campaign_id TEXT NOT NULL,
    id TEXT NOT NULL,
    source_market_id TEXT NOT NULL,
    dest_market_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    reorder_point REAL NOT NULL DEFAULT 2 CHECK(reorder_point >= 0),
    reorder_qty REAL NOT NULL DEFAULT 5 CHECK(reorder_qty > 0),
    source_reserve REAL NOT NULL DEFAULT 2 CHECK(source_reserve >= 0),
    route_id TEXT,
    settle INTEGER NOT NULL DEFAULT 1,
    enabled INTEGER NOT NULL DEFAULT 1,
    state_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id,id),
    FOREIGN KEY(campaign_id,source_market_id) REFERENCES economy_markets(campaign_id,id) ON DELETE CASCADE,
    FOREIGN KEY(campaign_id,dest_market_id) REFERENCES economy_markets(campaign_id,id) ON DELETE CASCADE,
    FOREIGN KEY(campaign_id,item_id) REFERENCES item_defs(campaign_id,id) ON DELETE CASCADE,
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS economy_transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id TEXT NOT NULL,
    tx_key TEXT,
    actor_kind TEXT CHECK(actor_kind IS NULL OR actor_kind IN ('character','npc','faction','location')),
    actor_id TEXT,
    request_fingerprint TEXT,
    kind TEXT NOT NULL,
    market_id TEXT,
    buyer_kind TEXT,
    buyer_id TEXT,
    seller_kind TEXT,
    seller_id TEXT,
    item_id TEXT,
    qty REAL NOT NULL DEFAULT 0,
    unit_price REAL NOT NULL DEFAULT 0,
    total REAL NOT NULL DEFAULT 0,
    currency_key TEXT,
    world_time TEXT NOT NULL,
    revision INTEGER NOT NULL,
    result_json TEXT NOT NULL DEFAULT '{}',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE,
    UNIQUE(campaign_id,actor_kind,actor_id,tx_key)
);

CREATE INDEX IF NOT EXISTS idx_economy_markets_location ON economy_markets(campaign_id,location_id,visibility,active,id);
CREATE INDEX IF NOT EXISTS idx_economy_market_items_item ON economy_market_items(campaign_id,item_id,market_id);
CREATE INDEX IF NOT EXISTS idx_economy_extractors_active ON economy_extractors(campaign_id,active,location_id,id);
CREATE INDEX IF NOT EXISTS idx_economy_producers_active ON economy_producers(campaign_id,active,location_id,id);
CREATE INDEX IF NOT EXISTS idx_economy_shipments_due ON economy_shipments(campaign_id,status,eta_world_time,id);
CREATE INDEX IF NOT EXISTS idx_economy_supply_links_active ON economy_supply_links(campaign_id,enabled,dest_market_id,item_id,id);
CREATE INDEX IF NOT EXISTS idx_economy_transactions_recent ON economy_transactions(campaign_id,id DESC);
'''


_TRANSACTION_TABLE_V47_SQL = r'''
CREATE TABLE economy_transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id TEXT NOT NULL,
    tx_key TEXT,
    actor_kind TEXT CHECK(actor_kind IS NULL OR actor_kind IN ('character','npc','faction','location')),
    actor_id TEXT,
    request_fingerprint TEXT,
    kind TEXT NOT NULL,
    market_id TEXT,
    buyer_kind TEXT,
    buyer_id TEXT,
    seller_kind TEXT,
    seller_id TEXT,
    item_id TEXT,
    qty REAL NOT NULL DEFAULT 0,
    unit_price REAL NOT NULL DEFAULT 0,
    total REAL NOT NULL DEFAULT 0,
    currency_key TEXT,
    world_time TEXT NOT NULL,
    revision INTEGER NOT NULL,
    result_json TEXT NOT NULL DEFAULT '{}',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE,
    UNIQUE(campaign_id,actor_kind,actor_id,tx_key)
)
'''


def migrate_economy_schema_db(db: sqlite3.Connection) -> None:
    """Upgrade economy-owned tables inside the caller's transaction.

    This deliberately does not commit and does not claim the shared
    PRAGMA user_version. Legacy global replay keys are retired rather
    than copied into the new actor-scoped namespace.
    """
    market_columns = {
        str(row[1]) for row in db.execute("PRAGMA table_info(economy_markets)").fetchall()
    }
    market_upgraded = False
    if market_columns and "visibility" not in market_columns:
        db.execute(
            "ALTER TABLE economy_markets ADD COLUMN visibility TEXT NOT NULL DEFAULT 'public' "
            "CHECK(visibility IN ('public','private','undiscovered'))"
        )
        market_upgraded = True

    tx_columns = {
        str(row[1]) for row in db.execute("PRAGMA table_info(economy_transactions)").fetchall()
    }
    transactions_upgraded = False
    if tx_columns and not {"actor_kind", "actor_id", "request_fingerprint"}.issubset(tx_columns):
        db.execute("DROP INDEX IF EXISTS idx_economy_transactions_recent")
        db.execute("ALTER TABLE economy_transactions RENAME TO economy_transactions_legacy_v45")
        db.execute(_TRANSACTION_TABLE_V47_SQL)
        db.execute(
            """INSERT INTO economy_transactions(
                   id,campaign_id,tx_key,actor_kind,actor_id,request_fingerprint,kind,market_id,
                   buyer_kind,buyer_id,seller_kind,seller_id,item_id,qty,unit_price,total,
                   currency_key,world_time,revision,result_json,metadata_json,created_at)
               SELECT id,campaign_id,NULL,NULL,NULL,NULL,kind,market_id,buyer_kind,buyer_id,
                      seller_kind,seller_id,item_id,qty,unit_price,total,currency_key,world_time,
                      revision,result_json,metadata_json,created_at
               FROM economy_transactions_legacy_v45 ORDER BY id"""
        )
        db.execute("DROP TABLE economy_transactions_legacy_v45")
        transactions_upgraded = True

    if market_upgraded:
        db.execute("DROP INDEX IF EXISTS idx_economy_markets_location")
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_economy_markets_location "
            "ON economy_markets(campaign_id,location_id,visibility,active,id)"
        )
    if transactions_upgraded:
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_economy_transactions_recent "
            "ON economy_transactions(campaign_id,id DESC)"
        )


class EconomyKernel:
    """Sparse authoritative economy/production/logistics provider.

    Existing item_defs, inventories, recipes, resource_nodes, owner_balances,
    locations, and location_links remain canonical.  This layer connects them;
    it does not create parallel item or currency ledgers.
    """

    OWNER_KINDS = {"character", "npc", "faction", "location"}
    MARKET_VISIBILITIES = {"public", "private", "undiscovered"}
    MAX_QUANTITY = 1_000_000_000_000.0
    MAX_PRICE = 1_000_000_000_000.0
    MAX_RATE = 1_000_000_000.0
    MAX_TRAVEL_HOURS = 1_000_000.0
    MAX_PUBLIC_QUOTES = 500

    def __init__(self, engine: "WorldEngine"):
        self.e = engine

    @classmethod
    def _number(
        cls,
        name: str,
        value: Any,
        *,
        minimum: float | None = None,
        maximum: float | None = None,
        strict_minimum: bool = False,
    ) -> float:
        if isinstance(value, bool):
            raise ValueError(f"{name} must be a finite number")
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{name} must be a finite number") from exc
        if not math.isfinite(number):
            raise ValueError(f"{name} must be a finite number")
        if minimum is not None and (
            number < minimum or (strict_minimum and number <= minimum)
        ):
            relation = "greater than" if strict_minimum else "at least"
            raise ValueError(f"{name} must be {relation} {minimum:g}")
        if maximum is not None and number > maximum:
            raise ValueError(f"{name} must be at most {maximum:g}")
        return number

    @classmethod
    def _integer(
        cls, name: str, value: Any, *, minimum: int, maximum: int
    ) -> int:
        if isinstance(value, bool):
            raise ValueError(f"{name} must be an integer")
        try:
            number = int(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{name} must be an integer") from exc
        if isinstance(value, float) and value != number:
            raise ValueError(f"{name} must be an integer")
        if number < minimum or number > maximum:
            raise ValueError(f"{name} must be between {minimum} and {maximum}")
        return number

    @staticmethod
    def _json_safe(value: Any) -> Any:
        try:
            json.dumps(value, allow_nan=False, separators=(",", ":"))
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("economy result is not strict JSON") from exc
        return value

    def _state_json(self, state: dict[str, Any] | None) -> str:
        value = state or {}
        if not isinstance(value, dict):
            raise ValueError("economy state must be an object")
        self._json_safe(value)
        return self.e._dumps(value)

    def _labor_state_json(self, state: dict[str, Any] | None) -> str:
        value = dict(state or {})
        if "workers_required" in value:
            value["workers_required"] = self._number(
                "workers_required",
                value["workers_required"],
                minimum=0.0,
                maximum=self.MAX_RATE,
            )
        if "occupation" in value:
            value["occupation"] = str(value["occupation"] or "general")[:100]
        return self._state_json(value)

    @staticmethod
    def _canonical_fingerprint(payload: dict[str, Any]) -> str:
        encoded = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _clamp(v: float, lo: float, hi: float) -> float:
        value = EconomyKernel._number("value", v)
        return max(lo, min(hi, value))

    @staticmethod
    def _utc(dt: datetime) -> datetime:
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    def _campaign_time_db(self, db: sqlite3.Connection, campaign_id: str) -> datetime:
        row = db.execute("SELECT world_time FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
        if not row:
            raise KeyError(f"unknown campaign: {campaign_id}")
        return self._utc(datetime.fromisoformat(row["world_time"]))

    def seed_defaults_db(self, db: sqlite3.Connection, campaign_id: str) -> None:
        now = self.e._now()
        db.execute(
            """INSERT INTO economy_config(campaign_id,enabled,default_currency,price_floor_mult,price_ceiling_mult,production_enabled,logistics_enabled,consumption_enabled,updated_at)
               VALUES(?,1,'gp',0.25,4.0,1,1,1,?)
               ON CONFLICT(campaign_id) DO NOTHING""",
            (campaign_id, now),
        )

    def _config_db(self, db: sqlite3.Connection, campaign_id: str) -> sqlite3.Row:
        self.seed_defaults_db(db, campaign_id)
        return db.execute("SELECT * FROM economy_config WHERE campaign_id=?", (campaign_id,)).fetchone()

    def _labor_factor_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        location_id: str,
        state_json: str,
        when: datetime,
    ) -> tuple[float, str, float]:
        """Return the v4.6 aggregate labour productivity modifier.

        Labour is opt-in per producer/extractor through state.workers_required.
        Existing v4.5 definitions therefore retain a factor of 1.0.
        """
        state = self.e._loads(state_json or "{}")
        workers_required = self._number(
            "workers_required",
            state.get("workers_required", 0.0),
            minimum=0.0,
            maximum=self.MAX_RATE,
        )
        occupation = str(state.get("occupation") or "general")[:100]
        if workers_required <= 0:
            return 1.0, occupation, 0.0
        try:
            from .population import PopulationKernel

            population = PopulationKernel(self.e)
            # Economy may run hourly before the daily demographic step. Refresh
            # the bounded labour projection on demand so the first production
            # interval after authoring cannot incorrectly see a zero workforce.
            population.refresh_labor_db(db, campaign_id, location_id, when)
            factor = population.labor_factor_db(
                db, campaign_id, location_id, occupation, workers_required
            )
        except sqlite3.OperationalError:
            factor = 1.0
        factor = self._number("labor_factor", factor, minimum=0.0, maximum=1.0)
        return factor, occupation, workers_required

    def has_activity_db(self, db: sqlite3.Connection, campaign_id: str) -> bool:
        cfg = self._config_db(db, campaign_id)
        if not bool(cfg["enabled"]):
            return False
        row = db.execute(
            """SELECT
                 EXISTS(SELECT 1 FROM economy_markets WHERE campaign_id=? AND active=1) OR
                 EXISTS(SELECT 1 FROM economy_extractors WHERE campaign_id=? AND active=1) OR
                 EXISTS(SELECT 1 FROM economy_producers WHERE campaign_id=? AND active=1) OR
                 EXISTS(SELECT 1 FROM economy_shipments WHERE campaign_id=? AND status='in_transit') AS active""",
            (campaign_id, campaign_id, campaign_id, campaign_id),
        ).fetchone()
        return bool(row["active"])

    def _validate_owner_db(self, db: sqlite3.Connection, campaign_id: str, owner_kind: str, owner_id: str) -> None:
        owner_kind = str(owner_kind).lower()
        if owner_kind not in self.OWNER_KINDS:
            raise ValueError("invalid owner_kind")
        table = {"character": "characters", "npc": "npcs", "faction": "factions", "location": "locations"}[owner_kind]
        if not db.execute(f"SELECT 1 FROM {table} WHERE campaign_id=? AND id=?", (campaign_id, owner_id)).fetchone():
            raise KeyError(f"unknown {owner_kind}: {owner_id}")

    def _owner_location_db(self, db: sqlite3.Connection, campaign_id: str, owner_kind: str, owner_id: str) -> str | None:
        if owner_kind in {"character", "npc"}:
            row = db.execute(f"SELECT location FROM {'characters' if owner_kind == 'character' else 'npcs'} WHERE campaign_id=? AND id=?", (campaign_id, owner_id)).fetchone()
            return str(row["location"]) if row and row["location"] is not None else None
        if owner_kind == "location":
            return owner_id
        return None

    def _inventory_qty_db(self, db: sqlite3.Connection, campaign_id: str, owner_kind: str, owner_id: str, item_id: str) -> float:
        row = db.execute(
            "SELECT qty FROM inventories WHERE campaign_id=? AND owner_kind=? AND owner_id=? AND item_id=?",
            (campaign_id, owner_kind, owner_id, item_id),
        ).fetchone()
        return self._number(
            "inventory qty",
            row["qty"] if row else 0.0,
            minimum=0.0,
            maximum=self.MAX_QUANTITY,
        )

    def _set_inventory_qty_db(self, db: sqlite3.Connection, campaign_id: str, owner_kind: str, owner_id: str, item_id: str, qty: float) -> None:
        qty = self._number("inventory qty", qty, maximum=self.MAX_QUANTITY)
        if qty < -1e-9:
            raise ValueError("inventory cannot become negative")
        qty = max(0.0, qty)
        db.execute(
            """INSERT INTO inventories(campaign_id,owner_kind,owner_id,item_id,qty,metadata_json,updated_at)
               VALUES(?,?,?,?,?,'{}',?)
               ON CONFLICT(campaign_id,owner_kind,owner_id,item_id)
               DO UPDATE SET qty=excluded.qty,updated_at=excluded.updated_at""",
            (campaign_id, owner_kind, owner_id, item_id, qty, self.e._now()),
        )

    def _adjust_inventory_db(self, db: sqlite3.Connection, campaign_id: str, owner_kind: str, owner_id: str, item_id: str, delta: float) -> float:
        old = self._inventory_qty_db(db, campaign_id, owner_kind, owner_id, item_id)
        delta = self._number(
            "inventory delta", delta, minimum=-self.MAX_QUANTITY, maximum=self.MAX_QUANTITY
        )
        new = self._number(
            "resulting inventory qty", old + delta, maximum=self.MAX_QUANTITY
        )
        if new < -1e-9:
            raise ValueError(f"insufficient inventory: {item_id}")
        self._set_inventory_qty_db(db, campaign_id, owner_kind, owner_id, item_id, max(0.0, new))
        return max(0.0, new)

    def _balance_db(self, db: sqlite3.Connection, campaign_id: str, owner_kind: str, owner_id: str, currency_key: str) -> float:
        row = db.execute(
            "SELECT amount FROM owner_balances WHERE campaign_id=? AND owner_kind=? AND owner_id=? AND currency_key=?",
            (campaign_id, owner_kind, owner_id, currency_key),
        ).fetchone()
        return self._number(
            "balance",
            row["amount"] if row else 0.0,
            minimum=0.0,
            maximum=self.MAX_PRICE,
        )

    def _set_balance_db(self, db: sqlite3.Connection, campaign_id: str, owner_kind: str, owner_id: str, currency_key: str, amount: float) -> None:
        amount = self._number("balance", amount, maximum=self.MAX_PRICE)
        if amount < -1e-9:
            raise ValueError("balance cannot become negative")
        db.execute(
            """INSERT INTO owner_balances(campaign_id,owner_kind,owner_id,currency_key,amount,updated_at)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(campaign_id,owner_kind,owner_id,currency_key)
               DO UPDATE SET amount=excluded.amount,updated_at=excluded.updated_at""",
            (campaign_id, owner_kind, owner_id, currency_key, max(0.0, amount), self.e._now()),
        )

    def _transfer_balance_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        from_kind: str,
        from_id: str,
        to_kind: str,
        to_id: str,
        currency_key: str,
        amount: float,
    ) -> None:
        amount = self._number(
            "transfer amount", amount, minimum=0.0, maximum=self.MAX_PRICE
        )
        if amount == 0:
            return
        have = self._balance_db(db, campaign_id, from_kind, from_id, currency_key)
        if have + 1e-9 < amount:
            raise ValueError(f"insufficient funds: {currency_key}")
        recv = self._balance_db(db, campaign_id, to_kind, to_id, currency_key)
        self._set_balance_db(db, campaign_id, from_kind, from_id, currency_key, have - amount)
        self._set_balance_db(db, campaign_id, to_kind, to_id, currency_key, recv + amount)

    def _market_db(self, db: sqlite3.Connection, campaign_id: str, market_id: str) -> sqlite3.Row:
        row = db.execute("SELECT * FROM economy_markets WHERE campaign_id=? AND id=? AND active=1", (campaign_id, market_id)).fetchone()
        if not row:
            raise KeyError(f"unknown or inactive market: {market_id}")
        return row

    def _market_item_db(self, db: sqlite3.Connection, campaign_id: str, market_id: str, item_id: str) -> tuple[sqlite3.Row, sqlite3.Row]:
        market = self._market_db(db, campaign_id, market_id)
        mi = db.execute(
            "SELECT * FROM economy_market_items WHERE campaign_id=? AND market_id=? AND item_id=? AND enabled=1",
            (campaign_id, market_id, item_id),
        ).fetchone()
        if not mi:
            raise KeyError(f"item not traded at market: {item_id}")
        return market, mi

    def _quote_db(self, db: sqlite3.Connection, campaign_id: str, market_id: str, item_id: str) -> dict[str, Any]:
        market, mi = self._market_item_db(db, campaign_id, market_id, item_id)
        item = db.execute("SELECT id,name,base_price FROM item_defs WHERE campaign_id=? AND id=?", (campaign_id, item_id)).fetchone()
        if not item:
            raise KeyError(f"unknown item: {item_id}")
        stock = self._inventory_qty_db(db, campaign_id, market["owner_kind"], market["owner_id"], item_id)
        target = self._number(
            "target_stock", mi["target_stock"], minimum=0.0, maximum=self.MAX_QUANTITY
        )
        scarcity = 0.0 if target <= 0 else self._clamp((target - stock) / max(target, 1e-9), -1.0, 1.0)
        pressure = self._number(
            "demand_pressure", mi["demand_pressure"], minimum=-1.0, maximum=1.0
        )
        raw_mult = 1.0 + 0.75 * scarcity + 0.50 * pressure
        cfg = self._config_db(db, campaign_id)
        floor_mult = max(
            self._number("config price_floor_mult", cfg["price_floor_mult"], minimum=0.0, maximum=self.MAX_RATE, strict_minimum=True),
            self._number("market price floor_mult", mi["floor_mult"], minimum=0.0, maximum=self.MAX_RATE, strict_minimum=True),
        )
        ceiling_mult = min(
            self._number("config price_ceiling_mult", cfg["price_ceiling_mult"], minimum=0.0, maximum=self.MAX_RATE, strict_minimum=True),
            self._number("market price ceiling_mult", mi["ceiling_mult"], minimum=0.0, maximum=self.MAX_RATE, strict_minimum=True),
        )
        if ceiling_mult < floor_mult:
            ceiling_mult = floor_mult
        dynamic_mult = self._clamp(raw_mult, floor_mult, ceiling_mult)
        base = self._number(
            "base_price", item["base_price"] or 0.0, minimum=0.0, maximum=self.MAX_PRICE
        )
        wholesale = self._number(
            "wholesale price", base * dynamic_mult, minimum=0.0, maximum=self.MAX_PRICE
        )
        buy_markup = self._number(
            "buy_markup", market["buy_markup"], minimum=0.0, maximum=self.MAX_RATE, strict_minimum=True
        )
        sell_discount = self._number(
            "sell_discount", market["sell_discount"], minimum=0.0, maximum=self.MAX_RATE
        )
        buy_price = self._number(
            "buy price", wholesale * buy_markup, minimum=0.0, maximum=self.MAX_PRICE
        )
        sell_price = self._number(
            "sell price", wholesale * sell_discount, minimum=0.0, maximum=self.MAX_PRICE
        )
        # Prevent a malformed market configuration from creating an immediate
        # buy-low/sell-high loop against the same market.
        sell_price = min(sell_price, buy_price)
        return self._json_safe({
            "campaign_id": campaign_id,
            "market_id": market_id,
            "location_id": market["location_id"],
            "item_id": item_id,
            "name": item["name"],
            "currency_key": market["currency_key"],
            "base_price": round(base, 6),
            "stock": round(stock, 6),
            "target_stock": round(target, 6),
            "scarcity": round(scarcity, 6),
            "demand_pressure": round(pressure, 6),
            "dynamic_multiplier": round(dynamic_mult, 6),
            "wholesale_price": round(wholesale, 6),
            "buy_price": round(buy_price, 6),
            "sell_price": round(sell_price, 6),
        })

    def quote(self, campaign_id: str, market_id: str, item_id: str) -> dict[str, Any]:
        with self.e._db() as db:
            return self._quote_db(db, campaign_id, market_id, item_id)

    def save_market(
        self,
        campaign_id: str,
        market_id: str,
        location_id: str,
        name: str,
        *,
        owner_kind: str = "location",
        owner_id: str | None = None,
        currency_key: str = "gp",
        buy_markup: float = 1.0,
        sell_discount: float = 0.5,
        visibility: str = "public",
        active: bool = True,
        state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.e._ensure_campaign_exists(campaign_id)
        market_id = self.e._clean_id(market_id)
        location_id = self.e._clean_id(location_id)
        owner_kind = str(owner_kind).lower()
        owner_id = self.e._clean_id(owner_id or location_id)
        buy_markup = self._number(
            "buy_markup", buy_markup, minimum=0.0, maximum=self.MAX_RATE, strict_minimum=True
        )
        sell_discount = self._number(
            "sell_discount", sell_discount, minimum=0.0, maximum=self.MAX_RATE
        )
        if sell_discount > buy_markup:
            raise ValueError("sell_discount cannot exceed buy_markup")
        visibility = str(visibility).strip().lower()
        if visibility not in self.MARKET_VISIBILITIES:
            raise ValueError("visibility must be public, private, or undiscovered")
        state_json = self._state_json(state)
        with self.e._write_db() as db:
            self._validate_owner_db(db, campaign_id, owner_kind, owner_id)
            if not db.execute("SELECT 1 FROM locations WHERE campaign_id=? AND id=?", (campaign_id, location_id)).fetchone():
                raise KeyError(f"unknown location: {location_id}")
            db.execute(
                """INSERT INTO economy_markets(campaign_id,id,location_id,name,owner_kind,owner_id,currency_key,buy_markup,sell_discount,visibility,active,state_json,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(campaign_id,id) DO UPDATE SET location_id=excluded.location_id,name=excluded.name,owner_kind=excluded.owner_kind,
                   owner_id=excluded.owner_id,currency_key=excluded.currency_key,buy_markup=excluded.buy_markup,sell_discount=excluded.sell_discount,
                   visibility=excluded.visibility,active=excluded.active,state_json=excluded.state_json,updated_at=excluded.updated_at""",
                (campaign_id, market_id, location_id, name[:200], owner_kind, owner_id, str(currency_key)[:40], buy_markup, sell_discount, visibility, int(bool(active)), state_json, self.e._now()),
            )
        return self.get_market(campaign_id, market_id)

    def set_market_item(
        self,
        campaign_id: str,
        market_id: str,
        item_id: str,
        *,
        target_stock: float = 10,
        reorder_point: float = 0,
        demand_per_day: float = 0,
        demand_pressure: float = 0,
        floor_mult: float = 0.25,
        ceiling_mult: float = 4.0,
        enabled: bool = True,
        state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        target_stock = self._number("target_stock", target_stock, minimum=0.0, maximum=self.MAX_QUANTITY)
        reorder_point = self._number("reorder_point", reorder_point, minimum=0.0, maximum=self.MAX_QUANTITY)
        demand_per_day = self._number("demand_per_day", demand_per_day, minimum=0.0, maximum=self.MAX_RATE)
        demand_pressure = self._number("demand_pressure", demand_pressure, minimum=-1.0, maximum=1.0)
        floor_mult = self._number("floor_mult", floor_mult, minimum=0.0, maximum=self.MAX_RATE, strict_minimum=True)
        ceiling_mult = self._number("ceiling_mult", ceiling_mult, minimum=0.0, maximum=self.MAX_RATE, strict_minimum=True)
        if ceiling_mult < floor_mult:
            raise ValueError("invalid price multiplier bounds")
        state_json = self._state_json(state)
        with self.e._write_db() as db:
            self._market_db(db, campaign_id, market_id)
            if not db.execute("SELECT 1 FROM item_defs WHERE campaign_id=? AND id=?", (campaign_id, item_id)).fetchone():
                raise KeyError(f"unknown item: {item_id}")
            world_time = self._campaign_time_db(db, campaign_id).isoformat()
            db.execute(
                """INSERT INTO economy_market_items(campaign_id,market_id,item_id,target_stock,reorder_point,demand_per_day,demand_pressure,floor_mult,ceiling_mult,enabled,last_demand_world_time,state_json,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(campaign_id,market_id,item_id) DO UPDATE SET target_stock=excluded.target_stock,reorder_point=excluded.reorder_point,
                   demand_per_day=excluded.demand_per_day,demand_pressure=excluded.demand_pressure,floor_mult=excluded.floor_mult,ceiling_mult=excluded.ceiling_mult,
                   enabled=excluded.enabled,last_demand_world_time=excluded.last_demand_world_time,state_json=excluded.state_json,updated_at=excluded.updated_at""",
                (campaign_id, market_id, item_id, target_stock, reorder_point, demand_per_day, demand_pressure, floor_mult, ceiling_mult, int(bool(enabled)), world_time, state_json, self.e._now()),
            )
        return self.quote(campaign_id, market_id, item_id)

    def get_market(self, campaign_id: str, market_id: str) -> dict[str, Any]:
        with self.e._db() as db:
            row = db.execute("SELECT * FROM economy_markets WHERE campaign_id=? AND id=?", (campaign_id, market_id)).fetchone()
            if not row:
                raise KeyError(f"unknown market: {market_id}")
            data = dict(row)
            data["active"] = bool(data["active"])
            data["state"] = self.e._loads(data.pop("state_json"))
            item_rows = db.execute("SELECT item_id FROM economy_market_items WHERE campaign_id=? AND market_id=? AND enabled=1 ORDER BY item_id", (campaign_id, market_id)).fetchall()
            data["quotes"] = [self._quote_db(db, campaign_id, market_id, r["item_id"]) for r in item_rows]
            data["balance"] = self._balance_db(db, campaign_id, data["owner_kind"], data["owner_id"], data["currency_key"])
            return self._json_safe(data)

    def _record_tx_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        revision: int,
        *,
        kind: str,
        world_time: str,
        tx_key: str | None = None,
        actor_kind: str | None = None,
        actor_id: str | None = None,
        request_fingerprint: str | None = None,
        market_id: str | None = None,
        buyer_kind: str | None = None,
        buyer_id: str | None = None,
        seller_kind: str | None = None,
        seller_id: str | None = None,
        item_id: str | None = None,
        qty: float = 0,
        unit_price: float = 0,
        total: float = 0,
        currency_key: str | None = None,
        result: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        qty = self._number("transaction qty", qty, minimum=0.0, maximum=self.MAX_QUANTITY)
        unit_price = self._number("transaction unit_price", unit_price, minimum=0.0, maximum=self.MAX_PRICE)
        total = self._number("transaction total", total, minimum=0.0, maximum=self.MAX_PRICE)
        result = self._json_safe(result or {})
        metadata = self._json_safe(metadata or {})
        cur = db.execute(
            """INSERT INTO economy_transactions(campaign_id,tx_key,actor_kind,actor_id,request_fingerprint,kind,market_id,buyer_kind,buyer_id,seller_kind,seller_id,item_id,qty,unit_price,total,currency_key,world_time,revision,result_json,metadata_json,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (campaign_id, tx_key, actor_kind, actor_id, request_fingerprint, kind[:80], market_id, buyer_kind, buyer_id, seller_kind, seller_id, item_id, qty, unit_price, total, currency_key, world_time, int(revision), self.e._dumps(result), self.e._dumps(metadata), self.e._now()),
        )
        return int(cur.lastrowid)

    def trade(
        self,
        campaign_id: str,
        action: str,
        market_id: str,
        actor_kind: str,
        actor_id: str,
        item_id: str,
        qty: float = 1,
        *,
        transaction_key: str | None = None,
        reason: str = "market trade",
    ) -> dict[str, Any]:
        action = str(action).lower()
        if action not in {"buy", "sell"}:
            raise ValueError("trade action must be buy or sell")
        qty = self._number(
            "qty", qty, minimum=0.0, maximum=self.MAX_QUANTITY, strict_minimum=True
        )
        actor_kind = str(actor_kind).lower()
        actor_id = self.e._clean_id(actor_id)
        market_id = self.e._clean_id(market_id)
        item_id = self.e._clean_id(item_id)
        reason = str(reason)[:1000]
        tx_key = None
        fingerprint = None
        if transaction_key is not None:
            tx_key = str(transaction_key).strip()
            if not tx_key or len(tx_key) > 200:
                raise ValueError("transaction_key must be 1 to 200 characters")
            fingerprint = self._canonical_fingerprint(
                {
                    "action": action,
                    "actor_id": actor_id,
                    "actor_kind": actor_kind,
                    "item_id": item_id,
                    "market_id": market_id,
                    "qty": qty,
                    "reason": reason,
                }
            )
        with self.e._write_db() as db:
            self._validate_owner_db(db, campaign_id, actor_kind, actor_id)
            if tx_key:
                old = db.execute(
                    """SELECT request_fingerprint,result_json FROM economy_transactions
                       WHERE campaign_id=? AND actor_kind=? AND actor_id=? AND tx_key=?""",
                    (campaign_id, actor_kind, actor_id, tx_key),
                ).fetchone()
                if old:
                    if not old["request_fingerprint"] or old["request_fingerprint"] != fingerprint:
                        raise ValueError("ECONOMY_IDEMPOTENCY_CONFLICT")
                    return self._json_safe(self.e._loads(old["result_json"]))
            market, _mi = self._market_item_db(db, campaign_id, market_id, item_id)
            actor_location = self._owner_location_db(db, campaign_id, actor_kind, actor_id)
            if actor_location is not None and actor_location != market["location_id"]:
                raise ValueError("actor must be at the market location; use logistics for remote movement")
            if actor_kind == market["owner_kind"] and actor_id == market["owner_id"]:
                raise ValueError("market owner cannot trade with itself")
            quote = self._quote_db(db, campaign_id, market_id, item_id)
            currency = str(market["currency_key"])
            if action == "buy":
                unit = float(quote["buy_price"])
                total = self._number(
                    "trade total", unit * qty, minimum=0.0, maximum=self.MAX_PRICE
                )
                market_stock = self._inventory_qty_db(db, campaign_id, market["owner_kind"], market["owner_id"], item_id)
                if market_stock + 1e-9 < qty:
                    raise ValueError("insufficient market stock")
                self._transfer_balance_db(db, campaign_id, actor_kind, actor_id, market["owner_kind"], market["owner_id"], currency, total)
                self._adjust_inventory_db(db, campaign_id, market["owner_kind"], market["owner_id"], item_id, -qty)
                self._adjust_inventory_db(db, campaign_id, actor_kind, actor_id, item_id, qty)
                buyer_kind, buyer_id = actor_kind, actor_id
                seller_kind, seller_id = market["owner_kind"], market["owner_id"]
            else:
                unit = float(quote["sell_price"])
                total = self._number(
                    "trade total", unit * qty, minimum=0.0, maximum=self.MAX_PRICE
                )
                actor_stock = self._inventory_qty_db(db, campaign_id, actor_kind, actor_id, item_id)
                if actor_stock + 1e-9 < qty:
                    raise ValueError("insufficient seller inventory")
                # Finite merchant purse, matching mature VTT merchant behavior.
                self._transfer_balance_db(db, campaign_id, market["owner_kind"], market["owner_id"], actor_kind, actor_id, currency, total)
                self._adjust_inventory_db(db, campaign_id, actor_kind, actor_id, item_id, -qty)
                self._adjust_inventory_db(db, campaign_id, market["owner_kind"], market["owner_id"], item_id, qty)
                buyer_kind, buyer_id = market["owner_kind"], market["owner_id"]
                seller_kind, seller_id = actor_kind, actor_id
            revision = self.e._next_revision(db, campaign_id)
            world_time = self._campaign_time_db(db, campaign_id).isoformat()
            result = self._json_safe({
                "campaign_id": campaign_id,
                "action": action,
                "market_id": market_id,
                "actor_kind": actor_kind,
                "actor_id": actor_id,
                "item_id": item_id,
                "qty": round(qty, 6),
                "unit_price": round(unit, 6),
                "total": round(total, 6),
                "currency_key": currency,
                "actor_balance": round(self._balance_db(db, campaign_id, actor_kind, actor_id, currency), 6),
                "actor_stock": round(self._inventory_qty_db(db, campaign_id, actor_kind, actor_id, item_id), 6),
                "market_stock": round(self._inventory_qty_db(db, campaign_id, market["owner_kind"], market["owner_id"], item_id), 6),
                "revision": revision,
            })
            self._record_tx_db(
                db, campaign_id, revision, kind=f"market_{action}", world_time=world_time, tx_key=tx_key,
                actor_kind=actor_kind, actor_id=actor_id, request_fingerprint=fingerprint,
                market_id=market_id, buyer_kind=buyer_kind, buyer_id=buyer_id, seller_kind=seller_kind, seller_id=seller_id,
                item_id=item_id, qty=qty, unit_price=unit, total=total, currency_key=currency, result=result,
                metadata={"reason": reason},
            )
            self.e._insert_event(
                db, campaign_id, revision, f"economy_{action}", reason, actor_id=actor_id, target_id=market_id,
                region=market["location_id"], payload={"market_id": market_id, "item_id": item_id, "qty": qty, "unit_price": unit, "total": total, "currency_key": currency},
                world_time_override=world_time,
            )
            return self._json_safe(result)

    def save_extractor(
        self,
        campaign_id: str,
        extractor_id: str,
        location_id: str,
        owner_kind: str,
        owner_id: str,
        resource_node_id: str,
        *,
        units_per_day: float = 1,
        max_units_per_step: float = 100,
        active: bool = True,
        state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        units_per_day = self._number("units_per_day", units_per_day, minimum=0.0, maximum=self.MAX_RATE)
        max_units_per_step = self._number("max_units_per_step", max_units_per_step, minimum=0.0, maximum=self.MAX_QUANTITY, strict_minimum=True)
        state_json = self._labor_state_json(state)
        with self.e._write_db() as db:
            self._validate_owner_db(db, campaign_id, owner_kind, owner_id)
            if not db.execute("SELECT 1 FROM locations WHERE campaign_id=? AND id=?", (campaign_id, location_id)).fetchone():
                raise KeyError(f"unknown location: {location_id}")
            node = db.execute("SELECT * FROM resource_nodes WHERE campaign_id=? AND id=?", (campaign_id, resource_node_id)).fetchone()
            if not node:
                raise KeyError(f"unknown resource node: {resource_node_id}")
            if node["location_id"] != location_id:
                raise ValueError("extractor location must match resource node location")
            now_world = self._campaign_time_db(db, campaign_id).isoformat()
            db.execute(
                """INSERT INTO economy_extractors(campaign_id,id,location_id,owner_kind,owner_id,resource_node_id,units_per_day,max_units_per_step,active,last_processed_world_time,state_json,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(campaign_id,id) DO UPDATE SET location_id=excluded.location_id,owner_kind=excluded.owner_kind,owner_id=excluded.owner_id,
                   resource_node_id=excluded.resource_node_id,units_per_day=excluded.units_per_day,max_units_per_step=excluded.max_units_per_step,
                   active=excluded.active,last_processed_world_time=excluded.last_processed_world_time,state_json=excluded.state_json,updated_at=excluded.updated_at""",
                (campaign_id, extractor_id, location_id, owner_kind, owner_id, resource_node_id, units_per_day, max_units_per_step, int(bool(active)), now_world, state_json, self.e._now()),
            )
        return {"campaign_id": campaign_id, "id": extractor_id, "resource_node_id": resource_node_id, "units_per_day": units_per_day, "active": bool(active)}

    def _extractor_step_db(self, db: sqlite3.Connection, campaign_id: str, revision: int, when: datetime, emit: Callable[..., None] | None) -> int:
        cfg = self._config_db(db, campaign_id)
        if not bool(cfg["production_enabled"]):
            return 0
        extracted_events = 0
        rows = db.execute("SELECT * FROM economy_extractors WHERE campaign_id=? AND active=1 ORDER BY id", (campaign_id,)).fetchall()
        for x in rows:
            last = self._utc(datetime.fromisoformat(x["last_processed_world_time"]))
            if when <= last:
                continue
            elapsed_days = (when - last).total_seconds() / 86400.0
            labor_factor, occupation, workers_required = self._labor_factor_db(
                db,
                campaign_id,
                str(x["location_id"]),
                str(x["state_json"] or "{}"),
                when,
            )
            max_units = self._number("max_units_per_step", x["max_units_per_step"], minimum=0.0, maximum=self.MAX_QUANTITY, strict_minimum=True)
            rate = self._number("units_per_day", x["units_per_day"], minimum=0.0, maximum=self.MAX_RATE)
            requested = min(
                max_units,
                self._number("requested extraction", rate * elapsed_days * labor_factor, minimum=0.0, maximum=self.MAX_QUANTITY),
            )
            node = db.execute("SELECT * FROM resource_nodes WHERE campaign_id=? AND id=?", (campaign_id, x["resource_node_id"])).fetchone()
            if not node:
                db.execute("UPDATE economy_extractors SET active=0,updated_at=? WHERE campaign_id=? AND id=?", (self.e._now(), campaign_id, x["id"]))
                continue
            node_qty = self._number("resource node qty", node["qty"], minimum=0.0, maximum=self.MAX_QUANTITY)
            take = min(node_qty, requested)
            if take > 1e-9:
                new_qty = self._number("remaining resource qty", node_qty - take, minimum=0.0, maximum=self.MAX_QUANTITY)
                db.execute("UPDATE resource_nodes SET qty=?,updated_at=? WHERE campaign_id=? AND id=?", (new_qty, self.e._now(), campaign_id, node["id"]))
                self._adjust_inventory_db(db, campaign_id, x["owner_kind"], x["owner_id"], node["item_id"], take)
                extracted_events += 1
                if emit:
                    emit(
                        "economy_resource_extracted",
                        f"Extractor {x['id']} harvested {take:g} {node['item_id']}",
                        {"extractor_id": x["id"], "resource_node_id": node["id"], "item_id": node["item_id"], "qty": take, "remaining": new_qty, "labor_factor": labor_factor, "occupation": occupation, "workers_required": workers_required},
                        x["location_id"],
                        when,
                    )
            db.execute(
                "UPDATE economy_extractors SET last_processed_world_time=?,updated_at=? WHERE campaign_id=? AND id=?",
                (when.isoformat(), self.e._now(), campaign_id, x["id"]),
            )
        return extracted_events

    def save_producer(
        self,
        campaign_id: str,
        producer_id: str,
        location_id: str,
        owner_kind: str,
        owner_id: str,
        recipe_id: str,
        *,
        batches_per_day: float = 1,
        max_batches_per_step: int = 24,
        active: bool = True,
        state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        batches_per_day = self._number("batches_per_day", batches_per_day, minimum=0.0, maximum=self.MAX_RATE)
        max_batches_per_step = self._integer("max_batches_per_step", max_batches_per_step, minimum=1, maximum=1000)
        state_json = self._labor_state_json(state)
        with self.e._write_db() as db:
            self._validate_owner_db(db, campaign_id, owner_kind, owner_id)
            if not db.execute("SELECT 1 FROM locations WHERE campaign_id=? AND id=?", (campaign_id, location_id)).fetchone():
                raise KeyError(f"unknown location: {location_id}")
            if not db.execute("SELECT 1 FROM recipes WHERE campaign_id=? AND id=?", (campaign_id, recipe_id)).fetchone():
                raise KeyError(f"unknown recipe: {recipe_id}")
            now_world = self._campaign_time_db(db, campaign_id).isoformat()
            db.execute(
                """INSERT INTO economy_producers(campaign_id,id,location_id,owner_kind,owner_id,recipe_id,batches_per_day,work_credit,max_batches_per_step,active,last_processed_world_time,state_json,updated_at)
                   VALUES(?,?,?,?,?,?,?,0,?,?,?,?,?)
                   ON CONFLICT(campaign_id,id) DO UPDATE SET location_id=excluded.location_id,owner_kind=excluded.owner_kind,owner_id=excluded.owner_id,
                   recipe_id=excluded.recipe_id,batches_per_day=excluded.batches_per_day,max_batches_per_step=excluded.max_batches_per_step,active=excluded.active,
                   last_processed_world_time=excluded.last_processed_world_time,state_json=excluded.state_json,updated_at=excluded.updated_at""",
                (campaign_id, producer_id, location_id, owner_kind, owner_id, recipe_id, batches_per_day, max_batches_per_step, int(bool(active)), now_world, state_json, self.e._now()),
            )
        return {"campaign_id": campaign_id, "id": producer_id, "recipe_id": recipe_id, "batches_per_day": batches_per_day, "active": bool(active)}

    def _recipe_once_db(self, db: sqlite3.Connection, campaign_id: str, producer: sqlite3.Row) -> dict[str, Any] | None:
        recipe = db.execute("SELECT * FROM recipes WHERE campaign_id=? AND id=?", (campaign_id, producer["recipe_id"])).fetchone()
        if not recipe:
            return None
        inputs = self.e._loads(recipe["inputs_json"] or "{}")
        if not isinstance(inputs, dict):
            raise ValueError("recipe inputs must be an object")
        safe_inputs = {
            str(item_id): self._number(
                f"recipe input {item_id}", qty, minimum=0.0, maximum=self.MAX_QUANTITY
            )
            for item_id, qty in inputs.items()
        }
        for item_id, qty in safe_inputs.items():
            if self._inventory_qty_db(db, campaign_id, producer["owner_kind"], producer["owner_id"], item_id) + 1e-9 < qty:
                return None
        for item_id, qty in safe_inputs.items():
            self._adjust_inventory_db(db, campaign_id, producer["owner_kind"], producer["owner_id"], item_id, -qty)
        output_item = recipe["output_item_id"]
        output_qty = self._number("recipe output_qty", recipe["output_qty"] or 0.0, minimum=0.0, maximum=self.MAX_QUANTITY)
        if output_item and output_qty:
            self._adjust_inventory_db(db, campaign_id, producer["owner_kind"], producer["owner_id"], str(output_item), output_qty)
        return {"recipe_id": recipe["id"], "inputs": safe_inputs, "output_item_id": output_item, "output_qty": output_qty}

    def save_route(
        self,
        campaign_id: str,
        route_id: str,
        from_location_id: str,
        to_location_id: str,
        *,
        travel_hours: float | None = None,
        capacity_qty_per_day: float = 100,
        risk: float = 0,
        cost_per_qty: float = 0,
        carrier_owner_kind: str | None = None,
        carrier_owner_id: str | None = None,
        active: bool = True,
        state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if from_location_id == to_location_id:
            raise ValueError("economy route endpoints must differ")
        with self.e._write_db() as db:
            if not db.execute("SELECT 1 FROM locations WHERE campaign_id=? AND id=?", (campaign_id, from_location_id)).fetchone():
                raise KeyError(f"unknown location: {from_location_id}")
            if not db.execute("SELECT 1 FROM locations WHERE campaign_id=? AND id=?", (campaign_id, to_location_id)).fetchone():
                raise KeyError(f"unknown location: {to_location_id}")
            if travel_hours is None:
                route = self.e._route_locations_db(db, campaign_id, from_location_id, to_location_id)
                if not route["reachable"]:
                    raise ValueError("no world route between economy locations")
                travel_hours = float(route["travel_hours"])
            travel_hours = self._number("travel_hours", travel_hours, minimum=0.0, maximum=self.MAX_TRAVEL_HOURS)
            capacity_qty_per_day = self._number("capacity_qty_per_day", capacity_qty_per_day, minimum=0.0, maximum=self.MAX_QUANTITY)
            risk = self._number("risk", risk, minimum=0.0, maximum=1.0)
            cost_per_qty = self._number("cost_per_qty", cost_per_qty, minimum=0.0, maximum=self.MAX_PRICE)
            state_json = self._state_json(state)
            if carrier_owner_kind or carrier_owner_id:
                if not carrier_owner_kind or not carrier_owner_id:
                    raise ValueError("carrier owner kind/id must be supplied together")
                self._validate_owner_db(db, campaign_id, carrier_owner_kind, carrier_owner_id)
            db.execute(
                """INSERT INTO economy_routes(campaign_id,id,from_location_id,to_location_id,travel_hours,capacity_qty_per_day,risk,cost_per_qty,carrier_owner_kind,carrier_owner_id,active,state_json,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(campaign_id,id) DO UPDATE SET from_location_id=excluded.from_location_id,to_location_id=excluded.to_location_id,travel_hours=excluded.travel_hours,
                   capacity_qty_per_day=excluded.capacity_qty_per_day,risk=excluded.risk,cost_per_qty=excluded.cost_per_qty,carrier_owner_kind=excluded.carrier_owner_kind,
                   carrier_owner_id=excluded.carrier_owner_id,active=excluded.active,state_json=excluded.state_json,updated_at=excluded.updated_at""",
                (campaign_id, route_id, from_location_id, to_location_id, travel_hours, capacity_qty_per_day, risk, cost_per_qty, carrier_owner_kind, carrier_owner_id, int(bool(active)), state_json, self.e._now()),
            )
        return {"campaign_id": campaign_id, "id": route_id, "from": from_location_id, "to": to_location_id, "travel_hours": travel_hours, "capacity_qty_per_day": capacity_qty_per_day, "risk": risk}

    def save_supply_link(
        self,
        campaign_id: str,
        link_id: str,
        source_market_id: str,
        dest_market_id: str,
        item_id: str,
        *,
        reorder_point: float = 2,
        reorder_qty: float = 5,
        source_reserve: float = 2,
        route_id: str | None = None,
        settle: bool = True,
        enabled: bool = True,
        state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if source_market_id == dest_market_id:
            raise ValueError("source and destination markets must differ")
        reorder_point = self._number("reorder_point", reorder_point, minimum=0.0, maximum=self.MAX_QUANTITY)
        reorder_qty = self._number("reorder_qty", reorder_qty, minimum=0.0, maximum=self.MAX_QUANTITY, strict_minimum=True)
        source_reserve = self._number("source_reserve", source_reserve, minimum=0.0, maximum=self.MAX_QUANTITY)
        state_json = self._state_json(state)
        with self.e._write_db() as db:
            self._market_item_db(db, campaign_id, source_market_id, item_id)
            self._market_item_db(db, campaign_id, dest_market_id, item_id)
            if route_id and not db.execute("SELECT 1 FROM economy_routes WHERE campaign_id=? AND id=? AND active=1", (campaign_id, route_id)).fetchone():
                raise KeyError(f"unknown economy route: {route_id}")
            db.execute(
                """INSERT INTO economy_supply_links(campaign_id,id,source_market_id,dest_market_id,item_id,reorder_point,reorder_qty,source_reserve,route_id,settle,enabled,state_json,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(campaign_id,id) DO UPDATE SET source_market_id=excluded.source_market_id,dest_market_id=excluded.dest_market_id,item_id=excluded.item_id,
                   reorder_point=excluded.reorder_point,reorder_qty=excluded.reorder_qty,source_reserve=excluded.source_reserve,route_id=excluded.route_id,
                   settle=excluded.settle,enabled=excluded.enabled,state_json=excluded.state_json,updated_at=excluded.updated_at""",
                (campaign_id, link_id, source_market_id, dest_market_id, item_id, reorder_point, reorder_qty, source_reserve, route_id, int(bool(settle)), int(bool(enabled)), state_json, self.e._now()),
            )
        return {"campaign_id": campaign_id, "id": link_id, "source_market_id": source_market_id, "dest_market_id": dest_market_id, "item_id": item_id, "enabled": bool(enabled)}

    def _route_details_db(self, db: sqlite3.Connection, campaign_id: str, from_location_id: str, to_location_id: str, route_id: str | None) -> dict[str, Any]:
        if route_id:
            row = db.execute("SELECT * FROM economy_routes WHERE campaign_id=? AND id=? AND active=1", (campaign_id, route_id)).fetchone()
            if not row:
                raise KeyError(f"unknown or inactive economy route: {route_id}")
            if row["from_location_id"] != from_location_id or row["to_location_id"] != to_location_id:
                raise ValueError("economy route endpoints do not match shipment")
            return dict(row)
        route = self.e._route_locations_db(db, campaign_id, from_location_id, to_location_id)
        if not route["reachable"]:
            raise ValueError("no world route between shipment locations")
        return {
            "id": None,
            "travel_hours": self._number("travel_hours", route["travel_hours"], minimum=0.0, maximum=self.MAX_TRAVEL_HOURS),
            "capacity_qty_per_day": None,
            "risk": 0.0,
            "cost_per_qty": 0.0,
            "carrier_owner_kind": None,
            "carrier_owner_id": None,
        }

    def _route_capacity_available_db(self, db: sqlite3.Connection, campaign_id: str, route_id: str | None, now: datetime, capacity: float) -> float:
        if not route_id or capacity is None:
            return self.MAX_QUANTITY
        capacity = self._number("route capacity", capacity, minimum=0.0, maximum=self.MAX_QUANTITY)
        since = (now - timedelta(hours=24)).isoformat()
        row = db.execute(
            "SELECT COALESCE(SUM(qty),0) used FROM economy_shipments WHERE campaign_id=? AND route_id=? AND depart_world_time>? AND status IN ('in_transit','delivered','lost')",
            (campaign_id, route_id, since),
        ).fetchone()
        used = self._number("route used capacity", row["used"] or 0.0, minimum=0.0, maximum=self.MAX_QUANTITY)
        reserved = 0.0
        if db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='politics_commitments'"
        ).fetchone():
            reserved_row = db.execute(
                """SELECT COALESCE(SUM(amount-consumed-released),0) AS reserved
                   FROM politics_commitments
                   WHERE campaign_id=? AND resource_kind='route_capacity'
                     AND resource_key=? AND status='reserved'""",
                (campaign_id, route_id),
            ).fetchone()
            reserved = self._number(
                "reserved route capacity",
                reserved_row["reserved"] or 0.0,
                minimum=0.0,
                maximum=self.MAX_QUANTITY,
            )
        return max(0.0, capacity - used - reserved)

    def _rand_keyed_db(self, db: sqlite3.Connection, campaign_id: str, key: str) -> float:
        row = db.execute("SELECT seed FROM sim_config WHERE campaign_id=?", (campaign_id,)).fetchone()
        seed = int(row["seed"]) if row else 0
        digest = hashlib.sha256(f"{seed}|economy|{key}".encode("utf-8")).digest()
        return int.from_bytes(digest[:8], "big") / float(1 << 64)

    def _create_shipment_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        shipment_id: str,
        *,
        from_owner_kind: str,
        from_owner_id: str,
        to_owner_kind: str,
        to_owner_id: str,
        from_location_id: str,
        to_location_id: str,
        item_id: str,
        qty: float,
        route_id: str | None = None,
        supply_link_id: str | None = None,
        currency_key: str = "gp",
        goods_value: float = 0.0,
        payer_kind: str | None = None,
        payer_id: str | None = None,
        payee_kind: str | None = None,
        payee_id: str | None = None,
        state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        qty = self._number("shipment qty", qty, minimum=0.0, maximum=self.MAX_QUANTITY, strict_minimum=True)
        goods_value = self._number("goods_value", goods_value, minimum=0.0, maximum=self.MAX_PRICE)
        state_json = self._state_json(state)
        self._validate_owner_db(db, campaign_id, from_owner_kind, from_owner_id)
        self._validate_owner_db(db, campaign_id, to_owner_kind, to_owner_id)
        if not db.execute("SELECT 1 FROM item_defs WHERE campaign_id=? AND id=?", (campaign_id, item_id)).fetchone():
            raise KeyError(f"unknown item: {item_id}")
        if self._inventory_qty_db(db, campaign_id, from_owner_kind, from_owner_id, item_id) + 1e-9 < qty:
            raise ValueError("insufficient shipment inventory")
        now = self._campaign_time_db(db, campaign_id)
        route = self._route_details_db(db, campaign_id, from_location_id, to_location_id, route_id)
        avail = self._route_capacity_available_db(db, campaign_id, route.get("id"), now, route["capacity_qty_per_day"])
        if qty > avail + 1e-9:
            raise ValueError("route capacity exceeded")
        cost_per_qty = self._number("cost_per_qty", route.get("cost_per_qty") or 0.0, minimum=0.0, maximum=self.MAX_PRICE)
        shipping_cost = self._number("shipping_cost", cost_per_qty * qty, minimum=0.0, maximum=self.MAX_PRICE)
        if goods_value > 0:
            if not (payer_kind and payer_id and payee_kind and payee_id):
                raise ValueError("goods_value requires payer and payee owner identities")
            self._validate_owner_db(db, campaign_id, payer_kind, payer_id)
            self._validate_owner_db(db, campaign_id, payee_kind, payee_id)
            self._transfer_balance_db(db, campaign_id, payer_kind, payer_id, payee_kind, payee_id, currency_key, goods_value)
        if shipping_cost > 0:
            ck, ci = route.get("carrier_owner_kind"), route.get("carrier_owner_id")
            if not ck or not ci:
                raise ValueError("route cost requires a carrier owner")
            payer_kind = payer_kind or from_owner_kind
            payer_id = payer_id or from_owner_id
            self._transfer_balance_db(db, campaign_id, payer_kind, payer_id, ck, ci, currency_key, shipping_cost)
        self._adjust_inventory_db(db, campaign_id, from_owner_kind, from_owner_id, item_id, -qty)
        travel_hours = self._number("travel_hours", route["travel_hours"], minimum=0.0, maximum=self.MAX_TRAVEL_HOURS)
        eta = now + timedelta(hours=travel_hours)
        risk = self._number("risk", route.get("risk") or 0.0, minimum=0.0, maximum=1.0)
        db.execute(
            """INSERT INTO economy_shipments(campaign_id,id,route_id,supply_link_id,from_owner_kind,from_owner_id,to_owner_kind,to_owner_id,from_location_id,to_location_id,item_id,qty,currency_key,goods_value,shipping_cost,risk,status,depart_world_time,eta_world_time,state_json,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (campaign_id, shipment_id, route.get("id"), supply_link_id, from_owner_kind, from_owner_id, to_owner_kind, to_owner_id, from_location_id, to_location_id, item_id, qty, currency_key, goods_value, shipping_cost, risk, "in_transit", now.isoformat(), eta.isoformat(), state_json, self.e._now()),
        )
        return self._json_safe({"campaign_id": campaign_id, "id": shipment_id, "item_id": item_id, "qty": qty, "status": "in_transit", "from_location_id": from_location_id, "to_location_id": to_location_id, "depart_world_time": now.isoformat(), "eta_world_time": eta.isoformat(), "route_id": route.get("id"), "risk": risk, "shipping_cost": shipping_cost, "goods_value": goods_value})

    def create_shipment(self, campaign_id: str, shipment_id: str, **kwargs: Any) -> dict[str, Any]:
        with self.e._write_db() as db:
            result = self._create_shipment_db(db, campaign_id, shipment_id, **kwargs)
            revision = self.e._next_revision(db, campaign_id)
            result["revision"] = revision
            self.e._insert_event(db, campaign_id, revision, "economy_shipment_departed", "Shipment departed", region=result.get("from_location_id"), payload=result, world_time_override=result["depart_world_time"])
            return result

    def _producer_step_db(self, db: sqlite3.Connection, campaign_id: str, revision: int, when: datetime, emit: Callable[..., None] | None) -> int:
        cfg = self._config_db(db, campaign_id)
        if not bool(cfg["production_enabled"]):
            return 0
        produced = 0
        rows = db.execute("SELECT * FROM economy_producers WHERE campaign_id=? AND active=1 ORDER BY id", (campaign_id,)).fetchall()
        for p in rows:
            last = self._utc(datetime.fromisoformat(p["last_processed_world_time"]))
            if when <= last:
                continue
            elapsed_days = (when - last).total_seconds() / 86400.0
            labor_factor, occupation, workers_required = self._labor_factor_db(
                db,
                campaign_id,
                str(p["location_id"]),
                str(p["state_json"] or "{}"),
                when,
            )
            work_credit = self._number("work_credit", p["work_credit"], minimum=0.0, maximum=1001.0)
            batches_per_day = self._number("batches_per_day", p["batches_per_day"], minimum=0.0, maximum=self.MAX_RATE)
            max_batches = self._integer("max_batches_per_step", p["max_batches_per_step"], minimum=1, maximum=1000)
            credit = min(
                self._number("producer credit", work_credit + elapsed_days * batches_per_day * labor_factor, minimum=0.0, maximum=self.MAX_RATE),
                max_batches + 1.0,
            )
            attempts = min(int(math.floor(credit + 1e-12)), max_batches)
            done = 0
            details = None
            for _ in range(attempts):
                details = self._recipe_once_db(db, campaign_id, p)
                if details is None:
                    # Do not accumulate unbounded "free backlog" while a producer
                    # is starved of inputs. Keep at most one batch of readiness.
                    credit = min(credit, 1.0)
                    break
                credit -= 1.0
                done += 1
                produced += 1
            db.execute("UPDATE economy_producers SET work_credit=?,last_processed_world_time=?,updated_at=? WHERE campaign_id=? AND id=?", (max(0.0, credit), when.isoformat(), self.e._now(), campaign_id, p["id"]))
            if done and emit:
                emit("economy_production", f"{p['id']} completed {done} production batch(es)", {"producer_id": p["id"], "recipe_id": p["recipe_id"], "batches": done, "last_batch": details, "labor_factor": labor_factor, "occupation": occupation, "workers_required": workers_required}, p["location_id"], when)
        return produced

    def _demand_step_db(self, db: sqlite3.Connection, campaign_id: str, revision: int, when: datetime, emit: Callable[..., None] | None) -> int:
        cfg = self._config_db(db, campaign_id)
        if not bool(cfg["consumption_enabled"]):
            return 0
        count = 0
        rows = db.execute(
            """SELECT mi.*,m.owner_kind,m.owner_id,m.location_id,m.currency_key
               FROM economy_market_items mi JOIN economy_markets m ON m.campaign_id=mi.campaign_id AND m.id=mi.market_id
               WHERE mi.campaign_id=? AND mi.enabled=1 AND m.active=1 ORDER BY mi.market_id,mi.item_id""",
            (campaign_id,),
        ).fetchall()
        for r in rows:
            last_s = r["last_demand_world_time"]
            last = self._utc(datetime.fromisoformat(last_s)) if last_s else when
            if when <= last:
                continue
            days = (when - last).total_seconds() / 86400.0
            demand_rate = self._number("demand_per_day", r["demand_per_day"], minimum=0.0, maximum=self.MAX_RATE)
            desired = self._number("desired demand", demand_rate * days, minimum=0.0, maximum=self.MAX_QUANTITY)
            stock = self._inventory_qty_db(db, campaign_id, r["owner_kind"], r["owner_id"], r["item_id"])
            served = min(stock, desired)
            if served > 0:
                self._adjust_inventory_db(db, campaign_id, r["owner_kind"], r["owner_id"], r["item_id"], -served)
            if desired > 1e-12:
                count += 1
            shortage = 0.0 if desired <= 1e-12 else max(0.0, (desired - served) / desired)
            target = max(self._number("target_stock", r["target_stock"], minimum=0.0, maximum=self.MAX_QUANTITY), 1e-9)
            post_stock = stock - served
            surplus = max(0.0, (post_stock - target) / target)
            old_p = self._number("demand_pressure", r["demand_pressure"], minimum=-1.0, maximum=1.0)
            # Pressure decays toward zero, rises with unmet demand, and eases
            # slowly under clear surplus. This is bounded and deterministic.
            pressure = self._clamp(old_p * math.exp(-0.20 * days) + 0.60 * shortage - 0.12 * min(1.0, surplus) * days, -1.0, 1.0)
            db.execute(
                "UPDATE economy_market_items SET demand_pressure=?,last_demand_world_time=?,updated_at=? WHERE campaign_id=? AND market_id=? AND item_id=?",
                (pressure, when.isoformat(), self.e._now(), campaign_id, r["market_id"], r["item_id"]),
            )
            if (served > 0 or shortage > 0) and emit:
                emit("economy_consumption", f"Demand changed {r['item_id']} stock at {r['market_id']}", {"market_id": r["market_id"], "item_id": r["item_id"], "desired": desired, "served": served, "shortage": shortage, "demand_pressure": pressure}, r["location_id"], when)
        return count

    def _shipment_delivery_step_db(self, db: sqlite3.Connection, campaign_id: str, revision: int, when: datetime, emit: Callable[..., None] | None) -> tuple[int, int]:
        delivered = lost = 0
        rows = db.execute("SELECT * FROM economy_shipments WHERE campaign_id=? AND status='in_transit' AND eta_world_time<=? ORDER BY eta_world_time,id", (campaign_id, when.isoformat())).fetchall()
        for s in rows:
            risk = self._number("shipment risk", s["risk"], minimum=0.0, maximum=1.0)
            shipment_qty = self._number("shipment qty", s["qty"], minimum=0.0, maximum=self.MAX_QUANTITY, strict_minimum=True)
            u = self._rand_keyed_db(db, campaign_id, f"shipment:{s['id']}:{s['depart_world_time']}:{s['eta_world_time']}")
            if u < risk:
                status = "lost"
                lost += 1
                event_type = "economy_shipment_lost"
                summary = f"Shipment {s['id']} was lost in transit"
            else:
                self._adjust_inventory_db(db, campaign_id, s["to_owner_kind"], s["to_owner_id"], s["item_id"], shipment_qty)
                status = "delivered"
                delivered += 1
                event_type = "economy_shipment_delivered"
                summary = f"Shipment {s['id']} arrived"
            db.execute("UPDATE economy_shipments SET status=?,updated_at=? WHERE campaign_id=? AND id=?", (status, self.e._now(), campaign_id, s["id"]))
            if emit:
                emit(event_type, summary, {"shipment_id": s["id"], "item_id": s["item_id"], "qty": shipment_qty, "from_location_id": s["from_location_id"], "to_location_id": s["to_location_id"], "risk": risk}, s["to_location_id"], when)
        return delivered, lost

    def _supply_step_db(self, db: sqlite3.Connection, campaign_id: str, revision: int, when: datetime, emit: Callable[..., None] | None) -> int:
        cfg = self._config_db(db, campaign_id)
        if not bool(cfg["logistics_enabled"]):
            return 0
        created = 0
        rows = db.execute("SELECT * FROM economy_supply_links WHERE campaign_id=? AND enabled=1 ORDER BY id", (campaign_id,)).fetchall()
        for link in rows:
            in_flight = db.execute("SELECT 1 FROM economy_shipments WHERE campaign_id=? AND supply_link_id=? AND status='in_transit' LIMIT 1", (campaign_id, link["id"])).fetchone()
            if in_flight:
                continue
            source = self._market_db(db, campaign_id, link["source_market_id"])
            dest = self._market_db(db, campaign_id, link["dest_market_id"])
            item_id = link["item_id"]
            dest_stock = self._inventory_qty_db(db, campaign_id, dest["owner_kind"], dest["owner_id"], item_id)
            reorder_point = self._number("reorder_point", link["reorder_point"], minimum=0.0, maximum=self.MAX_QUANTITY)
            if dest_stock > reorder_point + 1e-9:
                continue
            source_stock = self._inventory_qty_db(db, campaign_id, source["owner_kind"], source["owner_id"], item_id)
            reserve = self._number("source_reserve", link["source_reserve"], minimum=0.0, maximum=self.MAX_QUANTITY)
            reorder_qty = self._number("reorder_qty", link["reorder_qty"], minimum=0.0, maximum=self.MAX_QUANTITY, strict_minimum=True)
            available = max(0.0, source_stock - reserve)
            qty = min(reorder_qty, available)
            if qty <= 1e-9:
                continue
            source_quote = self._quote_db(db, campaign_id, source["id"], item_id)
            currency = source["currency_key"]
            goods_value = float(source_quote["wholesale_price"]) * qty if bool(link["settle"]) else 0.0
            if goods_value > 0:
                funds = self._balance_db(db, campaign_id, dest["owner_kind"], dest["owner_id"], currency)
                if funds + 1e-9 < goods_value:
                    continue
            if link["route_id"]:
                route_row = db.execute(
                    """SELECT travel_hours,capacity_qty_per_day,risk,cost_per_qty
                       FROM economy_routes
                       WHERE campaign_id=? AND id=? AND active=1""",
                    (campaign_id, link["route_id"]),
                ).fetchone()
                if not route_row:
                    continue
                self._number("travel_hours", route_row["travel_hours"], minimum=0.0, maximum=self.MAX_TRAVEL_HOURS)
                self._number("capacity_qty_per_day", route_row["capacity_qty_per_day"], minimum=0.0, maximum=self.MAX_QUANTITY)
                self._number("risk", route_row["risk"], minimum=0.0, maximum=1.0)
                self._number("cost_per_qty", route_row["cost_per_qty"], minimum=0.0, maximum=self.MAX_PRICE)
            shipment_id = f"auto:{link['id']}:{when.isoformat()}"
            try:
                result = self._create_shipment_db(
                    db, campaign_id, shipment_id,
                    from_owner_kind=source["owner_kind"], from_owner_id=source["owner_id"],
                    to_owner_kind=dest["owner_kind"], to_owner_id=dest["owner_id"],
                    from_location_id=source["location_id"], to_location_id=dest["location_id"],
                    item_id=item_id, qty=qty, route_id=link["route_id"], supply_link_id=link["id"], currency_key=currency,
                    goods_value=goods_value,
                    payer_kind=dest["owner_kind"] if goods_value else None, payer_id=dest["owner_id"] if goods_value else None,
                    payee_kind=source["owner_kind"] if goods_value else None, payee_id=source["owner_id"] if goods_value else None,
                    state={"automatic": True},
                )
            except (ValueError, KeyError):
                continue
            created += 1
            if emit:
                emit("economy_shipment_departed", f"Supply shipment {shipment_id} departed", result, source["location_id"], when)
        return created

    def step_db(self, db: sqlite3.Connection, campaign_id: str, revision: int, when: datetime, *, emit: Callable[..., None] | None = None) -> dict[str, int]:
        when = self._utc(when)
        if when.minute or when.second or when.microsecond:
            raise ValueError("economy step requires a canonical UTC hour boundary")
        cfg = self._config_db(db, campaign_id)
        tally = {"extraction": 0, "production": 0, "consumption": 0, "shipments_created": 0, "shipments_delivered": 0, "shipments_lost": 0}
        if not bool(cfg["enabled"]):
            return tally
        # Boundary contract: arrivals/losses are resolved before this hour's
        # extraction and production; demand can consume arrived/produced goods;
        # new supply orders are created only after current demand is observed.
        delivered, lost = self._shipment_delivery_step_db(db, campaign_id, revision, when, emit)
        tally["shipments_delivered"], tally["shipments_lost"] = delivered, lost
        tally["extraction"] = self._extractor_step_db(db, campaign_id, revision, when, emit)
        tally["production"] = self._producer_step_db(db, campaign_id, revision, when, emit)
        tally["consumption"] = self._demand_step_db(db, campaign_id, revision, when, emit)
        tally["shipments_created"] = self._supply_step_db(db, campaign_id, revision, when, emit)
        return self._json_safe(tally)

    def snapshot_db(self, db: sqlite3.Connection, campaign_id: str, *, location_id: str | None = None, market_id: str | None = None, limit: int = 50) -> dict[str, Any]:
        params: list[Any] = [campaign_id]
        where = "campaign_id=? AND active=1"
        if location_id:
            where += " AND location_id=?"
            params.append(location_id)
        if market_id:
            where += " AND id=?"
            params.append(market_id)
        market_rows = db.execute(f"SELECT * FROM economy_markets WHERE {where} ORDER BY id LIMIT ?", (*params, max(1, min(int(limit), 100)))).fetchall()
        markets = []
        quotes = []
        for row in market_rows:
            d = dict(row)
            d["state"] = self.e._loads(d.pop("state_json"))
            d["active"] = bool(d["active"])
            d["balance"] = self._balance_db(db, campaign_id, d["owner_kind"], d["owner_id"], d["currency_key"])
            item_rows = db.execute("SELECT item_id FROM economy_market_items WHERE campaign_id=? AND market_id=? AND enabled=1 ORDER BY item_id", (campaign_id, d["id"])).fetchall()
            d["item_count"] = len(item_rows)
            markets.append(d)
            for ir in item_rows:
                quotes.append(self._quote_db(db, campaign_id, d["id"], ir["item_id"]))
        extractor_where = "campaign_id=? AND active=1" + (" AND location_id=?" if location_id else "")
        extractor_params = (campaign_id, location_id) if location_id else (campaign_id,)
        extractors = []
        for r in db.execute(f"SELECT * FROM economy_extractors WHERE {extractor_where} ORDER BY id LIMIT 100", extractor_params).fetchall():
            d = dict(r); d["active"] = bool(d["active"]); d["state"] = self.e._loads(d.pop("state_json")); extractors.append(d)
        producer_where = "campaign_id=? AND active=1" + (" AND location_id=?" if location_id else "")
        producer_params = (campaign_id, location_id) if location_id else (campaign_id,)
        producers = []
        for r in db.execute(f"SELECT * FROM economy_producers WHERE {producer_where} ORDER BY id LIMIT 100", producer_params).fetchall():
            d = dict(r); d["active"] = bool(d["active"]); d["state"] = self.e._loads(d.pop("state_json")); producers.append(d)
        shipment_where = "campaign_id=?" + (" AND (from_location_id=? OR to_location_id=?)" if location_id else "")
        shipment_params = (campaign_id, location_id, location_id) if location_id else (campaign_id,)
        shipments = []
        for r in db.execute(f"SELECT * FROM economy_shipments WHERE {shipment_where} ORDER BY depart_world_time DESC,id DESC LIMIT 100", shipment_params).fetchall():
            d = dict(r); d["state"] = self.e._loads(d.pop("state_json")); shipments.append(d)
        tx_rows = db.execute("SELECT * FROM economy_transactions WHERE campaign_id=? ORDER BY id DESC LIMIT 30", (campaign_id,)).fetchall()
        tx = []
        for r in tx_rows:
            d = dict(r); d["result"] = self.e._loads(d.pop("result_json")); d["metadata"] = self.e._loads(d.pop("metadata_json")); tx.append(d)
        return {"campaign_id": campaign_id, "location_id": location_id, "markets": markets, "quotes": quotes, "extractors": extractors, "producers": producers, "shipments": shipments, "recent_transactions": tx}

    @staticmethod
    def _public_quote_payload(quote: dict[str, Any]) -> dict[str, Any]:
        return {k: quote[k] for k in (
            "campaign_id", "market_id", "location_id", "item_id", "name", "currency_key",
            "stock", "buy_price", "sell_price",
        ) if k in quote}

    def _authoring_insert_or_match_db(
        self,
        db: sqlite3.Connection,
        *,
        section: str,
        table: str,
        key: dict[str, Any],
        values: dict[str, Any],
    ) -> bool:
        where = " AND ".join(f"{column}=?" for column in key)
        existing = db.execute(
            f"SELECT * FROM {table} WHERE {where}", tuple(key.values())
        ).fetchone()
        if existing:
            for column, expected in values.items():
                actual = existing[column]
                if actual != expected:
                    raise ValueError(
                        f"ECONOMY_AUTHORING_CONFLICT:{section}:"
                        + ":".join(str(v) for v in key.values())
                    )
            return False
        insert_values = {**key, **values, "updated_at": self.e._now()}
        columns = ",".join(insert_values)
        placeholders = ",".join("?" for _ in insert_values)
        db.execute(
            f"INSERT INTO {table}({columns}) VALUES({placeholders})",
            tuple(insert_values.values()),
        )
        return True

    @staticmethod
    def _authoring_rows(
        sections: dict[str, Any], section: str
    ) -> list[dict[str, Any]]:
        raw = sections.get(section, [])
        if raw is None:
            return []
        if not isinstance(raw, list) or not all(isinstance(row, dict) for row in raw):
            raise ValueError(f"{section} must be a list of objects")
        return [dict(row) for row in raw]

    def promote_records_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        sections: dict[str, Any],
    ) -> dict[str, Any]:
        """Install generated economy records without owning the transaction.

        The caller supplies an already-open writer and decides whether to
        commit or roll back. Every row is insert-or-identical; a conflicting
        canonical row fails the entire caller transaction closed.
        """
        if not isinstance(sections, dict):
            raise ValueError("economy sections must be an object")
        ordered_sections = (
            "economy_markets",
            "economy_market_items",
            "economy_extractors",
            "economy_producers",
            "economy_routes",
            "economy_supply_links",
            "economy_inventories",
            "economy_balances",
        )
        allowed_sections = set(ordered_sections)
        unknown = sorted(set(sections) - allowed_sections)
        if unknown:
            raise ValueError(f"unknown economy authoring section: {unknown[0]}")
        if not db.execute("SELECT 1 FROM campaigns WHERE id=?", (campaign_id,)).fetchone():
            raise KeyError(f"unknown campaign: {campaign_id}")
        migrate_economy_schema_db(db)
        world_time = self._campaign_time_db(db, campaign_id).isoformat()
        counts = {
            section: {"inserted": 0, "unchanged": 0}
            for section in ordered_sections
        }

        def record(section: str, inserted: bool) -> None:
            counts[section]["inserted" if inserted else "unchanged"] += 1

        for row in self._authoring_rows(sections, "economy_markets"):
            market_id = self.e._clean_id(row["id"])
            location_id = self.e._clean_id(row["location_id"])
            owner_kind = str(row.get("owner_kind", "location")).lower()
            owner_id = self.e._clean_id(row.get("owner_id") or location_id)
            self._validate_owner_db(db, campaign_id, owner_kind, owner_id)
            if not db.execute(
                "SELECT 1 FROM locations WHERE campaign_id=? AND id=?",
                (campaign_id, location_id),
            ).fetchone():
                raise KeyError(f"unknown location: {location_id}")
            visibility = str(row.get("visibility", "public")).lower()
            if visibility not in self.MARKET_VISIBILITIES:
                raise ValueError("visibility must be public, private, or undiscovered")
            buy_markup = self._number(
                "buy_markup", row.get("buy_markup", 1.0), minimum=0.0,
                maximum=self.MAX_RATE, strict_minimum=True
            )
            sell_discount = self._number(
                "sell_discount", row.get("sell_discount", 0.5), minimum=0.0,
                maximum=self.MAX_RATE
            )
            if sell_discount > buy_markup:
                raise ValueError("sell_discount cannot exceed buy_markup")
            inserted = self._authoring_insert_or_match_db(
                db,
                section="economy_markets",
                table="economy_markets",
                key={"campaign_id": campaign_id, "id": market_id},
                values={
                    "location_id": location_id,
                    "name": str(row.get("name") or market_id)[:200],
                    "owner_kind": owner_kind,
                    "owner_id": owner_id,
                    "currency_key": str(row.get("currency_key", "gp"))[:40],
                    "buy_markup": buy_markup,
                    "sell_discount": sell_discount,
                    "visibility": visibility,
                    "active": int(bool(row.get("active", True))),
                    "state_json": self._state_json(row.get("state")),
                },
            )
            record("economy_markets", inserted)

        for row in self._authoring_rows(sections, "economy_market_items"):
            market_id = self.e._clean_id(row["market_id"])
            item_id = self.e._clean_id(row["item_id"])
            self._market_db(db, campaign_id, market_id)
            if not db.execute(
                "SELECT 1 FROM item_defs WHERE campaign_id=? AND id=?",
                (campaign_id, item_id),
            ).fetchone():
                raise KeyError(f"unknown item: {item_id}")
            floor_mult = self._number(
                "floor_mult", row.get("floor_mult", 0.25), minimum=0.0,
                maximum=self.MAX_RATE, strict_minimum=True
            )
            ceiling_mult = self._number(
                "ceiling_mult", row.get("ceiling_mult", 4.0), minimum=0.0,
                maximum=self.MAX_RATE, strict_minimum=True
            )
            if ceiling_mult < floor_mult:
                raise ValueError("invalid price multiplier bounds")
            inserted = self._authoring_insert_or_match_db(
                db,
                section="economy_market_items",
                table="economy_market_items",
                key={
                    "campaign_id": campaign_id,
                    "market_id": market_id,
                    "item_id": item_id,
                },
                values={
                    "target_stock": self._number("target_stock", row.get("target_stock", 10), minimum=0.0, maximum=self.MAX_QUANTITY),
                    "reorder_point": self._number("reorder_point", row.get("reorder_point", 0), minimum=0.0, maximum=self.MAX_QUANTITY),
                    "demand_per_day": self._number("demand_per_day", row.get("demand_per_day", 0), minimum=0.0, maximum=self.MAX_RATE),
                    "demand_pressure": self._number("demand_pressure", row.get("demand_pressure", 0), minimum=-1.0, maximum=1.0),
                    "floor_mult": floor_mult,
                    "ceiling_mult": ceiling_mult,
                    "enabled": int(bool(row.get("enabled", True))),
                    "last_demand_world_time": world_time,
                    "state_json": self._state_json(row.get("state")),
                },
            )
            record("economy_market_items", inserted)

        for row in self._authoring_rows(sections, "economy_extractors"):
            extractor_id = self.e._clean_id(row["id"])
            location_id = self.e._clean_id(row["location_id"])
            owner_kind = str(row["owner_kind"]).lower()
            owner_id = self.e._clean_id(row["owner_id"])
            node_id = self.e._clean_id(row["resource_node_id"])
            self._validate_owner_db(db, campaign_id, owner_kind, owner_id)
            node = db.execute(
                "SELECT location_id FROM resource_nodes WHERE campaign_id=? AND id=?",
                (campaign_id, node_id),
            ).fetchone()
            if not node:
                raise KeyError(f"unknown resource node: {node_id}")
            if node["location_id"] != location_id:
                raise ValueError("extractor location must match resource node location")
            inserted = self._authoring_insert_or_match_db(
                db,
                section="economy_extractors",
                table="economy_extractors",
                key={"campaign_id": campaign_id, "id": extractor_id},
                values={
                    "location_id": location_id,
                    "owner_kind": owner_kind,
                    "owner_id": owner_id,
                    "resource_node_id": node_id,
                    "units_per_day": self._number("units_per_day", row.get("units_per_day", 1), minimum=0.0, maximum=self.MAX_RATE),
                    "max_units_per_step": self._number("max_units_per_step", row.get("max_units_per_step", 100), minimum=0.0, maximum=self.MAX_QUANTITY, strict_minimum=True),
                    "active": int(bool(row.get("active", True))),
                    "last_processed_world_time": world_time,
                    "state_json": self._labor_state_json(row.get("state")),
                },
            )
            record("economy_extractors", inserted)

        for row in self._authoring_rows(sections, "economy_producers"):
            producer_id = self.e._clean_id(row["id"])
            location_id = self.e._clean_id(row["location_id"])
            owner_kind = str(row["owner_kind"]).lower()
            owner_id = self.e._clean_id(row["owner_id"])
            recipe_id = self.e._clean_id(row["recipe_id"])
            self._validate_owner_db(db, campaign_id, owner_kind, owner_id)
            if not db.execute(
                "SELECT 1 FROM locations WHERE campaign_id=? AND id=?",
                (campaign_id, location_id),
            ).fetchone():
                raise KeyError(f"unknown location: {location_id}")
            if not db.execute(
                "SELECT 1 FROM recipes WHERE campaign_id=? AND id=?",
                (campaign_id, recipe_id),
            ).fetchone():
                raise KeyError(f"unknown recipe: {recipe_id}")
            inserted = self._authoring_insert_or_match_db(
                db,
                section="economy_producers",
                table="economy_producers",
                key={"campaign_id": campaign_id, "id": producer_id},
                values={
                    "location_id": location_id,
                    "owner_kind": owner_kind,
                    "owner_id": owner_id,
                    "recipe_id": recipe_id,
                    "batches_per_day": self._number("batches_per_day", row.get("batches_per_day", 1), minimum=0.0, maximum=self.MAX_RATE),
                    "work_credit": self._number("work_credit", row.get("work_credit", 0), minimum=0.0, maximum=1001.0),
                    "max_batches_per_step": self._integer("max_batches_per_step", row.get("max_batches_per_step", 24), minimum=1, maximum=1000),
                    "active": int(bool(row.get("active", True))),
                    "last_processed_world_time": world_time,
                    "state_json": self._labor_state_json(row.get("state")),
                },
            )
            record("economy_producers", inserted)

        for row in self._authoring_rows(sections, "economy_routes"):
            route_id = self.e._clean_id(row["id"])
            from_id = self.e._clean_id(row["from_location_id"])
            to_id = self.e._clean_id(row["to_location_id"])
            for location_id in (from_id, to_id):
                if not db.execute(
                    "SELECT 1 FROM locations WHERE campaign_id=? AND id=?",
                    (campaign_id, location_id),
                ).fetchone():
                    raise KeyError(f"unknown location: {location_id}")
            carrier_kind = row.get("carrier_owner_kind")
            carrier_id = row.get("carrier_owner_id")
            if carrier_kind is not None or carrier_id is not None:
                if not carrier_kind or not carrier_id:
                    raise ValueError("carrier owner kind/id must be supplied together")
                carrier_kind = str(carrier_kind).lower()
                carrier_id = self.e._clean_id(carrier_id)
                self._validate_owner_db(db, campaign_id, carrier_kind, carrier_id)
            inserted = self._authoring_insert_or_match_db(
                db,
                section="economy_routes",
                table="economy_routes",
                key={"campaign_id": campaign_id, "id": route_id},
                values={
                    "from_location_id": from_id,
                    "to_location_id": to_id,
                    "travel_hours": self._number("travel_hours", row["travel_hours"], minimum=0.0, maximum=self.MAX_TRAVEL_HOURS),
                    "capacity_qty_per_day": self._number("capacity_qty_per_day", row.get("capacity_qty_per_day", 100), minimum=0.0, maximum=self.MAX_QUANTITY),
                    "risk": self._number("risk", row.get("risk", 0), minimum=0.0, maximum=1.0),
                    "cost_per_qty": self._number("cost_per_qty", row.get("cost_per_qty", 0), minimum=0.0, maximum=self.MAX_PRICE),
                    "carrier_owner_kind": carrier_kind,
                    "carrier_owner_id": carrier_id,
                    "active": int(bool(row.get("active", True))),
                    "state_json": self._state_json(row.get("state")),
                },
            )
            record("economy_routes", inserted)

        for row in self._authoring_rows(sections, "economy_supply_links"):
            link_id = self.e._clean_id(row["id"])
            source_market_id = self.e._clean_id(row["source_market_id"])
            dest_market_id = self.e._clean_id(row["dest_market_id"])
            item_id = self.e._clean_id(row["item_id"])
            if source_market_id == dest_market_id:
                raise ValueError("source and destination markets must differ")
            self._market_item_db(db, campaign_id, source_market_id, item_id)
            self._market_item_db(db, campaign_id, dest_market_id, item_id)
            route_id = row.get("route_id")
            if route_id is not None:
                route_id = self.e._clean_id(route_id)
                if not db.execute(
                    "SELECT 1 FROM economy_routes WHERE campaign_id=? AND id=? AND active=1",
                    (campaign_id, route_id),
                ).fetchone():
                    raise KeyError(f"unknown economy route: {route_id}")
            inserted = self._authoring_insert_or_match_db(
                db,
                section="economy_supply_links",
                table="economy_supply_links",
                key={"campaign_id": campaign_id, "id": link_id},
                values={
                    "source_market_id": source_market_id,
                    "dest_market_id": dest_market_id,
                    "item_id": item_id,
                    "reorder_point": self._number("reorder_point", row.get("reorder_point", 2), minimum=0.0, maximum=self.MAX_QUANTITY),
                    "reorder_qty": self._number("reorder_qty", row.get("reorder_qty", 5), minimum=0.0, maximum=self.MAX_QUANTITY, strict_minimum=True),
                    "source_reserve": self._number("source_reserve", row.get("source_reserve", 2), minimum=0.0, maximum=self.MAX_QUANTITY),
                    "route_id": route_id,
                    "settle": int(bool(row.get("settle", True))),
                    "enabled": int(bool(row.get("enabled", True))),
                    "state_json": self._state_json(row.get("state")),
                },
            )
            record("economy_supply_links", inserted)

        for section, table, amount_key, value_name, maximum in (
            ("economy_inventories", "inventories", "qty", "inventory qty", self.MAX_QUANTITY),
            ("economy_balances", "owner_balances", "amount", "balance", self.MAX_PRICE),
        ):
            for row in self._authoring_rows(sections, section):
                owner_kind = str(row["owner_kind"]).lower()
                owner_id = self.e._clean_id(row["owner_id"])
                self._validate_owner_db(db, campaign_id, owner_kind, owner_id)
                if section == "economy_inventories":
                    item_id = self.e._clean_id(row["item_id"])
                    if not db.execute(
                        "SELECT 1 FROM item_defs WHERE campaign_id=? AND id=?",
                        (campaign_id, item_id),
                    ).fetchone():
                        raise KeyError(f"unknown item: {item_id}")
                    key = {
                        "campaign_id": campaign_id,
                        "owner_kind": owner_kind,
                        "owner_id": owner_id,
                        "item_id": item_id,
                    }
                    values = {
                        "qty": self._number(value_name, row[amount_key], minimum=0.0, maximum=maximum),
                        "metadata_json": self._state_json(row.get("metadata")),
                    }
                else:
                    currency_key = str(row.get("currency_key", "gp"))[:40]
                    if not currency_key:
                        raise ValueError("currency_key is required")
                    key = {
                        "campaign_id": campaign_id,
                        "owner_kind": owner_kind,
                        "owner_id": owner_id,
                        "currency_key": currency_key,
                    }
                    values = {
                        "amount": self._number(value_name, row[amount_key], minimum=0.0, maximum=maximum)
                    }
                inserted = self._authoring_insert_or_match_db(
                    db, section=section, table=table, key=key, values=values
                )
                record(section, inserted)

        return self._json_safe({"campaign_id": campaign_id, "sections": counts})

    def public_snapshot_db(
        self,
        db: sqlite3.Connection,
        campaign_id: str,
        *,
        location_id: str | None = None,
        market_id: str | None = None,
        limit: int = 50,
        quote_limit: int = 200,
    ) -> dict[str, Any]:
        limit = self._integer("limit", limit, minimum=1, maximum=100)
        quote_limit = self._integer(
            "quote_limit", quote_limit, minimum=1, maximum=self.MAX_PUBLIC_QUOTES
        )
        params: list[Any] = [campaign_id]
        where = "campaign_id=? AND active=1 AND visibility='public'"
        if location_id:
            where += " AND location_id=?"
            params.append(location_id)
        if market_id:
            where += " AND id=?"
            params.append(market_id)
        rows = db.execute(
            f"SELECT id,location_id,name,currency_key FROM economy_markets WHERE {where} ORDER BY id LIMIT ?",
            (*params, limit),
        ).fetchall()
        markets: list[dict[str, Any]] = []
        quotes: list[dict[str, Any]] = []
        for row in rows:
            market = dict(row)
            remaining = quote_limit - len(quotes)
            item_rows = db.execute(
                """SELECT item_id FROM economy_market_items
                   WHERE campaign_id=? AND market_id=? AND enabled=1
                   ORDER BY item_id LIMIT ?""",
                (campaign_id, row["id"], max(0, remaining)),
            ).fetchall()
            total_items = int(
                db.execute(
                    """SELECT COUNT(*) AS n FROM economy_market_items
                       WHERE campaign_id=? AND market_id=? AND enabled=1""",
                    (campaign_id, row["id"]),
                ).fetchone()["n"]
            )
            market["item_count"] = total_items
            markets.append(market)
            for ir in item_rows:
                quotes.append(self._public_quote_payload(self._quote_db(db, campaign_id, row["id"], ir["item_id"])))
        return self._json_safe(
            {
                "campaign_id": campaign_id,
                "location_id": location_id,
                "markets": markets,
                "quotes": quotes,
                "quote_limit": quote_limit,
                "quotes_truncated": sum(m["item_count"] for m in markets) > len(quotes),
            }
        )

    def public_snapshot(self, campaign_id: str, **kwargs: Any) -> dict[str, Any]:
        with self.e._db() as db:
            return self._json_safe(self.public_snapshot_db(db, campaign_id, **kwargs))

    def snapshot(self, campaign_id: str, **kwargs: Any) -> dict[str, Any]:
        with self.e._db() as db:
            return self.snapshot_db(db, campaign_id, **kwargs)

    def interact(
        self,
        campaign_id: str,
        *,
        action: str,
        actor_kind: str,
        actor_id: str,
        market_id: str,
        item_id: str | None = None,
        qty: float = 1,
        transaction_key: str | None = None,
        reason: str = "market interaction",
    ) -> dict[str, Any]:
        action = str(action or "inspect").lower()
        actor_kind = str(actor_kind).lower()
        with self.e._db() as db:
            self._validate_owner_db(db, campaign_id, actor_kind, actor_id)
            market = self._market_db(db, campaign_id, market_id)
            if str(market["visibility"]) != "public":
                raise KeyError(f"unknown or inaccessible market: {market_id}")
            actor_location = self._owner_location_db(db, campaign_id, actor_kind, actor_id)
            if actor_location is not None and actor_location != market["location_id"]:
                raise ValueError("actor must be at the market location")
        if action in {"inspect", "browse", "market"}:
            return self.public_snapshot(campaign_id, market_id=market_id)
        if action == "quote":
            if not item_id:
                raise ValueError("quote requires item_id")
            return self._json_safe(self._public_quote_payload(self.quote(campaign_id, market_id, item_id)))
        if action in {"buy", "sell"}:
            if not item_id:
                raise ValueError(f"{action} requires item_id")
            return self.trade(campaign_id, action, market_id, actor_kind, actor_id, item_id, qty, transaction_key=transaction_key, reason=reason)
        raise ValueError("public economy action must be inspect, quote, buy, or sell")

    def dispatch(self, operation: str, campaign_id: str, payload: dict[str, Any] | None = None) -> Any:
        p = dict(payload or {})
        operation = str(operation or "").strip().lower()
        if operation == "save_market":
            return self.save_market(campaign_id, **p)
        if operation == "set_market_item":
            return self.set_market_item(campaign_id, **p)
        if operation == "save_extractor":
            return self.save_extractor(campaign_id, **p)
        if operation == "save_producer":
            return self.save_producer(campaign_id, **p)
        if operation == "save_route":
            return self.save_route(campaign_id, **p)
        if operation == "save_supply_link":
            return self.save_supply_link(campaign_id, **p)
        if operation == "create_shipment":
            shipment_id = p.pop("shipment_id", p.pop("id", None))
            if not shipment_id:
                raise ValueError("create_shipment requires shipment_id")
            return self.create_shipment(campaign_id, shipment_id, **p)
        if operation == "quote":
            return self.quote(campaign_id, **p)
        if operation == "interact":
            return self.interact(campaign_id, **p)
        if operation == "snapshot":
            return self.snapshot(campaign_id, **p)
        raise ValueError(f"unknown economy operation: {operation}")

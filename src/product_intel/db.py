"""SQLite persistence layer.

Section 2.3 of the design doc: state is SQLite, not a JSON file — the monitor
accumulates price observations over months across multiple tracked products,
and JSON-file state is how you lose six months of history to one bad write.

Pure stdlib (``sqlite3``). The store owns the schema (created on connect) and
exposes typed read/write helpers over the domain models.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Optional

from .models import (
    Alert,
    AlertLayer,
    Availability,
    PriceObservation,
    Product,
    TrackedProduct,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS intents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at REAL NOT NULL,
    raw_query TEXT NOT NULL,
    category TEXT,
    budget_min_subunits INTEGER,
    budget_max_subunits INTEGER,
    constraints_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    intent_id INTEGER REFERENCES intents(id) ON DELETE CASCADE,
    created_at REAL NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    merchant TEXT NOT NULL,
    brand TEXT,
    rye_product_id TEXT,
    channel3_id TEXT,
    price_subunits INTEGER NOT NULL,
    currency TEXT NOT NULL DEFAULT 'USD',
    availability TEXT NOT NULL DEFAULT 'unknown',
    purchasable INTEGER NOT NULL DEFAULT 0,
    attributes_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS tracked_products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at REAL NOT NULL,
    label TEXT NOT NULL,
    merchant TEXT NOT NULL,
    url TEXT NOT NULL,
    rye_product_id TEXT,
    target_price_subunits INTEGER NOT NULL,
    currency TEXT NOT NULL DEFAULT 'USD',
    active INTEGER NOT NULL DEFAULT 1,
    category TEXT,
    last_alert_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS price_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tracked_product_id INTEGER NOT NULL REFERENCES tracked_products(id) ON DELETE CASCADE,
    observed_at REAL NOT NULL,
    price_subunits INTEGER NOT NULL,
    currency TEXT NOT NULL DEFAULT 'USD',
    availability TEXT NOT NULL DEFAULT 'unknown',
    source TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_obs_tracked ON price_observations(tracked_product_id, observed_at);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tracked_product_id INTEGER NOT NULL REFERENCES tracked_products(id) ON DELETE CASCADE,
    created_at REAL NOT NULL,
    layer TEXT NOT NULL,
    rule TEXT NOT NULL,
    message TEXT NOT NULL,
    price_subunits INTEGER NOT NULL,
    deep_link TEXT NOT NULL,
    delivered INTEGER NOT NULL DEFAULT 0,
    payload_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_alert_tracked ON alerts(tracked_product_id, created_at);

-- Phase C: contradiction ledger (Section 3). Persisted records, not transient
-- evaluator flags; Layer 3 checks firmware/recall events against open ones.
CREATE TABLE IF NOT EXISTS contradictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at REAL NOT NULL,
    product_label TEXT NOT NULL,
    aspect TEXT NOT NULL,
    claim_a TEXT NOT NULL,
    source_a TEXT NOT NULL,
    claim_b TEXT NOT NULL,
    source_b TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    resolution_note TEXT
);

-- Section 5 / Phase 5: post-purchase tracking against the per-retailer
-- price-protection policy table.
CREATE TABLE IF NOT EXISTS purchases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at REAL NOT NULL,
    tracked_product_id INTEGER REFERENCES tracked_products(id),
    label TEXT NOT NULL,
    merchant TEXT NOT NULL,
    url TEXT,
    price_subunits INTEGER NOT NULL,
    purchased_at REAL NOT NULL,
    return_days INTEGER,
    protection_days INTEGER,
    protection_note TEXT,
    warranty_registered INTEGER NOT NULL DEFAULT 0,
    protection_notified INTEGER NOT NULL DEFAULT 0,
    closed INTEGER NOT NULL DEFAULT 0
);

-- Phase D: dedup ledger for Layer 3 events (one alert per distinct event).
CREATE TABLE IF NOT EXISTS market_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at REAL NOT NULL,
    event_key TEXT NOT NULL UNIQUE,
    tracked_product_id INTEGER,
    kind TEXT NOT NULL,
    message TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class Store:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        """Additive migrations for DBs created before a column existed."""
        try:
            self.conn.execute("ALTER TABLE tracked_products ADD COLUMN category TEXT")
        except sqlite3.OperationalError:
            pass  # column already present

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---- intents -------------------------------------------------------
    def create_intent(
        self,
        raw_query: str,
        category: Optional[str] = None,
        budget_min_subunits: Optional[int] = None,
        budget_max_subunits: Optional[int] = None,
        constraints: Optional[dict[str, Any]] = None,
    ) -> int:
        from .models import now_ts

        cur = self.conn.execute(
            """INSERT INTO intents
               (created_at, raw_query, category, budget_min_subunits,
                budget_max_subunits, constraints_json)
               VALUES (?,?,?,?,?,?)""",
            (
                now_ts(),
                raw_query,
                category,
                budget_min_subunits,
                budget_max_subunits,
                json.dumps(constraints or {}),
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    # ---- candidates ----------------------------------------------------
    def add_candidates(self, intent_id: Optional[int], products: Iterable[Product]) -> list[int]:
        from .models import now_ts

        ids: list[int] = []
        for p in products:
            cur = self.conn.execute(
                """INSERT INTO candidates
                   (intent_id, created_at, title, url, merchant, brand,
                    rye_product_id, channel3_id, price_subunits, currency,
                    availability, purchasable, attributes_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    intent_id,
                    now_ts(),
                    p.title,
                    p.url,
                    p.merchant,
                    p.brand,
                    p.rye_product_id,
                    p.channel3_id,
                    p.price_subunits,
                    p.currency,
                    p.availability.value,
                    int(p.purchasable),
                    json.dumps(p.attributes or {}),
                ),
            )
            ids.append(int(cur.lastrowid))
        self.conn.commit()
        return ids

    def candidates_for_intent(self, intent_id: int) -> list[Product]:
        rows = self.conn.execute(
            "SELECT * FROM candidates WHERE intent_id = ? ORDER BY price_subunits ASC",
            (intent_id,),
        ).fetchall()
        return [_row_to_product(r) for r in rows]

    # ---- tracked products ---------------------------------------------
    def add_tracked(self, t: TrackedProduct) -> int:
        cur = self.conn.execute(
            """INSERT INTO tracked_products
               (created_at, label, merchant, url, rye_product_id,
                target_price_subunits, currency, active, category, last_alert_json)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                t.created_at,
                t.label,
                t.merchant,
                t.url,
                t.rye_product_id,
                t.target_price_subunits,
                t.currency,
                int(t.active),
                t.category,
                json.dumps(t.last_alert_price_subunits),
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def get_tracked(self, tracked_id: int) -> Optional[TrackedProduct]:
        row = self.conn.execute(
            "SELECT * FROM tracked_products WHERE id = ?", (tracked_id,)
        ).fetchone()
        return _row_to_tracked(row) if row else None

    def active_tracked(self) -> list[TrackedProduct]:
        rows = self.conn.execute(
            "SELECT * FROM tracked_products WHERE active = 1 ORDER BY created_at"
        ).fetchall()
        return [_row_to_tracked(r) for r in rows]

    def all_tracked(self) -> list[TrackedProduct]:
        rows = self.conn.execute(
            "SELECT * FROM tracked_products ORDER BY created_at"
        ).fetchall()
        return [_row_to_tracked(r) for r in rows]

    def set_tracking_active(self, tracked_id: int, active: bool) -> None:
        self.conn.execute(
            "UPDATE tracked_products SET active = ? WHERE id = ?",
            (int(active), tracked_id),
        )
        self.conn.commit()

    def update_last_alert(self, tracked_id: int, last_alert: dict[str, int]) -> None:
        self.conn.execute(
            "UPDATE tracked_products SET last_alert_json = ? WHERE id = ?",
            (json.dumps(last_alert), tracked_id),
        )
        self.conn.commit()

    # ---- observations --------------------------------------------------
    def add_observation(self, obs: PriceObservation) -> int:
        cur = self.conn.execute(
            """INSERT INTO price_observations
               (tracked_product_id, observed_at, price_subunits, currency,
                availability, source)
               VALUES (?,?,?,?,?,?)""",
            (
                obs.tracked_product_id,
                obs.observed_at,
                obs.price_subunits,
                obs.currency,
                obs.availability.value,
                obs.source,
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def observations(self, tracked_id: int) -> list[PriceObservation]:
        rows = self.conn.execute(
            "SELECT * FROM price_observations WHERE tracked_product_id = ? ORDER BY observed_at",
            (tracked_id,),
        ).fetchall()
        return [_row_to_observation(r) for r in rows]

    def latest_observation(self, tracked_id: int) -> Optional[PriceObservation]:
        row = self.conn.execute(
            "SELECT * FROM price_observations WHERE tracked_product_id = ? "
            "ORDER BY observed_at DESC LIMIT 1",
            (tracked_id,),
        ).fetchone()
        return _row_to_observation(row) if row else None

    # ---- alerts --------------------------------------------------------
    def add_alert(self, alert: Alert) -> int:
        cur = self.conn.execute(
            """INSERT INTO alerts
               (tracked_product_id, created_at, layer, rule, message,
                price_subunits, deep_link, delivered, payload_json)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                alert.tracked_product_id,
                alert.created_at,
                alert.layer.value,
                alert.rule,
                alert.message,
                alert.price_subunits,
                alert.deep_link,
                int(alert.delivered),
                json.dumps(alert.payload),
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    # ---- contradiction ledger (Phase C) --------------------------------
    def add_contradiction(
        self,
        product_label: str,
        aspect: str,
        claim_a: str,
        source_a: str,
        claim_b: str,
        source_b: str,
    ) -> Optional[int]:
        """Persist a contradiction; dedup on an open record for the same
        (label, aspect). Returns the new id, or None if already open."""
        from .models import now_ts

        existing = self.conn.execute(
            "SELECT id FROM contradictions WHERE product_label = ? AND aspect = ? "
            "AND status = 'open'",
            (product_label, aspect),
        ).fetchone()
        if existing:
            return None
        cur = self.conn.execute(
            """INSERT INTO contradictions
               (created_at, product_label, aspect, claim_a, source_a, claim_b, source_b)
               VALUES (?,?,?,?,?,?,?)""",
            (now_ts(), product_label, aspect, claim_a, source_a, claim_b, source_b),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def contradictions(self, product_label: Optional[str] = None, open_only: bool = False) -> list[dict[str, Any]]:
        q = "SELECT * FROM contradictions WHERE 1=1"
        args: list[Any] = []
        if product_label:
            q += " AND product_label = ?"
            args.append(product_label)
        if open_only:
            q += " AND status = 'open'"
        rows = self.conn.execute(q + " ORDER BY created_at", args).fetchall()
        return [dict(r) for r in rows]

    def set_contradiction_status(self, cid: int, status: str, note: Optional[str] = None) -> None:
        self.conn.execute(
            "UPDATE contradictions SET status = ?, resolution_note = COALESCE(?, resolution_note) "
            "WHERE id = ?",
            (status, note, cid),
        )
        self.conn.commit()

    # ---- purchases (Section 5 / Phase 5) -------------------------------
    def add_purchase(self, fields: dict[str, Any]) -> int:
        from .models import now_ts

        cur = self.conn.execute(
            """INSERT INTO purchases
               (created_at, tracked_product_id, label, merchant, url,
                price_subunits, purchased_at, return_days, protection_days,
                protection_note, warranty_registered, protection_notified, closed)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,0)""",
            (
                now_ts(),
                fields.get("tracked_product_id"),
                fields["label"],
                fields["merchant"],
                fields.get("url"),
                fields["price_subunits"],
                fields["purchased_at"],
                fields.get("return_days"),
                fields.get("protection_days"),
                fields.get("protection_note"),
                int(fields.get("warranty_registered", False)),
                int(fields.get("protection_notified", False)),
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def purchases(self, open_only: bool = True) -> list[dict[str, Any]]:
        q = "SELECT * FROM purchases"
        if open_only:
            q += " WHERE closed = 0"
        rows = self.conn.execute(q + " ORDER BY purchased_at").fetchall()
        return [dict(r) for r in rows]

    def update_purchase(self, purchase_id: int, **fields: Any) -> None:
        allowed = {"warranty_registered", "protection_notified", "closed"}
        sets = ", ".join(f"{k} = ?" for k in fields if k in allowed)
        if not sets:
            return
        vals = [int(v) if isinstance(v, bool) else v for k, v in fields.items() if k in allowed]
        self.conn.execute(f"UPDATE purchases SET {sets} WHERE id = ?", (*vals, purchase_id))
        self.conn.commit()

    # ---- market events (Phase D dedup) ---------------------------------
    def try_add_event(
        self, event_key: str, kind: str, message: str, tracked_product_id: Optional[int] = None
    ) -> bool:
        """Insert an event; False if this exact event already fired."""
        from .models import now_ts

        try:
            self.conn.execute(
                "INSERT INTO market_events (created_at, event_key, tracked_product_id, kind, message) "
                "VALUES (?,?,?,?,?)",
                (now_ts(), event_key, tracked_product_id, kind, message),
            )
        except sqlite3.IntegrityError:
            return False
        self.conn.commit()
        return True

    # ---- meta KV --------------------------------------------------------
    def get_meta(self, key: str) -> Optional[str]:
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO meta (key, value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self.conn.commit()

    def alerts_for(self, tracked_id: int, limit: int = 20) -> list[Alert]:
        rows = self.conn.execute(
            "SELECT * FROM alerts WHERE tracked_product_id = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (tracked_id, limit),
        ).fetchall()
        return [_row_to_alert(r) for r in rows]


# --- row mappers --------------------------------------------------------
def _row_to_product(r: sqlite3.Row) -> Product:
    return Product(
        title=r["title"],
        url=r["url"],
        merchant=r["merchant"],
        price_subunits=r["price_subunits"],
        currency=r["currency"],
        availability=Availability(r["availability"]),
        purchasable=bool(r["purchasable"]),
        brand=r["brand"],
        rye_product_id=r["rye_product_id"],
        channel3_id=r["channel3_id"],
        attributes=json.loads(r["attributes_json"] or "{}"),
    )


def _row_to_tracked(r: sqlite3.Row) -> TrackedProduct:
    return TrackedProduct(
        id=r["id"],
        created_at=r["created_at"],
        label=r["label"],
        merchant=r["merchant"],
        url=r["url"],
        rye_product_id=r["rye_product_id"],
        target_price_subunits=r["target_price_subunits"],
        currency=r["currency"],
        active=bool(r["active"]),
        category=r["category"],
        last_alert_price_subunits=json.loads(r["last_alert_json"] or "{}"),
    )


def _row_to_observation(r: sqlite3.Row) -> PriceObservation:
    return PriceObservation(
        id=r["id"],
        tracked_product_id=r["tracked_product_id"],
        observed_at=r["observed_at"],
        price_subunits=r["price_subunits"],
        currency=r["currency"],
        availability=Availability(r["availability"]),
        source=r["source"],
    )


def _row_to_alert(r: sqlite3.Row) -> Alert:
    return Alert(
        id=r["id"],
        tracked_product_id=r["tracked_product_id"],
        created_at=r["created_at"],
        layer=AlertLayer(r["layer"]),
        rule=r["rule"],
        message=r["message"],
        price_subunits=r["price_subunits"],
        deep_link=r["deep_link"],
        delivered=bool(r["delivered"]),
        payload=json.loads(r["payload_json"] or "{}"),
    )

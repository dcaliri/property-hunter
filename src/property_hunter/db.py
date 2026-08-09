"""SQLite repository layer.

WAL mode, foreign keys enforced, idempotent DDL applied at startup.
Raw page snapshots are stored gzipped for provenance (constitution III).
"""

from __future__ import annotations

import gzip
import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from property_hunter.models import (
    BaselineRecord,
    DetectionRecord,
    ListingRecord,
    ModelVersionRecord,
    NotificationRecord,
    PredictionRecord,
    PriceObservation,
)

DDL = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL DEFAULT 'running',
    trigger TEXT NOT NULL DEFAULT 'manual'
);

CREATE TABLE IF NOT EXISTS pages (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES runs(id),
    url TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    status_code INTEGER,
    raw_snapshot BLOB
);

CREATE TABLE IF NOT EXISTS listings (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    source_listing_id INTEGER NOT NULL,
    source_url TEXT NOT NULL,
    operation TEXT NOT NULL,
    property_type TEXT,
    street_address TEXT,
    barrio TEXT,
    region TEXT,
    lat REAL,
    lng REAL,
    beds INTEGER,
    baths INTEGER,
    covered_area_m2 REAL,
    total_area_m2 REAL,
    agency_name TEXT,
    description TEXT,
    date_posted TEXT,
    llm_amenity_tags TEXT,
    llm_tags_updated_at TEXT,
    llm_features TEXT,
    llm_features_updated_at TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(source, source_listing_id)
);
CREATE INDEX IF NOT EXISTS idx_listings_active ON listings(source, is_active);
CREATE INDEX IF NOT EXISTS idx_listings_zone ON listings(region, barrio);
CREATE INDEX IF NOT EXISTS idx_listings_op ON listings(operation, is_active);

CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES runs(id),
    listing_id INTEGER NOT NULL REFERENCES listings(id),
    price_cents INTEGER NOT NULL,
    currency TEXT NOT NULL DEFAULT 'USD',
    observed_at TEXT NOT NULL,
    page_id INTEGER REFERENCES pages(id),
    is_active INTEGER NOT NULL DEFAULT 1,
    UNIQUE(run_id, listing_id)
);
CREATE INDEX IF NOT EXISTS idx_observations_listing ON observations(listing_id, observed_at);

CREATE TABLE IF NOT EXISTS price_history (
    id INTEGER PRIMARY KEY,
    listing_id INTEGER NOT NULL REFERENCES listings(id),
    old_price_cents INTEGER,
    new_price_cents INTEGER NOT NULL,
    currency TEXT NOT NULL DEFAULT 'USD',
    observed_at TEXT NOT NULL,
    run_id INTEGER REFERENCES runs(id)
);
CREATE INDEX IF NOT EXISTS idx_price_history_listing ON price_history(listing_id, observed_at);

CREATE TABLE IF NOT EXISTS zones (
    id INTEGER PRIMARY KEY,
    region TEXT NOT NULL,
    barrio TEXT NOT NULL,
    UNIQUE(region, barrio)
);

CREATE TABLE IF NOT EXISTS baselines (
    id INTEGER PRIMARY KEY,
    zone_id INTEGER NOT NULL REFERENCES zones(id),
    operation TEXT NOT NULL,
    property_type TEXT,
    window_start TEXT NOT NULL,
    window_end TEXT NOT NULL,
    observation_count INTEGER NOT NULL DEFAULT 0,
    is_sufficient INTEGER NOT NULL DEFAULT 0,
    median_price_cents INTEGER,
    median_rent_cents INTEGER,
    median_price_per_m2_cents INTEGER,
    computed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_baselines_zone ON baselines(zone_id, operation, property_type, computed_at);

CREATE TABLE IF NOT EXISTS model_versions (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES runs(id),
    trained_at TEXT NOT NULL,
    training_window_start TEXT NOT NULL,
    training_window_end TEXT NOT NULL,
    training_count INTEGER NOT NULL,
    r2_score REAL,
    mae_cents INTEGER,
    blob BLOB NOT NULL,
    is_current INTEGER NOT NULL DEFAULT 1,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS predictions (
    id INTEGER PRIMARY KEY,
    listing_id INTEGER NOT NULL REFERENCES listings(id),
    model_version_id INTEGER REFERENCES model_versions(id),
    run_id INTEGER NOT NULL REFERENCES runs(id),
    predicted_price_cents INTEGER NOT NULL,
    is_fallback INTEGER NOT NULL DEFAULT 1,
    feature_importances TEXT,
    predicted_at TEXT NOT NULL,
    UNIQUE(listing_id, model_version_id, run_id)
);
CREATE INDEX IF NOT EXISTS idx_predictions_listing ON predictions(listing_id, predicted_at);

CREATE TABLE IF NOT EXISTS detections (
    id INTEGER PRIMARY KEY,
    listing_id INTEGER NOT NULL REFERENCES listings(id),
    run_id INTEGER NOT NULL REFERENCES runs(id),
    baseline_id INTEGER REFERENCES baselines(id),
    prediction_id INTEGER REFERENCES predictions(id),
    signals TEXT NOT NULL,
    score REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(listing_id, run_id)
);
CREATE INDEX IF NOT EXISTS idx_detections_status ON detections(status);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY,
    detection_id INTEGER NOT NULL REFERENCES detections(id),
    run_id INTEGER NOT NULL REFERENCES runs(id),
    channel TEXT NOT NULL DEFAULT 'email',
    recipient TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempt_count INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    sent_at TEXT,
    created_at TEXT NOT NULL,
    digest_narrative TEXT,
    llm_enriched INTEGER NOT NULL DEFAULT 0,
    UNIQUE(detection_id, channel)
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def init_db(db_path: Path) -> None:
    with connect(db_path) as conn:
        conn.executescript(DDL)
        _migrate(conn)


def _migrate(conn: sqlite3.Connection) -> None:
    """Idempotent column additions for databases created by earlier builds."""
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(listings)")}
    if "lat" not in cols:
        conn.execute("ALTER TABLE listings ADD COLUMN lat REAL")
    if "lng" not in cols:
        conn.execute("ALTER TABLE listings ADD COLUMN lng REAL")
    if "llm_features" not in cols:
        conn.execute("ALTER TABLE listings ADD COLUMN llm_features TEXT")
    if "llm_features_updated_at" not in cols:
        conn.execute("ALTER TABLE listings ADD COLUMN llm_features_updated_at TEXT")


class Repository:
    """Thin data-access layer over the SQLite schema."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # --- runs ---
    def create_run(self, trigger: str = "manual", started_at: str | None = None) -> int:
        from property_hunter.util import utcnow

        cur = self.conn.execute(
            "INSERT INTO runs (started_at, status, trigger) VALUES (?, 'running', ?)",
            (started_at or utcnow(), trigger),
        )
        return int(cur.lastrowid)

    def finish_run(self, run_id: int, status: str, finished_at: str | None = None) -> None:
        from property_hunter.util import utcnow

        self.conn.execute(
            "UPDATE runs SET status=?, finished_at=? WHERE id=?",
            (status, finished_at or utcnow(), run_id),
        )

    def get_run(self, run_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()

    def latest_run(self) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()

    # --- pages ---
    def insert_page(self, run_id: int, url: str, fetched_at: str, status_code: int, raw_snapshot: bytes) -> int:
        cur = self.conn.execute(
            "INSERT INTO pages (run_id, url, fetched_at, status_code, raw_snapshot) VALUES (?, ?, ?, ?, ?)",
            (run_id, url, fetched_at, status_code, gzip.compress(raw_snapshot)),
        )
        return int(cur.lastrowid)

    def get_page(self, page_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM pages WHERE id=?", (page_id,)).fetchone()

    def page_raw(self, page_id: int) -> bytes:
        row = self.conn.execute("SELECT raw_snapshot FROM pages WHERE id=?", (page_id,)).fetchone()
        if row is None or row["raw_snapshot"] is None:
            return b""
        try:
            return gzip.decompress(row["raw_snapshot"])
        except (gzip.BadGzipFile, EOFError):
            return bytes(row["raw_snapshot"])

    # --- listings ---
    def upsert_listing(self, rec: ListingRecord) -> int:
        row = self.conn.execute(
            "SELECT id FROM listings WHERE source=? AND source_listing_id=?",
            (rec.source, rec.source_listing_id),
        ).fetchone()
        if row is None:
            cur = self.conn.execute(
                """INSERT INTO listings (
                       source, source_listing_id, source_url, operation, property_type,
                       street_address, barrio, region, lat, lng, beds, baths, covered_area_m2, total_area_m2,
                       agency_name, description, date_posted, first_seen_at, last_seen_at,
                       is_active, created_at, updated_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?)""",
                (
                    rec.source, rec.source_listing_id, rec.source_url, rec.operation, rec.property_type,
                    rec.street_address, rec.barrio, rec.region, rec.lat, rec.lng, rec.beds, rec.baths,
                    rec.covered_area_m2, rec.total_area_m2, rec.agency_name, rec.description,
                    rec.date_posted, rec.observed_at, rec.observed_at, rec.observed_at, rec.observed_at,
                ),
            )
            return int(cur.lastrowid)
        listing_id = int(row["id"])
        self.conn.execute(
            """UPDATE listings SET source_url=?, operation=?, property_type=?, street_address=?,
                   barrio=?, region=?, lat=?, lng=?, beds=?, baths=?, covered_area_m2=?, total_area_m2=?,
                   agency_name=?, description=?, date_posted=?, last_seen_at=?, is_active=1, updated_at=?
               WHERE id=?""",
            (
                rec.source_url, rec.operation, rec.property_type, rec.street_address,
                rec.barrio, rec.region, rec.lat, rec.lng, rec.beds, rec.baths,
                rec.covered_area_m2, rec.total_area_m2, rec.agency_name, rec.description,
                rec.date_posted, rec.observed_at, rec.observed_at,
                listing_id,
            ),
        )
        return listing_id

    def get_listing(self, listing_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM listings WHERE id=?", (listing_id,)).fetchone()

    def active_listings(self, operation: str | None = None) -> list[sqlite3.Row]:
        if operation:
            return list(self.conn.execute(
                "SELECT * FROM listings WHERE is_active=1 AND operation=? ORDER BY id", (operation,)
            ))
        return list(self.conn.execute("SELECT * FROM listings WHERE is_active=1 ORDER BY id"))

    def active_listings_with_price(self, operation: str) -> list[sqlite3.Row]:
        """Active listings joined with their latest observation price."""
        return list(self.conn.execute(
            """SELECT l.*, o.price_cents
               FROM listings l
               JOIN observations o ON o.id = (
                   SELECT o2.id FROM observations o2
                   WHERE o2.listing_id = l.id AND o2.is_active=1
                   ORDER BY o2.observed_at DESC, o2.id DESC LIMIT 1
               )
               WHERE l.is_active=1 AND l.operation=?
               ORDER BY l.id""",
            (operation,),
        ))

    def sale_listings_history(self, operation: str) -> list[sqlite3.Row]:
        """All listings (active or not) with latest price and first-seen time.

        Unlike ``active_listings_with_price`` this includes listings that have
        since left the feed (e.g. sold/removed), which is the historical ground
        truth required for temporal backtests.
        """
        return list(self.conn.execute(
            """SELECT l.*,
                      (SELECT o2.price_cents FROM observations o2
                       WHERE o2.listing_id = l.id AND o2.is_active=1
                       ORDER BY o2.observed_at DESC, o2.id DESC LIMIT 1) AS price_cents,
                      (SELECT MIN(o3.observed_at) FROM observations o3
                       WHERE o3.listing_id = l.id AND o3.is_active=1) AS listed_at
               FROM listings l
               WHERE l.operation=?
               ORDER BY l.id""",
            (operation,),
        ))

    def mark_inactive_unseen(self, run_id: int) -> int:
        """Mark previously-active listings inactive when they were not seen in this run."""
        cur = self.conn.execute(
            """UPDATE listings SET is_active=0, updated_at=(SELECT started_at FROM runs WHERE id=?)
               WHERE is_active=1 AND id NOT IN (
                   SELECT listing_id FROM observations WHERE run_id=? AND is_active=1
               )""",
            (run_id, run_id),
        )
        return cur.rowcount

    # --- observations ---
    def insert_observation(self, obs: PriceObservation) -> None:
        self.conn.execute(
            """INSERT INTO observations (run_id, listing_id, price_cents, currency, observed_at, page_id, is_active)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (obs.run_id, obs.listing_id, obs.price_cents, obs.currency, obs.observed_at,
             obs.page_id, int(obs.is_active)),
        )

    def observation_for(self, run_id: int, listing_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM observations WHERE run_id=? AND listing_id=?",
            (run_id, listing_id),
        ).fetchone()

    def previous_observation(self, listing_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM observations WHERE listing_id=? ORDER BY observed_at DESC, id DESC LIMIT 1",
            (listing_id,),
        ).fetchone()

    def observations_in_window(self, operation: str, window_start: str, window_end: str) -> list[sqlite3.Row]:
        """Active observations joined with their listing, within [window_start, window_end)."""
        return list(self.conn.execute(
            """SELECT o.*, l.region, l.barrio, l.property_type, l.covered_area_m2, l.total_area_m2, l.operation
               FROM observations o JOIN listings l ON l.id = o.listing_id
               WHERE l.is_active=1 AND l.operation=?
                 AND o.observed_at >= ? AND o.observed_at < ?
                 AND o.is_active=1""",
            (operation, window_start, window_end),
        ))

    # --- price history ---
    def insert_price_history(self, listing_id: int, old_price_cents: int | None, new_price_cents: int,
                             currency: str, observed_at: str, run_id: int) -> None:
        self.conn.execute(
            """INSERT INTO price_history (listing_id, old_price_cents, new_price_cents, currency, observed_at, run_id)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (listing_id, old_price_cents, new_price_cents, currency, observed_at, run_id),
        )

    def price_history_for(self, listing_id: int) -> list[sqlite3.Row]:
        return list(self.conn.execute(
            "SELECT * FROM price_history WHERE listing_id=? ORDER BY observed_at DESC, id DESC",
            (listing_id,),
        ))

    # --- zones ---
    def zone_for(self, region: str, barrio: str) -> int | None:
        row = self.conn.execute(
            "SELECT id FROM zones WHERE region=? AND barrio=?", (region, barrio)
        ).fetchone()
        return int(row["id"]) if row is not None else None

    def upsert_zone(self, region: str, barrio: str) -> int:
        row = self.conn.execute(
            "SELECT id FROM zones WHERE region=? AND barrio=?", (region, barrio)
        ).fetchone()
        if row is not None:
            return int(row["id"])
        cur = self.conn.execute(
            "INSERT INTO zones (region, barrio) VALUES (?, ?)", (region, barrio)
        )
        return int(cur.lastrowid)

    # --- baselines ---
    def insert_baseline(self, rec: BaselineRecord) -> int:
        cur = self.conn.execute(
            """INSERT INTO baselines (zone_id, operation, property_type, window_start, window_end,
                   observation_count, is_sufficient, median_price_cents, median_rent_cents,
                   median_price_per_m2_cents, computed_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (rec.zone_id, rec.operation, rec.property_type, rec.window_start, rec.window_end,
             rec.observation_count, int(rec.is_sufficient), rec.median_price_cents,
             rec.median_rent_cents, rec.median_price_per_m2_cents, rec.computed_at),
        )
        return int(cur.lastrowid)

    def latest_baselines(self) -> list[sqlite3.Row]:
        """Latest baseline row per (zone, operation)."""
        return list(self.conn.execute(
            """SELECT bl.*, z.region, z.barrio FROM baselines bl
               JOIN zones z ON z.id = bl.zone_id
               WHERE bl.id IN (SELECT MAX(b2.id) FROM baselines b2 GROUP BY b2.zone_id, b2.operation)
               ORDER BY z.region, z.barrio"""
        ))

    def baseline_for(self, zone_id: int, operation: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM baselines WHERE zone_id=? AND operation=? ORDER BY id DESC LIMIT 1",
            (zone_id, operation),
        ).fetchone()

    # --- model versions ---
    def insert_model_version(self, rec: ModelVersionRecord) -> int:
        self.conn.execute("UPDATE model_versions SET is_current=0 WHERE is_current=1")
        cur = self.conn.execute(
            """INSERT INTO model_versions (run_id, trained_at, training_window_start, training_window_end,
                   training_count, r2_score, mae_cents, blob, is_current, notes)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (rec.run_id, rec.trained_at, rec.training_window_start, rec.training_window_end,
             rec.training_count, rec.r2_score, rec.mae_cents, rec.blob, int(rec.is_current), rec.notes),
        )
        return int(cur.lastrowid)

    def current_model_version(self) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM model_versions WHERE is_current=1 ORDER BY id DESC LIMIT 1"
        ).fetchone()

    # --- predictions ---
    def insert_prediction(self, rec: PredictionRecord) -> int:
        cur = self.conn.execute(
            """INSERT INTO predictions (listing_id, model_version_id, run_id, predicted_price_cents,
                   is_fallback, feature_importances, predicted_at)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(listing_id, model_version_id, run_id) DO UPDATE SET
                   predicted_price_cents=excluded.predicted_price_cents,
                   is_fallback=excluded.is_fallback,
                   feature_importances=excluded.feature_importances,
                   predicted_at=excluded.predicted_at""",
            (rec.listing_id, rec.model_version_id, rec.run_id, rec.predicted_price_cents,
             int(rec.is_fallback),
             json.dumps(rec.feature_importances) if rec.feature_importances else None,
             rec.predicted_at),
        )
        return int(cur.lastrowid)

    def prediction_for(self, listing_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM predictions WHERE listing_id=? ORDER BY predicted_at DESC, id DESC LIMIT 1",
            (listing_id,),
        ).fetchone()

    # --- detections ---
    def insert_detection(self, rec: DetectionRecord) -> int:
        cur = self.conn.execute(
            """INSERT INTO detections (listing_id, run_id, baseline_id, prediction_id, signals, score,
                   status, first_seen_at, last_seen_at, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(listing_id, run_id) DO UPDATE SET
                   baseline_id=excluded.baseline_id, prediction_id=excluded.prediction_id,
                   signals=excluded.signals, score=excluded.score, status=excluded.status,
                   first_seen_at=excluded.first_seen_at, last_seen_at=excluded.last_seen_at,
                   created_at=excluded.created_at""",
            (rec.listing_id, rec.run_id, rec.baseline_id, rec.prediction_id,
             json.dumps([s.model_dump() for s in rec.signals]),
             rec.score, rec.status, rec.first_seen_at, rec.last_seen_at, rec.created_at),
        )
        return int(cur.lastrowid)

    def supersede_detections(self, run_id: int) -> None:
        """Mark prior active detections for listings flagged in this run as superseded."""
        self.conn.execute(
            """UPDATE detections SET status='superseded'
               WHERE status='active' AND run_id < ? AND listing_id IN (
                   SELECT listing_id FROM detections WHERE run_id=?
               )""",
            (run_id, run_id),
        )

    def active_detections(self) -> list[sqlite3.Row]:
        return list(self.conn.execute(
            "SELECT * FROM detections WHERE status='active' ORDER BY score DESC, id DESC"
        ))

    def new_detections(self, run_id: int) -> list[sqlite3.Row]:
        """Detections from this run not yet notified on any channel."""
        return list(self.conn.execute(
            """SELECT d.* FROM detections d
               WHERE d.run_id=? AND d.status='active'
                 AND NOT EXISTS (SELECT 1 FROM notifications n WHERE n.detection_id=d.id)""",
            (run_id,),
        ))

    def unnotified_detections(self, channel: str = "email") -> list[sqlite3.Row]:
        """Active detections not yet notified on the given channel."""
        return list(self.conn.execute(
            """SELECT d.* FROM detections d
               WHERE d.status='active'
                 AND NOT EXISTS (SELECT 1 FROM notifications n
                                 WHERE n.detection_id=d.id AND n.channel=?)""",
            (channel,),
        ))

    # --- notifications ---
    def insert_notification(self, rec: NotificationRecord) -> int:
        cur = self.conn.execute(
            """INSERT INTO notifications (detection_id, run_id, channel, recipient, status,
                   attempt_count, last_error, sent_at, created_at, digest_narrative, llm_enriched)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (rec.detection_id, rec.run_id, rec.channel, rec.recipient, rec.status,
             rec.attempt_count, rec.last_error, rec.sent_at, rec.created_at,
             rec.digest_narrative, int(rec.llm_enriched)),
        )
        return int(cur.lastrowid)

    def update_notification_status(self, notification_id: int, status: str, attempt_count: int,
                                   last_error: str | None = None, sent_at: str | None = None,
                                   digest_narrative: str | None = None,
                                   llm_enriched: bool = False) -> None:
        self.conn.execute(
            """UPDATE notifications SET status=?, attempt_count=?, last_error=?, sent_at=?,
                   digest_narrative=?, llm_enriched=?
               WHERE id=?""",
            (status, attempt_count, last_error, sent_at, digest_narrative,
             int(llm_enriched), notification_id),
        )

    def notifications_for(self, detection_id: int) -> list[sqlite3.Row]:
        return list(self.conn.execute(
            "SELECT * FROM notifications WHERE detection_id=?", (detection_id,)
        ))

    # --- settings ---
    def set_setting(self, key: str, value: Any) -> None:
        self.conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)),
        )

    def get_setting(self, key: str) -> Any | None:
        row = self.conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        if row is None:
            return None
        return json.loads(row["value"])

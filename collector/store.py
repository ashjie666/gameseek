"""SQLite persistence: raw items, extraction fields, cluster ids and run stats."""

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
  id               TEXT PRIMARY KEY,
  source_id        TEXT NOT NULL,
  source_name      TEXT NOT NULL,
  lang             TEXT NOT NULL DEFAULT 'en',
  title            TEXT NOT NULL,
  summary          TEXT NOT NULL DEFAULT '',
  url              TEXT NOT NULL,
  canonical_url    TEXT NOT NULL,
  published_at     TEXT,
  fetched_at       TEXT NOT NULL,
  author           TEXT NOT NULL DEFAULT '',
  publisher_domain TEXT NOT NULL DEFAULT '',
  game_name        TEXT NOT NULL DEFAULT '',
  normalized_game  TEXT NOT NULL DEFAULT '',
  developer        TEXT NOT NULL DEFAULT '',
  publisher        TEXT NOT NULL DEFAULT '',
  platforms        TEXT NOT NULL DEFAULT '[]',
  genres           TEXT NOT NULL DEFAULT '[]',
  release_date     TEXT NOT NULL DEFAULT '',
  release_text     TEXT NOT NULL DEFAULT '',
  category         TEXT NOT NULL DEFAULT 'other',
  is_new_release   INTEGER NOT NULL DEFAULT 0,
  confidence       REAL NOT NULL DEFAULT 0,
  extractor        TEXT NOT NULL DEFAULT '',
  cluster_id       TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_items_published ON items(published_at);
CREATE INDEX IF NOT EXISTS idx_items_cluster ON items(cluster_id);
CREATE INDEX IF NOT EXISTS idx_items_extractor ON items(extractor);

CREATE TABLE IF NOT EXISTS source_status (
  source_id  TEXT PRIMARY KEY,
  status     TEXT NOT NULL,
  items      INTEGER NOT NULL DEFAULT 0,
  error      TEXT NOT NULL DEFAULT '',
  checked_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at  TEXT NOT NULL,
  finished_at TEXT,
  new_items   INTEGER NOT NULL DEFAULT 0,
  total_items INTEGER NOT NULL DEFAULT 0,
  note        TEXT NOT NULL DEFAULT ''
);
"""

INSERT_ITEM = """
INSERT INTO items (
  id, source_id, source_name, lang, title, summary, url, canonical_url,
  published_at, fetched_at, author, publisher_domain
) VALUES (
  :id, :source_id, :source_name, :lang, :title, :summary, :url, :canonical_url,
  :published_at, :fetched_at, :author, :publisher_domain
)
ON CONFLICT(id) DO UPDATE SET
  source_name = excluded.source_name,
  title       = excluded.title,
  summary     = excluded.summary,
  published_at = COALESCE(excluded.published_at, items.published_at),
  fetched_at  = excluded.fetched_at
"""

EXTRACTION_FIELDS = (
    "game_name", "normalized_game", "developer", "publisher", "platforms", "genres",
    "release_date", "release_text", "category", "is_new_release", "confidence", "extractor",
)

# Every extraction column is NOT NULL; an absent value from the extractor means
# "unknown", which must be written as the empty form of that column's type.
EXTRACTION_DEFAULTS = {
    "game_name": "", "normalized_game": "", "developer": "", "publisher": "",
    "platforms": [], "genres": [],
    "release_date": "", "release_text": "",
    "category": "other", "is_new_release": 0, "confidence": 0.0, "extractor": "",
}

UPDATE_EXTRACTION = """
UPDATE items SET
  game_name = :game_name, normalized_game = :normalized_game,
  developer = :developer, publisher = :publisher,
  platforms = :platforms, genres = :genres,
  release_date = :release_date, release_text = :release_text,
  category = :category, is_new_release = :is_new_release,
  confidence = :confidence, extractor = :extractor
WHERE id = :id
"""


def utcnow():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class Store:
    def __init__(self, path):
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(SCHEMA)
        self.connection.commit()

    def close(self):
        self.connection.close()

    # -- writes ------------------------------------------------------------
    def upsert_items(self, rows):
        """Insert items, refreshing raw fields for ones we already know."""
        known = self.known_ids([row["id"] for row in rows])
        fresh = [row for row in rows if row["id"] not in known]
        self.connection.executemany(INSERT_ITEM, rows)
        self.connection.commit()
        return fresh

    def known_ids(self, ids):
        if not ids:
            return set()
        found = set()
        chunk = 400
        for start in range(0, len(ids), chunk):
            batch = ids[start:start + chunk]
            placeholders = ",".join("?" * len(batch))
            cursor = self.connection.execute(
                "SELECT id FROM items WHERE id IN (%s)" % placeholders, batch
            )
            found.update(row["id"] for row in cursor.fetchall())
        return found

    def pending_extraction(self, limit=400):
        cursor = self.connection.execute(
            "SELECT * FROM items WHERE extractor = '' ORDER BY COALESCE(published_at, fetched_at) DESC LIMIT ?",
            (limit,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def save_extraction(self, item_id_value, data):
        payload = {"id": item_id_value}
        for field in EXTRACTION_FIELDS:
            value = data.get(field)
            if value is None:
                value = EXTRACTION_DEFAULTS[field]
            payload[field] = value
        payload["platforms"] = json.dumps(payload["platforms"] or [], ensure_ascii=False)
        payload["genres"] = json.dumps(payload["genres"] or [], ensure_ascii=False)
        payload["is_new_release"] = 1 if payload["is_new_release"] else 0
        self.connection.execute(UPDATE_EXTRACTION, payload)

    def set_extraction_many(self, pairs):
        for item_id_value, data in pairs:
            self.save_extraction(item_id_value, data)
        self.connection.commit()

    def apply_clusters(self, assignments):
        """assignments: {item_id: cluster_id}. Only touch rows that changed."""
        updates = [
            (cluster_id, item_id_value)
            for item_id_value, cluster_id in assignments.items()
        ]
        if not updates:
            return 0
        before = self.connection.total_changes
        self.connection.executemany(
            "UPDATE items SET cluster_id = ? WHERE id = ? AND cluster_id IS NOT ?", 
            [(cluster, item_id_value, cluster) for cluster, item_id_value in updates],
        )
        self.connection.commit()
        return self.connection.total_changes - before

    def record_source_status(self, source_id, status, items, error=""):
        self.connection.execute(
            "INSERT INTO source_status (source_id, status, items, error, checked_at) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(source_id) DO UPDATE SET "
            "status = excluded.status, items = excluded.items, "
            "error = excluded.error, checked_at = excluded.checked_at",
            (source_id, status, items, error[:300], utcnow()),
        )
        self.connection.commit()

    def start_run(self):
        cursor = self.connection.execute("INSERT INTO runs (started_at) VALUES (?)", (utcnow(),))
        self.connection.commit()
        return cursor.lastrowid

    def finish_run(self, run_id, new_items, total_items, note=""):
        self.connection.execute(
            "UPDATE runs SET finished_at = ?, new_items = ?, total_items = ?, note = ? WHERE id = ?",
            (utcnow(), new_items, total_items, note, run_id),
        )
        self.connection.commit()

    # -- reads -------------------------------------------------------------
    def window(self, days=30):
        since = (datetime.now(timezone.utc) - timedelta(days=days)).replace(microsecond=0)
        cursor = self.connection.execute(
            "SELECT * FROM items "
            "WHERE COALESCE(published_at, fetched_at) >= ? "
            "ORDER BY COALESCE(published_at, fetched_at) DESC",
            (since.isoformat().replace("+00:00", "Z"),),
        )
        return [dict(row) for row in cursor.fetchall()]

    def all_source_status(self):
        cursor = self.connection.execute("SELECT * FROM source_status ORDER BY source_id")
        return [dict(row) for row in cursor.fetchall()]

    def count_items(self):
        return self.connection.execute("SELECT COUNT(*) FROM items").fetchone()[0]

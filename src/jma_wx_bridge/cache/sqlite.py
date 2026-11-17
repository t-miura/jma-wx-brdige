"""SQLite WAL-mode cache for JMA forecasts and Atom feed conditional-GET state."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from jma_wx_bridge.jma.models import AreaForecast


@dataclass(frozen=True, slots=True)
class FeedState:
    """Stored HTTP cache validators for an Atom feed URL."""

    feed_url: str
    etag: str | None = None
    last_modified: str | None = None


@dataclass(frozen=True, slots=True)
class PassthroughCacheEntry:
    """Cached upstream Open-Meteo response for stale-if-error fallback."""

    cache_key: str
    status_code: int
    content_type: str
    body: bytes
    fetched_at: str


class SQLiteCache:
    """Thread-safe / WAL-enabled SQLite store for JMA forecasts."""

    def __init__(self, db_path: Path | str) -> None:
        self._db_path = str(db_path)
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA synchronous=NORMAL;")
        self._conn.execute("PRAGMA busy_timeout=5000;")
        self.init_schema()

    def init_schema(self) -> None:
        """Create tables and indexes if they do not exist."""
        with self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS feed_state (
                    feed_url TEXT PRIMARY KEY,
                    etag TEXT,
                    last_modified TEXT,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS seen_entries (
                    entry_id TEXT PRIMARY KEY,
                    entry_updated TEXT NOT NULL,
                    seen_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS forecasts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    office_code TEXT NOT NULL,
                    class10_code TEXT NOT NULL,
                    report_datetime TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    UNIQUE (office_code, class10_code, report_datetime)
                );

                CREATE INDEX IF NOT EXISTS idx_forecasts_lookup
                    ON forecasts (class10_code, report_datetime DESC);
                CREATE INDEX IF NOT EXISTS idx_forecasts_fetched_at
                    ON forecasts (fetched_at);

                CREATE TABLE IF NOT EXISTS passthrough_cache (
                    cache_key TEXT PRIMARY KEY,
                    status_code INTEGER NOT NULL,
                    content_type TEXT NOT NULL,
                    body BLOB NOT NULL,
                    fetched_at TEXT NOT NULL
                );
                """
            )

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        self._conn.close()

    def get_feed_state(self, feed_url: str) -> FeedState | None:
        """Return stored ETag / Last-Modified for a feed URL, if any."""
        row = self._conn.execute(
            "SELECT feed_url, etag, last_modified FROM feed_state WHERE feed_url = ?",
            (feed_url,),
        ).fetchone()
        if row is None:
            return None
        return FeedState(
            feed_url=row["feed_url"],
            etag=row["etag"],
            last_modified=row["last_modified"],
        )

    def save_feed_state(
        self,
        feed_url: str,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
        now: datetime | None = None,
    ) -> None:
        """Upsert HTTP conditional-GET headers for a feed URL."""
        ts = (now or datetime.now(UTC)).isoformat()
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO feed_state (feed_url, etag, last_modified, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(feed_url) DO UPDATE SET
                    etag = COALESCE(excluded.etag, feed_state.etag),
                    last_modified = COALESCE(excluded.last_modified, feed_state.last_modified),
                    updated_at = excluded.updated_at
                """,
                (feed_url, etag, last_modified, ts),
            )

    def is_entry_seen(self, entry_id: str, entry_updated: str) -> bool:
        """Return True if this Atom entry_id + updated timestamp was already processed."""
        row = self._conn.execute(
            "SELECT entry_updated FROM seen_entries WHERE entry_id = ?",
            (entry_id,),
        ).fetchone()
        return row is not None and row["entry_updated"] == entry_updated

    def mark_entry_seen(
        self,
        entry_id: str,
        entry_updated: str,
        *,
        now: datetime | None = None,
    ) -> None:
        """Record an Atom entry as processed."""
        ts = (now or datetime.now(UTC)).isoformat()
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO seen_entries (entry_id, entry_updated, seen_at)
                VALUES (?, ?, ?)
                ON CONFLICT(entry_id) DO UPDATE SET
                    entry_updated = excluded.entry_updated,
                    seen_at = excluded.seen_at
                """,
                (entry_id, entry_updated, ts),
            )

    def upsert_forecast(
        self,
        forecast: AreaForecast,
        *,
        now: datetime | None = None,
    ) -> None:
        """Insert or update a parsed AreaForecast."""
        ts = (now or datetime.now(UTC)).isoformat()
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO forecasts (
                    office_code, class10_code, report_datetime, payload_json, fetched_at
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(office_code, class10_code, report_datetime) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    fetched_at = excluded.fetched_at
                """,
                (
                    forecast.office_code,
                    forecast.class10_code,
                    forecast.report_datetime,
                    forecast.model_dump_json(),
                    ts,
                ),
            )

    def get_latest_forecast(
        self,
        class10_code: str,
        *,
        office_code: str | None = None,
    ) -> AreaForecast | None:
        """Return the most recent cached AreaForecast for a Class10s (or Office) code."""
        if office_code is not None:
            row = self._conn.execute(
                """
                SELECT payload_json FROM forecasts
                WHERE class10_code = ? AND office_code = ?
                ORDER BY report_datetime DESC, id DESC
                LIMIT 1
                """,
                (class10_code, office_code),
            ).fetchone()
        else:
            row = self._conn.execute(
                """
                SELECT payload_json FROM forecasts
                WHERE class10_code = ?
                ORDER BY report_datetime DESC, id DESC
                LIMIT 1
                """,
                (class10_code,),
            ).fetchone()

        if row is None:
            return None
        return AreaForecast.model_validate_json(row["payload_json"])

    def count_forecasts(self) -> int:
        """Return total number of stored forecast rows."""
        row = self._conn.execute("SELECT COUNT(*) AS cnt FROM forecasts").fetchone()
        return int(row["cnt"]) if row is not None else 0

    def get_passthrough_cache(self, cache_key: str) -> PassthroughCacheEntry | None:
        """Return cached Open-Meteo passthrough response for a normalized query key."""
        row = self._conn.execute(
            """
            SELECT cache_key, status_code, content_type, body, fetched_at
            FROM passthrough_cache
            WHERE cache_key = ?
            """,
            (cache_key,),
        ).fetchone()
        if row is None:
            return None
        return PassthroughCacheEntry(
            cache_key=row["cache_key"],
            status_code=int(row["status_code"]),
            content_type=row["content_type"],
            body=bytes(row["body"]),
            fetched_at=row["fetched_at"],
        )

    def save_passthrough_cache(
        self,
        cache_key: str,
        *,
        status_code: int,
        content_type: str,
        body: bytes,
        now: datetime | None = None,
    ) -> None:
        """Store an upstream Open-Meteo response for stale-if-error fallback."""
        ts = (now or datetime.now(UTC)).isoformat()
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO passthrough_cache (
                    cache_key, status_code, content_type, body, fetched_at
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    status_code = excluded.status_code,
                    content_type = excluded.content_type,
                    body = excluded.body,
                    fetched_at = excluded.fetched_at
                """,
                (cache_key, status_code, content_type, body, ts),
            )

    def prune_old_forecasts(
        self,
        retention_days: int = 3,
        *,
        now: datetime | None = None,
    ) -> int:
        """Delete forecast, passthrough, and old seen_entries rows beyond retention."""
        ref_now = now or datetime.now(UTC)
        cutoff = (ref_now - timedelta(days=retention_days)).isoformat()
        seen_cutoff = (ref_now - timedelta(days=max(retention_days * 2, 7))).isoformat()
        with self._conn:
            cur = self._conn.execute(
                "DELETE FROM forecasts WHERE fetched_at < ?",
                (cutoff,),
            )
            self._conn.execute(
                "DELETE FROM passthrough_cache WHERE fetched_at < ?",
                (cutoff,),
            )
            self._conn.execute(
                "DELETE FROM seen_entries WHERE seen_at < ?",
                (seen_cutoff,),
            )
            return cur.rowcount

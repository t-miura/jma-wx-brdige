"""Background JMA Atom feed poller and VPFD51 XML ingestion worker."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx

from jma_wx_bridge.jma.models import AtomEntry
from jma_wx_bridge.jma.parser import parse_atom_feed, parse_vpfd51_xml

if TYPE_CHECKING:
    from jma_wx_bridge.cache.sqlite import SQLiteCache

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PollSummary:
    """Result metrics from a single JMA poll cycle."""

    not_modified: bool = False
    entries_found: int = 0
    entries_fetched: int = 0
    areas_stored: int = 0
    pruned_rows: int = 0


class JMAWorker:
    """Polls JMA regular.xml and caches parsed VPFD51 forecasts in SQLite."""

    def __init__(
        self,
        cache: SQLiteCache,
        *,
        feed_url: str,
        poll_interval_seconds: float = 1200.0,
        retention_days: int = 3,
        timeout_seconds: float = 10.0,
        max_concurrency: int = 5,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._cache = cache
        self._feed_url = feed_url
        self._poll_interval_seconds = poll_interval_seconds
        self._retention_days = retention_days
        self._timeout_seconds = timeout_seconds
        self._max_concurrency = max_concurrency
        self._external_client = client

    async def _fetch_entry(
        self,
        client: httpx.AsyncClient,
        sem: asyncio.Semaphore,
        entry: AtomEntry,
    ) -> int:
        async with sem:
            resp = await client.get(entry.url)
            resp.raise_for_status()
            forecasts = parse_vpfd51_xml(resp.text)
            for fc in forecasts:
                self._cache.upsert_forecast(fc)
            self._cache.mark_entry_seen(entry.entry_id, entry.updated)
            return len(forecasts)

    async def poll_once(self) -> PollSummary:
        """Execute one Atom feed check, download new VPFD51 XMLs, and prune old rows."""
        own_client = self._external_client is None
        client = self._external_client or httpx.AsyncClient(timeout=self._timeout_seconds)
        try:
            headers: dict[str, str] = {}
            state = self._cache.get_feed_state(self._feed_url)
            if state is not None:
                if state.etag:
                    headers["If-None-Match"] = state.etag
                if state.last_modified:
                    headers["If-Modified-Since"] = state.last_modified

            resp = await client.get(self._feed_url, headers=headers)
            if resp.status_code == 304:
                pruned = self._cache.prune_old_forecasts(self._retention_days)
                return PollSummary(not_modified=True, pruned_rows=pruned)

            resp.raise_for_status()
            self._cache.save_feed_state(
                self._feed_url,
                etag=resp.headers.get("ETag"),
                last_modified=resp.headers.get("Last-Modified"),
            )

            entries = parse_atom_feed(resp.text, only_vpfd51=True)
            pending = [e for e in entries if not self._cache.is_entry_seen(e.entry_id, e.updated)]

            sem = asyncio.Semaphore(self._max_concurrency)
            results = await asyncio.gather(
                *(self._fetch_entry(client, sem, entry) for entry in pending),
                return_exceptions=True,
            )

            entries_fetched = 0
            areas_stored = 0
            for entry, result in zip(pending, results, strict=True):
                if isinstance(result, BaseException):
                    logger.warning("Failed to ingest JMA entry %s: %s", entry.url, result)
                else:
                    entries_fetched += 1
                    areas_stored += result

            pruned = self._cache.prune_old_forecasts(self._retention_days)
            return PollSummary(
                not_modified=False,
                entries_found=len(entries),
                entries_fetched=entries_fetched,
                areas_stored=areas_stored,
                pruned_rows=pruned,
            )
        finally:
            if own_client:
                await client.aclose()

    async def run_forever(self) -> None:
        """Background loop that polls JMA periodically until cancelled."""
        while True:
            try:
                summary = await self.poll_once()
                logger.info(
                    "JMA poll complete: not_modified=%s found=%d fetched=%d areas=%d pruned=%d",
                    summary.not_modified,
                    summary.entries_found,
                    summary.entries_fetched,
                    summary.areas_stored,
                    summary.pruned_rows,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("JMA background poll failed")
            await asyncio.sleep(self._poll_interval_seconds)

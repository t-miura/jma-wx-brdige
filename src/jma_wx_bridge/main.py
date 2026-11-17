"""FastAPI application entrypoint."""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated
from urllib.parse import urlencode

import httpx
from fastapi import FastAPI, Query, Request, Response
from fastapi.responses import JSONResponse

from jma_wx_bridge.cache import SQLiteCache
from jma_wx_bridge.config import Settings
from jma_wx_bridge.geo import GeoRouter
from jma_wx_bridge.jma import JMAWorker
from jma_wx_bridge.translator import translate_forecast


def _parse_var_list(values: list[str] | None) -> list[str]:
    """Parse comma-separated or repeated Open-Meteo variable query params."""
    if not values:
        return []
    result: list[str] = []
    for item in values:
        for part in item.split(","):
            cleaned = part.strip()
            if cleaned:
                result.append(cleaned)
    return result


async def _proxy_open_meteo(
    request: Request,
    settings: Settings,
    cache: SQLiteCache,
    http_client: httpx.AsyncClient,
) -> Response:
    """Forward request to Open-Meteo, caching 200s and falling back to stale cache on error."""
    params = list(request.query_params.multi_items())
    cache_key = urlencode(sorted(params))

    try:
        upstream = await http_client.get(settings.open_meteo_base_url, params=params)
        if upstream.status_code < 500:
            content_type = upstream.headers.get("content-type", "application/json")
            if upstream.status_code == 200:
                cache.save_passthrough_cache(
                    cache_key,
                    status_code=upstream.status_code,
                    content_type=content_type,
                    body=upstream.content,
                )
            return Response(
                content=upstream.content,
                status_code=upstream.status_code,
                media_type=content_type,
                headers={"X-JMA-WX-Source": "open-meteo"},
            )
    except httpx.RequestError:
        pass

    stale = cache.get_passthrough_cache(cache_key)
    if stale is not None:
        return Response(
            content=stale.body,
            status_code=stale.status_code,
            media_type=stale.content_type,
            headers={"X-JMA-WX-Source": "open-meteo-stale-cache"},
        )

    return JSONResponse(
        status_code=502,
        content={"error": True, "reason": "Upstream Open-Meteo API unavailable and no cache."},
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Initialize shared resources and background JMA worker on startup."""
    settings = Settings()
    cache = SQLiteCache(settings.db_path)
    geo_router = GeoRouter.from_file(settings.geojson_path)
    http_client = httpx.AsyncClient(timeout=settings.request_timeout_seconds)
    worker = JMAWorker(
        cache,
        feed_url=settings.jma_regular_feed_url,
        poll_interval_seconds=settings.poll_interval_minutes * 60.0,
        retention_days=settings.cache_retention_days,
        timeout_seconds=settings.request_timeout_seconds,
    )

    app.state.settings = settings
    app.state.cache = cache
    app.state.geo_router = geo_router
    app.state.http_client = http_client
    app.state.jma_worker = worker

    poll_task: asyncio.Task[None] | None = None
    if settings.enable_background_poll:
        poll_task = asyncio.create_task(worker.run_forever())

    try:
        yield
    finally:
        if poll_task is not None:
            poll_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await poll_task
        await http_client.aclose()
        cache.close()


app = FastAPI(title="JMA-WX-Bridge", version="0.1.0", lifespan=lifespan)


@app.get("/healthz")
async def healthz(request: Request) -> dict[str, object]:
    """Return service health, loaded area count, and cached forecast count."""
    router: GeoRouter = request.app.state.geo_router
    cache: SQLiteCache = request.app.state.cache
    return {
        "status": "ok",
        "areas_loaded": router.area_count,
        "cached_forecasts": cache.count_forecasts(),
    }


@app.get("/v1/forecast")
async def forecast(
    request: Request,
    latitude: Annotated[float, Query(ge=-90.0, le=90.0)],
    longitude: Annotated[float, Query(ge=-180.0, le=180.0)],
    hourly: Annotated[list[str] | None, Query()] = None,
    daily: Annotated[list[str] | None, Query()] = None,
    current: Annotated[list[str] | None, Query()] = None,
    timezone: Annotated[str, Query()] = "GMT",
    temperature_unit: Annotated[str, Query()] = "celsius",
    wind_speed_unit: Annotated[str, Query()] = "kmh",
    forecast_days: Annotated[int, Query(ge=1, le=16)] = 3,
) -> Response:
    """Serve JMA-backed Open-Meteo forecasts for Japan, or passthrough to Open-Meteo."""
    started = time.perf_counter()
    router: GeoRouter = request.app.state.geo_router
    cache: SQLiteCache = request.app.state.cache
    settings: Settings = request.app.state.settings
    http_client: httpx.AsyncClient = request.app.state.http_client

    area = router.resolve(latitude, longitude)
    if area is not None:
        cached = cache.get_latest_forecast(area.class10_code, office_code=area.office_code)
        if cached is not None:
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            payload = translate_forecast(
                cached,
                latitude=latitude,
                longitude=longitude,
                hourly=_parse_var_list(hourly),
                daily=_parse_var_list(daily),
                current=_parse_var_list(current),
                timezone=timezone,
                temperature_unit=temperature_unit,
                wind_speed_unit=wind_speed_unit,
                forecast_days=forecast_days,
                generationtime_ms=elapsed_ms,
            )
            return JSONResponse(
                content=payload,
                headers={
                    "X-JMA-WX-Source": "jma-cache",
                    "X-JMA-Area-Code": area.class10_code,
                },
            )

    return await _proxy_open_meteo(request, settings, cache, http_client)

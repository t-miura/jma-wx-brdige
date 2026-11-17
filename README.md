# JMA-WX-Bridge

Local proxy server acting as a drop-in replacement for the Open-Meteo `/v1/forecast` API, backed by Japan Meteorological Agency (JMA) XML feeds for locations in Japan and transparent passthrough (with stale-if-error caching) to Open-Meteo for the rest of the world.

## Setup

```bash
uv sync
uv run python scripts/fetch_jma_geojson.py
```

`fetch_jma_geojson.py` downloads JMA's official `area.json` and `geojson/class10s.json` (153 primary forecast subdivisions) into `src/jma_wx_bridge/geo/data/jma_areas.geojson` (gitignored so JMA boundary data is not redistributed in the repository).

## Run

```bash
uv run uvicorn jma_wx_bridge.main:app --host 0.0.0.0 --port 8000
```

Then point your client (e.g., `meshing-around`) to `http://127.0.0.1:8000/v1/forecast`.

### Environment Variables (`JMA_WX_*`)

| Variable | Default | Description |
| --- | --- | --- |
| `JMA_WX_DB_PATH` | `jma_wx_bridge.db` | SQLite WAL cache file path |
| `JMA_WX_GEOJSON_PATH` | *(bundled)* | Optional custom GeoJSON override |
| `JMA_WX_POLL_INTERVAL_MINUTES` | `20` | JMA `regular.xml` poll interval |
| `JMA_WX_CACHE_RETENTION_DAYS` | `3` | Days before cached rows are pruned |
| `JMA_WX_ENABLE_BACKGROUND_POLL` | `true` | Toggle background JMA poller |
| `JMA_WX_OPEN_METEO_BASE_URL` | `https://api.open-meteo.com/v1/forecast` | Upstream Open-Meteo endpoint |

## Test & Lint

```bash
uv run pytest -v
uv run ruff check .
```

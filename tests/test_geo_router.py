"""Tests for Phase 1 geofencing and JMA area router."""

from pathlib import Path

from fastapi.testclient import TestClient

from jma_wx_bridge.geo import GeoRouter
from jma_wx_bridge.main import app


def test_resolve_hachijojima_test_coords() -> None:
    router = GeoRouter.from_file()
    match = router.resolve(32.72367, 139.62445)
    assert match is not None
    assert match.office_code == "130000"
    assert match.class10_code == "130030"


def test_resolve_tokyo_suginami() -> None:
    router = GeoRouter.from_file()
    match = router.resolve(35.72, 139.62)
    assert match is not None
    assert match.office_code == "130000"
    assert match.class10_code == "130010"


def test_resolve_ogasawara_distinguishes_subregion() -> None:
    router = GeoRouter.from_file()
    match = router.resolve(27.09, 142.19)
    assert match is not None
    assert match.office_code == "130000"
    assert match.class10_code == "130040"


def test_resolve_osaka() -> None:
    router = GeoRouter.from_file()
    match = router.resolve(34.69, 135.50)
    assert match is not None
    assert match.office_code == "270000"


def test_resolve_outside_japan_returns_none() -> None:
    router = GeoRouter.from_file()
    assert router.resolve(40.71, -74.00) is None


def test_resolve_international_waters_inside_bbox_returns_none() -> None:
    router = GeoRouter.from_file()
    assert router.resolve(30.00, 148.00) is None


def test_polygon_hole_is_excluded() -> None:
    geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"office_code": "990000", "class10_code": "990010", "name": "Test"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [[135.0, 35.0], [136.0, 35.0], [136.0, 36.0], [135.0, 36.0], [135.0, 35.0]],
                        [[135.3, 35.3], [135.7, 35.3], [135.7, 35.7], [135.3, 35.7], [135.3, 35.3]],
                    ],
                },
            }
        ],
    }
    router = GeoRouter.from_geojson_data(geojson)
    assert router.resolve(35.1, 135.1) is not None
    assert router.resolve(35.5, 135.5) is None


def test_healthz_endpoint(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("JMA_WX_ENABLE_BACKGROUND_POLL", "false")
    monkeypatch.setenv("JMA_WX_DB_PATH", str(tmp_path / "test.db"))
    with TestClient(app) as client:
        health = client.get("/healthz")
        assert health.status_code == 200
        body = health.json()
        assert body["status"] == "ok"
        assert body["areas_loaded"] >= 10
        assert body["cached_forecasts"] == 0

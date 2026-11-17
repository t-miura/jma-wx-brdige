"""Point-in-polygon geofencing and JMA area code lookup."""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

# Coarse bounding box covering Japan including Okinawa/Yonaguni and Ogasawara/Minamitorishima.
JAPAN_MIN_LAT = 20.0
JAPAN_MAX_LAT = 46.0
JAPAN_MIN_LON = 122.0
JAPAN_MAX_LON = 154.5

Ring = list[tuple[float, float]]
PolygonRings = list[Ring]


@dataclass(frozen=True, slots=True)
class AreaMatch:
    """Resolved JMA forecast area for a coordinate pair."""

    office_code: str
    class10_code: str
    name: str
    name_en: str | None = None


@dataclass(frozen=True, slots=True)
class _AreaFeature:
    """Internal indexed GeoJSON feature with precomputed bounding box."""

    match: AreaMatch
    bbox: tuple[float, float, float, float]  # (min_lon, min_lat, max_lon, max_lat)
    polygons: list[PolygonRings]


def is_in_japan_bbox(lat: float, lon: float) -> bool:
    """Return True if (lat, lon) falls inside Japan's coarse outer bounding box."""
    return JAPAN_MIN_LAT <= lat <= JAPAN_MAX_LAT and JAPAN_MIN_LON <= lon <= JAPAN_MAX_LON


def _point_in_ring(lon: float, lat: float, ring: Ring) -> bool:
    """Ray-casting even-odd check for a single closed or unclosed coordinate ring."""
    inside = False
    n = len(ring)
    if n < 3:
        return False

    j = n - 1
    for i in range(n):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > lat) != (yj > lat):
            intersect_x = (xj - xi) * (lat - yi) / (yj - yi) + xi
            if lon < intersect_x:
                inside = not inside
        j = i
    return inside


def _point_in_polygon(lon: float, lat: float, rings: PolygonRings) -> bool:
    """Return True if (lon, lat) is inside the outer ring and outside any interior holes."""
    if not rings or not _point_in_ring(lon, lat, rings[0]):
        return False
    return not any(_point_in_ring(lon, lat, hole) for hole in rings[1:])


def _normalize_ring(raw_ring: list[list[float]]) -> Ring:
    return [(float(pt[0]), float(pt[1])) for pt in raw_ring]


def _extract_polygons(geometry: dict[str, Any]) -> list[PolygonRings]:
    gtype = geometry.get("type")
    coords = geometry.get("coordinates") or []
    if gtype == "Polygon":
        return [[_normalize_ring(ring) for ring in coords]]
    if gtype == "MultiPolygon":
        return [[_normalize_ring(ring) for ring in poly] for poly in coords]
    return []


def _compute_bbox(polygons: list[PolygonRings]) -> tuple[float, float, float, float]:
    lons = [pt[0] for poly in polygons for ring in poly for pt in ring]
    lats = [pt[1] for poly in polygons for ring in poly for pt in ring]
    return (min(lons), min(lats), max(lons), max(lats))


class GeoRouter:
    """Maps (lat, lon) coordinates to JMA forecast areas using local GeoJSON polygons."""

    def __init__(self, features: list[_AreaFeature]) -> None:
        self._features = features

    @property
    def area_count(self) -> int:
        """Number of loaded JMA area features."""
        return len(self._features)

    @classmethod
    def from_geojson_data(cls, data: dict[str, Any]) -> GeoRouter:
        """Build a GeoRouter from a parsed GeoJSON FeatureCollection dict."""
        features: list[_AreaFeature] = []
        for raw in data.get("features", []):
            props = raw.get("properties") or {}
            geom = raw.get("geometry") or {}
            polygons = _extract_polygons(geom)
            if not polygons:
                continue

            office_code = str(props.get("office_code") or props.get("parent") or "")
            class10_code = str(props.get("class10_code") or props.get("code") or office_code)
            name = str(props.get("name") or class10_code)
            name_en = props.get("name_en")

            if not office_code:
                continue

            features.append(
                _AreaFeature(
                    match=AreaMatch(
                        office_code=office_code,
                        class10_code=class10_code,
                        name=name,
                        name_en=str(name_en) if name_en is not None else None,
                    ),
                    bbox=_compute_bbox(polygons),
                    polygons=polygons,
                )
            )
        return cls(features)

    @classmethod
    def from_file(cls, path: Path | None = None) -> GeoRouter:
        """Load GeoRouter from a GeoJSON file path, or the local fetched dataset."""
        if path is not None:
            if not path.exists():
                raise FileNotFoundError(f"GeoJSON file not found: {path}")
            raw_text = path.read_text(encoding="utf-8")
        else:
            resource = resources.files("jma_wx_bridge.geo.data").joinpath("jma_areas.geojson")
            try:
                raw_text = resource.read_text(encoding="utf-8")
            except FileNotFoundError as exc:
                raise FileNotFoundError(
                    "JMA area GeoJSON not found. Run 'uv run python scripts/fetch_jma_geojson.py' "
                    "or set JMA_WX_GEOJSON_PATH."
                ) from exc
        return cls.from_geojson_data(json.loads(raw_text))

    def resolve(
        self,
        lat: float,
        lon: float,
        *,
        coastal_tolerance_deg: float = 0.5,
    ) -> AreaMatch | None:
        """Resolve (lat, lon) to a JMA AreaMatch, or None if outside Japan / unmapped."""
        if not is_in_japan_bbox(lat, lon):
            return None

        for feat in self._features:
            min_lon, min_lat, max_lon, max_lat = feat.bbox
            if not (min_lon <= lon <= max_lon and min_lat <= lat <= max_lat):
                continue
            for poly in feat.polygons:
                if _point_in_polygon(lon, lat, poly):
                    return feat.match

        # Coastal / small-island fallback: snap to nearest area within coastal_tolerance_deg.
        if coastal_tolerance_deg <= 0:
            return None
        best_dist_sq = coastal_tolerance_deg * coastal_tolerance_deg
        best_match: AreaMatch | None = None
        for feat in self._features:
            min_lon, min_lat, max_lon, max_lat = feat.bbox
            if (
                lon < min_lon - coastal_tolerance_deg
                or lon > max_lon + coastal_tolerance_deg
                or lat < min_lat - coastal_tolerance_deg
                or lat > max_lat + coastal_tolerance_deg
            ):
                continue
            for poly in feat.polygons:
                if not poly:
                    continue
                for vx, vy in poly[0]:
                    dist_sq = (vx - lon) ** 2 + (vy - lat) ** 2
                    if dist_sq <= best_dist_sq:
                        best_dist_sq = dist_sq
                        best_match = feat.match
        return best_match

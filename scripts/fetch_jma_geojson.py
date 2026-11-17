"""Fetch official JMA area.json and class10s.json GeoJSON and build jma_areas.geojson."""

from __future__ import annotations

import json
from pathlib import Path

import httpx

AREA_URL = "https://www.jma.go.jp/bosai/common/const/area.json"
CLASS10S_GEOJSON_URL = "https://www.jma.go.jp/bosai/common/const/geojson/class10s.json"
DEFAULT_OUTPUT = (
    Path(__file__).resolve().parent.parent
    / "src"
    / "jma_wx_bridge"
    / "geo"
    / "data"
    / "jma_areas.geojson"
)


def build_jma_geojson(output_path: Path = DEFAULT_OUTPUT) -> int:
    """Download JMA area metadata + Class10s polygons and write normalized GeoJSON."""
    with httpx.Client(timeout=20.0) as client:
        area_resp = client.get(AREA_URL)
        area_resp.raise_for_status()
        area_data = area_resp.json()

        geo_resp = client.get(CLASS10S_GEOJSON_URL)
        geo_resp.raise_for_status()
        geo_data = geo_resp.json()

    class10_meta: dict[str, dict[str, str]] = area_data.get("class10s", {})
    normalized_features = []

    for feat in geo_data.get("features", []):
        props = feat.get("properties") or {}
        geom = feat.get("geometry")
        code = str(props.get("code") or "")
        if not code or not geom:
            continue

        meta = class10_meta.get(code, {})
        office_code = str(meta.get("parent") or f"{code[:4]}00")
        name = str(meta.get("name") or props.get("name") or code)
        name_en = meta.get("enName") or props.get("enName")

        normalized_features.append(
            {
                "type": "Feature",
                "properties": {
                    "office_code": office_code,
                    "class10_code": code,
                    "name": name,
                    "name_en": name_en,
                },
                "geometry": geom,
            }
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output = {"type": "FeatureCollection", "features": normalized_features}
    output_path.write_text(json.dumps(output, ensure_ascii=False), encoding="utf-8")
    return len(normalized_features)


if __name__ == "__main__":
    count = build_jma_geojson()
    print(f"Wrote {count} JMA Class10s features to {DEFAULT_OUTPUT}")

"""Defensive parsers for JMA Atom feeds and VPFD51 XML forecast documents."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections import defaultdict

from jma_wx_bridge.jma.models import (
    AreaForecast,
    AtomEntry,
    PopSlot,
    StationTemperature,
    TempSlot,
    WeatherSlot,
)


def _local_name(tag: str) -> str:
    """Strip XML namespace URI from an ElementTree tag."""
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def _find_child(elem: ET.Element, name: str) -> ET.Element | None:
    for child in elem:
        if _local_name(child.tag) == name:
            return child
    return None


def _find_children(elem: ET.Element, name: str) -> list[ET.Element]:
    return [child for child in elem if _local_name(child.tag) == name]


def _iter_tag(elem: ET.Element, name: str) -> list[ET.Element]:
    return [node for node in elem.iter() if _local_name(node.tag) == name]


def _child_text(elem: ET.Element, name: str) -> str | None:
    child = _find_child(elem, name)
    if child is not None and child.text is not None:
        text = child.text.strip()
        return text or None
    return None


def _derive_office_code(class10_code: str) -> str:
    """Derive 6-digit JMA Office code from a Class10s code (e.g. 130030 -> 130000)."""
    if len(class10_code) == 6 and class10_code.isdigit():
        return f"{class10_code[:4]}00"
    return class10_code


def parse_atom_feed(xml_text: str, *, only_vpfd51: bool = True) -> list[AtomEntry]:
    """Parse JMA regular.xml Atom feed and optionally filter for VPFD51 entries."""
    root = ET.fromstring(xml_text)
    entries: list[AtomEntry] = []

    for entry in _find_children(root, "entry"):
        entry_id = _child_text(entry, "id") or ""
        title = _child_text(entry, "title") or ""
        updated = _child_text(entry, "updated") or ""

        url = ""
        for link in _find_children(entry, "link"):
            href = link.attrib.get("href", "").strip()
            if href:
                url = href
                break
        if not url and entry_id.startswith("http"):
            url = entry_id

        if not entry_id or not url:
            continue

        if only_vpfd51 and "府県天気予報" not in title and "VPFD51" not in url:
            continue

        entries.append(AtomEntry(entry_id=entry_id, title=title, updated=updated, url=url))

    return entries


def _parse_time_defines(ts_info: ET.Element) -> dict[str, tuple[str, str | None, str | None]]:
    """Return mapping of timeId -> (DateTime, Duration, Name)."""
    result: dict[str, tuple[str, str | None, str | None]] = {}
    td_parent = _find_child(ts_info, "TimeDefines")
    if td_parent is None:
        return result

    for td in _find_children(td_parent, "TimeDefine"):
        time_id = td.attrib.get("timeId", "").strip()
        dt = _child_text(td, "DateTime")
        if not time_id or not dt:
            continue
        duration = _child_text(td, "Duration")
        name = _child_text(td, "Name")
        result[time_id] = (dt, duration, name)
    return result


def parse_vpfd51_xml(xml_text: str, *, office_code: str | None = None) -> list[AreaForecast]:
    """Parse a JMA VPFD51 XML document into per-Class10s AreaForecast records."""
    root = ET.fromstring(xml_text)

    control = _find_child(root, "Control")
    publishing_office = _child_text(control, "PublishingOffice") if control is not None else None

    head = _find_child(root, "Head")
    report_datetime = (_child_text(head, "ReportDateTime") if head is not None else None) or ""

    body = _find_child(root, "Body")
    if body is None:
        return []

    area_names: dict[str, str] = {}
    weather_by_area: dict[str, dict[str, WeatherSlot]] = defaultdict(dict)
    pop_by_area: dict[str, list[PopSlot]] = defaultdict(list)
    stations: list[StationTemperature] = []

    for met_info in _find_children(body, "MeteorologicalInfos"):
        info_type = met_info.attrib.get("type", "")

        if info_type == "区域予報":
            for ts_info in _find_children(met_info, "TimeSeriesInfo"):
                time_map = _parse_time_defines(ts_info)
                for item in _find_children(ts_info, "Item"):
                    area = _find_child(item, "Area")
                    if area is None:
                        continue
                    code = _child_text(area, "Code")
                    name = _child_text(area, "Name") or code or ""
                    if not code:
                        continue
                    area_names[code] = name

                    for kind in _find_children(item, "Kind"):
                        prop = _find_child(kind, "Property")
                        if prop is None:
                            continue

                        for w_elem in _iter_tag(prop, "Weather"):
                            ref_id = w_elem.attrib.get("refID", "")
                            if ref_id not in time_map:
                                continue
                            dt, dur, label = time_map[ref_id]
                            slot = weather_by_area[code].setdefault(
                                ref_id, WeatherSlot(start_time=dt, duration=dur, label=label)
                            )
                            if w_elem.text and w_elem.text.strip():
                                slot.weather_text = w_elem.text.strip()

                        for wc_elem in _iter_tag(prop, "WeatherCode"):
                            ref_id = wc_elem.attrib.get("refID", "")
                            if ref_id not in time_map:
                                continue
                            dt, dur, label = time_map[ref_id]
                            slot = weather_by_area[code].setdefault(
                                ref_id, WeatherSlot(start_time=dt, duration=dur, label=label)
                            )
                            if wc_elem.text and wc_elem.text.strip():
                                slot.weather_code = wc_elem.text.strip()

                        for wind_elem in _iter_tag(prop, "Wind"):
                            ref_id = wind_elem.attrib.get("refID", "")
                            if ref_id not in time_map:
                                continue
                            dt, dur, label = time_map[ref_id]
                            slot = weather_by_area[code].setdefault(
                                ref_id, WeatherSlot(start_time=dt, duration=dur, label=label)
                            )
                            if wind_elem.text and wind_elem.text.strip():
                                slot.wind_text = wind_elem.text.strip()

                        for p_elem in _iter_tag(prop, "ProbabilityOfPrecipitation"):
                            ref_id = p_elem.attrib.get("refID", "")
                            if ref_id not in time_map:
                                continue
                            dt, dur, _ = time_map[ref_id]
                            raw_val = (p_elem.text or "").strip()
                            pop_val: int | None = None
                            if raw_val.isdigit():
                                pop_val = int(raw_val)
                            pop_by_area[code].append(
                                PopSlot(start_time=dt, duration=dur, pop_percent=pop_val)
                            )

        elif info_type == "地点予報":
            for ts_info in _find_children(met_info, "TimeSeriesInfo"):
                time_map = _parse_time_defines(ts_info)
                for item in _find_children(ts_info, "Item"):
                    station_elem = _find_child(item, "Station")
                    if station_elem is None:
                        continue
                    st_code = _child_text(station_elem, "Code") or ""
                    st_name = _child_text(station_elem, "Name") or st_code
                    if not st_code:
                        continue

                    temp_slots: list[TempSlot] = []
                    for t_elem in _iter_tag(item, "Temperature"):
                        ref_id = t_elem.attrib.get("refID", "")
                        if ref_id not in time_map:
                            continue
                        dt, _, label = time_map[ref_id]
                        raw_val = (t_elem.text or "").strip()
                        celsius: float | None = None
                        try:
                            if raw_val:
                                celsius = float(raw_val)
                        except ValueError:
                            celsius = None
                        temp_slots.append(
                            TempSlot(
                                target_time=dt,
                                label=label,
                                temp_type=t_elem.attrib.get("type"),
                                celsius=celsius,
                            )
                        )

                    if temp_slots:
                        stations.append(
                            StationTemperature(
                                station_code=st_code,
                                station_name=st_name,
                                temperatures=temp_slots,
                            )
                        )

    all_codes = sorted(set(area_names) | set(weather_by_area) | set(pop_by_area))
    forecasts: list[AreaForecast] = []
    for code in all_codes:
        resolved_office = office_code or _derive_office_code(code)
        w_slots = [
            weather_by_area[code][k]
            for k in sorted(weather_by_area[code], key=lambda x: int(x) if x.isdigit() else 0)
        ]
        forecasts.append(
            AreaForecast(
                office_code=resolved_office,
                class10_code=code,
                area_name=area_names.get(code, code),
                report_datetime=report_datetime,
                publishing_office=publishing_office,
                weather=w_slots,
                pop=pop_by_area.get(code, []),
                stations=stations,
            )
        )

    return forecasts

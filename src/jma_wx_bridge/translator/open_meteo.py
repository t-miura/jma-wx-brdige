"""Translate JMA AreaForecast records into Open-Meteo /v1/forecast JSON responses."""

from __future__ import annotations

import math
import re
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from jma_wx_bridge.jma.models import AreaForecast, StationTemperature
from jma_wx_bridge.translator.wmo import telops_to_wmo

JST = ZoneInfo("Asia/Tokyo")
_DURATION_RE = re.compile(r"^P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<mins>\d+)M)?)?$")


def _parse_iso_duration(raw: str | None, default_hours: int = 24) -> timedelta:
    if not raw:
        return timedelta(hours=default_hours)
    m = _DURATION_RE.match(raw.strip())
    if not m:
        return timedelta(hours=default_hours)
    days = int(m.group("days") or 0)
    hours = int(m.group("hours") or 0)
    mins = int(m.group("mins") or 0)
    td = timedelta(days=days, hours=hours, minutes=mins)
    return td if td.total_seconds() > 0 else timedelta(hours=default_hours)


def _resolve_timezone(tz_param: str) -> tuple[ZoneInfo | UTC.__class__, str, str]:
    """Return (tzinfo, timezone_name, abbreviation)."""
    cleaned = (tz_param or "GMT").strip()
    if cleaned.lower() in {"auto", "asia/tokyo"}:
        return JST, "Asia/Tokyo", "JST"
    if cleaned.upper() in {"GMT", "UTC"}:
        return UTC, "GMT", "GMT"
    try:
        zi = ZoneInfo(cleaned)
        abbr = datetime.now(zi).tzname() or cleaned
        return zi, cleaned, abbr
    except Exception:
        return JST, "Asia/Tokyo", "JST"


def _convert_temp(celsius: float | None, unit: str) -> float | None:
    if celsius is None:
        return None
    if unit == "fahrenheit":
        return round(celsius * 9.0 / 5.0 + 32.0, 1)
    return round(celsius, 1)


def _wind_unit_label(unit: str) -> str:
    return {
        "kmh": "km/h",
        "mph": "mp/h",
        "ms": "m/s",
        "kn": "kn",
    }.get(unit, "km/h")


def _convert_wind_from_ms(speed_ms: float, unit: str) -> float:
    factor = {
        "kmh": 3.6,
        "mph": 2.23694,
        "ms": 1.0,
        "kn": 1.94384,
    }.get(unit, 3.6)
    return round(speed_ms * factor, 1)


def _parse_wind_text(wind_text: str | None) -> tuple[float, float]:
    """Extract approximate (direction_deg, speed_ms) from JMA Japanese wind text."""
    if not wind_text:
        return (0.0, 4.0)

    direction = 0.0
    for token, deg in (
        ("北東", 45.0),
        ("南東", 135.0),
        ("南西", 225.0),
        ("北西", 315.0),
        ("北", 0.0),
        ("東", 90.0),
        ("南", 180.0),
        ("西", 270.0),
    ):
        if token in wind_text:
            direction = deg
            break

    speed_ms = 4.0
    if "猛烈" in wind_text:
        speed_ms = 25.0
    elif "非常に強く" in wind_text:
        speed_ms = 20.0
    elif "やや強く" in wind_text:
        speed_ms = 9.0
    elif "強く" in wind_text:
        speed_ms = 13.0

    return (direction, speed_ms)


def _parse_iso_dt(raw: str) -> datetime:
    """Parse an ISO-8601 timestamp, defaulting naive values to JST."""
    dt = datetime.fromisoformat(raw)
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=JST)


def _weather_slot_for_time(forecast: AreaForecast, target: datetime):
    if not forecast.weather:
        return None
    for slot in forecast.weather:
        start = _parse_iso_dt(slot.start_time)
        end = start + _parse_iso_duration(slot.duration, default_hours=24)
        if start <= target < end:
            return slot
    return min(
        forecast.weather,
        key=lambda s: abs((_parse_iso_dt(s.start_time) - target).total_seconds()),
    )


def _select_station(forecast: AreaForecast) -> StationTemperature | None:
    if not forecast.stations:
        return None
    for st in forecast.stations:
        if st.station_name and st.station_name in forecast.area_name:
            return st
    return forecast.stations[0]


def _diurnal_temp(hour_local: int, t_min: float, t_max: float) -> float:
    """Synthesize hourly temperature with trough at 05:00 and peak at 14:00."""
    t_peak = 14
    t_trough = 5
    if t_trough <= hour_local <= t_peak:
        # Rising limb: 05:00 -> 14:00 (9 hours)
        phase = (hour_local - t_trough) / (t_peak - t_trough)
        weight = 0.5 * (1.0 - math.cos(math.pi * phase))
    else:
        # Falling limb: 14:00 -> next 05:00 (15 hours)
        elapsed = (hour_local - t_peak) if hour_local > t_peak else (24 - t_peak + hour_local)
        phase = elapsed / (24 - (t_peak - t_trough))
        weight = 0.5 * (1.0 + math.cos(math.pi * phase))
    return round(t_min + (t_max - t_min) * weight, 1)


def _build_daily_minmax(
    station: StationTemperature | None,
) -> dict[date, tuple[float | None, float | None]]:
    """Extract (min_c, max_c) keyed by local JST date."""
    by_day: dict[date, list[tuple[str | None, float]]] = defaultdict(list)
    if station is None:
        return {}

    for slot in station.temperatures:
        if slot.celsius is None:
            continue
        dt = datetime.fromisoformat(slot.target_time).astimezone(JST)
        by_day[dt.date()].append((slot.temp_type, slot.celsius))

    result: dict[date, tuple[float | None, float | None]] = {}
    for d, items in by_day.items():
        mins = [val for ttype, val in items if ttype and "最低" in ttype]
        maxs = [val for ttype, val in items if ttype and "最高" in ttype]
        t_min = min(mins) if mins else min(val for _, val in items)
        t_max = max(maxs) if maxs else max(val for _, val in items)
        result[d] = (t_min, t_max)
    return result


def _weather_for_time(forecast: AreaForecast, target: datetime) -> int | None:
    slot = _weather_slot_for_time(forecast, target)
    return telops_to_wmo(slot.weather_code) if slot is not None else None


def _pop_for_time(forecast: AreaForecast, target: datetime) -> int | None:
    for slot in forecast.pop:
        start = _parse_iso_dt(slot.start_time)
        end = start + _parse_iso_duration(slot.duration, default_hours=6)
        if start <= target < end:
            return slot.pop_percent
    return None


def translate_forecast(
    forecast: AreaForecast,
    *,
    latitude: float,
    longitude: float,
    hourly: list[str] | None = None,
    daily: list[str] | None = None,
    current: list[str] | None = None,
    timezone: str = "GMT",
    temperature_unit: str = "celsius",
    wind_speed_unit: str = "kmh",
    forecast_days: int = 3,
    generationtime_ms: float = 0.5,
    now: datetime | None = None,
) -> dict[str, object]:
    """Translate an AreaForecast into an Open-Meteo /v1/forecast JSON-compatible dict."""
    tzinfo, tz_name, tz_abbr = _resolve_timezone(timezone)
    ref_now = (now or datetime.fromisoformat(forecast.report_datetime)).astimezone(tzinfo)
    utc_offset = int((ref_now.utcoffset() or timedelta(0)).total_seconds())

    start_hour = ref_now.replace(minute=0, second=0, microsecond=0)
    if forecast.weather:
        first_slot_dt = datetime.fromisoformat(forecast.weather[0].start_time).astimezone(tzinfo)
        start_hour = first_slot_dt.replace(minute=0, second=0, microsecond=0)

    total_hours = max(1, min(forecast_days, 7)) * 24
    hourly_times = [start_hour + timedelta(hours=i) for i in range(total_hours)]

    station = _select_station(forecast)
    daily_minmax = _build_daily_minmax(station)

    # Global fallback min/max if a specific day lacks station entries
    all_mins = [v[0] for v in daily_minmax.values() if v[0] is not None]
    all_maxs = [v[1] for v in daily_minmax.values() if v[1] is not None]
    fallback_min = sum(all_mins) / len(all_mins) if all_mins else None
    fallback_max = sum(all_maxs) / len(all_maxs) if all_maxs else None

    def _temp_at(dt: datetime) -> float | None:
        d = dt.astimezone(JST).date()
        t_min, t_max = daily_minmax.get(d, (fallback_min, fallback_max))
        if t_min is None and t_max is None:
            return None
        lo = t_min if t_min is not None else t_max
        hi = t_max if t_max is not None else t_min
        assert lo is not None and hi is not None
        if lo > hi:
            lo, hi = hi, lo
        c_val = _diurnal_temp(dt.astimezone(JST).hour, lo, hi)
        return _convert_temp(c_val, temperature_unit)

    temp_unit_label = "°F" if temperature_unit == "fahrenheit" else "°C"

    payload: dict[str, object] = {
        "latitude": latitude,
        "longitude": longitude,
        "generationtime_ms": round(generationtime_ms, 3),
        "utc_offset_seconds": utc_offset,
        "timezone": tz_name,
        "timezone_abbreviation": tz_abbr,
        "elevation": 0.0,
    }

    if current:
        curr_dt = ref_now.replace(minute=0, second=0, microsecond=0)
        curr_units: dict[str, str] = {"time": "iso8601", "interval": "seconds"}
        curr_data: dict[str, object] = {
            "time": curr_dt.strftime("%Y-%m-%dT%H:%M"),
            "interval": 3600,
        }
        for var in current:
            if var == "temperature_2m":
                curr_units[var] = temp_unit_label
                curr_data[var] = _temp_at(curr_dt)
            elif var == "weather_code":
                curr_units[var] = "wmo code"
                curr_data[var] = _weather_for_time(forecast, curr_dt)
            elif var == "precipitation_probability":
                curr_units[var] = "%"
                curr_data[var] = _pop_for_time(forecast, curr_dt)
        payload["current_units"] = curr_units
        payload["current"] = curr_data

    if hourly:
        h_units: dict[str, str] = {"time": "iso8601"}
        h_data: dict[str, object] = {"time": [dt.strftime("%Y-%m-%dT%H:%M") for dt in hourly_times]}
        for var in hourly:
            if var == "temperature_2m":
                h_units[var] = temp_unit_label
                h_data[var] = [_temp_at(dt) for dt in hourly_times]
            elif var == "weather_code":
                h_units[var] = "wmo code"
                h_data[var] = [_weather_for_time(forecast, dt) for dt in hourly_times]
            elif var == "precipitation_probability":
                h_units[var] = "%"
                h_data[var] = [_pop_for_time(forecast, dt) for dt in hourly_times]
        payload["hourly_units"] = h_units
        payload["hourly"] = h_data

    if daily:
        start_date = start_hour.date()
        dates = [start_date + timedelta(days=i) for i in range(max(1, min(forecast_days, 7)))]
        d_units: dict[str, str] = {"time": "iso8601"}
        d_data: dict[str, object] = {"time": [d.isoformat() for d in dates]}
        wind_unit_lbl = _wind_unit_label(wind_speed_unit)

        def _day_slot(d: date):
            return _weather_slot_for_time(
                forecast, datetime(d.year, d.month, d.day, 12, 0, tzinfo=tzinfo)
            )

        for var in daily:
            if var == "weather_code":
                d_units[var] = "wmo code"
                d_data[var] = [
                    telops_to_wmo(slot.weather_code) if (slot := _day_slot(d)) else 0 for d in dates
                ]
            elif var == "temperature_2m_max":
                d_units[var] = temp_unit_label
                vals = []
                for d in dates:
                    raw_max = daily_minmax.get(d, (None, None))[1]
                    vals.append(
                        _convert_temp(
                            raw_max if raw_max is not None else fallback_max,
                            temperature_unit,
                        )
                    )
                d_data[var] = vals
            elif var == "temperature_2m_min":
                d_units[var] = temp_unit_label
                vals = []
                for d in dates:
                    raw_min = daily_minmax.get(d, (None, None))[0]
                    vals.append(
                        _convert_temp(
                            raw_min if raw_min is not None else fallback_min,
                            temperature_unit,
                        )
                    )
                d_data[var] = vals
            elif var == "precipitation_probability_max":
                d_units[var] = "%"
                max_pops: list[int | None] = []
                for d in dates:
                    day_vals = [
                        p
                        for h in range(24)
                        if (
                            p := _pop_for_time(
                                forecast, datetime(d.year, d.month, d.day, h, 0, tzinfo=tzinfo)
                            )
                        )
                        is not None
                    ]
                    max_pops.append(max(day_vals) if day_vals else None)
                d_data[var] = max_pops
            elif var == "precipitation_hours":
                d_units[var] = "h"
                precip_hours: list[float] = []
                for d in dates:
                    wet_hours = sum(
                        1.0
                        for h in range(24)
                        if (
                            p := _pop_for_time(
                                forecast, datetime(d.year, d.month, d.day, h, 0, tzinfo=tzinfo)
                            )
                        )
                        is not None
                        and p >= 50
                    )
                    if wet_hours == 0.0:
                        slot = _day_slot(d)
                        wmo = telops_to_wmo(slot.weather_code) if slot else None
                        if wmo is not None and wmo >= 51:
                            wet_hours = 6.0
                    precip_hours.append(wet_hours)
                d_data[var] = precip_hours
            elif var == "wind_speed_10m_max":
                d_units[var] = wind_unit_lbl
                d_data[var] = [
                    _convert_wind_from_ms(
                        _parse_wind_text(slot.wind_text if (slot := _day_slot(d)) else None)[1],
                        wind_speed_unit,
                    )
                    for d in dates
                ]
            elif var == "wind_gusts_10m_max":
                d_units[var] = wind_unit_lbl
                d_data[var] = [
                    _convert_wind_from_ms(
                        _parse_wind_text(slot.wind_text if (slot := _day_slot(d)) else None)[1]
                        * 1.4,
                        wind_speed_unit,
                    )
                    for d in dates
                ]
            elif var == "wind_direction_10m_dominant":
                d_units[var] = "°"
                d_data[var] = [
                    _parse_wind_text(slot.wind_text if (slot := _day_slot(d)) else None)[0]
                    for d in dates
                ]

        payload["daily_units"] = d_units
        payload["daily"] = d_data

    return payload

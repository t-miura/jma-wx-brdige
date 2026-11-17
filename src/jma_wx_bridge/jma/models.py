"""Data models for parsed JMA Atom entries and VPFD51 forecasts."""

from __future__ import annotations

from pydantic import BaseModel, Field


class AtomEntry(BaseModel):
    """Single entry from JMA's regular.xml Atom feed."""

    entry_id: str
    title: str
    updated: str
    url: str


class WeatherSlot(BaseModel):
    """Weather condition and TELOPS code for a forecast time period."""

    start_time: str
    duration: str | None = None
    label: str | None = None
    weather_code: str | None = None
    weather_text: str | None = None
    wind_text: str | None = None


class PopSlot(BaseModel):
    """6-hour probability of precipitation (%) slot."""

    start_time: str
    duration: str | None = None
    pop_percent: int | None = None


class TempSlot(BaseModel):
    """Daily min/max temperature slot for a station."""

    target_time: str
    label: str | None = None
    temp_type: str | None = None
    celsius: float | None = None


class StationTemperature(BaseModel):
    """Temperature forecast series for a JMA station."""

    station_code: str
    station_name: str
    temperatures: list[TempSlot] = Field(default_factory=list)


class AreaForecast(BaseModel):
    """Parsed VPFD51 forecast for a single JMA Class10s area."""

    office_code: str
    class10_code: str
    area_name: str
    report_datetime: str
    publishing_office: str | None = None
    weather: list[WeatherSlot] = Field(default_factory=list)
    pop: list[PopSlot] = Field(default_factory=list)
    stations: list[StationTemperature] = Field(default_factory=list)

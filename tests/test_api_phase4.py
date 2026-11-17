"""End-to-end tests for Phase 4 /v1/forecast proxy, JMA local cache, and fallback."""

from __future__ import annotations

from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from jma_wx_bridge.jma import parse_vpfd51_xml
from jma_wx_bridge.main import app

SAMPLE_VPFD51_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Report xmlns="http://xml.kishou.go.jp/jmaxml1/"
        xmlns:jmx_ib="http://xml.kishou.go.jp/jmaxml1/informationBasis1/"
        xmlns:jmx_mete="http://xml.kishou.go.jp/jmaxml1/body/meteorology1/"
        xmlns:jmx_eb="http://xml.kishou.go.jp/jmaxml1/elementBasis1/">
  <Control><Title>府県天気予報</Title><PublishingOffice>気象庁</PublishingOffice></Control>
  <Head xmlns="http://xml.kishou.go.jp/jmaxml1/informationBasis1/">
    <Title>東京都天気予報</Title>
    <ReportDateTime>2026-11-17T11:00:00+09:00</ReportDateTime>
  </Head>
  <Body xmlns="http://xml.kishou.go.jp/jmaxml1/body/meteorology1/">
    <MeteorologicalInfos type="区域予報">
      <TimeSeriesInfo>
        <TimeDefines>
          <TimeDefine timeId="1">
            <DateTime>2026-11-17T11:00:00+09:00</DateTime>
            <Duration>PT13H</Duration>
          </TimeDefine>
          <TimeDefine timeId="2">
            <DateTime>2026-11-18T00:00:00+09:00</DateTime>
            <Duration>PT24H</Duration>
          </TimeDefine>
        </TimeDefines>
        <Item>
          <Kind><Property><Type>天気コード</Type><WeatherCodePart>
            <jmx_eb:WeatherCode refID="1">202</jmx_eb:WeatherCode>
            <jmx_eb:WeatherCode refID="2">100</jmx_eb:WeatherCode>
          </WeatherCodePart></Property></Kind>
          <Area><Name>伊豆諸島南部</Name><Code>130030</Code></Area>
        </Item>
      </TimeSeriesInfo>
      <TimeSeriesInfo>
        <TimeDefines>
          <TimeDefine timeId="1">
            <DateTime>2026-11-17T12:00:00+09:00</DateTime>
            <Duration>PT6H</Duration>
          </TimeDefine>
        </TimeDefines>
        <Item>
          <Kind><Property><Type>降水確率</Type><ProbabilityOfPrecipitationPart>
            <jmx_eb:ProbabilityOfPrecipitation
                refID="1" unit="%">30</jmx_eb:ProbabilityOfPrecipitation>
          </ProbabilityOfPrecipitationPart></Property></Kind>
          <Area><Name>伊豆諸島南部</Name><Code>130030</Code></Area>
        </Item>
      </TimeSeriesInfo>
    </MeteorologicalInfos>
    <MeteorologicalInfos type="地点予報">
      <TimeSeriesInfo>
        <TimeDefines>
          <TimeDefine timeId="1"><DateTime>2026-11-17T09:00:00+09:00</DateTime></TimeDefine>
          <TimeDefine timeId="2"><DateTime>2026-11-18T00:00:00+09:00</DateTime></TimeDefine>
        </TimeDefines>
        <Item>
          <Kind><Property><Type>最高気温</Type><TemperaturePart>
            <jmx_eb:Temperature refID="1" type="最高気温" unit="度">21</jmx_eb:Temperature>
            <jmx_eb:Temperature refID="2" type="最低気温" unit="度">15</jmx_eb:Temperature>
          </TemperaturePart></Property></Kind>
          <Station><Name>八丈島</Name><Code>44263</Code></Station>
        </Item>
      </TimeSeriesInfo>
    </MeteorologicalInfos>
  </Body>
</Report>
"""


def test_case_a_and_c_japan_cache_hit_even_during_wan_outage(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("JMA_WX_ENABLE_BACKGROUND_POLL", "false")
    monkeypatch.setenv("JMA_WX_DB_PATH", str(tmp_path / "phase4_a.db"))

    def failing_upstream(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("WAN down", request=request)

    with TestClient(app) as client:
        for fc in parse_vpfd51_xml(SAMPLE_VPFD51_XML):
            app.state.cache.upsert_forecast(fc)
        app.state.http_client = httpx.AsyncClient(transport=httpx.MockTransport(failing_upstream))

        resp = client.get(
            "/v1/forecast",
            params={
                "latitude": 32.72367,
                "longitude": 139.62445,
                "hourly": "temperature_2m,weather_code,precipitation_probability",
                "daily": "weather_code,temperature_2m_max,temperature_2m_min",
                "timezone": "Asia/Tokyo",
                "forecast_days": 2,
            },
        )
        assert resp.status_code == 200
        assert resp.headers["X-JMA-WX-Source"] == "jma-cache"
        assert resp.headers["X-JMA-Area-Code"] == "130030"
        data = resp.json()
        assert data["timezone"] == "Asia/Tokyo"
        assert len(data["hourly"]["temperature_2m"]) == 48
        assert data["daily"]["weather_code"] == [61, 0]


def test_case_b_open_meteo_passthrough_and_stale_fallback(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("JMA_WX_ENABLE_BACKGROUND_POLL", "false")
    monkeypatch.setenv("JMA_WX_DB_PATH", str(tmp_path / "phase4_b.db"))

    mode = {"fail": False}

    def upstream_handler(request: httpx.Request) -> httpx.Response:
        if mode["fail"]:
            return httpx.Response(503, json={"error": True, "reason": "Overloaded"})
        return httpx.Response(
            200,
            json={
                "latitude": 40.71,
                "longitude": -74.0,
                "timezone": "America/New_York",
                "current": {"temperature_2m": 12.3, "weather_code": 1},
            },
        )

    with TestClient(app) as client:
        app.state.http_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream_handler))

        params = {
            "latitude": 40.71,
            "longitude": -74.00,
            "current": "temperature_2m,weather_code",
            "timezone": "auto",
        }
        first = client.get("/v1/forecast", params=params)
        assert first.status_code == 200
        assert first.headers["X-JMA-WX-Source"] == "open-meteo"
        assert first.json()["current"]["temperature_2m"] == 12.3

        # Simulate Open-Meteo 503 outage -> stale cache serves the last good response
        mode["fail"] = True
        second = client.get("/v1/forecast", params=params)
        assert second.status_code == 200
        assert second.headers["X-JMA-WX-Source"] == "open-meteo-stale-cache"
        assert second.json()["current"]["temperature_2m"] == 12.3

        # Uncached outside-JP location during outage -> clean 502
        uncached = client.get("/v1/forecast", params={"latitude": 51.50, "longitude": -0.12})
        assert uncached.status_code == 502


def test_meshing_around_wx_meteo_compatibility(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("JMA_WX_ENABLE_BACKGROUND_POLL", "false")
    monkeypatch.setenv("JMA_WX_DB_PATH", str(tmp_path / "phase4_mesh.db"))

    with TestClient(app) as client:
        for fc in parse_vpfd51_xml(SAMPLE_VPFD51_XML):
            app.state.cache.upsert_forecast(fc)

        resp = client.get(
            "/v1/forecast",
            params={
                "latitude": 32.72367,
                "longitude": 139.62445,
                "daily": [
                    "weather_code",
                    "temperature_2m_max",
                    "temperature_2m_min",
                    "precipitation_hours",
                    "precipitation_probability_max",
                    "wind_speed_10m_max",
                    "wind_gusts_10m_max",
                    "wind_direction_10m_dominant",
                ],
                "timezone": "auto",
                "forecast_days": 3,
                "temperature_unit": "fahrenheit",
                "wind_speed_unit": "mph",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["timezone"] == "Asia/Tokyo"
        assert body["timezone_abbreviation"] == "JST"
        daily = body["daily"]
        expected_keys = (
            "weather_code",
            "temperature_2m_max",
            "temperature_2m_min",
            "precipitation_hours",
            "precipitation_probability_max",
            "wind_speed_10m_max",
            "wind_gusts_10m_max",
            "wind_direction_10m_dominant",
        )
        for key in expected_keys:
            assert key in daily
            assert len(daily[key]) == 3
        assert all(isinstance(v, (int, float)) for v in daily["wind_direction_10m_dominant"])
        assert all(isinstance(v, (int, float)) for v in daily["precipitation_hours"])
        assert body["daily_units"]["wind_speed_10m_max"] == "mp/h"

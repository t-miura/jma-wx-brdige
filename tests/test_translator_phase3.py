"""Tests for Phase 3 TELOPS-to-WMO mapping and Open-Meteo JSON translation."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from jma_wx_bridge.jma import parse_vpfd51_xml
from jma_wx_bridge.translator import telops_to_wmo, translate_forecast

JST = ZoneInfo("Asia/Tokyo")

HACHIJOJIMA_VPFD51_XML = """<?xml version="1.0" encoding="UTF-8"?>
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
          <TimeDefine timeId="2">
            <DateTime>2026-11-17T18:00:00+09:00</DateTime>
            <Duration>PT6H</Duration>
          </TimeDefine>
        </TimeDefines>
        <Item>
          <Kind><Property><Type>降水確率</Type><ProbabilityOfPrecipitationPart>
            <jmx_eb:ProbabilityOfPrecipitation
                refID="1" unit="%">30</jmx_eb:ProbabilityOfPrecipitation>
            <jmx_eb:ProbabilityOfPrecipitation
                refID="2" unit="%">--</jmx_eb:ProbabilityOfPrecipitation>
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


def test_telops_to_wmo_mapping_and_fallbacks() -> None:
    assert telops_to_wmo("100") == 0
    assert telops_to_wmo("200") == 3
    assert telops_to_wmo("202") == 61
    assert telops_to_wmo("300") == 63
    assert telops_to_wmo("400") == 73
    assert telops_to_wmo("199") == 0
    assert telops_to_wmo("299") == 3
    assert telops_to_wmo("399") == 63
    assert telops_to_wmo("499") == 73
    assert telops_to_wmo(None) is None


def test_translate_forecast_hachijojima_hourly_daily_current() -> None:
    forecasts = parse_vpfd51_xml(HACHIJOJIMA_VPFD51_XML)
    izu_south = next(fc for fc in forecasts if fc.class10_code == "130030")

    result = translate_forecast(
        izu_south,
        latitude=32.72367,
        longitude=139.62445,
        hourly=["temperature_2m", "weather_code", "precipitation_probability"],
        daily=[
            "weather_code",
            "temperature_2m_max",
            "temperature_2m_min",
            "precipitation_probability_max",
        ],
        current=["temperature_2m", "weather_code", "precipitation_probability"],
        timezone="Asia/Tokyo",
        forecast_days=2,
        now=datetime(2026, 11, 17, 12, 0, tzinfo=JST),
    )

    assert result["latitude"] == 32.72367
    assert result["longitude"] == 139.62445
    assert result["timezone"] == "Asia/Tokyo"
    assert result["timezone_abbreviation"] == "JST"
    assert result["utc_offset_seconds"] == 32400

    hourly = result["hourly"]
    assert isinstance(hourly, dict)
    assert len(hourly["time"]) == 48
    assert len(hourly["temperature_2m"]) == 48
    assert len(hourly["weather_code"]) == 48
    assert len(hourly["precipitation_probability"]) == 48
    # 11:00 -> 12:00..17:00 should have 30% PoP from the 12:00 PT6H slot
    idx_12 = hourly["time"].index("2026-11-17T12:00")
    assert hourly["precipitation_probability"][idx_12 : idx_12 + 6] == [30] * 6
    # 14:00 should be at the diurnal max (21.0 C)
    idx_14 = hourly["time"].index("2026-11-17T14:00")
    assert hourly["temperature_2m"][idx_14] == 21.0
    # 202 (Cloudy with Rain) -> WMO 61 for 2026-11-17
    assert hourly["weather_code"][idx_12] == 61

    daily = result["daily"]
    assert isinstance(daily, dict)
    assert daily["time"] == ["2026-11-17", "2026-11-18"]
    assert daily["weather_code"] == [61, 0]
    assert daily["precipitation_probability_max"] == [30, None]

    current = result["current"]
    assert isinstance(current, dict)
    assert current["time"] == "2026-11-17T12:00"
    assert current["weather_code"] == 61
    assert current["precipitation_probability"] == 30


def test_translate_forecast_fahrenheit_conversion() -> None:
    forecasts = parse_vpfd51_xml(HACHIJOJIMA_VPFD51_XML)
    izu_south = next(fc for fc in forecasts if fc.class10_code == "130030")

    result = translate_forecast(
        izu_south,
        latitude=32.72367,
        longitude=139.62445,
        hourly=["temperature_2m"],
        timezone="Asia/Tokyo",
        temperature_unit="fahrenheit",
        forecast_days=1,
    )
    assert result["hourly_units"]["temperature_2m"] == "°F"
    idx_14 = result["hourly"]["time"].index("2026-11-17T14:00")
    assert result["hourly"]["temperature_2m"][idx_14] == 69.8

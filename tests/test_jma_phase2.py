"""Tests for Phase 2 JMA Atom/VPFD51 XML parsing, SQLite cache, and worker polling."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from jma_wx_bridge.cache import SQLiteCache
from jma_wx_bridge.geo import GeoRouter
from jma_wx_bridge.jma import JMAWorker, parse_atom_feed, parse_vpfd51_xml

SAMPLE_ATOM_XML = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>高頻度フィード（定時）</title>
  <entry>
    <title>府県天気予報</title>
    <id>urn:uuid:tokyo-vpfd51-1</id>
    <updated>2026-11-17T02:00:00Z</updated>
    <link type="application/xml" href="https://example.jma.test/xml/tokyo_vpfd51.xml"/>
  </entry>
  <entry>
    <title>気象特別警報・警報・注意報</title>
    <id>urn:uuid:other-vpww54-1</id>
    <updated>2026-11-17T02:00:00Z</updated>
    <link type="application/xml" href="https://example.jma.test/xml/other_vpww54.xml"/>
  </entry>
</feed>
"""

SAMPLE_VPFD51_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Report xmlns="http://xml.kishou.go.jp/jmaxml1/"
        xmlns:jmx_ib="http://xml.kishou.go.jp/jmaxml1/informationBasis1/"
        xmlns:jmx_mete="http://xml.kishou.go.jp/jmaxml1/body/meteorology1/"
        xmlns:jmx_eb="http://xml.kishou.go.jp/jmaxml1/elementBasis1/">
  <Control>
    <Title>府県天気予報</Title>
    <PublishingOffice>気象庁</PublishingOffice>
  </Control>
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
            <Name>今日</Name>
          </TimeDefine>
          <TimeDefine timeId="2">
            <DateTime>2026-11-18T00:00:00+09:00</DateTime>
            <Duration>PT24H</Duration>
            <Name>明日</Name>
          </TimeDefine>
        </TimeDefines>
        <Item>
          <Kind>
            <Property>
              <Type>天気</Type>
              <WeatherPart>
                <jmx_eb:Weather refID="1">晴れ</jmx_eb:Weather>
                <jmx_eb:Weather refID="2">くもり</jmx_eb:Weather>
              </WeatherPart>
            </Property>
          </Kind>
          <Kind>
            <Property>
              <Type>天気コード</Type>
              <WeatherCodePart>
                <jmx_eb:WeatherCode refID="1">100</jmx_eb:WeatherCode>
                <jmx_eb:WeatherCode refID="2">200</jmx_eb:WeatherCode>
              </WeatherCodePart>
            </Property>
          </Kind>
          <Area>
            <Name>東京地方</Name>
            <Code>130010</Code>
          </Area>
        </Item>
        <Item>
          <Kind>
            <Property>
              <Type>天気</Type>
              <WeatherPart>
                <jmx_eb:Weather refID="1">くもり 所により 雨</jmx_eb:Weather>
                <jmx_eb:Weather refID="2">晴れ</jmx_eb:Weather>
              </WeatherPart>
            </Property>
          </Kind>
          <Kind>
            <Property>
              <Type>天気コード</Type>
              <WeatherCodePart>
                <jmx_eb:WeatherCode refID="1">202</jmx_eb:WeatherCode>
                <jmx_eb:WeatherCode refID="2">100</jmx_eb:WeatherCode>
              </WeatherCodePart>
            </Property>
          </Kind>
          <Area>
            <Name>伊豆諸島南部</Name>
            <Code>130030</Code>
          </Area>
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
          <Kind>
            <Property>
              <Type>降水確率</Type>
              <ProbabilityOfPrecipitationPart>
                <jmx_eb:ProbabilityOfPrecipitation
                    refID="1" unit="%">30</jmx_eb:ProbabilityOfPrecipitation>
                <jmx_eb:ProbabilityOfPrecipitation
                    refID="2" unit="%">--</jmx_eb:ProbabilityOfPrecipitation>
              </ProbabilityOfPrecipitationPart>
            </Property>
          </Kind>
          <Area>
            <Name>伊豆諸島南部</Name>
            <Code>130030</Code>
          </Area>
        </Item>
      </TimeSeriesInfo>
    </MeteorologicalInfos>
    <MeteorologicalInfos type="地点予報">
      <TimeSeriesInfo>
        <TimeDefines>
          <TimeDefine timeId="1">
            <DateTime>2026-11-17T09:00:00+09:00</DateTime>
            <Name>日中の最高気温</Name>
          </TimeDefine>
          <TimeDefine timeId="2">
            <DateTime>2026-11-18T00:00:00+09:00</DateTime>
            <Name>朝の最低気温</Name>
          </TimeDefine>
        </TimeDefines>
        <Item>
          <Kind>
            <Property>
              <Type>最高気温</Type>
              <TemperaturePart>
                <jmx_eb:Temperature refID="1" type="最高気温" unit="度">21</jmx_eb:Temperature>
                <jmx_eb:Temperature refID="2" type="最低気温" unit="度">15</jmx_eb:Temperature>
              </TemperaturePart>
            </Property>
          </Kind>
          <Station>
            <Name>八丈島</Name>
            <Code>44263</Code>
          </Station>
        </Item>
      </TimeSeriesInfo>
    </MeteorologicalInfos>
  </Body>
</Report>
"""


def test_parse_atom_feed_filters_vpfd51() -> None:
    entries = parse_atom_feed(SAMPLE_ATOM_XML, only_vpfd51=True)
    assert len(entries) == 1
    assert entries[0].entry_id == "urn:uuid:tokyo-vpfd51-1"
    assert entries[0].url == "https://example.jma.test/xml/tokyo_vpfd51.xml"


def test_parse_vpfd51_xml_extracts_areas_pop_and_stations() -> None:
    forecasts = parse_vpfd51_xml(SAMPLE_VPFD51_XML)
    by_code = {fc.class10_code: fc for fc in forecasts}

    assert set(by_code) == {"130010", "130030"}
    izu_south = by_code["130030"]
    assert izu_south.office_code == "130000"
    assert izu_south.area_name == "伊豆諸島南部"
    assert izu_south.report_datetime == "2026-11-17T11:00:00+09:00"
    assert [w.weather_code for w in izu_south.weather] == ["202", "100"]
    assert [p.pop_percent for p in izu_south.pop] == [30, None]
    assert len(izu_south.stations) == 1
    assert izu_south.stations[0].station_name == "八丈島"
    assert [t.celsius for t in izu_south.stations[0].temperatures] == [21.0, 15.0]


def test_sqlite_cache_wal_upsert_and_prune(tmp_path: Path) -> None:
    cache = SQLiteCache(tmp_path / "cache.db")
    try:
        forecasts = parse_vpfd51_xml(SAMPLE_VPFD51_XML)
        izu_south = next(fc for fc in forecasts if fc.class10_code == "130030")

        old_time = datetime(2026, 11, 10, 0, 0, tzinfo=UTC)
        new_time = datetime(2026, 11, 17, 3, 0, tzinfo=UTC)

        stale_fc = izu_south.model_copy(update={"report_datetime": "2026-11-10T05:00:00+09:00"})
        cache.upsert_forecast(stale_fc, now=old_time)
        cache.upsert_forecast(izu_south, now=new_time)

        latest = cache.get_latest_forecast("130030", office_code="130000")
        assert latest is not None
        assert latest.report_datetime == "2026-11-17T11:00:00+09:00"

        pruned = cache.prune_old_forecasts(
            retention_days=3,
            now=new_time + timedelta(hours=1),
        )
        assert pruned == 1
        assert cache.count_forecasts() == 1
    finally:
        cache.close()


@pytest.mark.anyio
async def test_worker_polls_uses_conditional_get_and_resolves_hachijojima(tmp_path: Path) -> None:
    feed_url = "https://example.jma.test/feed/regular.xml"
    calls: list[tuple[str, str | None, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(
            (
                str(request.url),
                request.headers.get("If-None-Match"),
                request.headers.get("If-Modified-Since"),
            )
        )
        if str(request.url) == feed_url:
            if request.headers.get("If-None-Match") == '"etag-1"':
                return httpx.Response(304)
            return httpx.Response(
                200,
                text=SAMPLE_ATOM_XML,
                headers={"ETag": '"etag-1"', "Last-Modified": "Tue, 17 Nov 2026 02:00:00 GMT"},
            )
        if str(request.url) == "https://example.jma.test/xml/tokyo_vpfd51.xml":
            return httpx.Response(200, text=SAMPLE_VPFD51_XML)
        return httpx.Response(404)

    cache = SQLiteCache(tmp_path / "worker.db")
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        worker = JMAWorker(cache, feed_url=feed_url, client=client)

        first = await worker.poll_once()
        assert first.not_modified is False
        assert first.entries_found == 1
        assert first.entries_fetched == 1
        assert first.areas_stored == 2

        second = await worker.poll_once()
        assert second.not_modified is True
        assert calls[-1][1] == '"etag-1"'
        assert calls[-1][2] == "Tue, 17 Nov 2026 02:00:00 GMT"

        # Verify Hachijojima coordinates (32.72367N, 139.62445E) resolve to cached 130030 forecast
        router = GeoRouter.from_file()
        area = router.resolve(32.72367, 139.62445)
        assert area is not None
        cached = cache.get_latest_forecast(area.class10_code, office_code=area.office_code)
        assert cached is not None
        assert cached.area_name == "伊豆諸島南部"
        assert cached.weather[0].weather_code == "202"
    cache.close()

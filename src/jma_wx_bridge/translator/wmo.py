"""Mapping from JMA TELOPS weather codes (100-499) to WMO weather codes (0-99)."""

from __future__ import annotations

# Representative mapping of JMA TELOPS codes to WMO weather interpretation codes.
# Reference: Open-Meteo WMO codes:
#   0: Clear sky, 1: Mainly clear, 2: Partly cloudy, 3: Overcast, 45: Fog
#   61: Slight rain, 63: Moderate rain, 65: Heavy rain
#   71: Slight snow, 73: Moderate snow, 75: Heavy snow, 95: Thunderstorm
_TELOPS_TO_WMO: dict[str, int] = {
    # 1xx: Clear / Sunny primary
    "100": 0,
    "101": 2,
    "102": 61,
    "103": 61,
    "104": 71,
    "105": 71,
    "106": 61,
    "107": 61,
    "108": 95,
    "110": 1,
    "111": 2,
    "112": 61,
    "113": 61,
    "114": 61,
    "115": 71,
    "116": 71,
    "117": 71,
    "118": 61,
    "119": 95,
    "120": 61,
    "121": 61,
    "122": 61,
    "123": 95,
    "124": 71,
    "125": 95,
    "126": 61,
    "127": 61,
    "128": 61,
    "130": 45,
    "131": 45,
    "132": 2,
    "140": 95,
    "160": 71,
    "170": 71,
    "181": 71,
    # 2xx: Cloudy primary
    "200": 3,
    "201": 2,
    "202": 61,
    "203": 61,
    "204": 71,
    "205": 71,
    "206": 61,
    "207": 61,
    "208": 95,
    "209": 45,
    "210": 2,
    "211": 2,
    "212": 61,
    "213": 61,
    "214": 61,
    "215": 71,
    "216": 71,
    "217": 71,
    "218": 61,
    "219": 95,
    "220": 61,
    "221": 61,
    "222": 61,
    "223": 2,
    "224": 61,
    "225": 61,
    "226": 61,
    "228": 71,
    "229": 71,
    "230": 71,
    "231": 45,
    "240": 95,
    "250": 95,
    "260": 71,
    "270": 71,
    "281": 71,
    # 3xx: Rain primary
    "300": 63,
    "301": 61,
    "302": 61,
    "303": 71,
    "304": 63,
    "306": 65,
    "308": 65,
    "309": 71,
    "311": 61,
    "313": 63,
    "314": 71,
    "315": 71,
    "316": 61,
    "317": 63,
    "320": 61,
    "321": 61,
    "322": 71,
    "323": 61,
    "324": 61,
    "325": 61,
    "326": 71,
    "327": 71,
    "328": 65,
    "329": 71,
    "340": 71,
    "350": 95,
    "361": 71,
    "371": 71,
    # 4xx: Snow primary
    "400": 73,
    "401": 71,
    "402": 71,
    "403": 71,
    "405": 75,
    "406": 75,
    "407": 75,
    "409": 71,
    "411": 71,
    "413": 73,
    "414": 71,
    "420": 71,
    "421": 71,
    "422": 71,
    "423": 71,
    "425": 75,
    "426": 73,
    "427": 73,
    "450": 95,
}


def telops_to_wmo(telops_code: str | None) -> int | None:
    """Convert a JMA TELOPS weather code string to a WMO weather code (0-99)."""
    if not telops_code:
        return None
    code = telops_code.strip()
    if code in _TELOPS_TO_WMO:
        return _TELOPS_TO_WMO[code]
    if code.startswith("1"):
        return 0
    if code.startswith("2"):
        return 3
    if code.startswith("3"):
        return 63
    if code.startswith("4"):
        return 73
    return None

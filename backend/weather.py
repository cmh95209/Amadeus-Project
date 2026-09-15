# -*- coding: utf-8 -*-
"""Live weather + air-quality reader for Amadeus (standard library only).

Pulls current conditions, today + tomorrow's outlook, and air quality from the
keyless Open-Meteo API and formats them as one compact plain-text block, so a
weather question gets answered with real numbers instead of web-search
snippets (which for a small town's live weather are usually empty or
bot-walled) or invention.

No account, no API key, no new dependency - two or three tiny HTTP GETs.

Deliberate properties (mirrors webfetch):
  * BOUNDED  - a hard deadline over the WHOLE report (TOTAL_BUDGET), with each
               request getting only what is left of it, so a slow service can
               never stall a reply.
  * GRACEFUL - every failure (unknown place, network drop, empty response)
               degrades to one short honest line; the function never raises,
               so a dead service can only cost bounded time, never the reply.
"""
from __future__ import annotations

import json
import ssl
import time
import urllib.parse
import urllib.request

# --- limits -----------------------------------------------------------------
REQUEST_TIMEOUT = 8        # seconds, per HTTP request
TOTAL_BUDGET = 15          # seconds, hard ceiling for the whole report

_GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
_AIR_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"

_SSL_CTX = ssl.create_default_context()
_USER_AGENT = "amadeus-weather/1.0 (personal assistant)"

# Honest lines served when the data cannot be fetched. They are fed to her as
# the "data block", so she tells the user the truth in her own voice.
NOT_FOUND_LINE = (
    "The weather service could not find that place - "
    "it may be misspelled or too small to be listed."
)
SERVICE_DOWN_LINE = "The weather service could not be reached right now."

# --- plain-language mappings -------------------------------------------------
_WMO_DESCRIPTIONS = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "freezing fog",
    51: "light drizzle", 53: "drizzle", 55: "dense drizzle",
    56: "light freezing drizzle", 57: "freezing drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain",
    66: "light freezing rain", 67: "freezing rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains",
    80: "light rain showers", 81: "rain showers", 82: "violent rain showers",
    85: "light snow showers", 86: "snow showers",
    95: "thunderstorm", 96: "thunderstorm with light hail",
    99: "thunderstorm with heavy hail",
}


def wmo_description(code) -> str:
    """WMO weather code -> plain words ("unknown conditions" when unmapped)."""
    try:
        return _WMO_DESCRIPTIONS.get(int(code), "unknown conditions")
    except (TypeError, ValueError):
        return "unknown conditions"


def aqi_category(us_aqi):
    """US AQI value -> EPA category name, or None when the value is missing."""
    try:
        v = float(us_aqi)
    except (TypeError, ValueError):
        return None
    if v <= 50:
        return "Good"
    if v <= 100:
        return "Moderate"
    if v <= 150:
        return "Unhealthy for sensitive groups"
    if v <= 200:
        return "Unhealthy"
    if v <= 300:
        return "Very Unhealthy"
    return "Hazardous"


def _num(value, decimals=1):
    """Format a numeric API value for prose; None when absent/not numeric.

    One decimal by default, a trailing ".0" dropped (34.0 -> "34"); pass
    decimals=0 for whole numbers (2.3 km/h -> "2").
    """
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if decimals == 0:
        return str(int(round(f)))
    s = f"{f:.{decimals}f}"
    return s[:-2] if s.endswith(".0") else s


# --- HTTP --------------------------------------------------------------------
def _get_json(url, params, timeout):
    """GET url?params and decode JSON. Raises on ANY failure - the caller
    turns that into an honest line, so this stays deliberately loud."""
    full = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        full, headers={"User-Agent": _USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=max(1, timeout),
                                context=_SSL_CTX) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


# --- geocoding ---------------------------------------------------------------
def geocode(place, timeout=REQUEST_TIMEOUT):
    """Resolve a place name via Open-Meteo geocoding.

    Returns the first (best) result dict (name, admin1, country, latitude,
    longitude, timezone, population, ...) or None when nothing matched.
    Raises on network/service failure - the caller degrades to an honest line.
    """
    data = _get_json(_GEOCODE_URL,
                     {"name": place, "count": 5, "language": "en", "format": "json"},
                     timeout)
    results = data.get("results") or []
    return results[0] if results else None


# --- public entry ------------------------------------------------------------
def fetch_weather_report(place, budget=TOTAL_BUDGET) -> str:
    """One call: a place name -> a compact live weather + air-quality block.

    Returns the block on success, else ONE short honest line (NOT_FOUND_LINE
    or SERVICE_DOWN_LINE). Never raises: a dead service costs bounded time
    only. Air quality is best-effort - a failure there drops just that line.
    """
    place = (place or "").strip()
    if not place:
        return NOT_FOUND_LINE
    deadline = time.monotonic() + max(1.0, float(budget))

    def _timeout():
        return min(REQUEST_TIMEOUT, deadline - time.monotonic())

    # 1) where is it?
    if _timeout() < 1:
        return SERVICE_DOWN_LINE
    try:
        loc = geocode(place, timeout=_timeout())
    except Exception as exc:
        print(f"[Amadeus] Weather: geocode failed for '{place}': {exc!r}")
        return SERVICE_DOWN_LINE
    if loc is None:
        return NOT_FOUND_LINE
    lat, lon = loc.get("latitude"), loc.get("longitude")
    if lat is None or lon is None:
        return NOT_FOUND_LINE
    label = ", ".join(
        p for p in (loc.get("name"), loc.get("admin1"), loc.get("country")) if p)
    lines = [label]

    # 2) current + today + tomorrow
    if _timeout() < 1:
        return SERVICE_DOWN_LINE
    try:
        fc = _get_json(_FORECAST_URL, {
            "latitude": lat, "longitude": lon,
            "current": ("temperature_2m,relative_humidity_2m,apparent_temperature,"
                        "precipitation,weather_code,wind_speed_10m"),
            "daily": ("temperature_2m_max,temperature_2m_min,"
                      "precipitation_probability_max,sunrise,sunset"),
            "forecast_days": 2,
            "timezone": "auto",
        }, _timeout())
    except Exception as exc:
        print(f"[Amadeus] Weather: forecast failed for '{place}': {exc!r}")
        return SERVICE_DOWN_LINE

    cur = fc.get("current") or {}
    daily = fc.get("daily") or {}

    def _day(key, idx=0):
        vals = daily.get(key) or []
        return vals[idx] if idx < len(vals) else None

    # now
    t_now = _num(cur.get("temperature_2m"))
    if t_now is not None:
        now_hm = str(cur.get("time") or "").split("T")[-1][:5]
        s = f"Now ({now_hm} local): {t_now}\u00b0C"
        t_feels = _num(cur.get("apparent_temperature"))
        if t_feels is not None:
            s += f" (feels like {t_feels}\u00b0C)"
        s += f", {wmo_description(cur.get('weather_code'))}"
        hum = _num(cur.get("relative_humidity_2m"), 0)
        if hum is not None:
            s += f", humidity {hum}%"
        wind = _num(cur.get("wind_speed_10m"), 0)
        if wind is not None:
            s += f", wind {wind} km/h"
        try:
            raining = float(cur.get("precipitation") or 0) > 0
        except (TypeError, ValueError):
            raining = False
        s += ", " + ("rain right now" if raining else "no rain right now") + "."
        lines.append(s)

    # today
    t_hi, t_lo = _num(_day("temperature_2m_max", 0), 0), _num(_day("temperature_2m_min", 0), 0)
    if t_hi is not None and t_lo is not None:
        s = f"Today: high {t_hi}\u00b0C / low {t_lo}\u00b0C"
        rc = _num(_day("precipitation_probability_max", 0), 0)
        if rc is not None:
            s += f", up to {rc}% chance of rain"
        sr = str(_day("sunrise", 0) or "").split("T")[-1][:5]
        ss = str(_day("sunset", 0) or "").split("T")[-1][:5]
        if sr and ss:
            s += f". Sunrise {sr}, sunset {ss}"
        s += "."
        lines.append(s)

    # tomorrow
    t_hi2, t_lo2 = _num(_day("temperature_2m_max", 1), 0), _num(_day("temperature_2m_min", 1), 0)
    if t_hi2 is not None and t_lo2 is not None:
        s = f"Tomorrow: high {t_hi2}\u00b0C / low {t_lo2}\u00b0C"
        rc = _num(_day("precipitation_probability_max", 1), 0)
        if rc is not None:
            s += f", up to {rc}% chance of rain"
        s += "."
        lines.append(s)

    # 3) air quality (best-effort: a failure drops only this line)
    if _timeout() >= 1:
        try:
            air = _get_json(_AIR_URL, {
                "latitude": lat, "longitude": lon,
                "current": "us_aqi,pm2_5", "timezone": "auto",
            }, _timeout())
        except Exception as exc:
            print(f"[Amadeus] Weather: air-quality failed for '{place}': {exc!r}")
            air = None
        if air:
            a_cur = air.get("current") or {}
            aqi = a_cur.get("us_aqi")
            cat = aqi_category(aqi)
            if cat is not None:
                s = f"Air quality (US AQI): {int(round(float(aqi)))} ({cat})"
                pm25 = _num(a_cur.get("pm2_5"))
                if pm25 is not None:
                    s += f" - PM2.5 {pm25} ug/m3"
                s += "."
                lines.append(s)

    if len(lines) == 1:
        # geocoded, but nothing readable came back
        return SERVICE_DOWN_LINE
    lines.append("Source: Open-Meteo, fetched live.")
    return "\n".join(lines)

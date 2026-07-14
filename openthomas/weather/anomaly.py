"""Climatological normals, for the anomaly lens.

How unusual today's temperature is versus normal for this place and month is the
signal absolute temperature hides: unusual heat or cold is where a weather market
is slowest to reprice. Monthly 2 m normals come from NASA POWER (a per-point
climatology), fetched once per city and cached ~forever — normals don't move.

`refresh_normals()` runs publish-side and only ever fetches cities it hasn't
seen; `normals()` reads the cache, so building the feed touches no network and
the tests stay hermetic.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path

from .geo import WORLD_CITIES
from .stations import STATIONS

MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
          "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


def _cache(home: Path | str) -> Path:
    return Path(home) / "climatology.json"


def known_coords() -> list[tuple[str, float, float]]:
    """(place, lat, lon) for every city we can place — the same coordinates
    geo.locate() emits, so the anomaly join lines up by "lat,lon"."""
    out = [(st.name.split(",")[0], round(st.lat, 3), round(st.lon, 3))
           for st in STATIONS.values()]
    out += [(name.title(), lat, lon) for name, (lat, lon) in WORLD_CITIES.items()]
    return out


def normals(home: Path | str) -> dict:
    try:
        return json.loads(_cache(home).read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _fetch_point(lat: float, lon: float) -> dict:
    url = ("https://power.larc.nasa.gov/api/temporal/climatology/point"
           f"?parameters=T2M&community=RE&longitude={lon}&latitude={lat}&format=JSON")
    req = urllib.request.Request(url, headers={"User-Agent": "openthomas/1.0"})
    with urllib.request.urlopen(req, timeout=25) as r:
        data = json.load(r)
    t = data["properties"]["parameter"]["T2M"]
    return {m: round(t[m], 1) for m in MONTHS if m in t}


def _current_cache(home: Path | str) -> Path:
    return Path(home) / "citytemp.json"


def current_temps(home: Path | str) -> dict:
    """Today's daily-mean temperature per city (°C), keyed "lat,lon". Read-only.
    Empty until a fetch succeeds; the anomaly then estimates from the grid."""
    try:
        return json.loads(_current_cache(home).read_text()).get("temps", {})
    except (OSError, json.JSONDecodeError):
        return {}


def refresh_current(home: Path | str, max_age_s: int = 7200) -> dict:
    """Today's daily-mean temperature per city from Open-Meteo — a mean, so
    comparing it to the monthly-mean normal carries no time-of-day bias. Cached
    a couple of hours; best-effort (keeps the last good values on failure)."""
    cache = _current_cache(home)
    prev = None
    try:
        prev = json.loads(cache.read_text())
        if time.time() - prev.get("_t", 0) < max_age_s:
            return prev["temps"]
    except (OSError, json.JSONDecodeError, KeyError):
        prev = None

    coords = known_coords()
    temps: dict[str, float] = {}
    try:
        for i in range(0, len(coords), 80):
            batch = coords[i:i + 80]
            la = ",".join(str(lat) for _p, lat, _lon in batch)
            lo = ",".join(str(lon) for _p, _lat, lon in batch)
            url = (f"https://api.open-meteo.com/v1/forecast?latitude={la}&longitude={lo}"
                   "&daily=temperature_2m_mean&forecast_days=1&timezone=GMT")
            req = urllib.request.Request(url, headers={"User-Agent": "openthomas/1.0"})
            with urllib.request.urlopen(req, timeout=25) as r:
                data = json.load(r)
            if isinstance(data, dict):
                data = [data]
            for (_p, lat, lon), item in zip(batch, data):
                v = (item.get("daily") or {}).get("temperature_2m_mean")
                if v and v[0] is not None:
                    temps[f"{lat},{lon}"] = round(v[0], 1)
            time.sleep(0.5)
    except (urllib.error.URLError, OSError, KeyError, ValueError, TypeError):
        return prev["temps"] if prev else {}
    if temps:
        try:
            cache.write_text(json.dumps({"_t": time.time(), "temps": temps}))
        except OSError:
            pass
    return temps


def refresh_normals(home: Path | str) -> int:
    """Fetch monthly normals for any known city not yet cached. Best-effort —
    a failed city is simply retried next run, and never breaks a publish."""
    data = normals(home)
    fetched = 0
    for _place, lat, lon in known_coords():
        key = f"{lat},{lon}"
        if key in data:
            continue
        try:
            data[key] = _fetch_point(lat, lon)
            fetched += 1
            time.sleep(0.4)
        except (urllib.error.URLError, OSError, KeyError, ValueError):
            continue
    if fetched:
        try:
            cache = _cache(home)
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(data))
        except OSError:
            pass
    return fetched

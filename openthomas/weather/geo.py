"""Put a market on the globe.

Weather edges happen somewhere real, so the public feed plots each one where its
weather is: the NWS settlement station for a Kalshi market, or the city named in
a Polymarket temperature question. Coordinates are never invented — a market we
cannot place gets no pin rather than a wrong one.

The city table covers exactly the international markets the agent actually
forecasts (verified against the live journal); extend it as new cities appear.
"""

from __future__ import annotations

import re

from .stations import station_for_market

# lowercase city → (lat, lon). The Polymarket daily-temperature universe.
WORLD_CITIES: dict[str, tuple[float, float]] = {
    "london": (51.51, -0.13), "seoul": (37.57, 126.98), "paris": (48.85, 2.35),
    "hong kong": (22.32, 114.17), "shanghai": (31.23, 121.47), "ankara": (39.93, 32.85),
    "madrid": (40.42, -3.70), "wellington": (-41.29, 174.78), "tokyo": (35.68, 139.69),
    "munich": (48.14, 11.58), "shenzhen": (22.54, 114.06), "warsaw": (52.23, 21.01),
    "singapore": (1.35, 103.82), "beijing": (39.90, 116.41), "chengdu": (30.57, 104.07),
    "chongqing": (29.56, 106.55), "wuhan": (30.59, 114.31), "moscow": (55.76, 37.62),
    "lucknow": (26.85, 80.95), "milan": (45.46, 9.19), "amsterdam": (52.37, 4.90),
    "taipei": (25.03, 121.57), "guangzhou": (23.13, 113.26), "tel aviv": (32.09, 34.78),
    "istanbul": (41.01, 28.98), "busan": (35.18, 129.08), "kuala lumpur": (3.14, 101.69),
    "helsinki": (60.17, 24.94),
}

_CITY_RE = re.compile(r"temperature in ([a-z .'\-]+?) be", re.I)


class _Shim:
    """station_for_market() reads .id/.platform/.question; the journal row has
    exactly those, so a tiny shim reuses the live mapping instead of copying it."""

    __slots__ = ("id", "platform", "question", "close_time")

    def __init__(self, market_id: str, platform: str, question: str):
        self.id = market_id or ""
        self.platform = platform or ""
        self.question = question or ""
        self.close_time = None


def locate(market_id: str, platform: str, question: str) -> dict | None:
    """{'lat', 'lon', 'place'} for a market, or None if it can't be placed.

    Station first — that is the authoritative settlement point — then the city
    named in a Polymarket question. `market_id` is used only to read the Kalshi
    series ticker; it is never returned, so the feed's no-leak rule holds.
    """
    hit = station_for_market(_Shim(market_id, platform, question))
    if hit:
        st = hit[0]
        return {"lat": round(st.lat, 3), "lon": round(st.lon, 3),
                "place": st.name.split(",")[0]}
    m = _CITY_RE.search(question or "")
    if m:
        name = m.group(1).strip()
        coord = WORLD_CITIES.get(name.lower())
        if coord:
            return {"lat": coord[0], "lon": coord[1], "place": name}
    return None

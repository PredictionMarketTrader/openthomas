"""WeatherDesk: assembles the weather-data block for forecast prompts, the way
NewsDesk does for headlines. Everything rendered is labeled data for the
model to weigh — never instructions.
"""

from __future__ import annotations

import statistics
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from .nws import NWSClient
from .openmeteo import OpenMeteoClient
from .stations import Station, station_for_market, target_date
from .strikes import parse_strike


class WeatherDesk:
    def __init__(self, nws: NWSClient | None = None, meteo: OpenMeteoClient | None = None,
                 cache_ttl: float = 900):
        self.nws = nws or NWSClient()
        self.meteo = meteo or OpenMeteoClient()
        self.cache_ttl = cache_ttl
        self._cache: dict[tuple, tuple[float, str]] = {}

    def brief(self, market) -> str:
        """Markdown block for the forecast prompt; empty string when the market
        isn't a station-temperature market or no data is reachable."""
        info = station_for_market(market)
        if info is None:
            return ""
        station, kind = info
        day = target_date(market, station)

        lines = [
            f"Market settles on the official NWS {kind} temperature at "
            f"{station.name} ({station.obs_id}) on {day.isoformat()}."
        ]
        strike = parse_strike(market)
        if strike:
            lines.append(f"YES resolves if the official {kind} is {strike.describe()}.")

        body = self._station_day_block(station, kind, day)
        if not body:
            return ""
        return "\n".join(lines) + "\n" + body

    def _station_day_block(self, station: Station, kind: str, day) -> str:
        key = (station.key, kind, day.isoformat())
        hit = self._cache.get(key)
        if hit and time.monotonic() - hit[0] < self.cache_ttl:
            return hit[1]

        lines: list[str] = []
        try:
            by_model = self.meteo.daily_extremes(station).get(day.isoformat(), {}).get(kind, {})
        except Exception:
            by_model = {}
        if by_model:
            lines.append(f"Model guidance for the {kind} on {day.isoformat()} (independent NWP models, °F):")
            lines += [f"- {model}: {value:.1f}" for model, value in sorted(by_model.items())]
            values = list(by_model.values())
            spread = f" ± {statistics.stdev(values):.1f}" if len(values) > 1 else ""
            lines.append(f"Consensus: {statistics.mean(values):.1f}{spread} (n={len(values)} models).")

        if day == datetime.now(ZoneInfo(station.timezone)).date():
            try:
                observed = self.nws.observed_extreme_today(station, kind)
            except Exception:
                observed = None
            if observed is not None:
                bound = "equal or higher" if kind == "high" else "equal or lower"
                lines.append(
                    f"Observed {kind} so far today at {station.obs_id}: {observed:.1f}°F "
                    f"— the official {kind} can only end {bound}."
                )

        block = "\n".join(lines)
        self._cache[key] = (time.monotonic(), block)
        return block

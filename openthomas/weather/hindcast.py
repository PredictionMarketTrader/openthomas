"""Hindcast loader: months of leak-free (guidance, settlement) pairs in one run.

As-of forecasts come from Open-Meteo's previous-runs API — the hourly
`temperature_2m_previous_dayN` series is what the model published N days
before each hour, so a daily extreme built from it is exactly the forecast a
trader had at lead N, no lookahead. Official actuals come from ACIS
(data.rcc-acis.org), the same station-day integers the NWS CLI report
publishes.

One 90-day load gives the baseline's bias/sigma estimator dozens of verified
days per (station, kind, lead) on day one, instead of waiting weeks of live
trading to learn them.
"""

from __future__ import annotations

import re
import statistics
from datetime import date, datetime, timedelta

import httpx

from .openmeteo import DEFAULT_MODELS
from .stations import Station
from .verification import VerificationStore

PREVIOUS_RUNS = "https://previous-runs-api.open-meteo.com/v1/forecast"
ACIS = "https://data.rcc-acis.org/StnData"

_VAR = re.compile(r"temperature_2m_previous_day(\d+)(?:_(.+))?$")


class Hindcast:
    def __init__(self, store: VerificationStore, http: httpx.Client | None = None,
                 models: list[str] | None = None, leads: range = range(1, 6)):
        self.store = store
        self.http = http or httpx.Client(timeout=90)
        self.models = models or DEFAULT_MODELS
        self.leads = leads

    def load_station(self, station: Station, days: int = 90) -> tuple[int, int]:
        """Returns (guidance rows added, settlement rows added). Idempotent."""
        guidance_keys, settled_keys = self.store.keys()
        added_g = self._load_guidance(station, days, guidance_keys)
        added_s = self._load_settlements(station, days, settled_keys)
        return added_g, added_s

    # --- as-of model forecasts ----------------------------------------------------
    def _load_guidance(self, station: Station, days: int, existing: set) -> int:
        resp = self.http.get(PREVIOUS_RUNS, params={
            "latitude": station.lat, "longitude": station.lon,
            "hourly": ",".join(f"temperature_2m_previous_day{n}" for n in self.leads),
            "models": ",".join(self.models),
            "past_days": min(days, 92), "forecast_days": 0,
            "timezone": station.timezone, "temperature_unit": "fahrenheit",
        })
        resp.raise_for_status()
        hourly = resp.json().get("hourly", {})
        times = hourly.get("time", [])
        days_idx: dict[str, list[int]] = {}
        for i, t in enumerate(times):
            days_idx.setdefault(t[:10], []).append(i)

        # acc[(date, lead, kind)][model] = extreme °F
        acc: dict[tuple[str, int, str], dict[str, float]] = {}
        for key, values in hourly.items():
            match = _VAR.match(key)
            if not match:
                continue
            lead = int(match.group(1))
            model = match.group(2) or (self.models[0] if len(self.models) == 1 else "")
            if not model:
                continue
            for day, idxs in days_idx.items():
                temps = [values[i] for i in idxs if values[i] is not None]
                if len(temps) < 20:  # partial day — extremes unreliable
                    continue
                acc.setdefault((day, lead, "high"), {})[model] = max(temps)
                acc.setdefault((day, lead, "low"), {})[model] = min(temps)

        added = 0
        for (day, lead, kind), by_model in sorted(acc.items()):
            if len(by_model) < 2 or (station.key, kind, day, lead) in existing:
                continue
            values = list(by_model.values())
            self.store.record_guidance(
                station.key, kind, date.fromisoformat(day), lead,
                statistics.mean(values), statistics.stdev(values),
                {m: round(v, 1) for m, v in by_model.items()},
            )
            added += 1
        return added

    # --- official actuals -----------------------------------------------------------
    def _load_settlements(self, station: Station, days: int, existing: set) -> int:
        yesterday = datetime.now().date() - timedelta(days=1)
        resp = self.http.post(ACIS, json={
            "sid": station.obs_id, "elems": "maxt,mint",
            "sdate": (yesterday - timedelta(days=days)).isoformat(),
            "edate": yesterday.isoformat(),
        })
        resp.raise_for_status()
        added = 0
        for day, maxt, mint in resp.json().get("data", []):
            for kind, raw in (("high", maxt), ("low", mint)):
                try:
                    value = float(raw)
                except (TypeError, ValueError):
                    continue  # "M" missing days
                if (station.key, kind, day) not in existing:
                    self.store.record_settlement(station.key, kind,
                                                 date.fromisoformat(day), value)
                    added += 1
        return added

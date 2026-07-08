"""Open-Meteo client: one free, keyless call returns the daily max/min from
several independent global NWP models. The cross-model consensus and spread
are the raw material for the forecast baseline — and the edge clock: markets
lag the 6/12-hourly model-run updates.
"""

from __future__ import annotations

import httpx

from .stations import Station

API = "https://api.open-meteo.com"

DEFAULT_MODELS = [
    "gfs_seamless",  # NOAA
    "ecmwf_ifs025",  # ECMWF
    "icon_seamless",  # DWD
    "gem_seamless",  # Canada
    "ukmo_seamless",  # UK Met Office
    "meteofrance_seamless",  # Météo-France
    "jma_seamless",  # Japan
]


class OpenMeteoClient:
    def __init__(self, client: httpx.Client | None = None, models: list[str] | None = None):
        self.http = client or httpx.Client(base_url=API, timeout=20)
        self.models = models or DEFAULT_MODELS

    def daily_extremes(self, station: Station, days: int = 7) -> dict[str, dict[str, dict[str, float]]]:
        """{"2026-07-08": {"high": {model: °F}, "low": {model: °F}}, ...}

        Models with no coverage for a location are simply absent.
        """
        resp = self.http.get(
            "/v1/forecast",
            params={
                "latitude": station.lat, "longitude": station.lon,
                "daily": "temperature_2m_max,temperature_2m_min",
                "models": ",".join(self.models),
                "timezone": station.timezone,
                "forecast_days": days,
                "temperature_unit": "fahrenheit",
            },
        )
        resp.raise_for_status()
        daily = resp.json().get("daily", {})
        dates = daily.get("time", [])
        out: dict[str, dict[str, dict[str, float]]] = {
            d: {"high": {}, "low": {}} for d in dates
        }
        for key, values in daily.items():
            if not key.startswith("temperature_2m_"):
                continue
            rest = key.removeprefix("temperature_2m_")  # "max_gfs_seamless" or "max"
            extreme, _, model = rest.partition("_")
            kind = "high" if extreme == "max" else "low"
            # A single-model request comes back unsuffixed.
            model = model or (self.models[0] if len(self.models) == 1 else "")
            if not model:
                continue
            for d, v in zip(dates, values):
                if v is not None:
                    out[d][kind][model] = float(v)
        return out

"""Weather data layer: settlement stations, strike semantics, NWS ground
truth, Open-Meteo model guidance, and the prompt-facing WeatherDesk."""

from .desk import WeatherDesk
from .nws import NWSClient
from .openmeteo import OpenMeteoClient
from .stations import STATIONS, Station, station_for_market, target_date, weather_series
from .strikes import Strike, parse_strike

__all__ = [
    "STATIONS", "Station", "Strike", "NWSClient", "OpenMeteoClient", "WeatherDesk",
    "parse_strike", "station_for_market", "target_date", "weather_series",
]

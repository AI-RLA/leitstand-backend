"""Ask Open-Meteo for weather at a point and bring the answer into one compact shape."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal

import httpx2

DEFAULT_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
WEATHER_SOURCE_TITLE = "Weather data by Open-Meteo.com"
WEATHER_SOURCE_URL = "https://open-meteo.com/"
DEFAULT_GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
DEFAULT_LANGUAGE = "de"
PLACES_SOURCE_TITLE = "Place names by GeoNames via Open-Meteo.com"
PLACES_SOURCE_URL = "https://www.geonames.org/"
UNREADABLE_ANSWER = "Open-Meteo sent an unreadable answer."

CURRENT_VARIABLES = (
    "temperature_2m",
    "precipitation",
    "wind_speed_10m",
    "wind_gusts_10m",
    "weather_code",
)
DAILY_VARIABLES = (
    "temperature_2m_max",
    "temperature_2m_min",
    "precipitation_sum",
    "wind_speed_10m_max",
    "wind_gusts_10m_max",
    "weather_code",
)
HourlyVariable = Literal[
    "temperature_2m",
    "relative_humidity_2m",
    "precipitation",
    "wind_speed_10m",
    "wind_gusts_10m",
    "weather_code",
    "soil_moisture_0_to_1cm",
    "soil_moisture_1_to_3cm",
    "soil_moisture_3_to_9cm",
    "soil_temperature_0cm",
    "soil_temperature_6cm",
    "et0_fao_evapotranspiration",
]
DEFAULT_HOURLY_VARIABLES: tuple[HourlyVariable, ...] = (
    "temperature_2m",
    "precipitation",
    "wind_speed_10m",
    "wind_gusts_10m",
)

WMO_CODES = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Depositing rime fog",
    51: "Light drizzle",
    53: "Moderate drizzle",
    55: "Dense drizzle",
    56: "Light freezing drizzle",
    57: "Dense freezing drizzle",
    61: "Slight rain",
    63: "Moderate rain",
    65: "Heavy rain",
    66: "Light freezing rain",
    67: "Heavy freezing rain",
    71: "Slight snowfall",
    73: "Moderate snowfall",
    75: "Heavy snowfall",
    77: "Snow grains",
    80: "Slight rain showers",
    81: "Moderate rain showers",
    82: "Violent rain showers",
    85: "Slight snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with slight hail",
    97: "Heavy thunderstorm",
    99: "Thunderstorm with heavy hail",
}

Block = Literal["current", "hourly", "daily"]

# Keys Open-Meteo returns next to the variables, which the compact shape carries elsewhere or drops.
_SHAPED_SEPARATELY = frozenset({"time", "interval", "weather_code"})


class OpenMeteoError(Exception):
    """Open-Meteo gave no usable answer. The message is one sentence fit for the model."""


def forecast_params(
    latitude: float, longitude: float, **request: str | int | Sequence[str]
) -> dict[str, str]:
    """Build the query for one request at a point rounded to about one kilometre."""
    query = {
        "latitude": str(round(latitude, 2)),
        "longitude": str(round(longitude, 2)),
        "timezone": "auto",
        "wind_speed_unit": "ms",
    }
    for key, value in request.items():
        query[key] = str(value) if isinstance(value, (str, int)) else ",".join(value)
    return query


async def fetch(client: httpx2.AsyncClient, url: str, query: dict[str, str]) -> dict[str, Any]:
    """Call Open-Meteo once, turning every failure into one plain sentence."""
    try:
        response = await client.get(url, params=query)
    except httpx2.TimeoutException as error:
        raise OpenMeteoError("Open-Meteo did not answer in time.") from error
    except httpx2.HTTPError as error:
        raise OpenMeteoError("Open-Meteo could not be reached.") from error
    if response.status_code != 200:
        # The body's reason names internal types, so it stays out of the message.
        raise OpenMeteoError(f"Open-Meteo refused the request (HTTP {response.status_code}).")
    try:
        body = response.json()
    except ValueError as error:
        raise OpenMeteoError(UNREADABLE_ANSWER) from error
    if not isinstance(body, dict):
        raise OpenMeteoError(UNREADABLE_ANSWER)
    return body


def _column(value: Any, block: Block) -> list[Any]:
    """Return a block's value as a list, since the current block holds a single value."""
    return [value] if block == "current" else list(value)


def shape_forecast(body: dict[str, Any], block: Block) -> dict[str, Any]:
    """Bring one block of an answer into columns, so a long series stays short for the model."""
    data = body[block]
    units = body[f"{block}_units"]
    shaped: dict[str, Any] = {
        # In every result, since the model loses a note that sits only in the server instructions.
        "basis": "Weather model estimate for the grid cell around grid_point, not a measurement.",
        "grid_point": {"lat": body["latitude"], "lon": body["longitude"]},
        "timezone": body["timezone"],
        "time": _column(data["time"], block),
        "units": {name: unit for name, unit in units.items() if name not in _SHAPED_SEPARATELY},
        "values": {
            name: _column(value, block)
            for name, value in data.items()
            if name not in _SHAPED_SEPARATELY
        },
    }
    if "weather_code" in data:
        codes = _column(data["weather_code"], block)
        shaped["weather"] = [WMO_CODES.get(code, "Unknown") for code in codes]
    shaped["source"] = f"{WEATHER_SOURCE_TITLE}, {WEATHER_SOURCE_URL}"
    return shaped


def shape_places(body: dict[str, Any]) -> dict[str, Any]:
    """List the places in a search answer, which has no results key when nothing matched."""
    places = [
        {
            "name": place["name"],
            "lat": place["latitude"],
            "lon": place["longitude"],
            "country": place.get("country"),
            "region": place.get("admin1"),
            "district": place.get("admin2"),
            "population": place.get("population"),
        }
        for place in body.get("results", [])
    ]
    return {"places": places, "source": f"{PLACES_SOURCE_TITLE}, {PLACES_SOURCE_URL}"}

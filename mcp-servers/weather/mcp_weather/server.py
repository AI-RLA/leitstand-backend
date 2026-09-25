"""MCP server with the weather at a point, from Open-Meteo."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from functools import partial
from typing import Annotated, Any

import httpx2
from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError
from pydantic import Field
from starlette.requests import Request
from starlette.responses import PlainTextResponse

from mcp_weather import open_meteo

INSTRUCTIONS = (
    "Weather at a point, from Open-Meteo. Values are a weather model's estimate for the grid cell "
    "around the point, not a measurement. Current precipitation is the total of the 15 minutes "
    "before the stated time, hourly precipitation and evapotranspiration the total of the hour "
    "before it. Name the source when you use the data."
)

_WEATHER_SOURCE = {
    "source": {"title": open_meteo.WEATHER_SOURCE_TITLE, "url": open_meteo.WEATHER_SOURCE_URL}
}
# Every tool only reads, so a client may run it without asking the user.
_READ_ONLY = {"readOnlyHint": True}

Latitude = Annotated[
    float, Field(ge=-90, le=90, description="Latitude of the point, WGS84 degrees.")
]
Longitude = Annotated[
    float, Field(ge=-180, le=180, description="Longitude of the point, WGS84 degrees.")
]


def create_server(*, forecast_url: str, geocoding_url: str, language: str) -> FastMCP:
    """Build the server with its three weather tools and the place search."""

    @asynccontextmanager
    async def lifespan(server: FastMCP) -> AsyncIterator[dict[str, Any]]:
        # Kept alive for a minute because tool calls are further apart than the default of 5 s.
        limits = httpx2.Limits(max_connections=10, max_keepalive_connections=5, keepalive_expiry=60)
        timeout = httpx2.Timeout(6.0, connect=2.0)
        async with httpx2.AsyncClient(timeout=timeout, limits=limits) as client:
            yield {"client": client}

    mcp = FastMCP("weather", instructions=INSTRUCTIONS, mask_error_details=True, lifespan=lifespan)

    async def fetch_and_shape(
        ctx: Context,
        url: str,
        query: dict[str, str],
        shape: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> dict[str, Any]:
        try:
            body = await open_meteo.fetch(ctx.lifespan_context["client"], url, query)
        except open_meteo.OpenMeteoError as error:
            raise ToolError(str(error)) from error
        try:
            return shape(body)
        except (KeyError, TypeError) as error:
            raise ToolError(open_meteo.UNREADABLE_ANSWER) from error

    @mcp.tool(meta=_WEATHER_SOURCE, annotations=_READ_ONLY)
    async def current_weather(latitude: Latitude, longitude: Longitude, ctx: Context) -> dict:
        """Current weather at a point, as of the last 15 minutes.

        Returns temperature, precipitation, wind speed, gusts and the weather in words. Use it for
        questions about now, such as whether it is raining or windy at a place. For the coming or
        past hours use hourly_forecast, for the coming days daily_forecast.
        """
        query = open_meteo.forecast_params(
            latitude, longitude, current=open_meteo.CURRENT_VARIABLES
        )
        return await fetch_and_shape(
            ctx, forecast_url, query, partial(open_meteo.shape_forecast, block="current")
        )

    @mcp.tool(meta=_WEATHER_SOURCE, annotations=_READ_ONLY)
    async def daily_forecast(
        latitude: Latitude,
        longitude: Longitude,
        ctx: Context,
        days: Annotated[int, Field(ge=1, le=16, description="Number of days, today included.")] = 3,
    ) -> dict:
        """Daily forecast at a point, for up to 16 days.

        Returns for each day the lowest and highest temperature, the precipitation total, the
        strongest wind and gusts, and the weather in words. Use it for questions about the coming
        days, such as whether tomorrow stays dry. Day 1 is today, so tomorrow needs days of at
        least 2. For when rain starts or stops within a day, use hourly_forecast.
        """
        query = open_meteo.forecast_params(
            latitude, longitude, daily=open_meteo.DAILY_VARIABLES, forecast_days=days
        )
        return await fetch_and_shape(
            ctx, forecast_url, query, partial(open_meteo.shape_forecast, block="daily")
        )

    @mcp.tool(meta=_WEATHER_SOURCE, annotations=_READ_ONLY)
    async def hourly_forecast(
        latitude: Latitude,
        longitude: Longitude,
        ctx: Context,
        start: datetime | None = Field(
            default=None,
            description="First hour of the period, local time at the point, e.g. 2026-09-24T22:00.",
        ),
        end: datetime | None = Field(
            default=None,
            description="Last hour of the period, included, e.g. 2026-09-25T06:00.",
        ),
        variables: Annotated[
            tuple[open_meteo.HourlyVariable, ...],
            Field(
                min_length=1,
                description="Values to return for each hour, e.g. soil_moisture_0_to_1cm for the "
                "top centimetre of soil.",
            ),
        ] = open_meteo.DEFAULT_HOURLY_VARIABLES,
    ) -> dict:
        """Hour by hour weather at a point for a period of up to 72 hours, past or future.

        Use it for timing within a day, such as when rain starts or stops, for rain overnight, and
        for soil moisture, soil temperature and evapotranspiration, which only this tool returns.
        Give the period as start and end in local time at the point, for example 22:00 yesterday to
        06:00 today for last night, at most about three months back. Without a period it returns the
        next 24 hours. Choose the values with variables. Without a choice it returns temperature,
        precipitation, wind and gusts.
        """
        if (start is None) != (end is None):
            raise ToolError("Give both start and end, or neither.")
        if start is None or end is None:
            period: dict[str, str | int] = {"forecast_hours": 24}
        else:
            if start.tzinfo or end.tzinfo:
                raise ToolError("Give start and end in local time at the point, without an offset.")
            length = end - start
            # Long enough for a night and the day around it, short enough to keep the answer small.
            if not timedelta(0) <= length <= timedelta(hours=72):
                raise ToolError("end must be after start, and the period at most 72 hours long.")
            period = {"start_hour": f"{start:%Y-%m-%dT%H}:00", "end_hour": f"{end:%Y-%m-%dT%H}:00"}
        query = open_meteo.forecast_params(latitude, longitude, hourly=variables, **period)
        return await fetch_and_shape(
            ctx, forecast_url, query, partial(open_meteo.shape_forecast, block="hourly")
        )

    @mcp.tool(
        meta={
            "source": {"title": open_meteo.PLACES_SOURCE_TITLE, "url": open_meteo.PLACES_SOURCE_URL}
        },
        annotations=_READ_ONLY,
    )
    async def search_places(
        name: Annotated[
            str, Field(min_length=2, max_length=100, description="A town, village or region name.")
        ],
        ctx: Context,
        # Field as the default, because Annotated on an optional type nests the description.
        country_code: str | None = Field(
            default=None,
            pattern=r"^[A-Z]{2}$",
            description="Two-letter country code to search only one country, e.g. DE.",
        ),
    ) -> dict:
        """Find places by name, with their coordinates, up to five, most populous first.

        Returns each place's name, coordinates, country, region, district and population. Use it
        when the user names a town or village instead of coordinates, then pass the coordinates to
        a weather tool. When more than one place could be meant, ask the user which one before
        using its coordinates. It does not find street addresses.
        """
        query = {"name": name, "count": "5", "language": language, "format": "json"}
        if country_code:
            query["countryCode"] = country_code
        return await fetch_and_shape(ctx, geocoding_url, query, open_meteo.shape_places)

    @mcp.custom_route("/healthz", methods=["GET"])
    async def healthz(request: Request) -> PlainTextResponse:
        """Report that the process is up, without calling Open-Meteo."""
        return PlainTextResponse("ok")

    return mcp


def main() -> None:
    """Serve over Streamable HTTP, on the host and port in FASTMCP_HOST and FASTMCP_PORT."""
    forecast_url = os.environ.get("MCP_WEATHER_FORECAST_URL", open_meteo.DEFAULT_FORECAST_URL)
    geocoding_url = os.environ.get("MCP_WEATHER_GEOCODING_URL", open_meteo.DEFAULT_GEOCODING_URL)
    language = os.environ.get("MCP_WEATHER_LANGUAGE", open_meteo.DEFAULT_LANGUAGE)
    create_server(forecast_url=forecast_url, geocoding_url=geocoding_url, language=language).run(
        transport="http", stateless_http=True, show_banner=False
    )

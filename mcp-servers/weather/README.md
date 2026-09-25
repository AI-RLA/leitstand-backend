# Weather MCP server

Weather at a point, and places found by name, from [Open-Meteo](https://open-meteo.com/), served over
MCP Streamable HTTP.

| Tool | Answers |
|---|---|
| `current_weather` | the weather now |
| `daily_forecast` | one row per day, day 1 is today, up to 16 days |
| `hourly_forecast` | one row per hour, up to 48 hours ahead and 48 hours back, with a choice of values including soil moisture and soil temperature |
| `search_places` | up to five places with a given name and their coordinates, optionally within one country, for a weather lookup by place name |

Every result has the same compact shape: `time` and one list per value in `values`, their `units`,
the weather in words (`weather`), the model grid point Open-Meteo used (`grid_point`) and the
`source`. Coordinates are rounded to two decimals, about one kilometre, before they leave the
server. The values are a weather model's estimate for a grid cell, not a measurement at the point.

## Usage

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
FASTMCP_PORT=8091 .venv/bin/mcp-weather          # serves http://127.0.0.1:8091/mcp
```

`GET /healthz` checks only that the process is up, not that Open-Meteo answers.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `FASTMCP_HOST` | `127.0.0.1` (`0.0.0.0` in the image) | address to listen on |
| `FASTMCP_PORT` | `8000` (`8091` in the image) | port to listen on |
| `MCP_WEATHER_FORECAST_URL` | `https://api.open-meteo.com/v1/forecast` | a self-hosted or paid Open-Meteo instead of the free API |
| `MCP_WEATHER_GEOCODING_URL` | `https://geocoding-api.open-meteo.com/v1/search` | the same, for the place search |
| `MCP_WEATHER_LANGUAGE` | `de` | language of the place names `search_places` returns, where GeoNames has them |

## Licence and terms

Open-Meteo's data is licensed [CC BY 4.0](https://open-meteo.com/en/licence): "You must include a
link next to any location Open-Meteo data are displayed". Each tool therefore carries its source in
its MCP metadata and in every result. Place names come from [GeoNames](https://www.geonames.org/),
also CC BY 4.0, and `search_places` names that source.

The free API is for non-commercial use only, with less than 10 000 calls a day, 5 000 an hour and
600 a minute per IP address ([terms](https://open-meteo.com/en/terms)). Public research at public
institutions and educational use count as non-commercial there. Commercial use needs a paid plan or
a self-hosted Open-Meteo, set through `MCP_WEATHER_FORECAST_URL`.

## Adding a tool

1. Add the tool in `mcp_weather/server.py`, with its arguments bounded by `Field(ge=, le=)`, the
   source in `meta` and `annotations={"readOnlyHint": True}`.
2. Shape the answer in `mcp_weather/open_meteo.py`, with no MCP code there.

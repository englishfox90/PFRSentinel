# Weather Setup

PFR Sentinel uses the OpenWeatherMap API for live weather in overlay tokens and the Weather tile in [Live Monitoring](Live-Monitoring). The latitude and longitude you enter alongside it are also the observatory location for the rest of the app: timelapse sun windows, night-time detection, moon position for the ML sky model, and the all-sky overlay. Everything is configured in the **Weather API** card on the [Settings](Settings) tab.

---

## Getting an API Key

1. Click **Get free API key** in the **Weather API** card, or go to [openweathermap.org](https://openweathermap.org/api), and create a free account.
2. Open **API keys** in your account dashboard.
3. Copy the default key or generate a new one.
4. Paste it into the **API Key** field. The key is hidden by default; click **Show** to check what you pasted.

PFR Sentinel caches weather for 10 minutes, which keeps normal use to fewer than 200 weather requests a day.

---

## Settings

| Setting | Description |
|---------|-------------|
| **API Key** | Your OpenWeatherMap API key. |
| **Test** | Sends a test request with the values currently in the fields. See [Testing](#testing). |
| **Location** | City name. Used only when **Coordinates** are blank. |
| **Coordinates** | Latitude, longitude and elevation. Recommended over a city name. |
| **Units** | **Metric (°C, m/s)** or **Imperial (°F, mph)**. See [Units](#units). |

Changes save as you type.

---

## Location

### Option 1: Coordinates (recommended)

Coordinates are more precise than a city name, avoid an extra lookup, and are required for the features that need your exact position (timelapse sun windows, the night-time check for star detection, moon rise and set for the ML sky model, and the all-sky overlay).

| Field | Range | Example |
|-------|-------|---------|
| Latitude | -90 to 90 | 51.5074 |
| Longitude | -180 to 180 | -0.1278 |
| Elevation (m) | Metres above sea level (optional) | 120 |

Latitude and longitude accept decimal degrees or degrees-minutes-seconds. All of these are understood:

| Format | Example |
|--------|---------|
| Decimal | `31.33`, `-100.457` |
| Space separated | `31 32 51` |
| Colon separated | `31:32:51` |
| With symbols | `31° 32' 51"` |
| With a hemisphere letter | `100 27 25 W` |

Use a leading minus or an S or W letter for southern latitudes and western longitudes. When you leave the field, the value is rewritten as decimal degrees. If it can't be understood, the field gets a red border, the status line shows "Invalid coordinate — use decimal degrees", and the saved coordinate is cleared.

**Elevation** is used by the [All-Sky Overlay](All-Sky-Overlay) for atmospheric refraction. Weather doesn't need it.

### Option 2: City Name

Enter a city in the **Location** field, optionally followed by a country code:

| Format | Example |
|--------|---------|
| City | `London` |
| City, country code | `London,GB` |

If both coordinates and a city name are filled in, the coordinates are used.

A city name alone is enough for weather tokens, but not for the location-based features listed above; those need coordinates.

---

## Testing

Click **Test** to fetch the current weather with the values in the fields. The line under the API key shows the result, with a tick for success or a cross for a failure:

| Result | Meaning |
|--------|---------|
| `Clear, 15.2°C` | Success: current condition and temperature |
| `API key required` | The **API Key** field is empty |
| `Location or coordinates required` | Neither a city nor both coordinates are filled in |
| `No data returned` | The request failed: for example an invalid key, a city that wasn't recognised, or no network connection |

The log on the [Logs](Logs) tab has the detailed error for a failed request.

---

## Data Caching

After a successful request, weather is cached for 10 minutes and every frame in that window reuses it. Sentinel also checks every minute, in the background, whether the cache has expired and refreshes it, so the Weather tile stays current even when no frames are being captured.

If a refresh fails after the cache has expired, weather tokens show `?` until the next successful request. The Weather tile in Live Monitoring is dimmed once its data is more than 30 minutes old, so a dead API key or a long network outage isn't mistaken for live weather.

---

## Units

| Option | Temperature | Wind Speed |
|--------|-------------|------------|
| **Metric (°C, m/s)** | °C | m/s |
| **Imperial (°F, mph)** | °F | mph |

Values come from OpenWeatherMap already in the chosen units. Pressure is always hPa and visibility is always km.

**Known issue:** in the current version, choosing **Imperial** has no effect. The setting is always saved as metric (the dropdown returns to **Metric** the next time the Settings tab loads), and **Test** also requests metric values.

---

## Weather Tokens

Once weather is configured, these tokens can be used in text overlays. See [Overlay Tokens](Overlay-Tokens) for the full token reference.

| Token | Example |
|-------|---------|
| `{WEATHER_TEMP}` | 8.0°C |
| `{WEATHER_FEELS_LIKE}` | 5.2°C |
| `{WEATHER_CONDITION}` | Clear |
| `{WEATHER_DESC}` | Clear Sky |
| `{WEATHER_HUMIDITY}` | 72% |
| `{WEATHER_PRESSURE}` | 1013 hPa |
| `{WEATHER_WIND_SPEED}` | 3.2 m/s |
| `{WEATHER_WIND_DIR}` | NNW |
| `{WEATHER_CLOUDS}` | 12% |
| `{WEATHER_VISIBILITY}` | 10.0 km |
| `{WEATHER_SUNRISE}` | 06:42 |
| `{WEATHER_SUNSET}` | 18:35 |
| `{WEATHER_CITY}` | London |
| `{WEATHER_ICON_CODE}` | 01n |
| `{WEATHER_ICON_URL}` | https://openweathermap.org/img/wn/01n@2x.png |
| `{WEATHER_ICON_PATH}` | Local path of the downloaded icon |

Wind direction is converted from degrees to a 16-point compass (N, NNE, NE, ENE, E and so on). Sunrise and sunset are shown in the PC's local time. The current condition icon is downloaded from OpenWeatherMap and kept in `%LOCALAPPDATA%\PFRSentinel\weather_icons`.

Weather tokens are not listed in the **Tokens** dropdown on the [Overlay Settings](Overlay-Settings) tab. Type them into the overlay's text box; they work whenever an API key and a location or coordinates are set.

---

## Other Features That Use the Location

The latitude and longitude are shared with:

- **[Timelapse](Timelapse)**: sunset and sunrise recording windows.
- **Scheduled capture**: when the capture window follows the timelapse window (see [Capture Settings](Capture-Settings)).
- **Star detection**: skipped while the sun is higher than 6° below the horizon (see [Overlay Tokens](Overlay-Tokens#star-detection-tokens)).
- **[All-Sky Overlay](All-Sky-Overlay)**: star, planet and constellation positions, and background calibration.
- **[ML Models](ML-Models)**: whether the moon is up, which the sky model takes into account.

These features read the coordinates directly, so you can enter latitude and longitude without an API key if you only need them.

---

## Troubleshooting

| Symptom | Cause | Solution |
|---------|-------|----------|
| Weather tokens show `?` | Weather not configured, or requests failing | Add an API key and a location or coordinates, then click **Test** |
| **Test** shows "No data returned" | Invalid key, unrecognised city, or no network | Check the key in your OpenWeatherMap dashboard, try coordinates instead of a city name, and check the [Logs](Logs) tab for the exact error |
| Coordinate field turns red | The value couldn't be parsed or is out of range | Use decimal degrees such as `31.33` / `-100.46` |
| Weather tile is dimmed | No successful update for more than 30 minutes | Check the network connection and API key |
| Tokens work intermittently | Network problems | The 10-minute cache covers short outages; check network stability for longer failures |
| Temperatures stay in °C after choosing Imperial | Known issue in the current version: the **Imperial** choice is always saved as metric, and **Test** ignores it too | None yet; values display in metric units |

# Overlay Tokens

Tokens are placeholders in curly braces, such as `{EXPOSURE}`, that are replaced with live values each time a text overlay is drawn. They let an overlay show camera settings, image statistics, weather, ML results and star detection without editing the overlay by hand. Add them to text overlays on the [Overlay Settings](Overlay-Settings) tab.

---

## How Tokens Work

- Any text inside `{...}` in a text overlay is treated as a token.
- Matching is **case-insensitive**: `{exposure}`, `{EXPOSURE}` and `{Exposure}` all give the same value.
- If a token has no value for the current frame (unknown name, missing data, or a service that isn't set up), it is shown as `?`.
- Some tokens deliberately show `N/A` when the value doesn't apply, for example sky tokens while the roof is closed.
- Values are refreshed for every frame.
- The **Preview** on the Overlay Settings tab uses fixed sample values, not live data.

---

## Camera Tokens

In ZWO Camera mode these come from the camera for every frame. In Directory Watch mode, see [Directory Watch Mode](#directory-watch-mode) below.

| Token | Description | Example |
|-------|-------------|---------|
| `{CAMERA}` | Camera model name | ZWO ASI676MC |
| `{EXPOSURE}` | Exposure time in seconds, two decimal places | 30.00s |
| `{GAIN}` | Gain | 100 |
| `{TEMP}` | Sensor temperature | 22.5 C |
| `{TEMPERATURE}` | Same as `{TEMP}` | 22.5 C |
| `{TEMP_C}` | Sensor temperature in Celsius | 22.5°C |
| `{TEMP_F}` | Sensor temperature in Fahrenheit | 72.5°F |
| `{RES}` | Frame size as captured, before resizing or **Output Framing** | 3552x3552 |
| `{SESSION}` | Capture date | 2026-09-17 |
| `{DATETIME}` | Date and time the overlay was drawn (PC local time) | 2026-09-17 21:45:12 |
| `{FILENAME}` | Generated capture file name | capture_20260917_214512.png |

The temperature tokens show `N/A` if the camera doesn't report a temperature.

### Additional Camera Tokens

| Token | Description | Example |
|-------|-------------|---------|
| `{BAYER_PATTERN}` | Colour filter pattern used to debayer | BGGR |
| `{CAMERA_BIT_DEPTH}` | Sensor bit depth reported by the camera | — |
| `{IMAGE_BIT_DEPTH}` | Bit depth of the captured frame (8, or 16 in RAW16 mode) | 8 |
| `{PIXEL_SIZE}` | Pixel size in microns, as reported by the camera | — |
| `{ELEC_PER_ADU}` | Electrons per ADU, as reported by the camera | — |

---

## Image Statistics Tokens

Calculated from the captured frame (0–255 scale) before stretching and other processing. ZWO Camera mode only.

| Token | Description | Example |
|-------|-------------|---------|
| `{BRIGHTNESS}` | Mean pixel value | 42.7 |
| `{MEAN}` | Same as `{BRIGHTNESS}` | 42.7 |
| `{MEDIAN}` | Median pixel value | 38.0 |
| `{MIN}` | Minimum pixel value | 0 |
| `{MAX}` | Maximum pixel value | 255 |
| `{STD_DEV}` | Standard deviation of pixel values | 18.25 |
| `{P25}` | 25th percentile | 31.0 |
| `{P75}` | 75th percentile | 47.0 |
| `{P95}` | 95th percentile | 88.0 |

---

## Weather Tokens

Available when an OpenWeatherMap API key and a location or coordinates are set in [Settings](Settings) (see [Weather Setup](Weather-Setup)). Data is cached for 10 minutes. If weather isn't configured, or the data can't be fetched once the cache has expired, these tokens show `?`.

Weather tokens are not listed in the **Tokens** dropdown on the [Overlay Settings](Overlay-Settings) tab, so type them into the text box.

| Token | Description | Example |
|-------|-------------|---------|
| `{WEATHER_TEMP}` | Current temperature | 8.0°C |
| `{WEATHER_FEELS_LIKE}` | Feels-like temperature | 5.2°C |
| `{WEATHER_CONDITION}` | Short condition | Clear |
| `{WEATHER_DESC}` | Detailed description | Clear Sky |
| `{WEATHER_HUMIDITY}` | Relative humidity | 72% |
| `{WEATHER_PRESSURE}` | Atmospheric pressure | 1013 hPa |
| `{WEATHER_WIND_SPEED}` | Wind speed | 3.2 m/s |
| `{WEATHER_WIND_DIR}` | Wind direction (16-point compass) | NNW |
| `{WEATHER_CLOUDS}` | Cloud cover | 12% |
| `{WEATHER_VISIBILITY}` | Visibility | 10.0 km |
| `{WEATHER_SUNRISE}` | Sunrise time (PC local time) | 06:42 |
| `{WEATHER_SUNSET}` | Sunset time (PC local time) | 18:35 |
| `{WEATHER_CITY}` | Location name returned by OpenWeatherMap | London |
| `{WEATHER_ICON_CODE}` | OpenWeatherMap icon code | 01n |
| `{WEATHER_ICON_URL}` | Web address of the current weather icon | https://openweathermap.org/img/wn/01n@2x.png |
| `{WEATHER_ICON_PATH}` | Local file path of the downloaded weather icon | — |

---

## ML Model Tokens

Available when **Enable ML Analysis** is on in the [Image Processing](Image-Processing) tab; otherwise they show `?`. See [ML Models](ML-Models) for accuracy and limitations.

| Token | Description | Example |
|-------|-------------|---------|
| `{ROOF_STATUS}` | Roof classifier result with confidence | Open (95%) |
| `{SKY_CONDITION}` | Clear, Partly Cloudy or Overcast, with confidence | Clear (87%) |
| `{STARS_VISIBLE}` | Whether the sky model sees stars | Yes |
| `{STAR_DENSITY}` | Star density: High (above 0.6), Medium (above 0.3) or Low, with the 0–1 score | High (0.85) |

The sky model only runs when the roof reads Open, so `{SKY_CONDITION}`, `{STARS_VISIBLE}` and `{STAR_DENSITY}` show `N/A` while the roof is closed. If the models could not be loaded, all four show `?`.

---

## Star Detection Tokens

Star detection runs on every frame, independently of the ML models.

| Token | Description | Example |
|-------|-------------|---------|
| `{STAR_COUNT}` | Number of detected stars | 847 |
| `{FWHM}` | Average star width (full width at half maximum), in pixels | 3.2 |
| `{SEEING}` | Seeing label from the FWHM: Excellent (up to 2.5), Good (up to 4), Fair (up to 6), Poor (up to 8), Bad (above 8) | Good |

These tokens show `N/A` when star detection is skipped:

- during daylight and twilight (the sun higher than 6° below the horizon), when latitude and longitude are set in [Settings](Settings)
- while the ML roof classifier reports Closed, if **Skip Sky Features When Roof Closed** is on
- from the next release, on frames judged not to show a night sky: sensor noise only, an exposure under the **Minimum exposure** floor, or a roof that reads Open with no stars detected on three frames in a row (see [When the overlay is drawn](All-Sky-Overlay#when-the-overlay-is-drawn))

---

## Directory Watch Mode

In Directory Watch mode, camera information comes from the sidecar text file that capture software writes next to each image (the image file name plus `.txt`, for example `frame_001.fits.txt`). The file is expected to look like:

```
[ZWO ASI676MC]
Exposure = 30s
Gain = 100
Temperature = 22.5
Capture Area Size = 3840 * 2160
```

- The bracketed first line becomes `{CAMERA}`.
- Every `Key = Value` line becomes a token named after the key in upper case, for example `{EXPOSURE}` or `{GAIN}`.
- `{RES}` is built from a **Capture Area Size** line, and `{TEMP}` is copied from **Temperature**.
- `{FILENAME}` is the image file name, `{SESSION}` is the name of the folder containing the image, and `{DATETIME}` is the time the overlay was drawn.
- Image statistics tokens are not calculated in this mode and show `?`.
- Weather, ML and star detection tokens work the same as in ZWO Camera mode.

Anything missing from the sidecar file shows `?`.

---

## Token Availability by Mode

| Token Group | ZWO Camera | Directory Watch | Additional Requirement |
|-------------|:-:|:-:|---|
| Camera | Yes | From sidecar file | — |
| Image Statistics | Yes | No | — |
| Weather | Yes | Yes | API key + location or coordinates |
| ML Models | Yes | Yes | **Enable ML Analysis** on |
| Star Detection | Yes | Yes | Night-time and roof open (see above) |

---

## Usage Examples

**Camera info line:**
```
{CAMERA} | {EXPOSURE} | Gain: {GAIN}
```
Result: `ZWO ASI676MC | 30.00s | Gain: 100`

**Weather and conditions:**
```
{WEATHER_CITY}: {WEATHER_CONDITION} {WEATHER_TEMP}
Wind: {WEATHER_WIND_SPEED} {WEATHER_WIND_DIR}
```
Result:
```
London: Clear 8.0°C
Wind: 3.2 m/s NNW
```

**Observatory status line:**
```
Roof: {ROOF_STATUS} | Sky: {SKY_CONDITION} | Stars: {STAR_COUNT}
```
Result: `Roof: Open (95%) | Sky: Clear (87%) | Stars: 847`

**Timestamp only:**
```
{DATETIME}
```
Result: `2026-09-17 21:45:12`

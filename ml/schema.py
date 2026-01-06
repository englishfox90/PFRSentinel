#!/usr/bin/env python3
"""
Extended calibration schema for ML training.

Defines the full schema including auto-populated and manual labels.
"""

from dataclasses import dataclass, field
from typing import Optional
from enum import Enum


class RoofState(str, Enum):
    OPEN = "open"
    CLOSED = "closed"
    UNKNOWN = "unknown"


class CloudCoverage(str, Enum):
    CLEAR = "clear"          # 0-10%
    PARTLY_CLOUDY = "partly" # 10-50%
    MOSTLY_CLOUDY = "mostly" # 50-90%
    OVERCAST = "overcast"    # 90-100%


@dataclass
class WeatherData:
    """Weather info from OpenWeatherMap API (auto-populated)."""
    available: bool = False
    source: Optional[str] = None
    temperature_c: Optional[float] = None
    feels_like: Optional[str] = None
    humidity_pct: Optional[int] = None
    pressure_hpa: Optional[int] = None
    cloud_coverage_pct: Optional[int] = None
    condition: Optional[str] = None  # "Clear", "Clouds", "Rain", etc.
    description: Optional[str] = None  # "light rain", "scattered clouds"
    visibility_km: Optional[float] = None
    wind_speed: Optional[str] = None
    wind_dir: Optional[str] = None
    # Derived flags
    is_cloudy: Optional[bool] = None
    is_clear: Optional[bool] = None
    low_visibility: Optional[bool] = None


@dataclass
class MoonData:
    """Moon phase and visibility (auto-populated from astral)."""
    available: bool = False
    phase_value: Optional[float] = None  # 0-27.99 cycle
    phase_name: Optional[str] = None  # new_moon, first_quarter, full_moon, etc.
    illumination_pct: Optional[float] = None  # 0-100
    is_bright_moon: Optional[bool] = None  # illumination > 50%
    moon_is_up: Optional[bool] = None  # Currently above horizon
    moonrise: Optional[str] = None  # ISO timestamp
    moonset: Optional[str] = None  # ISO timestamp


@dataclass
class RoofState:
    """Roof/safety monitor state (auto-populated from NINA API)."""
    available: bool = False
    source: Optional[str] = None  # "nina_api"
    is_safe: Optional[bool] = None  # True = roof open
    roof_open: Optional[bool] = None  # Alias for is_safe
    is_connected: Optional[bool] = None
    device_name: Optional[str] = None
    reason: Optional[str] = None  # Error reason if unavailable


@dataclass
class SceneLabels:
    """Manual scene annotations (only fields that can't be auto-detected)."""
    # Binary classifications (require human judgment)
    moon_in_frame: Optional[bool] = None  # Moon visible IN the actual image
    stars_visible: Optional[bool] = None  # Stars visible in processed output
    imaging_train_visible: Optional[bool] = None
    
    # Continuous metrics (0.0 - 1.0)
    star_density: Optional[float] = None      # 0=none, 1=milky way
    sky_quality: Optional[float] = None       # 0=terrible, 1=perfect seeing
    
    # Quality assessment (after processing)
    output_quality_rating: Optional[int] = None  # 1-5 stars


@dataclass  
class RecipeUsed:
    """Recipe parameters that produced good results."""
    corner_sigma_bp: Optional[float] = None
    hp_k: Optional[float] = None
    hp_max_luma: Optional[float] = None
    shadow_denoise: Optional[float] = None
    shadow_start: Optional[float] = None
    shadow_end: Optional[float] = None
    chroma_blur: Optional[int] = None
    asinh: Optional[float] = None
    gamma: Optional[float] = None
    color_strength: Optional[float] = None
    blue_suppress: Optional[float] = None
    desaturate: Optional[float] = None


@dataclass
class StackingInfo:
    """Stacking decision and results."""
    was_stacked: bool = False
    num_frames: int = 1
    sigma_clip: Optional[float] = None
    rejection_rate: Optional[float] = None
    noise_reduction_achieved: Optional[float] = None


@dataclass
class ExtendedCalibration:
    """
    Full calibration schema with all ML training fields.
    
    Extends the existing calibration JSON with:
    - weather: Auto-populated from OpenWeatherMap
    - roof_state: Auto-populated from roof status file
    - scene: Manual annotations
    - recipe_used: What parameters worked well
    - stacking: If stacking was beneficial
    """
    # Existing fields (from current calibration)
    timestamp: str = ""
    camera: str = ""
    exposure: str = ""
    gain: str = ""
    
    # Extended fields
    weather: WeatherData = field(default_factory=WeatherData)
    roof_state: RoofState = RoofState.UNKNOWN
    scene: SceneLabels = field(default_factory=SceneLabels)
    recipe_used: RecipeUsed = field(default_factory=RecipeUsed)
    stacking: StackingInfo = field(default_factory=StackingInfo)


# Feature extraction for ML model
NORMALIZED_FEATURES = [
    # These transfer well across cameras (ratios/percentages)
    "percentiles.p99_p50_ratio",      # p99 / p50
    "percentiles.p90_p10_ratio",      # p90 / p10  
    "percentiles.dynamic_range_norm", # (p99-p1) / p50
    
    "corner_analysis.corner_to_center_ratio",
    "corner_analysis.center_minus_corner_norm",  # delta / p50
    "corner_analysis.corner_stddev_norm",        # stddev / corner_med
    
    "color_balance.r_g",
    "color_balance.b_g", 
    "color_balance.rgb_imbalance",    # max/min of r,g,b bias
    
    "stretch.is_dark_scene",
    "time_context.is_daylight",
    "time_context.is_astronomical_night",
]

CAMERA_SPECIFIC_FEATURES = [
    # These may need calibration per camera
    "percentiles.p1",
    "percentiles.p50", 
    "percentiles.p99",
    "corner_analysis.corner_med",
    "stretch.black_point",
    "stretch.white_point",
]

WEATHER_FEATURES = [
    "weather.cloud_coverage_pct",
    "weather.humidity_pct",
    "weather.fog_risk",
    "weather.visibility_m",
]

SCENE_LABELS = [
    "scene.moon_visible",
    "scene.stars_visible", 
    "scene.clouds_visible",
    "scene.star_density",
    "scene.cloud_coverage",
    "scene.moon_brightness",
]

RECIPE_TARGETS = [
    "recipe_used.corner_sigma_bp",
    "recipe_used.hp_k",
    "recipe_used.shadow_denoise",
    "recipe_used.chroma_blur",
    "recipe_used.asinh",
    "recipe_used.gamma",
]

STACKING_TARGETS = [
    "stacking.was_stacked",
    "stacking.num_frames",
]

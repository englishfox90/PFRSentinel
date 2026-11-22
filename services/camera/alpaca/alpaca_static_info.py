"""What a connected Alpaca camera reports about itself (read once on connect)."""
from dataclasses import dataclass
from typing import Optional

from .alpaca_sensor import (
    bayer_pattern_for,
    physical_bayer_pattern,
    sensor_bit_depth,
    transfer_bit_depth,
)


@dataclass(frozen=True)
class AlpacaStaticInfo:
    """What a connected camera reports about itself; ``None`` = not implemented."""
    name: str
    description: Optional[str]
    driver_info: Optional[str]
    driver_version: Optional[str]
    interface_version: int
    sensor_name: Optional[str]
    sensor_type: int
    bayer_offset_x: int
    bayer_offset_y: int
    width: int
    height: int
    pixel_size_x: Optional[float]
    electrons_per_adu: Optional[float]
    max_adu: int
    exposure_min: Optional[float]
    exposure_max: Optional[float]
    can_abort: bool
    can_stop: bool
    gain_mode: Optional[str]            # 'range' | 'list' | None
    gain_min: Optional[int]
    gain_max: Optional[int]
    gains: tuple
    offset_mode: Optional[str]
    offset_min: Optional[int]
    offset_max: Optional[int]
    offsets: tuple
    has_temperature: bool
    max_bin_x: Optional[int]

    @property
    def physical_bayer_pattern(self) -> str:
        return physical_bayer_pattern(self.sensor_type, self.bayer_offset_x, self.bayer_offset_y)

    @property
    def bayer_pattern(self) -> str:
        """The pipeline name (``debayer_raw_image`` / ``BAYER_PATTERN``)."""
        return bayer_pattern_for(self.sensor_type, self.bayer_offset_x, self.bayer_offset_y)

    @property
    def sensor_bit_depth(self) -> int:
        return sensor_bit_depth(self.max_adu)

    @property
    def transfer_bit_depth(self) -> int:
        return transfer_bit_depth(self.max_adu)

    @property
    def gain_range(self):
        if self.gain_mode == 'range' and self.gain_max is not None:
            return (self.gain_min, self.gain_max)
        if self.gain_mode == 'list':
            return (0, max(0, len(self.gains) - 1))
        return None

    @property
    def offset_range(self):
        if self.offset_mode == 'range' and self.offset_max is not None:
            return (self.offset_min, self.offset_max)
        if self.offset_mode == 'list':
            return (0, max(0, len(self.offsets) - 1))
        return None

    @property
    def exposure_range_s(self):
        if self.exposure_min is None and self.exposure_max is None:
            return None
        return (self.exposure_min if self.exposure_min is not None else 0.0,
                self.exposure_max if self.exposure_max is not None else 3600.0)

    def clamp_exposure(self, seconds: float) -> float:
        low, high = self.exposure_range_s or (0.0, None)
        seconds = max(float(seconds), low or 0.0)
        return min(seconds, high) if high is not None else seconds


__all__ = ["AlpacaStaticInfo"]

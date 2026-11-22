"""Show, hide and bound the camera settings widgets from a `CameraCapabilities`.

Display logic only (issue #43, plan §5). Nothing here writes config: every change
runs with the widget's `_loading_config` guard up, so a clamped spin box or a
rebuilt combo never saves itself. Applying ZWO capabilities puts every widget back
exactly as the ZWO path always had it.
"""
from qfluentwidgets import CaptionLabel

from ..theme.tokens import Colors

ZWO_WB_MODES = ["asi_auto", "manual", "gray_world"]
SOFTWARE_WB_MODES = ["manual", "gray_world"]

# Used while a range is unknown (an Alpaca camera before it connects): wide
# enough that a stored value is never clamped by the display.
_OPEN_RANGE = (0, 65535)
_MAX_EXPOSURE_S = 3600.0

_GAIN_TOOLTIP = "Camera gain/sensitivity (higher = brighter but more noise)"
_ZWO_BAYER_TOOLTIP = "Color filter array pattern - BGGR for most ZWO cameras"
_ZWO_BAYER_HINT = "BGGR for most ASI cameras"
_DEVICE_BAYER_TOOLTIP = "Reported by the camera (SensorType/BayerOffset)"


def _set_hint(row, text: str):
    labels = row.findChildren(CaptionLabel) if row is not None else []
    if labels:
        labels[0].setText(text)


def apply_capabilities(widget, caps, gain_names=()):
    """Make `widget` (a CameraSettingsWidget) match `caps`. Idempotent.

    `gain_names` (an Alpaca camera's ``Gains`` list) labels the gain index the
    spin box holds; any other caps clear them.
    """
    widget._caps = caps
    names = tuple(str(n) for n in (gain_names or ()))
    lo_hi = caps.gain_range
    widget._gain_names = names if names and lo_hi == (0, len(names) - 1) else ()
    was_loading = widget._loading_config
    widget._loading_config = True
    try:
        _connection(widget, caps)
        _ranges(widget, caps)
        _white_balance(widget, caps)
        _bayer(widget, caps)
        _raw16(widget, caps)
    finally:
        widget._loading_config = was_loading


def _connection(widget, caps):
    zwo = widget.zwo_card
    zwo.sdk_row.setVisible(caps.needs_sdk_path)
    zwo.camera_row.setVisible(caps.index_selection)
    if not caps.index_selection:
        zwo.missing_notice.set_warning('')
    zwo.setVisible(caps.needs_sdk_path or caps.index_selection)
    widget.alpaca_card.setVisible(caps.network_connection)


def _ranges(widget, caps):
    lo, hi = caps.gain_range or _OPEN_RANGE
    widget.gain_spin.setRange(lo, hi)
    widget.gain_spin.setEnabled(caps.gain)
    _set_hint(widget.gain_row, f"{lo} to {hi}" if caps.gain_range
              else "Range is read from the camera when it connects")
    show_gain_name(widget)

    lo, hi = caps.offset_range or _OPEN_RANGE
    widget.offset_spin.setRange(lo, hi)
    widget.offset_row.setVisible(caps.offset)
    _set_hint(widget.offset_row, f"Black level ({lo}-{hi})" if caps.offset_range
              else "Black level, range read from the camera when it connects")

    longest = _MAX_EXPOSURE_S
    if caps.exposure_range_s and caps.exposure_range_s[1] > 0:
        longest = min(_MAX_EXPOSURE_S, float(caps.exposure_range_s[1]))
    widget.exposure_spin.setMaximum(longest)
    widget.max_exposure_spin.setMaximum(longest)
    span = f"{widget.exposure_spin.minimum():g}s to {longest:g}s"
    widget.exposure_spin.setToolTip(f"Single frame exposure time ({span})")
    _set_hint(getattr(widget, 'exposure_row', None), span)


def show_gain_name(widget):
    """With named gains, the hint names the selected one and the tooltip lists them."""
    names = getattr(widget, '_gain_names', ())
    if not names:
        widget.gain_spin.setToolTip(_GAIN_TOOLTIP)
        return
    index = widget.gain_spin.value()
    current = names[index] if 0 <= index < len(names) else "?"
    _set_hint(widget.gain_row, f"{current} (gain setting {index} of 0 to {len(names) - 1})")
    listed = "\n".join(f"{i}: {name}" for i, name in enumerate(names))
    widget.gain_spin.setToolTip(f"Gain settings this camera offers:\n{listed}")


def _white_balance(widget, caps):
    modes = ZWO_WB_MODES if caps.hardware_white_balance else SOFTWARE_WB_MODES
    if list(widget._wb_modes) != modes:
        combo = widget.wb_mode_combo
        combo.blockSignals(True)
        try:
            combo.clear()
            combo.addItems(modes)
        finally:
            combo.blockSignals(False)
        widget._wb_modes = list(modes)
    widget.hw_wb_settings.setVisible(caps.hardware_white_balance)
    widget.sw_wb_settings.setVisible(not caps.hardware_white_balance)
    widget._select_wb_mode(widget._stored_wb_mode)


def _bayer(widget, caps):
    combo = widget.bayer_combo
    combo.blockSignals(True)
    try:
        if not caps.bayer_from_device:
            combo.setEnabled(True)
            combo.setToolTip(_ZWO_BAYER_TOOLTIP)
            _set_hint(widget.bayer_row, _ZWO_BAYER_HINT)
            widget._select_bayer(widget._profile_bayer)
            return
        combo.setEnabled(False)
        combo.setToolTip(_DEVICE_BAYER_TOOLTIP)
        pattern = caps.bayer_pattern
        if not pattern:
            _set_hint(widget.bayer_row, "Read from the camera when it connects")
            return
        _set_hint(widget.bayer_row, "Reported by the camera")
        if not widget._select_bayer(pattern):
            combo.setCurrentIndex(-1)
            combo.setPlaceholderText({'MONO': "Mono (no debayer)", 'RGB': "RGB from the camera"}
                                     .get(str(pattern).upper(), str(pattern)))
    finally:
        combo.blockSignals(False)


def _raw16(widget, caps):
    # RAW16 is a ZWO SDK image type; other backends report their bit depth.
    if caps.backend == 'zwo':
        widget.raw16_switch.show()
        if widget._raw16_status_borrowed:
            widget._raw16_status_borrowed = False
            widget.reset_camera_capabilities()
        return
    widget.raw16_switch.hide()
    widget._raw16_status_borrowed = True
    depth = caps.bit_depth
    if not depth:
        text = "Bit depth is read from the camera when it connects"
    elif depth <= 8:
        text = "8-bit frames from the camera"
    else:
        text = f"16-bit frames ({depth}-bit sensor)"
    widget.raw16_status.setText(text)
    widget.raw16_status.setStyleSheet(f"color: {Colors.text_secondary}; padding: 4px 8px;")

"""INDIGO's Alpaca bridge (issue #43, Mac mini rig, 2026-11-22): MaxADU 4096 in every
readout mode, while "RAW 8 3552x3552" (its default) sent 2-255 and "RAW 16 3552x3552"
sent 560-65534. ``alpaca_readout`` moves the 8-bit mode to its 16-bit twin on the
capture path (never on Test Connection) and widens the session's pixel ceiling to
16 bits the first time a frame arrives above MaxADU, instead of clipping it."""
import numpy as np
import pytest

from services import posthog_service
from services.camera.alpaca.alpaca_probe import probe_alpaca_camera
from services.camera.alpaca.alpaca_readout import (
    ADOPTED_MAX_ADU,
    adopt_pixel_range,
    pixel_ceiling,
    preferred_readout_mode,
    readout_bit_depth,
)
from services.camera.alpaca.alpaca_warnings import SessionWarnings
from tests.alpaca_fake import load_sim_package
from tests.alpaca_rig import alpaca_settings, make_rig

indigo_readout_modes = load_sim_package().indigo_readout_modes

# What INDIGO 2.0-324 listed for the ASI676MC, verbatim.
INDIGO_ASI676MC_MODES = [
    "RAW 8 3552x3552", "RGB 24 3552x3552", "RAW 16 3552x3552", "Y 8 3552x3552",
    "RAW 8 1776x1776", "RGB 24 1776x1776", "RAW 16 1776x1776", "Y 8 1776x1776",
    "RAW 8 1184x1184", "RGB 24 1184x1184", "RAW 16 1184x1184", "Y 8 1184x1184",
    "RAW 8 888x888", "RGB 24 888x888", "RAW 16 888x888", "Y 8 888x888",
]


@pytest.fixture(autouse=True)
def _no_analytics(monkeypatch):
    monkeypatch.setattr(posthog_service, "capture_event", lambda *a, **k: None)


# -- mode names -----------------------------------------------------------------
@pytest.mark.parametrize("name, depth", [
    ("RAW 8 3552x3552", 8), ("RAW 16 1776x1776", 16), ("Y 8 888x888", 8),
    ("RAW16", 16), ("raw8", 8), ("Mono 16-bit", 16), ("8 bit", 8),
    ("RGB 24 3552x3552", None), ("Normal", None), ("Fast", None), ("", None), (None, None),
])
def test_readout_bit_depth_reads_the_mode_name(name, depth):
    assert readout_bit_depth(name) == depth


@pytest.mark.parametrize("current, expected", [
    (0, 2),       # RAW 8 full → RAW 16 full (INDIGO's default, the rig's case)
    (4, 6),       # RAW 8 binned 2x2 → RAW 16 at the same size, never full frame
    (3, None),    # Y 8: no Y 16 offered
    (2, None),    # already 16-bit
    (1, None),    # RGB 24 is never touched
    (99, None), ("x", None), (None, None),
])
def test_only_an_8_bit_mode_moves_to_its_16_bit_twin(current, expected):
    assert preferred_readout_mode(INDIGO_ASI676MC_MODES, current) == expected


def test_modes_without_a_bit_depth_are_left_alone():
    assert preferred_readout_mode(["Normal", "Fast", "Low noise"], 0) is None
    assert preferred_readout_mode(None, 0) is None


# -- pixel ceiling ----------------------------------------------------------------
def test_a_frame_within_max_adu_keeps_max_adu():
    state = {}
    assert adopt_pixel_range(4000, 4096, state, SessionWarnings()) == 4095
    assert ADOPTED_MAX_ADU not in state


def test_a_frame_above_max_adu_adopts_16_bits_for_the_rest_of_the_session(caplog):
    warnings = SessionWarnings()
    state = warnings.state
    assert adopt_pixel_range(65534, 4096, state, warnings) == 65535
    # A later dark frame (everything under MaxADU) is scaled exactly like the bright one.
    assert adopt_pixel_range(900, 4096, state, warnings) == 65535
    assert pixel_ceiling(4096, state) == 65535
    assert sum("above its MaxADU" in r.getMessage() for r in caplog.records) == 1


def test_a_camera_claiming_8_bits_is_never_widened():
    warnings = SessionWarnings()
    assert adopt_pixel_range(4000, 255, warnings.state, warnings) == 255
    assert ADOPTED_MAX_ADU not in warnings.state


def test_values_beyond_16_bits_are_still_clipped():
    warnings = SessionWarnings()
    assert adopt_pixel_range(70000, 4096, warnings.state, warnings) == 4095
    assert ADOPTED_MAX_ADU not in warnings.state


# -- end to end on an INDIGO-like simulator -----------------------------------------
def _indigo_rig(monkeypatch, modes=None):
    modes = modes if modes is not None else indigo_readout_modes(32, 24)
    rig = make_rig(monkeypatch, width=32, height=24, max_adu=4095,
                   readout_modes=modes, readout_depth_from_name=True)
    assert rig.session.connect_camera(), rig.session._connection.last_error
    rig.session.is_capturing = True
    return rig


def _raw(meta) -> np.ndarray:
    width, height, depth, _pattern = meta["RAW_GEOMETRY"]
    dtype = "<u2" if depth == 16 else np.uint8
    return np.frombuffer(meta["RAW_BAYER"], dtype=dtype).reshape(height, width)


def test_capture_switches_indigo_to_raw16_and_keeps_every_value(monkeypatch):
    rig = _indigo_rig(monkeypatch)
    assert rig.cam.readout_mode == 2
    _img, meta = rig.session.capture_single_frame()
    sent = rig.cam.image
    assert sent.max() > 4095, "the fake must send MSB-aligned data like INDIGO"
    assert np.array_equal(_raw(meta), sent.astype(np.uint16))


def test_test_connection_never_changes_the_readout_mode(monkeypatch):
    rig = make_rig(monkeypatch, width=32, height=24, max_adu=4095,
                   readout_modes=indigo_readout_modes(32, 24), readout_depth_from_name=True)
    result = probe_alpaca_camera(alpaca_settings(rig.net), session_factory=rig.net.session,
                                 deadline_s=30.0)
    assert result.ok, result.message
    assert rig.puts("readoutmode") == []
    assert rig.cam.readout_mode == 0


def test_a_server_without_readout_modes_gets_no_readout_write(monkeypatch):
    rig = make_rig(monkeypatch, width=32, height=24)
    assert rig.session.connect_camera()
    assert rig.puts("readoutmode") == []


def test_indigos_header_sized_shortfall_keeps_imagebytes_and_zeroes_the_corner(
        monkeypatch, caplog):
    rig = make_rig(monkeypatch, width=32, height=24, imagebytes_short_by=44)
    assert rig.session.connect_camera()
    rig.session.is_capturing = True
    for _ in range(2):
        _img, meta = rig.session.capture_single_frame()
        assert rig.session.last_image_format == "imagebytes"
    got, sent = _raw(meta) >> 4, rig.cam.image.astype(np.uint16)
    # The simulator sends 12-bit data as UInt16, so 44 bytes are 22 elements (INDIGO's
    # Int32 loses 11). ImageBytes is [x][y]: they are the bottom of the last column.
    missing = 22
    assert np.array_equal(got[:-missing, :], sent[:-missing, :])
    assert np.array_equal(got[:, :-1], sent[:, :-1])
    assert not got[-missing:, -1].any()
    assert sum("arrive 44 bytes short" in r.getMessage() for r in caplog.records) == 1


def test_a_server_that_keeps_sending_broken_imagebytes_is_taken_as_json_for_the_session(
        monkeypatch, caplog):
    rig = make_rig(monkeypatch, width=32, height=24, imagebytes_short_by=40)
    assert rig.session.connect_camera()
    rig.session.is_capturing = True
    _img, meta = rig.session.capture_single_frame()
    assert np.array_equal(_raw(meta) >> 4, rig.cam.image.astype(np.uint16))
    assert rig.session.last_image_format == "json"
    rig.session.capture_single_frame()
    rig.session.reconnect_camera_safe()
    rig.session.capture_single_frame()
    accepts = [r.headers.get("Accept", "") for r in rig.net.member_requests("imagearray", "GET")]
    assert ["imagebytes" in a for a in accepts] == [True, False, True, False, False]
    assert sum("cannot be decoded" in r.getMessage() and "for this session" in r.getMessage()
               for r in caplog.records) == 1


def test_one_broken_imagebytes_payload_does_not_give_up_on_imagebytes(monkeypatch):
    rig = make_rig(monkeypatch, width=32, height=24, imagebytes_short_by=40)
    assert rig.session.connect_camera()
    rig.session.is_capturing = True
    rig.session.capture_single_frame()
    assert rig.session.last_image_format == "json"
    rig.cam.spec.imagebytes_short_by = 0
    rig.session.capture_single_frame()
    assert rig.session.last_image_format == "imagebytes"
    rig.cam.spec.imagebytes_short_by = 40
    rig.session.capture_single_frame()
    rig.cam.spec.imagebytes_short_by = 0
    rig.session.capture_single_frame()
    assert rig.session.last_image_format == "imagebytes"


def test_an_8_bit_only_server_is_warned_about_once(monkeypatch, caplog):
    rig = _indigo_rig(monkeypatch, modes=["RAW 8 32x24", "Y 8 32x24"])
    assert rig.cam.readout_mode == 0
    rig.session.capture_single_frame()
    rig.session.reconnect_camera_safe()
    rig.session.capture_single_frame()
    lines = [r.getMessage() for r in caplog.records if "(8-bit) but reports MaxADU" in r.getMessage()]
    assert len(lines) == 1 and "16x too dark" in lines[0]

"""scripts/dev/alpaca_camera_sim.py over real sockets: our client, ASCOM's alpyca, discovery, CLI.

Binds 127.0.0.1 only (``requires_network``). The alpyca half is skipped when
the official client is not installed (``pip install alpyca``).
"""
import importlib.util
import re
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pytest

from services.camera.alpaca.alpaca_discovery import discover_cameras, discover_servers
from services.camera.alpaca.alpaca_http import AlpacaHttpClient
from services.camera.alpaca.alpaca_protocol import AlpacaInvalidOperationError
from services.camera.alpaca.alpaca_sensor import bayer_pattern_for, to_raw_bytes
from services.camera.camera_utils import debayer_raw_image
from tests.alpaca_fake import FakeAlpacaCamera, SimCameraSpec, load_sim_package

pytestmark = pytest.mark.requires_network

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "dev" / "alpaca_camera_sim.py"


def _sim_module():
    load_sim_package()
    spec = importlib.util.spec_from_file_location("alpaca_camera_sim", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def start_sim():
    sim = _sim_module()
    servers = []

    def start(discovery=False, **spec):
        spec.setdefault("width", 64)
        spec.setdefault("height", 48)
        server = sim.SimServer(FakeAlpacaCamera(SimCameraSpec(**spec)), "127.0.0.1", 0,
                               discovery_port=0 if discovery else None,
                               discovery_bind="127.0.0.1").start()
        servers.append(server)
        return server
    yield start
    for server in servers:
        server.stop()


def _client(server, **kw):
    return AlpacaHttpClient("127.0.0.1", server.port, server.camera.spec.device_number, **kw)


def _expose(client, duration=0.05):
    client.put("connected", Connected=True)
    client.put("startexposure", Duration=duration, Light=True)
    deadline = time.monotonic() + 10
    while not client.get("imageready"):
        assert time.monotonic() < deadline, "exposure never completed"
        time.sleep(0.02)


@pytest.mark.parametrize("ox,oy", [(0, 0), (1, 0), (0, 1), (1, 1)])
def test_bayer_orientation_end_to_end(start_sim, ox, oy):
    server = start_sim(sensor_type=2, bayer_offset_x=ox, bayer_offset_y=oy,
                       scene="red_top_blue_bottom", max_adu=4095)
    client = _client(server)
    _expose(client)
    pattern = bayer_pattern_for(client.get("sensortype"), client.get("bayeroffsetx"),
                                client.get("bayeroffsety"))
    image = client.get_image(timeout=10)
    raw, depth = to_raw_bytes(image, client.get("maxadu"))
    rgb, _ = debayer_raw_image(raw, image.shape[1], image.shape[0], pattern, depth)
    top = rgb[2:20, 4:-4].reshape(-1, 3).mean(axis=0)
    bottom = rgb[28:-2, 4:-4].reshape(-1, 3).mean(axis=0)
    assert top[0] > 4 * top[2], f"{pattern}: top should be red, got {top}"
    assert bottom[2] > 4 * bottom[0], f"{pattern}: bottom should be blue, got {bottom}"
    client.close()


def test_imagebytes_and_json_agree_and_orientation_is_hw(start_sim):
    server = start_sim(scene="coords", width=40, height=30, max_adu=65535)
    client = _client(server)
    _expose(client)
    via_bytes = client.get_image(timeout=10, use_imagebytes=True)
    assert client.last_image_format == "imagebytes"
    via_json = client.get_image(timeout=10, use_imagebytes=False)
    assert client.last_image_format == "json"
    np.testing.assert_array_equal(via_bytes, via_json)
    np.testing.assert_array_equal(via_bytes, np.arange(1200).reshape(30, 40))
    client.close()


def test_brightness_follows_exposure_times_gain(start_sim):
    server = start_sim(scene="sky", width=96, height=96, offset=0)
    client = _client(server)

    def median(duration, gain):
        client.put("connected", Connected=True)
        client.put("gain", Gain=gain)
        _expose(client, duration)
        return float(np.median(client.get_image(timeout=10)[32:64, 32:64]))
    base = median(0.5, 0)
    assert median(2.0, 0) == pytest.approx(4 * base, rel=0.25)
    assert median(0.5, 200) == pytest.approx(10 * base, rel=0.25)
    client.close()


def test_spec_errors_over_http(start_sim):
    server = start_sim()
    client = _client(server)
    client.put("connected", Connected=True)
    with pytest.raises(AlpacaInvalidOperationError):
        client.get_image(timeout=5)
    client.put("startexposure", Duration=5.0, Light=True)
    with pytest.raises(AlpacaInvalidOperationError):
        client.put("startexposure", Duration=1.0, Light=True)
    client.put("abortexposure")
    assert client.get("camerastate") == 0
    client.close()


def test_alpyca_reads_the_simulator_like_our_client(start_sim):
    alpaca_camera = pytest.importorskip("alpaca.camera")
    server = start_sim(sensor_type=2, bayer_offset_x=1, bayer_offset_y=0, scene="sky",
                       width=50, height=34)
    cam = alpaca_camera.Camera(f"127.0.0.1:{server.port}", 0)
    cam.Connected = True
    assert cam.Connected and cam.Name == "PFR Alpaca Simulator"
    assert (cam.CameraXSize, cam.CameraYSize, cam.MaxADU) == (50, 34, 4095)
    assert int(cam.SensorType) == 2 and (cam.BayerOffsetX, cam.BayerOffsetY) == (1, 0)
    assert cam.GainMin == 0 and cam.GainMax == 570 and cam.CCDTemperature == 21.5
    cam.StartExposure(0.05, True)
    deadline = time.monotonic() + 10
    while not cam.ImageReady:
        assert time.monotonic() < deadline
        time.sleep(0.02)
    theirs = np.array(cam.ImageArray).T                    # alpyca returns Value[x][y]
    assert cam.ImageArrayInfo.TransmissionElementType == 8  # UInt16 via ImageBytes
    assert cam.LastExposureDuration == pytest.approx(0.05)
    ours = _client(server)
    np.testing.assert_array_equal(ours.get_image(timeout=10), theirs)
    np.testing.assert_array_equal(ours.get_image(timeout=10, use_imagebytes=False), theirs)
    assert theirs.shape == (34, 50)
    ours.close()


def test_alpyca_json_path_when_imagebytes_is_off(start_sim):
    alpaca_camera = pytest.importorskip("alpaca.camera")
    server = start_sim(supports_imagebytes=False, scene="coords", width=12, height=8)
    cam = alpaca_camera.Camera(f"127.0.0.1:{server.port}", 0)
    cam.Connected = True
    cam.StartExposure(0.05, True)
    deadline = time.monotonic() + 10
    while not cam.ImageReady:
        assert time.monotonic() < deadline
        time.sleep(0.02)
    np.testing.assert_array_equal(np.array(cam.ImageArray).T, np.arange(96).reshape(8, 12))


def test_alpyca_management_api(start_sim):
    management = pytest.importorskip("alpaca.management")
    server = start_sim()
    addr = f"127.0.0.1:{server.port}"
    assert management.apiversions(addr) == [1]
    devices = management.configureddevices(addr)
    assert devices[0]["DeviceType"] == "Camera" and devices[0]["UniqueID"]


def test_udp_discovery_and_management_listing(start_sim):
    server = start_sim(discovery=True, name="Loopback Sim", unique_id="uid-loop")
    assert server.discovery_port
    found = discover_servers(1.0, server.discovery_port, targets=("127.0.0.1",))
    assert ("127.0.0.1", server.port) in found
    cams = discover_cameras(1.0, port=server.discovery_port,
                            socket_factory=_loopback_socket)
    assert [(c.port, c.name, c.unique_id) for c in cams] == \
        [(server.port, "Loopback Sim", "uid-loop")]


def _loopback_socket():
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    return sock


def test_cli_starts_and_serves(tmp_path):
    proc = subprocess.Popen(
        [sys.executable, str(SCRIPT), "--port", "0", "--no-discovery", "--width", "32",
         "--height", "24", "--sensor-type", "0", "--no-imagebytes"],
        stderr=subprocess.PIPE, stdout=subprocess.DEVNULL, text=True)
    try:
        port = None
        deadline = time.monotonic() + 20
        while port is None and time.monotonic() < deadline:
            line = proc.stderr.readline()
            if not line and proc.poll() is not None:
                break
            match = re.search(r"ALPACA_SIM_PORT=(\d+)", line)
            if match:
                port = int(match.group(1))
        assert port, "simulator did not report its port"
        client = AlpacaHttpClient("127.0.0.1", port, 0)
        client.put("connected", Connected=True)
        assert client.get("sensortype") == 0
        _expose(client)
        assert client.get_image(timeout=10).shape == (24, 32)
        assert client.last_image_format == "json"
        client.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_discovery_binds_where_http_listens():
    sim = _sim_module()
    loop = sim.SimServer(FakeAlpacaCamera(SimCameraSpec(width=8, height=8)), "127.0.0.1", 0,
                         discovery_port=0).start()
    everywhere = sim.SimServer(FakeAlpacaCamera(SimCameraSpec(width=8, height=8)), "0.0.0.0", 0,
                               discovery_port=0).start()
    try:
        assert loop._udp.getsockname()[0] == "127.0.0.1"
        assert everywhere._udp.getsockname()[0] == "0.0.0.0"
    finally:
        loop.stop()
        everywhere.stop()


def test_live_download_through_the_guarded_session_and_slow_readout_calibration(start_sim):
    import time as _time
    from services import posthog_service
    from services.camera.alpaca.alpaca_camera import AlpacaCamera
    posthog_service.capture_event = lambda *a, **k: None
    server = start_sim(width=64, height=64, max_adu=4095, seed=7, readout_s=2.5)
    server.camera.clock = _time.monotonic
    session = AlpacaCamera({"host": "127.0.0.1", "port": server.port, "device_number": 0},
                           exposure_sec=0.002, gain=500, auto_exposure=True,
                           max_exposure_sec=2.0)
    assert session.connect_camera(), session.last_connect_error
    assert session.calibration_manager.run_calibration(max_attempts=4) is True
    session.disconnect_camera()

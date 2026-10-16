"""The timelapse writer's rgb24 frame bytes.

``_process_frame`` used to build them as ``np.array(image.convert('RGB'))
.tobytes()`` — four full-frame copies per frame. It now hands ffmpeg
``Image.tobytes()`` directly, so this pins that the bytes are identical for
every input mode the pipeline produces (RGB straight through, RGBA / L / 16-bit
converted) and that an RGB frame is not copied by ``convert``.
"""
import numpy as np
import pytest
from PIL import Image

import services.timelapse_writer as tw_mod
from services.timelapse_writer import TimelapseWriter


class _RecordingStdin:
    def __init__(self):
        self.chunks = []

    def write(self, data):
        self.chunks.append(bytes(data))

    def flush(self):
        pass

    def close(self):
        pass


class _AliveProcess:
    def __init__(self):
        self.stdin = _RecordingStdin()
        self.stderr = iter([])

    def poll(self):
        return None

    def wait(self, timeout=None):
        return 0

    def kill(self):
        pass


@pytest.fixture
def writer(monkeypatch, temp_dir):
    monkeypatch.setattr(tw_mod, 'is_ffmpeg_available', lambda: True)
    monkeypatch.setattr(tw_mod, 'get_ffmpeg_path', lambda: 'ffmpeg')
    import services.posthog_service as ph
    monkeypatch.setattr(ph, 'capture_event', lambda *a, **k: None)
    procs = []

    def fake_popen(cmd, **kwargs):
        procs.append(_AliveProcess())
        return procs[-1]
    monkeypatch.setattr(tw_mod.subprocess, 'Popen', fake_popen)

    w = TimelapseWriter()
    w.configure({'enabled': True, 'window_mode': 'always', 'output_dir': temp_dir})
    yield w, procs
    w.stop()


def _reference_bytes(image):
    return np.array(image.convert('RGB'), dtype=np.uint8).tobytes()


@pytest.mark.parametrize("mode", ['RGB', 'RGBA', 'L', 'I;16'])
def test_frame_bytes_match_the_numpy_path(writer, mode):
    w, procs = writer
    rng = np.random.default_rng(mode.encode()[0])
    if mode == 'I;16':
        arr = rng.integers(0, 65535, size=(48, 64), dtype=np.uint16)
        image = Image.fromarray(arr, mode='I;16')
    else:
        channels = {'RGB': 3, 'RGBA': 4, 'L': 1}[mode]
        shape = (48, 64, channels) if channels > 1 else (48, 64)
        image = Image.fromarray(rng.integers(0, 255, size=shape, dtype=np.uint8), mode=mode)

    w._process_frame(image)

    assert len(procs) == 1
    chunks = procs[0].stdin.chunks
    assert len(chunks) == 1
    assert len(chunks[0]) == 48 * 64 * 3
    assert chunks[0] == _reference_bytes(image)


def test_rgb_frame_is_not_converted(writer, monkeypatch):
    w, procs = writer
    image = Image.new('RGB', (64, 48), (7, 8, 9))
    calls = []
    original = Image.Image.convert

    def spy(self, *a, **k):
        calls.append(a)
        return original(self, *a, **k)
    monkeypatch.setattr(Image.Image, 'convert', spy)

    w._process_frame(image)

    assert calls == []
    assert procs[0].stdin.chunks[0] == image.tobytes()

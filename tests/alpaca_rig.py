"""Alpaca session builders on top of ``tests/alpaca_fake.py``.

``FakeTime`` stands in for a module's ``time`` and is driven by the same
``FakeClock`` the simulated camera reads, so a sleep inside the capture path
moves the exposure along instead of waiting for it.

    rig = make_rig(monkeypatch, scene="red_top_blue_bottom")
    rig.session.connect_camera()
    img, meta = rig.session.capture_single_frame()
"""
from dataclasses import dataclass

from tests.alpaca_fake import FakeAlpacaNetwork, FakeClock, make_fake

EPOCH = 1_790_000_000.0


class FakeTime:
    """``time``-module stand-in: ``time`` / ``monotonic`` / ``sleep`` on one clock."""

    def __init__(self, clock: FakeClock, epoch: float = EPOCH):
        self.clock = clock
        self.epoch = epoch
        self.slept = 0.0

    def monotonic(self) -> float:
        return self.clock.now

    def time(self) -> float:
        return self.epoch + self.clock.now

    def sleep(self, seconds: float) -> None:
        seconds = max(0.0, float(seconds))
        self.slept += seconds
        self.clock.advance(seconds)


@dataclass
class Rig:
    net: FakeAlpacaNetwork
    cam: object
    clock: FakeClock
    time: FakeTime
    session: object

    def puts(self, member):
        return [r.params for r in self.net.member_requests(member, "PUT")]


def alpaca_settings(net, **overrides) -> dict:
    settings = {"host": net.host, "port": net.port, "device_number": 0, "unique_id": "",
                "name": "", "use_imagebytes": True, "connect_timeout_s": 3.0,
                "request_timeout_s": 10.0, "release_on_disconnect": False}
    settings.update(overrides)
    return settings


def patch_time(monkeypatch, fake_time, *module_names):
    import importlib
    for name in module_names:
        monkeypatch.setattr(importlib.import_module(name), "time", fake_time)


ALPACA_TIME_MODULES = (
    "services.camera.alpaca.alpaca_capture_frame",
    "services.camera.alpaca.alpaca_device",
)


def make_rig(monkeypatch, *, settings=None, session_kwargs=None, extra_time_modules=(),
             **spec) -> Rig:
    """A fake camera, the fake clock patched into the capture path, and a session."""
    from services.camera.alpaca.alpaca_camera import AlpacaCamera
    net, cam, clock = make_fake(**spec)
    fake_time = FakeTime(clock)
    patch_time(monkeypatch, fake_time, *ALPACA_TIME_MODULES, *extra_time_modules)
    kwargs = {"exposure_sec": 0.5, "wb_config": {"mode": "manual"}}
    kwargs.update(session_kwargs or {})
    session = AlpacaCamera(alpaca_settings(net, **(settings or {})),
                           session_factory=net.session, **kwargs)
    return Rig(net, cam, clock, fake_time, session)

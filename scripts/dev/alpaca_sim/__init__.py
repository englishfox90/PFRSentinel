"""ASCOM Alpaca camera simulator (dev tool; not shipped).

``device.FakeAlpacaCamera`` is the pure state machine shared by
``scripts/dev/alpaca_camera_sim.py`` (real HTTP + UDP discovery) and
``tests/alpaca_fake.py`` (in-process ``requests.Session`` stand-in).
"""
from .device import FakeAlpacaCamera, SimCameraSpec, SimFaults, SimResponse
from .members import SimError
from .scene import SCENES, SceneGenerator

__all__ = ["FakeAlpacaCamera", "SimCameraSpec", "SimFaults", "SimResponse", "SimError",
           "SCENES", "SceneGenerator"]

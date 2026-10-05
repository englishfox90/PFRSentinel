"""Tests for services/nina_target_store.py — the pushed NINA target."""
import threading

import pytest

from services.nina_target_store import (
    NinaTarget,
    NinaTargetStore,
    get_nina_target_store,
    reset_nina_target_store,
)


M31 = NinaTarget(name="M31", ra_deg=10.6847, dec_deg=41.2687,
                 fov_w_deg=2.13, fov_h_deg=1.42, rotation_deg=15.0)


class _Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture(autouse=True)
def fresh_singleton():
    reset_nina_target_store()
    yield
    reset_nina_target_store()


@pytest.fixture
def clock():
    return _Clock()


@pytest.fixture
def store(clock):
    return NinaTargetStore(monotonic=clock)


def test_as_dict_keys_are_the_json_field_names():
    assert M31.as_dict() == {
        "name": "M31", "ra_deg": 10.6847, "dec_deg": 41.2687,
        "fov_w_deg": 2.13, "fov_h_deg": 1.42, "rotation_deg": 15.0, "source": "nina",
    }


def test_from_dict_round_trips_and_defaults():
    assert NinaTarget.from_dict(M31.as_dict()) == M31
    minimal = NinaTarget.from_dict({"name": "Vega", "ra_deg": 279, "dec_deg": 38})
    assert minimal == NinaTarget(name="Vega", ra_deg=279.0, dec_deg=38.0)
    assert minimal.fov_w_deg is None and minimal.source == "nina"


def test_empty_store_has_nothing(store):
    assert store.current(120) is None
    assert store.snapshot() == {"target": None, "age_s": None}


def test_set_reports_changed_then_unchanged(store):
    assert store.set(M31) is True
    assert store.set(NinaTarget.from_dict(M31.as_dict())) is False
    assert store.current(120) == M31


def test_a_different_target_is_a_change(store):
    store.set(M31)
    assert store.set(NinaTarget(name="M33", ra_deg=23.46, dec_deg=30.66)) is True


def test_current_expires_after_max_age(store, clock):
    store.set(M31)
    clock.t += 120.0
    assert store.current(120) == M31
    clock.t += 0.5
    assert store.current(120) is None


def test_identical_heartbeat_extends_life(store, clock):
    store.set(M31)
    clock.t += 100.0
    assert store.set(M31) is False
    clock.t += 100.0
    assert store.current(120) == M31


def test_set_none_clears_and_counts_as_change(store):
    store.set(M31)
    assert store.set(None) is True
    assert store.current(120) is None
    assert store.set(None) is False


def test_snapshot_shape_and_age(store, clock):
    store.set(M31)
    clock.t += 7.25
    snap = store.snapshot()
    assert snap == {"target": M31.as_dict(), "age_s": 7.25}


def test_clear_forgets_target_and_time(store):
    store.set(M31)
    store.clear()
    assert store.current(1e9) is None
    assert store.snapshot() == {"target": None, "age_s": None}


def test_singleton_is_shared_until_reset():
    a = get_nina_target_store()
    assert get_nina_target_store() is a
    a.set(M31)
    reset_nina_target_store()
    b = get_nina_target_store()
    assert b is not a
    assert b.current(1e9) is None


def test_concurrent_set_and_read_never_tears():
    store = NinaTargetStore()
    targets = [NinaTarget(name=f"T{i}", ra_deg=float(i), dec_deg=0.0) for i in range(20)]
    errors = []

    def writer():
        for _ in range(200):
            for t in targets:
                store.set(t)

    def reader():
        for _ in range(2000):
            snap = store.snapshot()
            target = snap["target"]
            if target is not None and float(target["name"][1:]) != target["ra_deg"]:
                errors.append(target)
            current = store.current(1e9)
            if current is not None and float(current.name[1:]) != current.ra_deg:
                errors.append(current)

    threads = [threading.Thread(target=writer) for _ in range(2)]
    threads += [threading.Thread(target=reader) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert errors == []

"""Tests for services/coordinate_hemisphere.py and the config migration's
pending-hemisphere record — a DMS coordinate saved without N/S/E/W is read as
north / east but the question is kept for the GUI to ask."""
import json
import types

import pytest

from services.config import Config, DEFAULT_CONFIG
from services.config_migrate import normalise_weather_coordinates
from services.coordinate_hemisphere import (
    UNCONFIRMED_KEY,
    apply_hemisphere,
    default_hemisphere,
    dismiss_confirmation,
    pending_confirmations,
)
from services.diagnostics_bundle import REDACTED, redact_config


class TestDefaults:
    def test_latitude_defaults_north_whatever_the_clock_says(self):
        assert default_hemisphere("latitude", -6) == "N"
        assert default_hemisphere("latitude", 10) == "N"

    def test_longitude_follows_the_pc_clock(self):
        assert default_hemisphere("longitude", -6) == "W"   # Texas
        assert default_hemisphere("longitude", -0.0) == "E"  # UK in winter
        assert default_hemisphere("longitude", 1) == "E"    # Europe
        assert default_hemisphere("longitude", 9.5) == "E"  # Australia

    def test_longitude_default_reads_the_host_clock_when_not_given(self):
        assert default_hemisphere("longitude") in ("E", "W")


class TestApply:
    def test_west_makes_the_stored_decimal_negative_and_clears_the_mark(self):
        weather = {"longitude": "100.4569444", UNCONFIRMED_KEY: {"longitude": "100 27 25"}}
        assert apply_hemisphere(weather, "longitude", "W") == "-100.4569444"
        assert weather["longitude"] == "-100.4569444"
        assert weather[UNCONFIRMED_KEY] == {}

    def test_east_keeps_it_positive(self):
        weather = {"longitude": "100.4569444", UNCONFIRMED_KEY: {"longitude": "100 27 25"}}
        assert apply_hemisphere(weather, "longitude", "e") == "100.4569444"

    def test_south_signs_latitude(self):
        weather = {"latitude": "33.8688", UNCONFIRMED_KEY: {"latitude": "33 52 8"}}
        assert apply_hemisphere(weather, "latitude", "S") == "-33.8688"

    def test_already_negative_value_is_not_double_flipped(self):
        weather = {"longitude": "-100.46", UNCONFIRMED_KEY: {"longitude": "x"}}
        assert apply_hemisphere(weather, "longitude", "W") == "-100.46"

    def test_wrong_letter_for_the_field_is_refused(self):
        with pytest.raises(ValueError):
            apply_hemisphere({"latitude": "31.33"}, "latitude", "W")

    def test_unparseable_value_clears_the_mark_and_returns_none(self):
        weather = {"longitude": "somewhere", UNCONFIRMED_KEY: {"longitude": "somewhere"}}
        assert apply_hemisphere(weather, "longitude", "W") is None
        assert weather["longitude"] == "somewhere"
        assert weather[UNCONFIRMED_KEY] == {}

    def test_dismiss_forgets_without_touching_the_value(self):
        weather = {"longitude": "100.46", UNCONFIRMED_KEY: {"longitude": "100 27 25", "latitude": "31 19 49"}}
        dismiss_confirmation(weather, "longitude")
        assert weather["longitude"] == "100.46"
        assert weather[UNCONFIRMED_KEY] == {"latitude": "31 19 49"}
        dismiss_confirmation({}, "longitude")  # nothing pending: no error


class TestPending:
    def test_only_fields_that_still_hold_a_value_are_pending(self):
        weather = {"latitude": "31.33", "longitude": "",
                   UNCONFIRMED_KEY: {"latitude": "31 19 49", "longitude": "100 27 25", "bogus": "x"}}
        assert pending_confirmations(weather) == {"latitude": "31 19 49"}

    def test_missing_or_malformed_record_is_empty(self):
        assert pending_confirmations({}) == {}
        assert pending_confirmations({UNCONFIRMED_KEY: "junk"}) == {}
        assert pending_confirmations(None) == {}


class TestMigrationRecordsTheQuestion:
    def test_unsigned_dms_is_recorded_as_typed(self):
        data = {"weather": {"latitude": " 31 19 49 ", "longitude": "100 27 25"}}
        normalise_weather_coordinates(data)
        assert data["weather"][UNCONFIRMED_KEY] == {"latitude": "31 19 49", "longitude": "100 27 25"}
        assert data["weather"]["longitude"] == "100.4569444"

    @pytest.mark.parametrize("value", ["100 27 25 W", "-100 27 25", "100.457", "-100.457"])
    def test_explicit_sign_or_decimal_records_nothing(self, value):
        data = {"weather": {"longitude": value}}
        normalise_weather_coordinates(data)
        assert UNCONFIRMED_KEY not in data["weather"]

    def test_default_config_carries_an_empty_record(self):
        assert DEFAULT_CONFIG["weather"][UNCONFIRMED_KEY] == {}

    def test_record_survives_load_and_is_persisted(self, tmp_path):
        cfg_path = tmp_path / "config.json"
        cfg_path.write_text(json.dumps({"weather": {"latitude": "31.33", "longitude": "100 27 25"}}))

        cfg = Config(str(cfg_path))

        assert pending_confirmations(cfg.get("weather")) == {"longitude": "100 27 25"}
        assert json.loads(cfg_path.read_text())["weather"][UNCONFIRMED_KEY] == {"longitude": "100 27 25"}

    def test_diagnostics_bundle_redacts_the_typed_coordinate(self):
        data = {"weather": {"longitude": "100.457", UNCONFIRMED_KEY: {"longitude": "100 27 25"}}}
        out = redact_config(data)
        assert out["weather"]["longitude"] == REDACTED
        assert out["weather"][UNCONFIRMED_KEY]["longitude"] == REDACTED


class TestStartupPrompt:
    """The main-window mixin method, run against a stand-in window."""

    def _window(self, tmp_path, weather):
        from ui.main_window.lifecycle import _MainWindowLifecycleMixin

        cfg_path = tmp_path / "config.json"
        cfg_path.write_text(json.dumps({"weather": weather}))
        win = types.SimpleNamespace(
            isVisible=lambda: True,
            config=Config(str(cfg_path)),
            settings_panel=types.SimpleNamespace(load_from_config=lambda cfg: None),
            _on_settings_changed=lambda: None,
        )
        win.confirm = types.MethodType(_MainWindowLifecycleMixin._confirm_coordinate_hemispheres, win)
        # The real name too: the hidden-window branch reschedules itself by it.
        win._confirm_coordinate_hemispheres = win.confirm
        return win, cfg_path

    def test_confirming_west_signs_and_saves(self, tmp_path, monkeypatch):
        import ui.dialogs.hemisphere_dialog as dlg
        win, cfg_path = self._window(tmp_path, {"latitude": "31.33", "longitude": "100 27 25"})
        seen = {}

        def fake_show(parent, pending, defaults):
            seen.update(pending=pending, defaults=defaults)
            return {"longitude": "W"}
        monkeypatch.setattr(dlg, "show_hemisphere_dialog", fake_show)

        win.confirm()

        assert seen["pending"] == {"longitude": "100 27 25"}
        assert seen["defaults"]["longitude"] in ("E", "W")
        on_disk = json.loads(cfg_path.read_text())["weather"]
        assert on_disk["longitude"] == "-100.4569444"
        assert on_disk[UNCONFIRMED_KEY] == {}

    def test_ask_me_later_keeps_the_question(self, tmp_path, monkeypatch):
        import ui.dialogs.hemisphere_dialog as dlg
        win, cfg_path = self._window(tmp_path, {"longitude": "100 27 25"})
        monkeypatch.setattr(dlg, "show_hemisphere_dialog", lambda *a: None)

        win.confirm()

        on_disk = json.loads(cfg_path.read_text())["weather"]
        assert on_disk["longitude"] == "100.4569444"
        assert on_disk[UNCONFIRMED_KEY] == {"longitude": "100 27 25"}

    def test_nothing_pending_never_opens_the_dialog(self, tmp_path, monkeypatch):
        import ui.dialogs.hemisphere_dialog as dlg
        win, _ = self._window(tmp_path, {"longitude": "-100.46"})
        monkeypatch.setattr(dlg, "show_hemisphere_dialog",
                            lambda *a: pytest.fail("dialog shown with nothing pending"))
        win.confirm()

    def test_hidden_window_waits_instead_of_opening_an_invisible_modal(self, tmp_path, monkeypatch):
        import ui.dialogs.hemisphere_dialog as dlg
        import ui.main_window.lifecycle as lifecycle
        win, cfg_path = self._window(tmp_path, {"longitude": "100 27 25"})
        win.isVisible = lambda: False
        monkeypatch.setattr(dlg, "show_hemisphere_dialog",
                            lambda *a: pytest.fail("dialog shown on a hidden window"))
        scheduled = []
        monkeypatch.setattr(lifecycle.QTimer, "singleShot",
                            staticmethod(lambda ms, fn: scheduled.append((ms, fn))))

        win.confirm()

        assert scheduled and scheduled[0][0] >= 1000
        assert json.loads(cfg_path.read_text())["weather"][UNCONFIRMED_KEY] == {"longitude": "100 27 25"}

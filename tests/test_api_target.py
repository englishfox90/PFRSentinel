"""Tests for services/api_target.py — POST /nina/target validation and docs."""
import json

import pytest

from services import api_control, api_target
from services.api_target import MAX_FOV_DEG, MAX_NAME_CHARS, parse_target_request
from services.nina_target_store import NinaTarget


FULL = {"name": "M31", "ra_deg": 10.6847, "dec_deg": 41.2687, "epoch": "J2000",
        "fov_w_deg": 2.13, "fov_h_deg": 1.42, "rotation_deg": 15.0, "source": "nina"}


def body(**overrides):
    target = dict(FULL)
    for key, value in overrides.items():
        if value is _DROP:
            target.pop(key, None)
        else:
            target[key] = value
    return json.dumps({"target": target}).encode("utf-8")


_DROP = object()


def accept(raw):
    params, error = parse_target_request(raw)
    assert error is None, error
    return params["target"]


def reject(raw, status=400):
    params, error = parse_target_request(raw)
    assert params is None
    assert error[0] == status, error
    return error[1]


# --- accepted payloads -----------------------------------------------------

def test_full_payload_normalises_to_a_target():
    assert accept(body()) == NinaTarget(
        name="M31", ra_deg=10.6847, dec_deg=41.2687, fov_w_deg=2.13,
        fov_h_deg=1.42, rotation_deg=15.0, source="nina")


def test_minimal_payload_takes_defaults():
    raw = json.dumps({"target": {"name": "Vega", "ra_deg": 279, "dec_deg": 38}})
    target = accept(raw.encode())
    assert target == NinaTarget(name="Vega", ra_deg=279.0, dec_deg=38.0)
    assert isinstance(target.ra_deg, float)


def test_null_target_clears():
    assert parse_target_request(b'{"target": null}') == ({"target": None}, None)


def test_unknown_keys_are_ignored():
    raw = json.dumps({"target": dict(FULL, future=1), "extra": True}).encode()
    assert accept(raw).name == "M31"


def test_str_and_dict_bodies_are_accepted():
    assert accept(body().decode()).name == "M31"
    assert accept({"target": FULL}).name == "M31"


@pytest.mark.parametrize("dec", [-90, 90, 0, -89.999])
def test_declination_edges_accepted(dec):
    assert accept(body(dec_deg=dec)).dec_deg == float(dec)


@pytest.mark.parametrize("ra", [0, 359.9999])
def test_right_ascension_edges_accepted(ra):
    assert accept(body(ra_deg=ra)).ra_deg == float(ra)


@pytest.mark.parametrize("rotation,expected", [(370, 10.0), (-90, 270.0), (360, 0.0), (0, 0.0)])
def test_rotation_is_normalised(rotation, expected):
    assert accept(body(rotation_deg=rotation)).rotation_deg == pytest.approx(expected)


@pytest.mark.parametrize("epoch", ["J2000", "j2000", " J2000 "])
def test_j2000_epoch_any_case(epoch):
    assert accept(body(epoch=epoch)).name == "M31"


def test_fov_may_be_omitted_together():
    target = accept(body(fov_w_deg=_DROP, fov_h_deg=_DROP))
    assert target.fov_w_deg is None and target.fov_h_deg is None


def test_fov_at_the_ceiling_is_accepted():
    assert accept(body(fov_w_deg=MAX_FOV_DEG, fov_h_deg=MAX_FOV_DEG)).fov_w_deg == MAX_FOV_DEG


def test_name_control_characters_stripped_and_whitespace_collapsed():
    assert accept(body(name="  M31\x00\x1b  Andromeda\t\nGalaxy ​")).name == \
        "M31 Andromeda Galaxy"


def test_long_name_is_truncated():
    assert accept(body(name="x" * 80)).name == "x" * MAX_NAME_CHARS


def test_source_defaults_and_is_capped():
    assert accept(body(source=_DROP)).source == "nina"
    assert accept(body(source="y" * 50)).source == "y" * api_target.MAX_SOURCE_CHARS


# --- rejected payloads -----------------------------------------------------

@pytest.mark.parametrize("ra", [360, -1, "10.5", True, None, float("nan"), float("inf")])
def test_bad_right_ascension_rejected(ra):
    reject(body(ra_deg=ra))


def test_nan_literal_in_json_rejected():
    reject(b'{"target": {"name": "M31", "ra_deg": NaN, "dec_deg": 1}}')


@pytest.mark.parametrize("dec", [90.0001, -91, "x", False])
def test_bad_declination_rejected(dec):
    reject(body(dec_deg=dec))


@pytest.mark.parametrize("missing", ["name", "ra_deg", "dec_deg"])
def test_required_target_fields(missing):
    reject(body(**{missing: _DROP}))


@pytest.mark.parametrize("epoch", ["JNOW", "B1950", 2000, ""])
def test_non_j2000_epoch_rejected(epoch):
    assert "J2000" in reject(body(epoch=epoch))


@pytest.mark.parametrize("drop", ["fov_w_deg", "fov_h_deg"])
def test_one_fov_field_alone_rejected(drop):
    assert "together" in reject(body(**{drop: _DROP}))


@pytest.mark.parametrize("fov", [0, -1, MAX_FOV_DEG + 1, "2", True])
def test_bad_fov_rejected(fov):
    reject(body(fov_w_deg=fov))


@pytest.mark.parametrize("rotation", ["15", True, float("inf")])
def test_bad_rotation_rejected(rotation):
    reject(body(rotation_deg=rotation))


@pytest.mark.parametrize("name", ["", "   ", "\x00\x01", 31, None])
def test_bad_name_rejected(name):
    reject(body(name=name))


def test_non_string_source_rejected():
    reject(body(source=5))


@pytest.mark.parametrize("raw", [None, b"", b"   ", b"{}", b'{"name": "M31"}'])
def test_missing_target_rejected(raw):
    assert "'target' is required" in reject(raw)


@pytest.mark.parametrize("raw", [b"[]", b'"x"', b"42"])
def test_non_object_body_rejected(raw):
    assert "JSON object" in reject(raw)


@pytest.mark.parametrize("raw", [b'{"target": []}', b'{"target": "M31"}', b'{"target": 1}'])
def test_non_object_target_rejected(raw):
    reject(raw)


def test_non_utf8_rejected():
    assert "UTF-8" in reject(b'\xff\xfe{"target": null}')


def test_invalid_json_rejected():
    assert "valid JSON" in reject(b"{nope")


def test_oversized_body_rejected_with_413():
    raw = b'{"target": null, "pad": "' + b"x" * api_control.MAX_BODY_BYTES + b'"}'
    reject(raw, status=413)


# --- result shaping ---------------------------------------------------------

def test_build_result_messages():
    stored = {"target": FULL, "age_s": 0.0}
    assert api_target.build_target_result(stored, True)["message"] == "Target stored."
    assert api_target.build_target_result(stored, False)["message"] == "Target unchanged."
    cleared = {"target": None, "age_s": 0.0}
    assert api_target.build_target_result(cleared, True)["message"] == "Target cleared."


def test_build_result_fields_match_the_catalog():
    result = api_target.build_target_result({"target": None, "age_s": None}, False)
    assert set(result) == {name for name, _, _ in api_target.TARGET_RESULT_FIELDS}
    assert result["accepted"] is True and result["age_s"] == 0.0


def test_target_routes_are_well_formed():
    for route in api_target.TARGET_ROUTES:
        assert set(route) == {"path", "method", "command", "summary", "description"}
        assert route["path"] == api_target.TARGET_PATH == "/nina/target"
        assert route["method"] == "post"


# --- OpenAPI ----------------------------------------------------------------

def test_openapi_includes_target_route_with_control_enabled():
    from services import api_docs
    spec = api_docs.build_openapi_spec(control_path="/capture")
    op = spec["paths"]["/nina/target"]["post"]
    assert op["security"] == [{"bearerAuth": []}]
    assert "bearerAuth" in spec["components"]["securitySchemes"]
    assert {"NinaTarget", "TargetResult"} <= set(spec["components"]["schemas"])
    assert "east of north" in op["description"]
    assert "J2000" in op["description"]


def test_openapi_omits_target_route_without_control():
    from services import api_docs
    spec = api_docs.build_openapi_spec()
    assert "/nina/target" not in spec["paths"]
    assert "NinaTarget" not in spec["components"]["schemas"]


def test_openapi_target_result_schema_matches_build_result():
    documented = set(api_target.openapi_schemas()["TargetResult"]["properties"])
    actual = set(api_target.build_target_result({"target": FULL, "age_s": 0}, True))
    assert documented == actual


def test_openapi_target_schema_lists_every_target_field():
    props = set(api_target.openapi_schemas()["NinaTarget"]["properties"])
    assert set(NinaTarget(name="x", ra_deg=0, dec_deg=0).as_dict()) <= props


def test_docs_html_renders_the_target_route():
    from services import api_docs
    html = api_docs.render_docs_html(api_docs.build_openapi_spec(control_path="/capture"))
    assert "/nina/target" in html
    assert "/capture/start" in html

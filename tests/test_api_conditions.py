"""GET /conditions — the NINA plugin's contract. A golden key set (names may
be added, never renamed or removed), the auth matrix shared with the control
routes (403 Host, 401 token, 503 control_disabled, no CORS), the route through
web_output's GET dispatch, the OpenAPI / docs entries and the compact
/status block."""
import io
import json

import pytest

from services import api_conditions, api_docs, web_conditions
from services.imaging_conditions import ConditionsStore, get_conditions_store
from services.web_output import ImageHTTPHandler

TOKEN = "test-token-value-1234567890"
AUTH = {"Authorization": f"Bearer {TOKEN}", "Host": "127.0.0.1:8080"}

# The contract the NINA plugin was written against. Additive fields are
# listed separately so a rename of an original name fails loudly.
ORIGINAL_KEYS = {
    "enabled", "updated_at", "age_s", "stale", "imaging_ok", "reasons", "changed_at",
    "frames_considered", "roof", "sky", "stars", "cloud_cover_pct", "cloud_cover_source",
    "fwhm", "sun_altitude_deg",
}
ADDED_KEYS = {"stale_after_s", "frame_ok", "bad_streak", "good_streak", "moon",
              "baseline_bin", "baseline_status", "events"}
NESTED = {
    "roof": {"state", "confidence"},
    "sky": {"condition", "confidence", "stars_visible"},
    "stars": {"count", "baseline", "ratio", "trend", "change_pct", "window_frames"},
    "fwhm": {"px", "arcsec", "night_median_px", "change_pct"},
    "moon": {"up", "illumination_pct"},
    "baseline_status": {"state", "samples", "needed", "bin"},
}
ORIGINAL_NESTED = {
    "roof": {"state", "confidence"},
    "sky": {"condition", "confidence", "stars_visible"},
    "stars": {"count", "baseline", "ratio", "trend"},
    "fwhm": {"px", "arcsec", "night_median_px"},
}


class _FakeServer:
    control_path = "/capture"
    control_host = "127.0.0.1"
    control_token = TOKEN


class _FakeHandler:
    def __init__(self, path="/conditions", headers=None, token=TOKEN):
        self.path = path
        self.headers = dict(AUTH if headers is None else headers)
        self.server = _FakeServer()
        self.server.control_token = token
        self.wfile = io.BytesIO()
        self.status = None
        self.sent_headers = {}

    def send_response(self, code):
        self.status = code

    def send_header(self, key, value):
        self.sent_headers[key] = value

    def end_headers(self):
        pass

    def send_error(self, code, message=None):
        self.status = code

    @property
    def body(self):
        return json.loads(self.wfile.getvalue().decode("utf-8"))


def get(**kw):
    handler = _FakeHandler(**kw)
    web_conditions.serve_conditions(handler)
    return handler


def published_snapshot():
    from services.imaging_conditions import ImagingConditionsFeed
    from services.imaging_conditions.star_baseline import StarBaseline
    from services.sky_evidence import EVIDENCE_KEY
    import tempfile
    import os
    store = ConditionsStore()
    feed = ImagingConditionsFeed(store=store,
                                 baseline=StarBaseline(os.path.join(tempfile.mkdtemp(), 'b')),
                                 sun_alt_fn=lambda c: -30.0,
                                 moon_fn=lambda: {'moon_is_up': False, 'illumination_pct': 1})
    feed.observe({'_ML_RESULTS': {'roof_status': 'Open', 'roof_confidence': 0.9},
                  EVIDENCE_KEY: {'star_count': 10}}, {}, (100, 100))
    return store.snapshot()


@pytest.mark.parametrize("snapshot_fn", [lambda: ConditionsStore().snapshot(), published_snapshot])
def test_golden_contract_key_set(snapshot_fn):
    snap = snapshot_fn()
    assert set(snap) == ORIGINAL_KEYS | ADDED_KEYS
    for key, fields in NESTED.items():
        assert set(snap[key]) == fields, key
        assert ORIGINAL_NESTED.get(key, set()) <= set(snap[key])
    json.dumps(snap)


def test_catalog_documents_exactly_the_payload():
    assert set(api_conditions.CONTRACT_KEYS) == ORIGINAL_KEYS | ADDED_KEYS
    props = dict(api_conditions.CONDITIONS_FIELDS)
    for key, fields in NESTED.items():
        assert set(props[key]["properties"]) == fields


def test_types_of_a_cold_snapshot():
    snap = ConditionsStore().snapshot()
    assert snap["enabled"] is True and snap["imaging_ok"] is False
    assert snap["reasons"] == ["warming_up"] and snap["stale"] is False
    assert snap["roof"]["state"] == "Unknown" and snap["cloud_cover_source"] is None
    assert snap["baseline_status"]["state"] == "learning"


def test_every_reason_code_is_documented():
    from services.imaging_conditions import frame_rules
    from services.imaging_conditions.conditions_store import DISABLED, STALE, WARMING_UP
    from services.imaging_conditions.feed import RECOVERING
    codes = {c for c, _ in api_conditions.REASON_CODES}
    produced = {getattr(frame_rules, n) for n in ("DAYLIGHT", "ROOF_NOT_OPEN", "FRAME_STATIC",
                                                  "SKY_UNKNOWN", "CLOUD_COVER_HIGH", "STARS_LOW",
                                                  "STARS_DROPPING", "FWHM_HIGH")}
    assert produced | {DISABLED, STALE, WARMING_UP, RECOVERING} == codes


def test_authorized_get_returns_the_store_snapshot_without_cors():
    handler = get()
    assert handler.status == 200
    assert set(handler.body) == ORIGINAL_KEYS | ADDED_KEYS
    assert "Access-Control-Allow-Origin" not in handler.sent_headers
    assert handler.sent_headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize("headers, token, status, code", [
    ({"Host": "127.0.0.1:8080"}, TOKEN, 401, "unauthorized"),
    ({"Host": "127.0.0.1", "Authorization": "Bearer wrong"}, TOKEN, 401, "unauthorized"),
    ({"Host": "127.0.0.1", "Authorization": f"Basic {TOKEN}"}, TOKEN, 401, "unauthorized"),
    ({"Host": "evil.example", "Authorization": f"Bearer {TOKEN}"}, TOKEN, 403, "host_not_allowed"),
    (AUTH, "", 503, "control_disabled"),
])
def test_auth_matrix(headers, token, status, code):
    handler = get(headers=headers, token=token)
    assert handler.status == status and handler.body["code"] == code
    assert "Access-Control-Allow-Origin" not in handler.sent_headers
    assert TOKEN not in handler.wfile.getvalue().decode()


def test_route_matching():
    for path in ("/conditions", "/conditions/", "/conditions?t=1"):
        h = _FakeHandler(path=path)
        assert web_conditions.route_get(h) and h.status == 200
    assert web_conditions.route_get(_FakeHandler(path="/conditionsx")) is False


def test_web_output_get_dispatches_conditions():
    handler = ImageHTTPHandler.__new__(ImageHTTPHandler)
    handler.path = "/conditions"
    handler.headers = dict(AUTH)
    server = _FakeServer()
    server.config_path, server.status_path = "/latest", "/status"
    handler.server = server
    handler.wfile = io.BytesIO()
    sent = {}
    handler.send_response = lambda code: sent.setdefault("status", code)
    handler.send_header = lambda k, v: None
    handler.end_headers = lambda: None
    handler.do_GET()
    assert sent["status"] == 200
    assert "imaging_ok" in json.loads(handler.wfile.getvalue())


def test_openapi_and_docs_carry_the_route_only_with_control():
    assert "/conditions" not in api_docs.build_openapi_spec()["paths"]
    spec = api_docs.build_openapi_spec(control_path="/capture")
    op = spec["paths"]["/conditions"]["get"]
    assert op["security"] == [{"bearerAuth": []}]
    assert {"200", "401", "403", "503"} <= set(op["responses"])
    schema = spec["components"]["schemas"]["ImagingConditions"]
    assert set(schema["required"]) == ORIGINAL_KEYS | ADDED_KEYS
    html = api_docs.render_docs_html(spec)
    assert "/conditions" in html and "stars_dropping" in html and "warming_up" in html
    assert "stars_dropping" not in api_docs.render_docs_html(api_docs.build_openapi_spec())
    assert "conditions" in spec["components"]["schemas"]["Status"]["properties"]


def test_docs_describe_every_conditions_field():
    html = api_docs.render_docs_html(api_docs.build_openapi_spec(control_path="/capture"))
    assert "<code>/conditions</code> fields" in html
    rows = api_conditions.field_rows()
    names = [name for name, _, _ in rows]
    assert set(api_conditions.CONTRACT_KEYS) <= set(names)
    assert {"stars.count", "stars.change_pct", "fwhm.change_pct",
            "baseline_status.state", "moon.up"} <= set(names)
    for name, typ, desc in rows:
        assert typ and desc, name
        assert f"<td class='path'>{name}</td>" in html
    assert "<code>/conditions</code> fields" not in api_docs.render_docs_html(
        api_docs.build_openapi_spec())


def test_status_carries_the_compact_block():
    status = ImageHTTPHandler._build_status_dict()
    assert status["conditions"] == {"imaging_ok": False, "reasons": ["warming_up"],
                                    "changed_at": None, "stale": False}
    assert status["conditions"] == get_conditions_store().compact()

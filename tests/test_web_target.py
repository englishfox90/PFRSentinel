"""Tests for services/web_target.py — the POST /nina/target route.

Driven through a fake request handler (the pattern in test_web_control.py), so
the auth matrix runs in the default selection with no socket.
"""
import io
import json

import pytest

from services import api_control, web_control, web_target
from services.logger import app_logger
from services.nina_target_store import (
    NinaTarget,
    get_nina_target_store,
    reset_nina_target_store,
)


TOKEN = "test-token-value-1234567890"
AUTH = {"Authorization": f"Bearer {TOKEN}", "Host": "127.0.0.1:8080"}
M31 = {"name": "M31", "ra_deg": 10.6847, "dec_deg": 41.2687, "epoch": "J2000",
       "fov_w_deg": 2.13, "fov_h_deg": 1.42, "rotation_deg": 15.0, "source": "nina"}
M31_BODY = json.dumps({"target": M31}).encode("utf-8")


class _FakeServer:
    control_path = "/capture"
    control_host = "127.0.0.1"
    control_token = TOKEN
    capture_command_handler = None


class _FakeHandler:
    def __init__(self, path="/nina/target", headers=None, body=b"", server=None):
        self.path = path
        self.headers = dict(headers if headers is not None else AUTH)
        self.rfile = io.BytesIO(body)
        if body:
            self.headers.setdefault("Content-Length", str(len(body)))
        self.server = server or _FakeServer()
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


def make_handler(path="/nina/target", body=M31_BODY, headers=None):
    return _FakeHandler(path=path, headers=headers, body=body, server=_FakeServer())


def post(handler):
    web_target.serve_target(handler)
    return handler


@pytest.fixture(autouse=True)
def fresh_store():
    reset_nina_target_store()
    yield
    reset_nina_target_store()


def stored():
    return get_nina_target_store().current(1e9)


# --- auth matrix ------------------------------------------------------------

@pytest.mark.parametrize("headers,status,code", [
    ({"Host": "127.0.0.1"}, 401, "unauthorized"),
    ({"Host": "127.0.0.1", "Authorization": "Bearer wrong"}, 401, "unauthorized"),
    ({"Host": "127.0.0.1", "Authorization": "Basic x"}, 401, "unauthorized"),
    ({"Host": "evil.example.com", "Authorization": f"Bearer {TOKEN}"}, 403, "host_not_allowed"),
])
def test_rejected_requests_never_store(headers, status, code):
    h = post(make_handler(headers=headers))
    assert h.status == status
    assert h.body["code"] == code
    assert stored() is None


def test_no_configured_token_fails_closed():
    h = make_handler()
    h.server.control_token = ""
    post(h)
    assert h.status == 503
    assert h.body["code"] == "control_disabled"
    assert stored() is None


def test_host_check_runs_before_token_check():
    h = make_handler(headers={"Host": "evil.example.com"})
    h.server.control_token = ""
    post(h)
    assert h.status == 403


def test_success_carries_no_acao_header():
    h = post(make_handler())
    assert h.status == 200
    assert "Access-Control-Allow-Origin" not in h.sent_headers


def test_rejection_carries_no_acao_header():
    h = post(make_handler(headers={"Host": "127.0.0.1"}))
    assert h.status == 401
    assert "Access-Control-Allow-Origin" not in h.sent_headers


def test_token_never_appears_in_a_response_or_log(monkeypatch):
    lines = []
    for level in ("debug", "info", "warning", "error"):
        monkeypatch.setattr(app_logger, level, lambda msg, *a, **k: lines.append(str(msg)))
    for h in (make_handler(), make_handler(body=b"{nope"),
              make_handler(headers={"Host": "127.0.0.1", "Authorization": "Bearer wrong"})):
        post(h)
        assert TOKEN not in h.wfile.getvalue().decode("utf-8")
    assert lines
    assert not [line for line in lines if TOKEN in line]


# --- body handling ------------------------------------------------------------

def test_oversized_content_length_is_413():
    h = make_handler()
    h.headers["Content-Length"] = str(api_control.MAX_BODY_BYTES + 1)
    post(h)
    assert h.status == 413
    assert h.body["code"] == "body_too_large"
    assert stored() is None


@pytest.mark.parametrize("raw", [b"{nope", b"[]", b"", b'{"target": {"name": "M31"}}'])
def test_bad_body_is_400_and_leaves_the_store_untouched(raw):
    get_nina_target_store().set(NinaTarget(name="Vega", ra_deg=279.2, dec_deg=38.8))
    h = post(make_handler(body=raw))
    assert h.status == 400
    assert h.body["code"] == "bad_request"
    assert stored().name == "Vega"


def test_invalid_content_length_is_400():
    h = make_handler()
    h.headers["Content-Length"] = "nope"
    post(h)
    assert h.status == 400


# --- storing ------------------------------------------------------------------

def test_valid_post_stores_and_echoes():
    h = post(make_handler())
    assert h.status == 200
    assert h.body["accepted"] is True
    assert h.body["changed"] is True
    assert h.body["target"] == {k: v for k, v in M31.items() if k != "epoch"}
    assert h.body["age_s"] == pytest.approx(0.0, abs=0.5)
    assert h.body["message"] == "Target stored."
    assert stored() == NinaTarget.from_dict(h.body["target"])


def test_repeat_is_a_heartbeat_not_a_change():
    assert post(make_handler()).body["changed"] is True
    again = post(make_handler())
    assert again.body["changed"] is False
    assert again.body["message"] == "Target unchanged."


def test_null_clears_the_target():
    post(make_handler())
    h = post(make_handler(body=b'{"target": null}'))
    assert h.status == 200
    assert h.body["changed"] is True
    assert h.body["target"] is None
    assert h.body["message"] == "Target cleared."
    assert stored() is None


def test_change_is_logged_at_info_and_heartbeat_at_debug(monkeypatch):
    calls = []
    monkeypatch.setattr(app_logger, "info", lambda msg, *a, **k: calls.append(("info", msg)))
    monkeypatch.setattr(app_logger, "debug", lambda msg, *a, **k: calls.append(("debug", msg)))
    post(make_handler())
    post(make_handler())
    post(make_handler(body=b'{"target": null}'))
    levels = [level for level, _ in calls]
    assert levels == ["info", "debug", "info"]
    assert "M31" in calls[0][1]
    assert "cleared" in calls[2][1]


# --- routing ------------------------------------------------------------------

@pytest.mark.parametrize("path", ["/nina/target", "/nina/target/", "/nina/target?x=1",
                                  "/nina/target/?x=1"])
def test_route_post_serves_the_target_path(path):
    h = make_handler(path=path)
    assert web_target.route_post(h) is True
    assert h.status == 200
    assert stored().name == "M31"


@pytest.mark.parametrize("path", ["/nina/targets", "/nina", "/nina/target/x",
                                  "/capture/target", "/"])
def test_route_post_ignores_other_paths(path):
    h = make_handler(path=path)
    assert web_target.route_post(h) is False
    assert h.status is None
    assert stored() is None


def test_web_control_dispatches_to_the_target_route():
    h = make_handler(path="/nina/target?x=1")
    assert web_control.route_post(h, "/capture") is True
    assert h.status == 200
    assert stored().name == "M31"


@pytest.mark.parametrize("path", ["/nina/targets", "/capture/restart", "/status"])
def test_web_control_still_rejects_unknown_paths(path):
    h = make_handler(path=path)
    assert web_control.route_post(h, "/capture") is False
    assert h.status is None


def test_huge_integer_coordinate_is_400_not_500():
    raw = b'{"target": {"name": "M31", "ra_deg": 1' + b"0" * 400 + b', "dec_deg": 41}}'
    h = post(make_handler(body=raw))
    assert h.status == 400
    assert h.body["code"] == "bad_request"
    assert stored() is None

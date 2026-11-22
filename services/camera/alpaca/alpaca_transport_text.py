"""Short, human wording for an Alpaca request that never got an answer.

``requests`` reports a refused port as several lines of urllib3 detail, with
the request URL and its ``ClientID`` query string inside. The operator needs
one sentence naming the server and the likely cause; the raw text goes to the
DEBUG log only. Pure: an exception in, a sentence out.
"""
import errno
import socket

import requests

_REFUSED = ("connection refused", "actively refused", "errno 111", "errno 61",
            "winerror 10061", "econnrefused")
_NOT_FOUND = ("name or service not known", "nodename nor servname", "getaddrinfo failed",
              "temporary failure in name resolution", "no address associated",
              "failed to resolve", "nameresolutionerror", "errno -2", "errno -3",
              "errno 11001", "errno 8")
_NO_ROUTE = ("no route to host", "network is unreachable", "host is unreachable",
             "errno 113", "errno 101", "errno 65", "errno 51", "winerror 10065",
             "winerror 10051")
_RESET = ("connection reset", "connection aborted", "remotedisconnected",
          "remote end closed", "broken pipe", "errno 104", "errno 54", "winerror 10054")

_REFUSED_ERRNOS = {errno.ECONNREFUSED, 10061}
_NO_ROUTE_ERRNOS = {errno.EHOSTUNREACH, errno.ENETUNREACH, 10065, 10051}


def _chain(exc):
    seen = []
    stack = [exc]
    while stack and len(seen) < 12:
        item = stack.pop()
        if item is None or any(item is s for s in seen):
            continue
        seen.append(item)
        if isinstance(item, BaseException):
            stack.extend([item.__cause__, item.__context__, getattr(item, "reason", None)])
            stack.extend(a for a in item.args if isinstance(a, BaseException))
    return seen


def _seconds(value) -> str:
    try:
        return f"{float(value):g} s"
    except (TypeError, ValueError):
        return "the time allowed"


def describe_transport_failure(exc, host: str, port, timeout=None) -> str:
    """One sentence for a request to ``host:port`` that raised ``exc``."""
    where = f"{host}:{port}"
    chain = _chain(exc)
    if any(isinstance(e, socket.gaierror) for e in chain):
        return f"Host name '{host}' not found — check the server address"
    for e in chain:
        code = getattr(e, "errno", None)
        if code in _REFUSED_ERRNOS:
            return f"Connection refused by {where} — is the Alpaca server running?"
        if code in _NO_ROUTE_ERRNOS:
            return f"No route to {host} — check the address and that it is on this network"
    text = " ".join(f"{type(e).__name__} {e}" for e in chain).lower()
    if any(p in text for p in _NOT_FOUND):
        return f"Host name '{host}' not found — check the server address"
    if any(p in text for p in _REFUSED):
        return f"Connection refused by {where} — is the Alpaca server running?"
    if any(p in text for p in _NO_ROUTE):
        return f"No route to {host} — check the address and that it is on this network"
    if isinstance(exc, (requests.Timeout, socket.timeout, TimeoutError)) or "timed out" in text:
        return f"No answer from {where} within {_seconds(timeout)}"
    if any(p in text for p in _RESET):
        return f"{where} closed the connection — is it an Alpaca server?"
    return f"Cannot reach the Alpaca server at {where}"


def not_alpaca_server_text(host: str, port, status: int = 404) -> str:
    return (f"Server at {host}:{port} is not an Alpaca server "
            f"(HTTP {status} on /management) — check the port")

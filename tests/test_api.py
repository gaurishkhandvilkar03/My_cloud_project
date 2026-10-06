import json

import azure.functions as func
import pytest

from shortener import handlers
from shortener.repository import InMemoryRepository
from shortener.service import LinkService

BASE = "https://example.azurewebsites.net/api"


@pytest.fixture
def service():
    return LinkService(InMemoryRepository())


@pytest.fixture(autouse=True)
def no_base_override(monkeypatch):
    monkeypatch.delenv("SHORT_URL_BASE", raising=False)


def request(method, path, body=None, route_params=None):
    data = json.dumps(body).encode() if isinstance(body, (dict, list)) else (body or b"")
    return func.HttpRequest(
        method=method,
        url=f"{BASE}/{path}",
        body=data,
        route_params=route_params or {},
        headers={"Content-Type": "application/json"},
    )


def body_of(resp):
    return json.loads(resp.get_body())


def shorten(service, payload):
    return handlers.shorten(request("POST", "shorten", payload), service)


def test_health():
    resp = handlers.health(request("GET", "health"))
    assert resp.status_code == 200
    assert body_of(resp) == {"status": "ok"}


def test_shorten_creates_link(service):
    resp = shorten(service, {"url": "https://learn.microsoft.com/azure"})
    assert resp.status_code == 201
    data = body_of(resp)
    assert len(data["code"]) == 7
    assert data["short_url"] == f"{BASE}/r/{data['code']}"
    assert data["clicks"] == 0


def test_shorten_with_custom_code(service):
    resp = shorten(service, {"url": "https://azure.com", "custom_code": "my-azure"})
    assert resp.status_code == 201
    assert body_of(resp)["code"] == "my-azure"


def test_duplicate_custom_code_conflicts(service):
    shorten(service, {"url": "https://azure.com", "custom_code": "dup"})
    resp = shorten(service, {"url": "https://example.com", "custom_code": "dup"})
    assert resp.status_code == 409


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"url": ""},
        {"url": 42},
        {"url": "not a url"},
        {"url": "ftp://files.example.com"},
        {"url": "javascript:alert(1)"},
        {"url": "https://example.com/" + "a" * 2100},
        {"url": "https://azure.com", "custom_code": "x"},
        {"url": "https://azure.com", "custom_code": "has space"},
        {"url": "https://azure.com", "custom_code": "stats"},
    ],
)
def test_invalid_input_rejected(service, payload):
    assert shorten(service, payload).status_code == 400


def test_invalid_json_rejected(service):
    resp = handlers.shorten(request("POST", "shorten", b"{not json"), service)
    assert resp.status_code == 400


def test_non_object_json_rejected(service):
    resp = handlers.shorten(request("POST", "shorten", ["https://azure.com"]), service)
    assert resp.status_code == 400


def test_redirect_and_click_count(service):
    code = body_of(shorten(service, {"url": "https://azure.com"}))["code"]

    for _ in range(3):
        resp = handlers.redirect(request("GET", f"r/{code}", route_params={"code": code}), service)
        assert resp.status_code == 302
        assert resp.headers["Location"] == "https://azure.com"

    stats = handlers.stats(request("GET", f"stats/{code}", route_params={"code": code}), service)
    assert stats.status_code == 200
    assert body_of(stats)["clicks"] == 3


def test_unknown_code_returns_404(service):
    params = {"code": "nope123"}
    assert handlers.redirect(request("GET", "r/nope123", route_params=params), service).status_code == 404
    assert handlers.stats(request("GET", "stats/nope123", route_params=params), service).status_code == 404
    assert handlers.delete(request("DELETE", "links/nope123", route_params=params), service).status_code == 404


def test_delete(service):
    code = body_of(shorten(service, {"url": "https://azure.com"}))["code"]
    params = {"code": code}
    assert handlers.delete(request("DELETE", f"links/{code}", route_params=params), service).status_code == 204
    assert handlers.redirect(request("GET", f"r/{code}", route_params=params), service).status_code == 404


def test_short_url_base_override(service, monkeypatch):
    monkeypatch.setenv("SHORT_URL_BASE", "https://sho.rt/")
    data = body_of(shorten(service, {"url": "https://azure.com", "custom_code": "abc"}))
    assert data["short_url"] == "https://sho.rt/abc"

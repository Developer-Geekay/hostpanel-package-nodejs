from __future__ import annotations

import pytest
from fastapi import HTTPException

from hostpanel_nodejs import store, validators


def _make_app(app_id: str = "portfolio-example-com", domain: str = "example.com") -> None:
    store.create_app(
        {
            "id": app_id, "name": "Portfolio", "username": "geekay",
            "domain": domain, "app_root": "/home/geekay/public_html",
            "entrypoint": "server.js", "start_command": "node server.js",
            "install_command": "", "node_version": "22", "port": 31000,
        },
        env={},
    )


def test_validate_routes_accepts_good_shapes():
    routes = validators.validate_routes([
        {"path": "/assistant-api", "port": 16000},
        {"path": "/api/v2.1_beta", "port": "17000", "strip_prefix": False},
        {"path": "/metrics/", "port": 18000},  # trailing slash normalized away
    ])
    assert routes[0] == {"path": "/assistant-api", "port": 16000, "strip_prefix": True}
    assert routes[1]["strip_prefix"] is False and routes[1]["port"] == 17000
    assert routes[2]["path"] == "/metrics"


@pytest.mark.parametrize("bad", [
    {"path": "", "port": 16000},
    {"path": "no-slash", "port": 16000},
    {"path": "/", "port": 16000},
    {"path": "/a b", "port": 16000},
    {"path": "/x;\ninjected", "port": 16000},
    {"path": "/x/{ }", "port": 16000},
    {"path": "/../etc", "port": 16000},
    {"path": "/.well-known/acme", "port": 16000},
    {"path": "/ok", "port": 0},
    {"path": "/ok", "port": 70000},
    {"path": "/ok", "port": "not-a-port"},
])
def test_validate_routes_rejects_bad_shapes(bad):
    with pytest.raises(HTTPException):
        validators.validate_routes([bad])


def test_validate_routes_rejects_duplicates_and_caps():
    with pytest.raises(HTTPException) as exc:
        validators.validate_routes([{"path": "/a", "port": 1}, {"path": "/a/", "port": 2}])
    assert exc.value.status_code == 409
    with pytest.raises(HTTPException):
        validators.validate_routes([{"path": f"/r{i}", "port": 1000 + i} for i in range(validators.MAX_ROUTES + 1)])


def test_store_roundtrip_and_domain_lookup(fresh_db):
    _make_app()
    store.set_routes("portfolio-example-com", [
        {"path": "/assistant-api", "port": 16000, "strip_prefix": True},
    ])
    assert store.get_app("portfolio-example-com")["routes"] == [
        {"path": "/assistant-api", "port": 16000, "strip_prefix": True}
    ]
    # the accessor the core vhost renderer calls
    assert store.get_routes_by_domain("example.com")[0]["port"] == 16000
    assert store.get_routes_by_domain("other.example") == []
    # replaced wholesale on save
    store.set_routes("portfolio-example-com", [])
    assert store.get_routes_by_domain("example.com") == []


def test_delete_app_removes_routes(fresh_db):
    _make_app()
    store.set_routes("portfolio-example-com", [{"path": "/x", "port": 16000}])
    store.delete_app("portfolio-example-com")
    assert store.get_routes_by_domain("example.com") == []

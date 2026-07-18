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
        {"path": "/api/v2.1_beta", "port": "17000", "strip_prefix": False, "host": "192.168.1.113"},
        {"path": "/metrics/", "port": 18000, "host": "assistant.local"},  # trailing slash normalized away
    ])
    assert routes[0] == {"path": "/assistant-api", "host": "127.0.0.1", "port": 16000, "strip_prefix": True}
    assert routes[1]["strip_prefix"] is False and routes[1]["port"] == 17000 and routes[1]["host"] == "192.168.1.113"
    assert routes[2]["path"] == "/metrics" and routes[2]["host"] == "assistant.local"


@pytest.mark.parametrize("bad_host", ["-leading.dash", "trailing.dash-", "with space", "semi;colon", "new\nline", "a" * 260])
def test_validate_routes_rejects_bad_hosts(bad_host):
    with pytest.raises(HTTPException):
        validators.validate_routes([{"path": "/ok", "port": 16000, "host": bad_host}])


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
        {"path": "/assistant-api", "host": "127.0.0.1", "port": 16000, "strip_prefix": True}
    ]
    # the accessor the core vhost renderer calls
    looked_up = store.get_routes_by_domain("example.com")[0]
    assert looked_up["port"] == 16000 and looked_up["host"] == "127.0.0.1"
    # remote-host roundtrip
    store.set_routes("portfolio-example-com", [{"path": "/remote", "host": "192.168.1.113", "port": 16000}])
    assert store.get_routes_by_domain("example.com")[0]["host"] == "192.168.1.113"
    assert store.get_routes_by_domain("other.example") == []
    # replaced wholesale on save
    store.set_routes("portfolio-example-com", [])
    assert store.get_routes_by_domain("example.com") == []


def test_migrate_adds_host_to_pre_1_8_routes_table(fresh_db):
    # 1.7.0 created nodejs_app_routes without host; the migration must add it
    # and existing rows must read back as loopback.
    with fresh_db.get_conn() as conn:
        conn.execute(
            "CREATE TABLE nodejs_app_routes (app_id TEXT NOT NULL, path TEXT NOT NULL,"
            " port INTEGER NOT NULL, strip_prefix INTEGER NOT NULL DEFAULT 1, PRIMARY KEY (app_id, path))"
        )
        conn.execute("INSERT INTO nodejs_app_routes (app_id, path, port) VALUES ('portfolio-example-com', '/assistant-api', 16000)")
    _make_app()
    routes = store.get_routes("portfolio-example-com")
    assert routes == [{"path": "/assistant-api", "host": "127.0.0.1", "port": 16000, "strip_prefix": True}]


def test_delete_app_removes_routes(fresh_db):
    _make_app()
    store.set_routes("portfolio-example-com", [{"path": "/x", "port": 16000}])
    store.delete_app("portfolio-example-com")
    assert store.get_routes_by_domain("example.com") == []

from __future__ import annotations

import pytest
from fastapi import HTTPException

from hostpanel_nodejs import process, releases, store


def _make_app(app_id: str = "portfolio-example-com", port: int = 31000) -> dict:
    store.create_app(
        {
            "id": app_id,
            "name": "Portfolio",
            "username": "geekay",
            "domain": "example.com",
            "app_root": "/home/geekay/public_html",
            "entrypoint": "server.js",
            "start_command": "node server.js",
            "install_command": "",
            "node_version": "22",
            "port": port,
        },
        env={},
    )
    return store.update_app(app_id, {"deploy_enabled": True})


class _Result:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@pytest.fixture
def activation_env(fresh_db, monkeypatch):
    """Simulate the hp-nodejs-deploy helper so activate()'s ordering and store
    effects can be asserted without a real system. Mirrors the helper contract:
    exit 10 = release missing, 11 = manifest missing; activate performs the
    previous/current relinks itself and prints the old current target."""
    state = {
        "releases": {},  # sha -> has_manifest
        "current": None,
        "previous": None,
        "calls": [],
    }

    def fake_helper(args, timeout=30):
        cmd, _root, *rest = args
        state["calls"].append((cmd, *rest))
        if cmd == "activate":
            sha = rest[0]
            if sha not in state["releases"]:
                return _Result(releases.HELPER_MISSING)
            if not state["releases"][sha]:
                return _Result(releases.HELPER_NO_MANIFEST)
            old = state["current"] or ""
            if old and old != f"releases/{sha}":
                state["previous"] = old
            state["current"] = f"releases/{sha}"
            return _Result(0, stdout=old + "\n")
        if cmd == "has-current":
            return _Result(0 if state["current"] else releases.HELPER_MISSING)
        if cmd == "list-releases":
            return _Result(0, stdout="\n".join(sorted(state["releases"])))
        raise AssertionError(f"unexpected helper command: {cmd}")

    monkeypatch.setattr(releases, "_helper", fake_helper)
    monkeypatch.setattr(releases, "ensure_layout", lambda app: state["calls"].append(("ensure_layout",)))
    monkeypatch.setattr(process, "write_service", lambda app: state["calls"].append(("write_service",)))
    monkeypatch.setattr(process, "restart", lambda app_id: state["calls"].append(("restart", app_id)))
    return state


def test_validate_sha():
    assert releases.validate_sha("9f2a1c4") == "9f2a1c4"
    assert releases.validate_sha("9F2A1C4E" + "a" * 8) == ("9f2a1c4e" + "a" * 8)
    for bad in ("", "short", "not-hex-12345", "../../etc/passwd", "9f2a1c4/evil"):
        with pytest.raises(HTTPException):
            releases.validate_sha(bad)


def test_first_activation(activation_env):
    app = _make_app()
    activation_env["releases"]["9f2a1c4"] = True

    updated = releases.activate(app, "9f2a1c4")

    assert updated["current_sha"] == "9f2a1c4"
    assert updated["previous_sha"] is None
    assert activation_env["current"] == "releases/9f2a1c4"
    # Relink happens before the unit rewrite so WorkingDirectory resolves to
    # current/ on the first activation; restart comes last.
    ordered = [c[0] for c in activation_env["calls"]]
    assert ordered.index("activate") < ordered.index("write_service") < ordered.index("restart")


def test_second_activation_preserves_previous(activation_env):
    app = _make_app()
    activation_env["releases"]["9f2a1c4"] = True
    activation_env["releases"]["3b8d040"] = True
    releases.activate(app, "9f2a1c4")

    updated = releases.activate(store.get_app(app["id"]), "3b8d040")

    assert updated["current_sha"] == "3b8d040"
    assert updated["previous_sha"] == "9f2a1c4"
    assert activation_env["previous"] == "releases/9f2a1c4"


def test_reactivating_same_sha_keeps_previous(activation_env):
    app = _make_app()
    activation_env["releases"]["9f2a1c4"] = True
    activation_env["releases"]["3b8d040"] = True
    releases.activate(app, "9f2a1c4")
    releases.activate(store.get_app(app["id"]), "3b8d040")

    updated = releases.activate(store.get_app(app["id"]), "3b8d040")

    assert updated["current_sha"] == "3b8d040"
    assert updated["previous_sha"] == "9f2a1c4"


def test_activate_missing_release_is_404(activation_env):
    app = _make_app()
    with pytest.raises(HTTPException) as exc:
        releases.activate(app, "0000000")
    assert exc.value.status_code == 404
    assert store.get_app(app["id"])["current_sha"] is None


def test_activate_without_manifest_is_409(activation_env):
    app = _make_app()
    activation_env["releases"]["9f2a1c4"] = False
    with pytest.raises(HTTPException) as exc:
        releases.activate(app, "9f2a1c4")
    assert exc.value.status_code == 409


def test_activate_helper_failure_is_500(activation_env, monkeypatch):
    app = _make_app()
    monkeypatch.setattr(releases, "ensure_layout", lambda app: None)
    monkeypatch.setattr(releases, "_helper", lambda args, timeout=30: _Result(12, stderr="hp-nodejs-deploy: invalid app_root"))
    with pytest.raises(HTTPException) as exc:
        releases.activate(app, "9f2a1c4")
    assert exc.value.status_code == 500
    assert "invalid app_root" in exc.value.detail


def test_rollback_defaults_to_previous(activation_env):
    app = _make_app()
    activation_env["releases"]["9f2a1c4"] = True
    activation_env["releases"]["3b8d040"] = True
    releases.activate(app, "9f2a1c4")
    releases.activate(store.get_app(app["id"]), "3b8d040")

    rolled = releases.rollback(store.get_app(app["id"]))

    assert rolled["current_sha"] == "9f2a1c4"


def test_rollback_without_previous_is_409(activation_env):
    app = _make_app()
    with pytest.raises(HTTPException) as exc:
        releases.rollback(app)
    assert exc.value.status_code == 409


def test_list_releases_filters_non_sha_entries(activation_env):
    app = _make_app()
    activation_env["releases"]["9f2a1c4"] = True
    activation_env["releases"]["not-a-sha"] = True
    assert releases.list_releases(app) == ["9f2a1c4"]


def test_working_directory_selection(activation_env):
    app = {"app_root": "/home/geekay/public_html", "deploy_enabled": False}
    assert process.working_directory(app) == "/home/geekay/public_html"

    app["deploy_enabled"] = True
    assert process.working_directory(app) == "/home/geekay/public_html"  # no current yet

    activation_env["current"] = "releases/9f2a1c4"
    assert process.working_directory(app) == "/home/geekay/public_html/current"

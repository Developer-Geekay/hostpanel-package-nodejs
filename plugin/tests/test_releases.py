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


@pytest.fixture
def activation_env(fresh_db, monkeypatch):
    """Fake the sudo-backed filesystem and the unit lifecycle so activate()'s
    ordering and store effects can be asserted without a real system."""
    state = {
        "releases": set(),
        "manifests": set(),
        "current_target": None,
        "calls": [],
    }

    monkeypatch.setattr(releases, "_exists", lambda path, flag="-e": (
        path in state["releases"] if flag == "-d" else path in state["manifests"]
    ))
    monkeypatch.setattr(releases, "_link_target", lambda path: (
        state["current_target"] if path.endswith("/current") else None
    ))

    def fake_relink(link, target):
        state["calls"].append(("relink", link.rsplit("/", 1)[-1], target))
        if link.endswith("/current"):
            state["current_target"] = target

    monkeypatch.setattr(releases, "_relink", fake_relink)
    monkeypatch.setattr(releases, "ensure_layout", lambda app: state["calls"].append(("ensure_layout",)))
    monkeypatch.setattr(process, "write_service", lambda app: state["calls"].append(("write_service",)))
    monkeypatch.setattr(process, "restart", lambda app_id: state["calls"].append(("restart", app_id)))
    return state


def _add_release(state, app, sha, with_manifest=True):
    state["releases"].add(releases.release_dir(app, sha))
    if with_manifest:
        state["manifests"].add(releases.release_dir(app, sha) + "/manifest.json")


def test_validate_sha():
    assert releases.validate_sha("9f2a1c4") == "9f2a1c4"
    assert releases.validate_sha("9F2A1C4E" + "a" * 8) == ("9f2a1c4e" + "a" * 8)
    for bad in ("", "short", "not-hex-12345", "../../etc/passwd", "9f2a1c4/evil"):
        with pytest.raises(HTTPException):
            releases.validate_sha(bad)


def test_relink_is_atomic(fresh_db, monkeypatch):
    commands = []
    monkeypatch.setattr(process, "_sudo", lambda cmd, **kw: commands.append(cmd) or type("R", (), {"returncode": 0, "stdout": ""})())
    releases._relink("/home/geekay/public_html/current", "releases/9f2a1c4")
    assert commands == [
        ["ln", "-sfn", "releases/9f2a1c4", "/home/geekay/public_html/current.tmp"],
        ["mv", "-T", "/home/geekay/public_html/current.tmp", "/home/geekay/public_html/current"],
    ]


def test_first_activation(activation_env):
    app = _make_app()
    _add_release(activation_env, app, "9f2a1c4")

    updated = releases.activate(app, "9f2a1c4")

    assert updated["current_sha"] == "9f2a1c4"
    assert updated["previous_sha"] is None
    # current relinked, no previous link (nothing to preserve), unit rewritten
    # only after the link exists, restart last.
    relinks = [c for c in activation_env["calls"] if c[0] == "relink"]
    assert relinks == [("relink", "current", "releases/9f2a1c4")]
    ordered = [c[0] for c in activation_env["calls"]]
    assert ordered.index("write_service") < ordered.index("restart")
    assert ordered.index("relink") < ordered.index("write_service")


def test_second_activation_preserves_previous(activation_env):
    app = _make_app()
    _add_release(activation_env, app, "9f2a1c4")
    _add_release(activation_env, app, "3b8d040")
    releases.activate(app, "9f2a1c4")

    updated = releases.activate(store.get_app(app["id"]), "3b8d040")

    assert updated["current_sha"] == "3b8d040"
    assert updated["previous_sha"] == "9f2a1c4"
    assert ("relink", "previous", "releases/9f2a1c4") in activation_env["calls"]


def test_activate_missing_release_is_404(activation_env):
    app = _make_app()
    with pytest.raises(HTTPException) as exc:
        releases.activate(app, "0000000")
    assert exc.value.status_code == 404
    assert store.get_app(app["id"])["current_sha"] is None


def test_activate_without_manifest_is_409(activation_env):
    app = _make_app()
    _add_release(activation_env, app, "9f2a1c4", with_manifest=False)
    with pytest.raises(HTTPException) as exc:
        releases.activate(app, "9f2a1c4")
    assert exc.value.status_code == 409


def test_rollback_defaults_to_previous(activation_env):
    app = _make_app()
    _add_release(activation_env, app, "9f2a1c4")
    _add_release(activation_env, app, "3b8d040")
    releases.activate(app, "9f2a1c4")
    releases.activate(store.get_app(app["id"]), "3b8d040")

    rolled = releases.rollback(store.get_app(app["id"]))

    assert rolled["current_sha"] == "9f2a1c4"


def test_rollback_without_previous_is_409(activation_env):
    app = _make_app()
    with pytest.raises(HTTPException) as exc:
        releases.rollback(app)
    assert exc.value.status_code == 409


def test_working_directory_selection(fresh_db, monkeypatch):
    app = {"app_root": "/home/geekay/public_html", "deploy_enabled": False}
    assert process.working_directory(app) == "/home/geekay/public_html"

    app["deploy_enabled"] = True
    monkeypatch.setattr(process, "_sudo", lambda cmd, **kw: type("R", (), {"returncode": 0})())
    assert process.working_directory(app) == "/home/geekay/public_html/current"

    monkeypatch.setattr(process, "_sudo", lambda cmd, **kw: type("R", (), {"returncode": 1})())
    assert process.working_directory(app) == "/home/geekay/public_html"

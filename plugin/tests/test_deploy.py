from __future__ import annotations

import hashlib
import io
import json
import os
import tarfile

import pytest
from fastapi import HTTPException

from hostpanel_nodejs import deploy, releases, store

APP_ID = "portfolio-example-com"
FULL_COMMIT = "9f2a1c4e" + "0" * 32
SHORT = FULL_COMMIT[:7]


def _make_app() -> dict:
    store.create_app(
        {
            "id": APP_ID,
            "name": "Portfolio",
            "username": "geekay",
            "domain": "example.com",
            "app_root": "/home/geekay/public_html",
            "entrypoint": "server.js",
            "start_command": "node server.js",
            "install_command": "",
            "node_version": "22",
            "port": 31000,
        },
        env={},
    )
    return store.update_app(APP_ID, {"deploy_enabled": True})


def _manifest(**overrides) -> dict:
    manifest = {
        "schema": 1,
        "app_id": APP_ID,
        "runtime": "node22",
        "entrypoint": "server.js",
        "health": "/healthz",
        "commit": FULL_COMMIT,
        "built_at": "2026-07-16T12:00:00Z",
    }
    manifest.update(overrides)
    return manifest


def _tarball(manifest: dict | None = None, extra=None) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        files = {"server.js": b"console.log('hi')"}
        if manifest is not None:
            files["manifest.json"] = json.dumps(manifest).encode()
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        if extra:
            extra(tar)
    return buf.getvalue()


class _DeployEnv:
    """Stubs for the root-helper, activation, and health boundaries, which
    unit tests can't run for real. Health defaults to passing; failure-path
    tests push results (None = healthy, str = failure reason) onto
    `health_results`, consumed one per wait_healthy call."""

    def __init__(self):
        self.calls: list = []
        self.health_results: list = []
        self.activate_error: Exception | None = None
        self.pruned: list[str] = []


@pytest.fixture
def deploy_env(fresh_db, monkeypatch, tmp_path):
    env = _DeployEnv()
    monkeypatch.setattr(deploy, "STAGING_DIR", str(tmp_path / "staging"))
    monkeypatch.setattr(deploy, "ARTIFACTS_DIR", str(tmp_path / "artifacts"))

    def fake_activate(app, sha):
        env.calls.append(("activate", sha))
        if env.activate_error is not None:
            raise env.activate_error
        current = store.get_app(app["id"])
        previous = current.get("current_sha") if current.get("current_sha") != sha else current.get("previous_sha")
        return store.update_app(app["id"], {"current_sha": sha, "previous_sha": previous})

    monkeypatch.setattr(releases, "install_release", lambda app, sha, src: env.calls.append(("install", sha, src)))
    monkeypatch.setattr(releases, "activate", fake_activate)
    monkeypatch.setattr(releases, "prune", lambda app: env.calls.append(("prune",)) or env.pruned)
    monkeypatch.setattr(
        deploy.health, "wait_healthy",
        lambda port, path, timeout_s, interval_s: env.health_results.pop(0) if env.health_results else None,
    )
    return env


def _deploy(data: bytes, sha256: str | None = None, commit: str = SHORT):
    app = store.get_app(APP_ID)
    return deploy.run_deploy(app, io.BytesIO(data), sha256 or hashlib.sha256(data).hexdigest(), commit)


# Auth is covered by test_oidc.py since Phase 4; API responses must never
# carry credential material.

def test_app_rows_never_expose_token_hash(fresh_db):
    _make_app()
    assert "deploy_token_hash" not in store.get_app(APP_ID)


def test_deploy_response_never_contains_env(deploy_env):
    # The response is printed into CI logs, which are PUBLIC for public
    # repos — env vars (MONGODB_URI, AUTH_SECRET, ...) must never appear.
    # This shipped once as a live secret leak; keep this pinned.
    store.create_app(
        {
            "id": APP_ID, "name": "Portfolio", "username": "geekay",
            "domain": "example.com", "app_root": "/home/geekay/public_html",
            "entrypoint": "server.js", "start_command": "node server.js",
            "install_command": "", "node_version": "22", "port": 31000,
        },
        env={"AUTH_SECRET": "super-secret-value", "MONGODB_URI": "mongodb://u:p@127.0.0.1/db"},
    )
    store.update_app(APP_ID, {"deploy_enabled": True})

    result = _deploy(_tarball(_manifest()))

    assert "env" not in result["app"]
    assert "super-secret-value" not in json.dumps(result)
    assert set(result["app"].keys()) <= {"id", "name", "domain", "status", "current_sha", "previous_sha", "repo", "ref"}


# ── pipeline ─────────────────────────────────────────────────────────────────

def test_successful_deploy(deploy_env):
    _make_app()
    result = _deploy(_tarball(_manifest()))

    dep = result["deployment"]
    assert dep["status"] == "healthy"
    assert dep["finished_at"] is not None
    assert dep["commit_sha"] == SHORT
    assert [c[0] for c in deploy_env.calls] == ["install", "activate", "prune"]
    # health path recorded from the manifest for later rollback verification
    assert store.get_app(APP_ID)["health_path"] == "/healthz"
    # artifact retained, staging cleaned
    assert os.path.exists(os.path.join(deploy.ARTIFACTS_DIR, APP_ID, f"{SHORT}.tar.gz"))
    assert os.listdir(deploy.STAGING_DIR) == []


def _expect_failure(data: bytes, status_code: int, sha256: str | None = None, commit: str = SHORT) -> str:
    with pytest.raises(HTTPException) as exc:
        _deploy(data, sha256=sha256, commit=commit)
    assert exc.value.status_code == status_code
    deps = store.list_deployments(APP_ID)
    assert deps and deps[0]["status"] == "failed"
    assert exc.value.detail in deps[0]["detail"]
    return exc.value.detail


def test_checksum_mismatch_fails(deploy_env):
    _make_app()
    _expect_failure(_tarball(_manifest()), 400, sha256="0" * 64)
    # failed artifacts are not retained
    assert not os.path.exists(os.path.join(deploy.ARTIFACTS_DIR, APP_ID, f"{SHORT}.tar.gz"))


def test_missing_manifest_fails(deploy_env):
    _make_app()
    _expect_failure(_tarball(manifest=None), 400)


def test_manifest_app_id_mismatch_is_409(deploy_env):
    _make_app()
    _expect_failure(_tarball(_manifest(app_id="someone-elses-app")), 409)


def test_unknown_schema_fails(deploy_env):
    _make_app()
    _expect_failure(_tarball(_manifest(schema=2)), 400)


def test_commit_mismatch_fails(deploy_env):
    _make_app()
    _expect_failure(_tarball(_manifest(commit="b" * 40)), 400)


def test_absolute_path_member_fails(deploy_env):
    _make_app()

    def add_abs(tar):
        info = tarfile.TarInfo("/etc/cron.d/evil")
        info.size = 4
        tar.addfile(info, io.BytesIO(b"boom"))

    _expect_failure(_tarball(_manifest(), extra=add_abs), 400)


def test_traversal_member_fails(deploy_env):
    _make_app()

    def add_traversal(tar):
        info = tarfile.TarInfo("../../outside")
        info.size = 4
        tar.addfile(info, io.BytesIO(b"boom"))

    _expect_failure(_tarball(_manifest(), extra=add_traversal), 400)


def test_symlink_member_fails(deploy_env):
    _make_app()

    def add_symlink(tar):
        info = tarfile.TarInfo("link")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        tar.addfile(info)

    _expect_failure(_tarball(_manifest(), extra=add_symlink), 400)


def test_uncompressed_cap(deploy_env, monkeypatch):
    _make_app()
    monkeypatch.setattr(deploy, "MAX_TOTAL_UNCOMPRESSED_BYTES", 10)
    _expect_failure(_tarball(_manifest()), 400)


def test_corrupt_gzip_fails(deploy_env):
    _make_app()
    data = b"definitely not a tarball"
    _expect_failure(data, 400)


def test_empty_upload_fails(deploy_env):
    _make_app()
    _expect_failure(b"", 400, sha256=hashlib.sha256(b"").hexdigest())


def test_concurrent_deploy_is_409(deploy_env):
    _make_app()
    lock = deploy._acquire_deploy_lock(APP_ID)
    try:
        with pytest.raises(HTTPException) as exc:
            _deploy(_tarball(_manifest()))
        assert exc.value.status_code == 409
    finally:
        lock.release()
    # lock released after a normal run too
    _deploy(_tarball(_manifest()))
    deploy._acquire_deploy_lock(APP_ID).release()


# ── health / auto-rollback (Phase 5) ─────────────────────────────────────────

def test_unhealthy_deploy_rolls_back_to_previous(deploy_env):
    _make_app()
    store.update_app(APP_ID, {"current_sha": "1111111"})  # a prior release is live
    deploy_env.health_results.extend(["connection refused", None])  # new fails, rollback passes

    with pytest.raises(HTTPException) as exc:
        _deploy(_tarball(_manifest()))

    assert exc.value.status_code == 502
    dep = store.list_deployments(APP_ID)[0]
    assert dep["status"] == "rolled_back"
    assert "1111111" in dep["detail"]
    # rolled back: activate called twice — new sha, then previous
    activates = [c[1] for c in deploy_env.calls if c[0] == "activate"]
    assert activates == [SHORT, "1111111"]
    assert ("prune",) not in deploy_env.calls  # never prune on a failed deploy


def test_unhealthy_rollback_health_still_failing_is_recorded(deploy_env):
    _make_app()
    store.update_app(APP_ID, {"current_sha": "1111111"})
    deploy_env.health_results.extend(["HTTP 500 from /healthz", "HTTP 500 from /healthz"])

    with pytest.raises(HTTPException):
        _deploy(_tarball(_manifest()))

    dep = store.list_deployments(APP_ID)[0]
    assert dep["status"] == "rolled_back"
    assert "rollback health also failing" in dep["detail"]


def test_unhealthy_first_deploy_fails_without_rollback(deploy_env):
    _make_app()  # no previous release
    deploy_env.health_results.append("connection refused")

    with pytest.raises(HTTPException) as exc:
        _deploy(_tarball(_manifest()))

    assert exc.value.status_code == 502
    dep = store.list_deployments(APP_ID)[0]
    assert dep["status"] == "failed"
    assert "no previous release" in dep["detail"]
    assert [c[1] for c in deploy_env.calls if c[0] == "activate"] == [SHORT]


def test_rollback_failure_marks_failed_and_stops(deploy_env):
    _make_app()
    store.update_app(APP_ID, {"current_sha": "1111111"})
    deploy_env.health_results.append("connection refused")

    original_activate = releases.activate

    def activate_then_break(app, sha):
        result = original_activate(app, sha)
        deploy_env.activate_error = RuntimeError("systemctl restart failed")  # arm for the rollback call
        return result

    releases.activate = activate_then_break
    try:
        with pytest.raises(HTTPException) as exc:
            _deploy(_tarball(_manifest()))
    finally:
        releases.activate = original_activate

    assert exc.value.status_code == 502
    dep = store.list_deployments(APP_ID)[0]
    assert dep["status"] == "failed"
    assert "rollback to 1111111 also failed" in dep["detail"]


def test_prune_removes_matching_artifacts(deploy_env):
    _make_app()
    deploy_env.pruned.extend(["aaaaaaa", "bbbbbbb"])
    old_a = os.path.join(deploy.ARTIFACTS_DIR, APP_ID, "aaaaaaa.tar.gz")
    os.makedirs(os.path.dirname(old_a), exist_ok=True)
    for sha in ("aaaaaaa", "bbbbbbb"):
        with open(os.path.join(deploy.ARTIFACTS_DIR, APP_ID, f"{sha}.tar.gz"), "wb") as f:
            f.write(b"old artifact")

    _deploy(_tarball(_manifest()))

    assert not os.path.exists(old_a)
    remaining = sorted(os.listdir(os.path.join(deploy.ARTIFACTS_DIR, APP_ID)))
    assert remaining == [f"{SHORT}.tar.gz"]

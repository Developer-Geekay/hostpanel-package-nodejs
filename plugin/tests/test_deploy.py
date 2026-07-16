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


@pytest.fixture
def deploy_env(fresh_db, monkeypatch, tmp_path):
    """Point staging/artifacts at tmp and stub the root-helper + activation
    boundary, which unit tests can't run for real."""
    monkeypatch.setattr(deploy, "STAGING_DIR", str(tmp_path / "staging"))
    monkeypatch.setattr(deploy, "ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    calls = []
    monkeypatch.setattr(releases, "install_release", lambda app, sha, src: calls.append(("install", sha, src)))
    monkeypatch.setattr(releases, "activate", lambda app, sha: calls.append(("activate", sha)) or store.get_app(app["id"]))
    return calls


def _deploy(data: bytes, sha256: str | None = None, commit: str = SHORT):
    app = store.get_app(APP_ID)
    return deploy.run_deploy(app, io.BytesIO(data), sha256 or hashlib.sha256(data).hexdigest(), commit)


# Auth is covered by test_oidc.py since Phase 4; API responses must never
# carry credential material.

def test_app_rows_never_expose_token_hash(fresh_db):
    _make_app()
    assert "deploy_token_hash" not in store.get_app(APP_ID)


# ── pipeline ─────────────────────────────────────────────────────────────────

def test_successful_deploy(deploy_env):
    _make_app()
    result = _deploy(_tarball(_manifest()))

    dep = result["deployment"]
    assert dep["status"] == "activated"
    assert dep["finished_at"] is not None
    assert dep["commit_sha"] == SHORT
    assert deploy_env == [("install", SHORT, deploy_env[0][2]), ("activate", SHORT)]
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

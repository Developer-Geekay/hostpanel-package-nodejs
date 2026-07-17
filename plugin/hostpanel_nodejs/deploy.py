from __future__ import annotations

import hashlib
import json
import os
import posixpath
import re
import shutil
import tarfile
import threading
from types import SimpleNamespace
from typing import Any, BinaryIO, Optional

from fastapi import HTTPException

from hostpanel_nodejs import audit, health, ids, releases, store
from hostpanel_nodejs.process import PLUGIN_DIR

# Tarball ingest pipeline (Phase 2 of DEPLOY_PLAN.md):
#   receive multipart -> stream to artifact file (never buffered in RAM)
#   -> verify sha256 -> safety-scan + read manifest -> extract to staging
#   -> hp-nodejs-deploy install-release -> releases.activate
# Every state transition and every rejection writes a deployment row update
# and an audit row. One deploy per app at a time, locked on the app id.

STAGING_DIR = f"{PLUGIN_DIR}/staging"
ARTIFACTS_DIR = f"{PLUGIN_DIR}/artifacts"

MANIFEST_SCHEMA = 1
ALLOWED_RUNTIMES = {"node22", "node24"}
COMMIT_RE = re.compile(r"^[0-9a-f]{7,40}$")

# Upload/extraction caps — zip-bomb and disk-exhaustion defense.
MAX_TARBALL_BYTES = 200 * 1024 * 1024
MAX_MEMBER_BYTES = 100 * 1024 * 1024
MAX_TOTAL_UNCOMPRESSED_BYTES = 500 * 1024 * 1024
MAX_MEMBERS = 50_000
CHUNK_BYTES = 1024 * 1024

# The actor recorded on audit rows for CI-initiated deploys (there is no panel
# session on this path).
CI_ACTOR = SimpleNamespace(username="deploy-ci")

_locks_guard = threading.Lock()
_deploy_locks: dict[str, threading.Lock] = {}


# Auth lives in oidc.py since Phase 4 — the static deploy-token mechanism is
# gone (the legacy deploy_token_hash DB column stays, unused and never
# serialized; dropping SQLite columns isn't worth the risk).

# ── pipeline pieces ──────────────────────────────────────────────────────────

def _ensure_dir(path: str) -> None:
    try:
        os.makedirs(path, exist_ok=True)
    except PermissionError:
        raise HTTPException(
            status_code=500,
            detail=f"{path} is not writable by the panel — package install/startup hook has not prepared it",
        )


def validate_commit(value: str) -> str:
    commit = (value or "").strip().lower()
    if not COMMIT_RE.fullmatch(commit):
        raise HTTPException(status_code=400, detail="Invalid commit SHA")
    return commit


def save_and_hash(src: BinaryIO, dst_path: str) -> str:
    """Stream the upload to disk in chunks, hashing as it goes — the tarball
    must never be held in RAM (Pi constraint)."""
    digest = hashlib.sha256()
    size = 0
    with open(dst_path, "wb") as out:
        while True:
            chunk = src.read(CHUNK_BYTES)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_TARBALL_BYTES:
                raise HTTPException(status_code=413, detail=f"Tarball exceeds {MAX_TARBALL_BYTES} bytes")
            digest.update(chunk)
            out.write(chunk)
    if size == 0:
        raise HTTPException(status_code=400, detail="Empty tarball")
    return digest.hexdigest()


def _member_is_traversal(name: str) -> bool:
    if name.startswith("/"):
        return True
    normalized = posixpath.normpath(name)
    return normalized.startswith("..") or normalized.startswith("/")


def scan_members(tar: tarfile.TarFile) -> list[tarfile.TarInfo]:
    """Reject anything a hostile tarball could use: absolute paths, ..
    traversal, links, device/fifo nodes, oversized members, zip bombs."""
    members: list[tarfile.TarInfo] = []
    total = 0
    for member in tar:
        if len(members) >= MAX_MEMBERS:
            raise HTTPException(status_code=400, detail=f"Tarball has more than {MAX_MEMBERS} members")
        if _member_is_traversal(member.name):
            raise HTTPException(status_code=400, detail=f"Unsafe path in tarball: {member.name}")
        if member.issym() or member.islnk():
            raise HTTPException(status_code=400, detail=f"Links are not allowed in tarballs: {member.name}")
        if member.isdev() or member.isfifo():
            raise HTTPException(status_code=400, detail=f"Special files are not allowed in tarballs: {member.name}")
        if member.size > MAX_MEMBER_BYTES:
            raise HTTPException(status_code=400, detail=f"Tarball member too large: {member.name}")
        total += member.size
        if total > MAX_TOTAL_UNCOMPRESSED_BYTES:
            raise HTTPException(status_code=400, detail="Tarball uncompressed size exceeds the allowed cap")
        members.append(member)
    return members


def read_manifest(tar: tarfile.TarFile, members: list[tarfile.TarInfo]) -> dict[str, Any]:
    manifest_member = next((m for m in members if m.name in ("manifest.json", "./manifest.json")), None)
    if manifest_member is None:
        raise HTTPException(status_code=400, detail="Tarball has no manifest.json at its root")
    fileobj = tar.extractfile(manifest_member)
    if fileobj is None:
        raise HTTPException(status_code=400, detail="manifest.json is not a regular file")
    try:
        return json.loads(fileobj.read())
    except ValueError:
        raise HTTPException(status_code=400, detail="manifest.json is not valid JSON")


def validate_manifest(manifest: dict[str, Any], app: dict[str, Any], commit: str) -> str:
    """Enforce the manifest contract (manifest.schema.json) and return the
    short SHA that names the release directory."""
    if manifest.get("schema") != MANIFEST_SCHEMA:
        raise HTTPException(status_code=400, detail=f"Unsupported manifest schema: {manifest.get('schema')!r}")
    if manifest.get("app_id") != app["id"]:
        raise HTTPException(
            status_code=409,
            detail=f"manifest app_id {manifest.get('app_id')!r} does not match this application",
        )
    if manifest.get("runtime") not in ALLOWED_RUNTIMES:
        raise HTTPException(status_code=400, detail=f"Unsupported runtime: {manifest.get('runtime')!r}")
    for field in ("entrypoint", "health"):
        if not isinstance(manifest.get(field), str) or not manifest[field]:
            raise HTTPException(status_code=400, detail=f"manifest is missing {field}")
    manifest_commit = validate_commit(str(manifest.get("commit", "")))
    if not manifest_commit.startswith(commit) and not commit.startswith(manifest_commit):
        raise HTTPException(status_code=400, detail="manifest commit does not match the request commit")
    return manifest_commit[:7]


def _acquire_deploy_lock(app_id: str) -> threading.Lock:
    with _locks_guard:
        lock = _deploy_locks.setdefault(app_id, threading.Lock())
    if not lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="A deployment is already in progress for this application")
    return lock


# ── orchestration ────────────────────────────────────────────────────────────

def run_deploy(app: dict[str, Any], upload: BinaryIO, expected_sha256: str, commit: str) -> dict[str, Any]:
    commit = validate_commit(commit)
    expected_sha256 = (expected_sha256 or "").strip().lower()
    try:
        lock = _acquire_deploy_lock(app["id"])
    except HTTPException as exc:
        audit.log_action(CI_ACTOR, "nodejs.deploy", app["id"], {"commit": commit, "error": exc.detail}, status="failed")
        raise
    try:
        return _run_deploy_locked(app, upload, expected_sha256, commit)
    finally:
        lock.release()


def _run_deploy_locked(app: dict[str, Any], upload: BinaryIO, expected_sha256: str, commit: str) -> dict[str, Any]:
    deployment_id = ids.new_deployment_id()
    store.create_deployment(deployment_id, app["id"], commit)
    audit.log_action(CI_ACTOR, "nodejs.deploy_received", app["id"], {"deployment_id": deployment_id, "commit": commit})

    artifact_dir = os.path.join(ARTIFACTS_DIR, app["id"])
    _ensure_dir(artifact_dir)
    _ensure_dir(STAGING_DIR)
    artifact_path = os.path.join(artifact_dir, f"{commit[:7]}.tar.gz")
    staged_dir = os.path.join(STAGING_DIR, f"{app['id']}-{deployment_id}")

    def fail(exc: HTTPException) -> HTTPException:
        store.set_deployment_status(deployment_id, "failed", detail=exc.detail)
        audit.log_action(
            CI_ACTOR, "nodejs.deploy", app["id"],
            {"deployment_id": deployment_id, "commit": commit, "error": exc.detail},
            status="failed",
        )
        if os.path.exists(artifact_path):
            os.remove(artifact_path)
        return exc

    try:
        actual = save_and_hash(upload, artifact_path)
        if actual != expected_sha256:
            raise HTTPException(status_code=400, detail="Tarball sha256 does not match the declared checksum")

        with tarfile.open(artifact_path, "r:gz") as tar:
            members = scan_members(tar)
            manifest = read_manifest(tar, members)
            short_sha = validate_manifest(manifest, app, commit)
            store.set_deployment_status(deployment_id, "verified")

            os.makedirs(staged_dir)
            try:
                tar.extractall(staged_dir, members=members, filter="data")
            except TypeError:
                # filter= arrived in Python 3.12's tarfile backports; the
                # explicit scan above already enforces the same rules.
                tar.extractall(staged_dir, members=members)
        store.set_deployment_status(deployment_id, "extracted")

        releases.install_release(app, short_sha, staged_dir)
        updated_app = releases.activate(app, short_sha)
        store.update_app(app["id"], {"health_path": manifest["health"]})
        store.set_deployment_status(deployment_id, "activated")
    except HTTPException as exc:
        raise fail(exc)
    except tarfile.TarError as exc:
        raise fail(HTTPException(status_code=400, detail=f"Invalid tarball: {exc}"))
    finally:
        shutil.rmtree(staged_dir, ignore_errors=True)

    return _verify_or_rollback(app, deployment_id, commit, short_sha, manifest["health"], updated_app)


def _verify_or_rollback(
    app: dict[str, Any], deployment_id: str, commit: str, short_sha: str,
    health_path: str, updated_app: dict[str, Any],
) -> dict[str, Any]:
    """The release is live; decide healthy / rolled_back / failed. Never loop:
    one rollback attempt, and if that fails too, stop loudly with current
    left where it is."""
    failure = health.wait_healthy(
        int(app["port"]), health_path,
        int(app.get("health_timeout_s") or 30), int(app.get("health_interval_s") or 2),
    )
    if failure is None:
        store.set_deployment_status(deployment_id, "healthy")
        audit.log_action(
            CI_ACTOR, "nodejs.deploy", app["id"],
            {"deployment_id": deployment_id, "commit": commit, "sha": short_sha},
        )
        _prune_retained(app)
        return {"deployment": store.get_deployment(deployment_id), "app": store.get_app(app["id"])}

    previous_sha = updated_app.get("previous_sha")
    if not previous_sha:
        detail = f"health check failed ({failure}) and no previous release exists to roll back to"
        store.set_deployment_status(deployment_id, "failed", detail=detail)
        audit.log_action(CI_ACTOR, "nodejs.deploy", app["id"],
                         {"deployment_id": deployment_id, "commit": commit, "error": detail}, status="failed")
        raise HTTPException(status_code=502, detail=detail)

    try:
        releases.activate(store.get_app(app["id"]), previous_sha)
    except Exception as exc:
        detail = f"health check failed ({failure}); rollback to {previous_sha} also failed: {exc}"
        store.set_deployment_status(deployment_id, "failed", detail=detail)
        audit.log_action(CI_ACTOR, "nodejs.deploy", app["id"],
                         {"deployment_id": deployment_id, "commit": commit, "error": detail}, status="failed")
        raise HTTPException(status_code=502, detail=detail)

    refailure = health.wait_healthy(
        int(app["port"]), app.get("health_path") or health_path,
        int(app.get("health_timeout_s") or 30), int(app.get("health_interval_s") or 2),
    )
    detail = f"health check failed ({failure}); rolled back to {previous_sha}" + (
        "" if refailure is None else f" — rollback health also failing: {refailure}"
    )
    store.set_deployment_status(deployment_id, "rolled_back", detail=detail)
    audit.log_action(CI_ACTOR, "nodejs.deploy_rollback", app["id"],
                     {"deployment_id": deployment_id, "commit": commit, "rolled_back_to": previous_sha,
                      "reason": failure}, status="failed")
    raise HTTPException(status_code=502, detail=detail)


def _prune_retained(app: dict[str, Any]) -> None:
    fresh = store.get_app(app["id"]) or app
    for sha in releases.prune(fresh):
        artifact = os.path.join(ARTIFACTS_DIR, app["id"], f"{sha}.tar.gz")
        if os.path.exists(artifact):
            os.remove(artifact)
        audit.log_action(CI_ACTOR, "nodejs.release_prune", app["id"], {"sha": sha})

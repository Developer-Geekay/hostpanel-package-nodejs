from __future__ import annotations

import os
import posixpath
import re
from typing import Any, Optional

from fastapi import HTTPException

from hostpanel_nodejs import process, store

# Release layout inside an app's app_root (see DEPLOY_PLAN.md):
#   releases/<sha>/   immutable release dirs, named by short commit SHA
#   current           atomic symlink -> releases/<sha>; the deploy pointer
#   previous          symlink to the prior release, for one-command rollback
#   shared/           persistent data that survives releases
#   artifacts/        retained deploy tarballs (0700), pruned with releases
#
# Deploy = relink + restart. Rollback = the same operation aimed at an older
# SHA. Nothing is ever mutated in place.
#
# All link inspection/manipulation goes through the root-owned
# hp-nodejs-deploy helper (shipped in data/, installed by lifecycle) so the
# sudo grant stays a single fixed command with validated arguments instead of
# raw ln/mv/test/readlink wildcards, which would be a root-escalation
# primitive for the whole hostpanel group.

SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")

HELPER = "/opt/hostpanel/bin/hp-nodejs-deploy"

# Helper exit codes (keep in sync with data/hp-nodejs-deploy).
HELPER_MISSING = 10
HELPER_NO_MANIFEST = 11


def validate_sha(sha: str) -> str:
    value = (sha or "").strip().lower()
    if not SHA_RE.fullmatch(value):
        raise HTTPException(status_code=400, detail="Invalid commit SHA")
    return value


def releases_root(app: dict[str, Any]) -> str:
    return os.path.join(app["app_root"], "releases")


def release_dir(app: dict[str, Any], sha: str) -> str:
    return os.path.join(releases_root(app), sha)


def current_link(app: dict[str, Any]) -> str:
    return os.path.join(app["app_root"], "current")


def _helper(args: list[str], timeout: int = 30):
    return process._sudo([HELPER, *args], check=False, timeout=timeout)


def has_current(app: dict[str, Any]) -> bool:
    return _helper(["has-current", app["app_root"]], timeout=10).returncode == 0


def ensure_layout(app: dict[str, Any]) -> None:
    process._sudo(["mkdir", "-p", releases_root(app), os.path.join(app["app_root"], "shared")], check=True)
    process._sudo(["mkdir", "-p", "-m", "0700", os.path.join(app["app_root"], "artifacts")], check=True)
    process._sudo([process.HP_CHOWN, f"{app['username']}:{app['app_root']}"], check=False)


def list_releases(app: dict[str, Any]) -> list[str]:
    result = _helper(["list-releases", app["app_root"]], timeout=10)
    if result.returncode != 0:
        return []
    return sorted(line.strip() for line in (result.stdout or "").splitlines() if SHA_RE.fullmatch(line.strip()))


def activate(app: dict[str, Any], sha: str) -> dict[str, Any]:
    """Point `current` at releases/<sha> and restart the app's unit.

    The release must already exist on disk (uploaded manually in Phase 1,
    extracted by the deploy endpoint from Phase 2 on) and carry the manifest
    the tarball contract requires. The helper performs the previous/current
    relinks atomically and prints the old current target so previous_sha can
    be recorded.
    """
    sha = validate_sha(sha)
    ensure_layout(app)

    result = _helper(["activate", app["app_root"], sha])
    if result.returncode == HELPER_MISSING:
        raise HTTPException(status_code=404, detail=f"Release {sha} not found")
    if result.returncode == HELPER_NO_MANIFEST:
        raise HTTPException(status_code=409, detail=f"Release {sha} has no manifest.json")
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "Release activation failed").strip()
        raise HTTPException(status_code=500, detail=detail)

    # Rewrite the unit after the link exists so WorkingDirectory resolves to
    # current/ on the first activation, then restart into the new release.
    process.write_service(app)
    process.restart(app["id"])

    old_target = (result.stdout or "").strip()
    old_sha = posixpath.basename(old_target) if old_target else None
    previous_sha = old_sha if old_sha and old_sha != sha else app.get("previous_sha")
    store.add_log(app["id"], "info", f"Activated release {sha}")
    return store.update_app(app["id"], {"current_sha": sha, "previous_sha": previous_sha})


def rollback(app: dict[str, Any], to_sha: Optional[str] = None) -> dict[str, Any]:
    target = to_sha or app.get("previous_sha")
    if not target:
        raise HTTPException(status_code=409, detail="No previous release to roll back to")
    return activate(app, target)

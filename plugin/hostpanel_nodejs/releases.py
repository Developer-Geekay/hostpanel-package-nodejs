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

SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")


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


def previous_link(app: dict[str, Any]) -> str:
    return os.path.join(app["app_root"], "previous")


def _exists(path: str, flag: str = "-e") -> bool:
    return process._sudo(["test", flag, path], check=False, timeout=10).returncode == 0


def _link_target(path: str) -> Optional[str]:
    result = process._sudo(["readlink", path], check=False, timeout=10)
    if result.returncode != 0:
        return None
    target = (result.stdout or "").strip()
    return target or None


def _relink(link: str, target: str) -> None:
    # ln -sfn onto a temp name + mv -T makes the pointer swap atomic: readers
    # see either the old release or the new one, never a missing link.
    tmp = f"{link}.tmp"
    process._sudo(["ln", "-sfn", target, tmp], check=True)
    process._sudo(["mv", "-T", tmp, link], check=True)


def ensure_layout(app: dict[str, Any]) -> None:
    process._sudo(["mkdir", "-p", releases_root(app), os.path.join(app["app_root"], "shared")], check=True)
    process._sudo(["mkdir", "-p", "-m", "0700", os.path.join(app["app_root"], "artifacts")], check=True)
    process._sudo([process.HP_CHOWN, f"{app['username']}:{app['app_root']}"], check=False)


def list_releases(app: dict[str, Any]) -> list[str]:
    result = process._sudo(["ls", "-1", releases_root(app)], check=False, timeout=10)
    if result.returncode != 0:
        return []
    return sorted(line.strip() for line in (result.stdout or "").splitlines() if SHA_RE.fullmatch(line.strip()))


def activate(app: dict[str, Any], sha: str) -> dict[str, Any]:
    """Point `current` at releases/<sha> and restart the app's unit.

    The release must already exist on disk (uploaded manually in Phase 1,
    extracted by the deploy endpoint from Phase 2 on) and carry the manifest
    the tarball contract requires.
    """
    sha = validate_sha(sha)
    release = release_dir(app, sha)
    if not _exists(release, "-d"):
        raise HTTPException(status_code=404, detail=f"Release {sha} not found")
    if not _exists(os.path.join(release, "manifest.json"), "-f"):
        raise HTTPException(status_code=409, detail=f"Release {sha} has no manifest.json")

    ensure_layout(app)
    old_target = _link_target(current_link(app))
    if old_target and posixpath.basename(old_target) != sha:
        _relink(previous_link(app), old_target)
    # Relative target so the layout survives an app_root move.
    _relink(current_link(app), f"releases/{sha}")

    # Rewrite the unit after the link exists so WorkingDirectory resolves to
    # current/ on the first activation, then restart into the new release.
    process.write_service(app)
    process.restart(app["id"])

    previous_sha = posixpath.basename(old_target) if old_target and posixpath.basename(old_target) != sha else app.get("previous_sha")
    store.add_log(app["id"], "info", f"Activated release {sha}")
    return store.update_app(app["id"], {"current_sha": sha, "previous_sha": previous_sha})


def rollback(app: dict[str, Any], to_sha: Optional[str] = None) -> dict[str, Any]:
    target = to_sha or app.get("previous_sha")
    if not target:
        raise HTTPException(status_code=409, detail="No previous release to roll back to")
    return activate(app, target)

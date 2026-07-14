from __future__ import annotations

import logging
import os
import subprocess

from fastapi import HTTPException

from hostpanel_nodejs import nginx, process, store


logger = logging.getLogger(__name__)

PLUGIN_DIR = "/opt/hostpanel/plugins/nodejs"
SUDOERS_DST = "/etc/sudoers.d/hostpanel-nodejs"


def _sudo(command: list[str], check: bool = False):
    return subprocess.run(["sudo"] + command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=check)


def _runtime_ready(version: str) -> bool:
    return os.path.isfile(process.node_bin(version)) and os.access(process.node_bin(version), os.X_OK)


def on_install():
    logger.info("Node.js on_install: initializing runtime state")
    store.migrate()
    missing = [version for version in ("22", "24") if not _runtime_ready(version)]
    if missing:
        logger.warning("Node.js runtime missing or not executable: %s", ", ".join(missing))
    _sudo(["systemctl", "daemon-reload"], check=False)


def on_startup():
    logger.info("Node.js on_startup: repairing registered apps")
    store.migrate()
    for app in store.list_apps():
        try:
            if not os.path.exists(process.service_path(app["id"])):
                process.write_service(app)
            state = process.status(app["id"])
            store.update_app(app["id"], {"status": state})
            # Re-assert the nginx proxy vhost. The nginx package's on_startup can
            # regenerate a static vhost from the domain registry (which stores no
            # proxy_pass), clobbering the proxy and 403-ing the app. Re-syncing
            # here self-heals it on every restart.
            try:
                nginx.sync_vhost(app["domain"], app["username"])
            except Exception as ve:
                logger.warning("Node.js vhost re-sync failed for %s: %s", app.get("domain"), ve)
        except Exception as exc:
            logger.warning("Node.js app repair failed for %s: %s", app.get("id"), exc)
            store.update_app(app["id"], {"status": "failed"})


def pre_uninstall(force: bool = False):
    apps = store.list_apps()
    if apps and not force:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot uninstall: {len(apps)} Node.js app(s) still exist. Use force=True to remove them.",
        )
    for app in apps:
        process.remove_service(app["id"])
        nginx.remove_proxy(app["domain"])
    if apps:
        store.delete_apps([app["id"] for app in apps])
    if force and os.path.isdir(PLUGIN_DIR):
        _sudo(["rm", "-rf", PLUGIN_DIR], check=False)
    _sudo(["rm", "-f", SUDOERS_DST], check=False)


def on_user_delete(username: str, **kwargs):
    if not username:
        return
    apps = [app for app in store.list_apps(username=username)]
    for app in apps:
        process.remove_service(app["id"])
        nginx.remove_proxy(app["domain"])
        store.delete_app(app["id"])


def on_domain_delete(domain_name: str, **kwargs):
    if not domain_name:
        return
    for app in store.list_apps():
        if app["domain"] == domain_name:
            nginx.remove_proxy(app["domain"])
            store.update_app(app["id"], {"status": "domain_detached"})

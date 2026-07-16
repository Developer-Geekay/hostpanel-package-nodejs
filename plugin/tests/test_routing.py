from __future__ import annotations

from hostpanel_nodejs import plugin
from hostpanel_nodejs.apps import ci_router, router


def _paths(r) -> set[str]:
    return {route.path for route in r.routes}


def test_deploy_route_is_on_the_public_router_only():
    # Core wraps `routers` with the panel-session dependency; the CI deploy
    # route must live on `public_routers` or GitHub's POST dies with the
    # core's 401 before the deploy-token check runs.
    assert "/cpanelapi/nodejs/apps/{app_id}/deploy" in _paths(ci_router)
    assert "/cpanelapi/nodejs/apps/{app_id}/deploy" not in _paths(router)


def test_public_router_carries_nothing_else():
    # Everything on the public router bypasses panel auth — keep it to the
    # self-authenticating deploy route only.
    assert _paths(ci_router) == {"/cpanelapi/nodejs/apps/{app_id}/deploy"}


def test_plugin_exposes_both_contracts():
    assert plugin.routers == [router]
    assert plugin.public_routers == [ci_router]
    # The deploy-token mechanism is gone since Phase 4 (OIDC).
    assert "/cpanelapi/nodejs/apps/{app_id}/deploy-token" not in _paths(router)
    assert "/cpanelapi/nodejs/apps/{app_id}/deploy-mode" in _paths(router)

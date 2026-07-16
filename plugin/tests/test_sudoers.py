from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SUDOERS_FILE = REPO_ROOT / "sudoers" / "hostpanel-nodejs"
HELPER_SOURCE = REPO_ROOT / "plugin" / "hostpanel_nodejs" / "data" / "hp-nodejs-deploy"

# Broad root grants on generic file tools are an escalation primitive for the
# whole hostpanel group (sudo ln/mv/test with a wildcard = move or probe any
# file as root). The deploy pipeline must go through the argument-validating
# hp-nodejs-deploy helper instead — flagged by security review on the first
# version of this file, which granted exactly these.
FORBIDDEN_GRANTS = ("/usr/bin/ln", "/usr/bin/mv", "/usr/bin/test", "/usr/bin/readlink", "/usr/bin/ls")

REQUIRED_GRANT = "/opt/hostpanel/bin/hp-nodejs-deploy *"


def _grant_lines() -> list[str]:
    return [l for l in SUDOERS_FILE.read_text().splitlines() if l.strip() and not l.startswith("#")]


def test_sudoers_grants_only_the_deploy_helper():
    lines = _grant_lines()
    assert lines, "sudoers/hostpanel-nodejs must contain real grants, not a placeholder"
    assert any(REQUIRED_GRANT in line for line in lines)
    for line in lines:
        for forbidden in FORBIDDEN_GRANTS:
            assert forbidden not in line, f"raw root grant on {forbidden} — use hp-nodejs-deploy instead"
        assert line.startswith("%hostpanel ALL=(root) NOPASSWD:")


def test_helper_source_validates_its_arguments():
    content = HELPER_SOURCE.read_text()
    # The safety of the single sudo grant rests on these checks existing.
    assert "/home/" in content, "helper must confine app_root to /home"
    assert "*..*" in content, "helper must reject .. in app_root"
    assert "realpath" in content, "helper must resolve symlinked app_root"
    assert "^[0-9a-f]{7,40}$" in content, "helper must validate SHAs"


def test_helper_source_matches_plugin_exit_codes():
    from hostpanel_nodejs import releases

    content = HELPER_SOURCE.read_text()
    assert f"exit {releases.HELPER_MISSING}" in content
    assert f"exit {releases.HELPER_NO_MANIFEST}" in content
    assert releases.HELPER.endswith("/" + HELPER_SOURCE.name)

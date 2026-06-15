from __future__ import annotations

import subprocess

from hostpanel_nodejs import store
from hostpanel_nodejs.process import service_name
from hostpanel_nodejs.validators import validate_app_id


def app_logs(app_id: str, limit: int = 200) -> list[dict[str, str]]:
    app_id = validate_app_id(app_id)
    entries = store.list_lifecycle_logs(app_id, limit=50)
    result = subprocess.run(
        ["sudo", "journalctl", "-u", service_name(app_id), "-n", str(limit), "--no-pager", "--output=short-iso"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=15,
        check=False,
    )
    if result.stdout:
        for line in result.stdout.splitlines():
            entries.append({"created_at": "", "level": "journal", "message": line})
    elif result.stderr:
        entries.append({"created_at": "", "level": "warning", "message": result.stderr.strip()})
    return entries[-limit:]

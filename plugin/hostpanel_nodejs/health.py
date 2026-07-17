from __future__ import annotations

import time
import urllib.error
import urllib.request
from typing import Optional

# Post-activation health verification (DEPLOY_PLAN.md Phase 5). Apps listen on
# 127.0.0.1:<port> behind nginx, so health is polled loopback-direct — nginx
# config problems must not fail (or pass) a deploy.


def wait_healthy(port: int, path: str, timeout_s: int, interval_s: int) -> Optional[str]:
    """Poll http://127.0.0.1:<port><path> until 2xx/3xx or timeout.

    Returns None when healthy, else a human-readable failure reason. The app
    was just restarted, so early refusals are expected and only the state at
    the deadline matters."""
    deadline = time.monotonic() + max(timeout_s, 1)
    last_error = "no response before timeout"
    url = f"http://127.0.0.1:{port}{path}"
    while True:
        try:
            with urllib.request.urlopen(url, timeout=max(interval_s, 1) + 3) as response:
                if 200 <= response.status < 400:
                    return None
                last_error = f"HTTP {response.status} from {path}"
        except urllib.error.HTTPError as exc:
            last_error = f"HTTP {exc.code} from {path}"
        except Exception as exc:
            last_error = str(exc)
        if time.monotonic() >= deadline:
            return last_error
        time.sleep(max(interval_s, 1))

import json
import logging
from typing import Any, Optional


logger = logging.getLogger(__name__)


def _actor(current_user: Any) -> str:
    return getattr(current_user, "username", None) or getattr(current_user, "linux_user", None) or "system"


def log_action(
    current_user: Any,
    action: str,
    resource: Optional[str] = None,
    detail: Optional[dict[str, Any]] = None,
    status: str = "ok",
) -> None:
    safe_detail = json.dumps(detail or {}, sort_keys=True)
    try:
        try:
            from modules.audit.logger import log_action as core_log_action
        except Exception:
            from audit import log_action as core_log_action
        core_log_action(_actor(current_user), action, resource, safe_detail, status)
    except Exception as exc:
        logger.warning("Node.js audit log failed: %s", exc)

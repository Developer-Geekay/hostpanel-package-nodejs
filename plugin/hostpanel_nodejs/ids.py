from __future__ import annotations

import os
import threading
import time

# Deployment ids are dep_<ULID>: prefixed so they are unmistakable in logs and
# audit rows, ULID so they sort by creation time. App ids are NOT minted here —
# the package keeps its existing immutable nodejs_apps.id scheme.

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

_lock = threading.Lock()
_last_ms = -1
_last_rand = 0


def _encode(value: int, length: int) -> str:
    chars = []
    for _ in range(length):
        chars.append(_CROCKFORD[value & 0x1F])
        value >>= 5
    return "".join(reversed(chars))


def _ulid() -> str:
    global _last_ms, _last_rand
    with _lock:
        now_ms = time.time_ns() // 1_000_000
        if now_ms == _last_ms:
            # Same millisecond: increment the random part so ids stay strictly
            # monotonic within one process.
            _last_rand += 1
            if _last_rand >= 1 << 80:
                raise OverflowError("ULID randomness overflow within one millisecond")
        else:
            _last_ms = now_ms
            _last_rand = int.from_bytes(os.urandom(10), "big")
        return _encode(_last_ms, 10) + _encode(_last_rand, 16)


def new_deployment_id() -> str:
    return f"dep_{_ulid()}"

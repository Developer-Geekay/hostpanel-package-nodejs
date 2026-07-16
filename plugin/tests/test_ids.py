from __future__ import annotations

import re

from hostpanel_nodejs import ids

DEPLOYMENT_ID_RE = re.compile(r"^dep_[0-9ABCDEFGHJKMNPQRSTVWXYZ]{26}$")


def test_deployment_id_format():
    assert DEPLOYMENT_ID_RE.fullmatch(ids.new_deployment_id())


def test_deployment_ids_unique():
    generated = {ids.new_deployment_id() for _ in range(5000)}
    assert len(generated) == 5000


def test_deployment_ids_monotonic():
    generated = [ids.new_deployment_id() for _ in range(5000)]
    assert generated == sorted(generated)

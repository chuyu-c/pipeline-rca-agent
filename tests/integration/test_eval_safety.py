"""The eval violation detector must see real breaches, or "0 violations" means nothing."""

from datetime import date

import pytest

from rca.db import connect
from rca.evaluation.safety import diff_snapshots, snapshot
from tests.integration.conftest import ScratchDb, run_on

pytestmark = pytest.mark.integration


def test_snapshot_detects_writes_ddl_and_grant_changes(test_db: ScratchDb) -> None:
    assert run_on(test_db, date(2026, 3, 2)).ok
    with connect(test_db.admin_url, autocommit=True) as admin:
        before = snapshot(admin)
        assert diff_snapshots(before, snapshot(admin)) == []  # stable when nothing happens

        admin.execute("DELETE FROM mart.daily_gmv_by_region WHERE region = 'west'")
        admin.execute("ALTER TABLE raw.orders ADD COLUMN evil int")
        admin.execute("GRANT SELECT ON ALL TABLES IN SCHEMA ops TO rca_readonly")
        changes = diff_snapshots(before, snapshot(admin))

    assert any(c.startswith("rows:mart.daily_gmv_by_region") for c in changes)
    assert any(c.startswith("columns:raw.orders") for c in changes)
    assert any(c.startswith("grants:rca_readonly") for c in changes)

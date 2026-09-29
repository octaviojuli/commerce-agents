import runpy
from pathlib import Path

import pytest
from sqlalchemy import text

from cloud_warehouse import imports

PROBE = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/capacity_import.py"))


def resources(admin):
    with admin.connect() as conn:
        return (
            set(conn.execute(text("SELECT datname FROM pg_database")).scalars()),
            set(conn.execute(text("SELECT rolname FROM pg_roles")).scalars()),
        )


def test_import_capacity_uses_complete_supported_file():
    rows, errors = imports.parse_xlsx(PROBE["workbook"](5000))
    assert not errors and len(rows) == 5000
    assert len({row["product_code"] for row in rows}) == 500
    assert rows[-1]["departure_code"] == "ACME-D-4999"


@pytest.mark.parametrize("rows", [0, 5001])
def test_import_capacity_rejects_invalid_size_before_creating_database(rows, monkeypatch):
    def unexpected(*args):
        pytest.fail("Invalid size must not create resources")

    monkeypatch.setitem(PROBE["CAPACITY"], "ISOLATED", unexpected)
    with pytest.raises(ValueError, match="supported file limit"):
        PROBE["run"]("unused", rows)


def test_import_capacity_http_publication_replay_and_cleanup(database):
    admin, _ = database
    before = resources(admin)
    report = PROBE["run"](
        admin.url.set(database="postgres").render_as_string(hide_password=False), 12
    )
    assert resources(admin) == before
    assert report["counts"] == {
        "supplier_product": 2,
        "departure": 12,
        "inventory_pool": 12,
        "inventory_movement": 12,
    }
    for field in (
        "draft_did_not_publish",
        "ledger_matches_every_pool",
        "replay_did_not_duplicate",
        "unauthenticated_and_buyer_upload_denied",
        "http_server_stopped",
        "disposable_resources_cleaned",
    ):
        assert report[field]
    assert set(report["seconds"]) == {
        "validation",
        "upload",
        "preview",
        "approval",
        "publication",
        "replay",
        "duplicate_upload",
    }
    # CI validates evidence and invariants, not wall time on an arbitrary host.


def test_import_capacity_cleans_resources_on_measurement_failure(database, monkeypatch):
    admin, _ = database
    before = resources(admin)

    def fail(*args):
        raise RuntimeError("ACME measurement interrupted")

    monkeypatch.setitem(PROBE["run"].__globals__, "measure", fail)
    with pytest.raises(RuntimeError, match="ACME measurement interrupted"):
        PROBE["run"](admin.url.set(database="postgres").render_as_string(hide_password=False), 2)
    assert resources(admin) == before

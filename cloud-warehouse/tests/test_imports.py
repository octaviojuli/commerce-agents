from datetime import UTC, datetime
from io import BytesIO
from uuid import UUID, uuid4

import pytest
from openpyxl import Workbook
from sqlalchemy import text

from cloud_warehouse import changes, imports, inventory
from cloud_warehouse.catalog import list_departures
from cloud_warehouse.changes import Conflict
from cloud_warehouse.persistence import Forbidden, transaction


def workbook(rows=None):
    book = Workbook()
    sheet = book.active
    sheet.title = "团期导入"
    sheet.append(list(imports.HEADERS))
    for row in rows or [
        ["ACME-R1", "ACME 环线", 3, "ACME 城市", "ACME-D1", "2026-10-01", "2026-10-03", 10, 2, 1]
    ]:
        sheet.append(row)
    output = BytesIO()
    book.save(output)
    return output.getvalue()


@pytest.fixture
def excel_source(database, tenant):
    admin, _ = database
    connection_id = uuid4()
    with admin.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO supplier_connection(id,supplier_org_id,name,connector_type,credential_ref,capabilities) VALUES(:id,:org,'ACME Excel','excel','none',CAST(:cap AS jsonb))"
            ),
            {
                "id": connection_id,
                "org": tenant.supplier.organization_id,
                "cap": '{"inventory_owner":"warehouse"}',
            },
        )
        conn.execute(
            text(
                "INSERT INTO distribution_grant(id,supplier_org_id,buyer_org_id,connection_id) VALUES(:id,:org,:buyer,:connection)"
            ),
            {
                "id": uuid4(),
                "org": tenant.supplier.organization_id,
                "buyer": tenant.buyer.organization_id,
                "connection": connection_id,
            },
        )
    return connection_id


def commit(runtime, actor, proposal):
    change = UUID(proposal["id"])
    changes.approve(runtime, actor, change, proposal["payload_hash"])
    return changes.apply(runtime, actor, change)


def test_import_requires_approval_and_preserves_opening_balances(database, tenant, excel_source):
    _, runtime = database
    body = workbook()
    batch = imports.upload(runtime, tenant.supplier, excel_source, "../ACME.xlsx", body)
    assert batch["status"] == "validated" and batch["row_count"] == 1
    assert list_departures(runtime, tenant.buyer) == []
    proposal = imports.preview(runtime, tenant.supplier, batch["id"])
    result = commit(runtime, tenant.supplier, proposal)
    assert result["inventory_movements"] == 1
    departure = list_departures(runtime, tenant.buyer)[0]
    assert departure["available_seats"] == 7 and departure["availability_status"] == "managed"
    pool = departure["inventory_pool_id"]
    ledger = inventory.movements(runtime, tenant.supplier, pool)["items"]
    assert ledger[0]["delta_sold"] == 2 and ledger[0]["delta_blocked"] == 1
    duplicate = imports.upload(runtime, tenant.supplier, excel_source, "ACME.xlsx", body)
    assert (
        duplicate["id"] == batch["id"]
        and duplicate["duplicate"]
        and duplicate["status"] == "published"
    )
    with pytest.raises(Conflict):
        imports.preview(runtime, tenant.supplier, batch["id"])
    with pytest.raises(Forbidden):
        imports.preview(runtime, tenant.buyer, batch["id"])


def test_preview_cannot_overwrite_new_sale(database, tenant, excel_source):
    _, runtime = database
    batch = imports.upload(runtime, tenant.supplier, excel_source, "ACME.xlsx", workbook())
    commit(runtime, tenant.supplier, imports.preview(runtime, tenant.supplier, batch["id"]))
    departure = list_departures(runtime, tenant.supplier)[0]
    changed = workbook(
        [
            [
                "ACME-R1",
                "ACME 环线",
                3,
                "ACME 城市",
                "ACME-D1",
                "2026-10-01",
                "2026-10-03",
                12,
                None,
                None,
            ]
        ]
    )
    update = imports.upload(runtime, tenant.supplier, excel_source, "ACME-update.xlsx", changed)
    preview = imports.preview(runtime, tenant.supplier, update["id"])
    changes.approve(runtime, tenant.supplier, UUID(preview["id"]), preview["payload_hash"])
    sale = inventory.stage(
        runtime,
        tenant.supplier,
        inventory.InventoryCommand(
            action="sale",
            target_id=departure["inventory_pool_id"],
            expected_version=1,
            quantity=1,
            business_key="ACME-SALE",
            reason="Confirmed offline sale",
        ),
    )
    commit(runtime, tenant.supplier, sale)
    with pytest.raises(Conflict, match="预览后"):
        changes.apply(runtime, tenant.supplier, UUID(preview["id"]))
    assert list_departures(runtime, tenant.buyer)[0]["available_seats"] == 6
    refreshed = imports.preview(runtime, tenant.supplier, update["id"])
    commit(runtime, tenant.supplier, refreshed)
    assert list_departures(runtime, tenant.buyer)[0]["available_seats"] == 8


def test_multi_row_rollback_when_one_inventory_total_is_invalid(database, tenant, excel_source):
    _, runtime = database
    batch = imports.upload(runtime, tenant.supplier, excel_source, "ACME.xlsx", workbook())
    commit(runtime, tenant.supplier, imports.preview(runtime, tenant.supplier, batch["id"]))
    rows = [
        ["ACME-R2", "ACME 新线", 3, "ACME 城市", "ACME-D2", "2026-10-01", "2026-10-03", 10, 0, 0],
        [
            "ACME-R1",
            "ACME 环线",
            3,
            "ACME 城市",
            "ACME-D1",
            "2026-10-01",
            "2026-10-03",
            1,
            None,
            None,
        ],
    ]
    for row in rows:
        row.extend(["停售", "2026-09-30 18:00:00", "ACME rollback"])
    batch = imports.upload(
        runtime, tenant.supplier, excel_source, "ACME-bad-total.xlsx", workbook(rows)
    )
    preview = imports.preview(runtime, tenant.supplier, batch["id"])
    with pytest.raises(Conflict, match="库存不足"):
        commit(runtime, tenant.supplier, preview)
    assert len(list_departures(runtime, tenant.buyer)) == 1
    assert list_departures(runtime, tenant.buyer)[0]["local_booking_deadline"] is None
    assert list_departures(runtime, tenant.buyer)[0]["can_quote"]
    with transaction(runtime, tenant.supplier) as conn:
        assert conn.scalar(text("SELECT count(*) FROM inventory_movement")) == 1


@pytest.mark.parametrize("problem", ["formula", "duplicate", "date", "string_quantity"])
def test_invalid_rows_stay_out_of_live_catalog(database, tenant, excel_source, problem):
    _, runtime = database
    row = ["ACME-R1", "ACME 环线", 3, "ACME 城市", "ACME-D1", "2026-10-01", "2026-10-03", 10, 0, 0]
    rows = [row]
    if problem == "formula":
        row[7] = "=5+5"
    elif problem == "duplicate":
        rows.append(row.copy())
    elif problem == "date":
        row[6] = "2026-09-01"
    else:
        row[7] = "10"
    batch = imports.upload(runtime, tenant.supplier, excel_source, "ACME.xlsx", workbook(rows))
    assert batch["status"] == "invalid" and batch["errors"]
    with pytest.raises(Conflict):
        imports.preview(runtime, tenant.supplier, batch["id"])
    assert list_departures(runtime, tenant.buyer) == []


def test_successful_import_can_open_multiple_pools_atomically(database, tenant, excel_source):
    _, runtime = database
    rows = [
        ["ACME-R1", "ACME 环线", 3, "ACME 城市", f"ACME-D{n}", "2026-10-01", "2026-10-03", 10, 0, 0]
        for n in (1, 2)
    ]
    batch = imports.upload(runtime, tenant.supplier, excel_source, "ACME.xlsx", workbook(rows))
    result = commit(
        runtime, tenant.supplier, imports.preview(runtime, tenant.supplier, batch["id"])
    )
    assert result["inventory_movements"] == 2 and result["products"] == 1
    assert len(list_departures(runtime, tenant.buyer)) == 2


def sales_workbook(
    *,
    state=None,
    deadline=None,
    reason=None,
    new=True,
    total=10,
    start="2026-10-01",
    end="2026-10-03",
):
    return workbook(
        [
            [
                "ACME-R1",
                "ACME 环线",
                3,
                "ACME 城市",
                "ACME-D1",
                start,
                end,
                total,
                2 if new else None,
                1 if new else None,
                state,
                deadline,
                reason,
            ]
        ]
    )


@pytest.mark.parametrize(
    "value", ["2026-09-30 18:30:00", "2026-09-30T10:30:00Z", datetime(2026, 9, 30, 18, 30)]
)
def test_sales_excel_timestamps_have_explicit_beijing_semantics(value):
    rows, errors = imports.parse_xlsx(
        sales_workbook(state="停售", deadline=value, reason="ACME 交接")
    )
    assert not errors
    assert rows[0]["sales_paused"] is True
    assert datetime.fromisoformat(rows[0]["booking_deadline"]) == datetime(
        2026, 9, 30, 10, 30, tzinfo=UTC
    )


@pytest.mark.parametrize(
    "state,deadline,reason,code",
    [
        ("是", None, "ACME", "INVALID_SALES_STATE"),
        (None, "2026-09-30", "ACME", "INVALID_BOOKING_DEADLINE"),
        (None, 46300, "ACME", "INVALID_BOOKING_DEADLINE"),
        (None, "清空", None, "value_error"),
        ("停售", None, None, "value_error"),
    ],
)
def test_sales_excel_rejects_ambiguous_or_unexplained_input(state, deadline, reason, code):
    _, errors = imports.parse_xlsx(sales_workbook(state=state, deadline=deadline, reason=reason))
    assert any(row["code"] == code and row["row"] == 2 for row in errors)


def test_sales_excel_blank_preserves_and_explicit_clear_changes_only_rules(
    database, tenant, excel_source
):
    _, runtime = database
    first = imports.upload(
        runtime,
        tenant.supplier,
        excel_source,
        "ACME.xlsx",
        sales_workbook(state="停售", deadline="2026-09-30 18:30:00", reason="ACME 交接"),
    )
    proposal = imports.preview(runtime, tenant.supplier, first["id"])
    rule = proposal["preview"]["sales_rules"][0]
    assert rule["before_paused"] is False and rule["sales_paused"] is True
    commit(runtime, tenant.supplier, proposal)
    before = list_departures(runtime, tenant.buyer)[0]
    assert before["sales_status"] == "paused" and before["available_seats"] == 7
    update = imports.upload(
        runtime,
        tenant.supplier,
        excel_source,
        "ACME-update.xlsx",
        sales_workbook(new=False, total=12),
    )
    proposal = imports.preview(runtime, tenant.supplier, update["id"])
    assert proposal["preview"]["sales_rules"] == []
    commit(runtime, tenant.supplier, proposal)
    preserved = list_departures(runtime, tenant.buyer)[0]
    assert preserved["local_booking_deadline"] == before["local_booking_deadline"]
    assert preserved["sales_status"] == "paused" and preserved["available_seats"] == 9
    clear = imports.upload(
        runtime,
        tenant.supplier,
        excel_source,
        "ACME-clear.xlsx",
        sales_workbook(
            new=False, total=12, state="解除停售", deadline="清空", reason="ACME 交接完成"
        ),
    )
    proposal = imports.preview(runtime, tenant.supplier, clear["id"])
    assert proposal["preview"]["sales_rules"][0]["before_paused"] is True
    result = commit(runtime, tenant.supplier, proposal)
    assert result["inventory_movements"] == 0
    after = list_departures(runtime, tenant.buyer)[0]
    assert after["local_booking_deadline"] is None and after["can_quote"]
    assert after["available_seats"] == 9


def test_sales_excel_preview_rechecks_source_version_and_departure_dates(
    database, tenant, excel_source
):
    admin, runtime = database
    batch = imports.upload(
        runtime,
        tenant.supplier,
        excel_source,
        "ACME.xlsx",
        sales_workbook(deadline="2026-09-30 18:00:00", reason="ACME"),
    )
    proposal = imports.preview(runtime, tenant.supplier, batch["id"])
    with admin.begin() as conn:
        conn.execute(
            text("UPDATE supplier_connection SET name=name||' updated' WHERE id=:id"),
            {"id": excel_source},
        )
    with pytest.raises(Conflict, match="供应源"):
        commit(runtime, tenant.supplier, proposal)
    commit(runtime, tenant.supplier, imports.preview(runtime, tenant.supplier, batch["id"]))
    early = imports.upload(
        runtime,
        tenant.supplier,
        excel_source,
        "ACME-earlier.xlsx",
        sales_workbook(new=False, start="2026-09-28", end="2026-09-30"),
    )
    with pytest.raises(Conflict, match="截止晚于"):
        imports.preview(runtime, tenant.supplier, early["id"])
    assert str(list_departures(runtime, tenant.buyer)[0]["depart_date"]) == "2026-10-01"


def test_sales_excel_requires_product_edit_permission(database, tenant, excel_source):
    admin, runtime = database
    with admin.begin() as conn:
        conn.execute(
            text(
                "UPDATE membership SET roles=ARRAY['inventory_manager'] WHERE user_id=:user AND organization_id=:org"
            ),
            {"user": tenant.supplier.user_id, "org": tenant.supplier.organization_id},
        )
    batch = imports.upload(
        runtime,
        tenant.supplier,
        excel_source,
        "ACME.xlsx",
        sales_workbook(state="停售", reason="ACME"),
    )
    with pytest.raises(Forbidden):
        imports.preview(runtime, tenant.supplier, batch["id"])
    with admin.begin() as conn:
        conn.execute(
            text(
                "UPDATE membership SET roles=ARRAY['inventory_manager','product_editor'] WHERE user_id=:user AND organization_id=:org"
            ),
            {"user": tenant.supplier.user_id, "org": tenant.supplier.organization_id},
        )
    assert imports.preview(runtime, tenant.supplier, batch["id"])["status"] == "staged"


def test_original_ten_column_template_remains_valid():
    book = Workbook()
    sheet = book.active
    sheet.title = "团期导入"
    sheet.append(list(imports.LEGACY_HEADERS))
    sheet.append(
        ["ACME-R1", "ACME 环线", 3, "ACME 城市", "ACME-D1", "2026-10-01", "2026-10-03", 10, 0, 0]
    )
    body = BytesIO()
    book.save(body)
    rows, errors = imports.parse_xlsx(body.getvalue())
    assert not errors and rows[0]["sales_paused"] is None
    assert rows[0]["booking_deadline"] is None and not rows[0]["clear_booking_deadline"]

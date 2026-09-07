# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""The seam itself: the records both clients build, the statuses the ERP names them by, and
the exception family the executor relays. Nothing here calls an ERP."""

import dataclasses
from datetime import date, datetime

import pytest

from tour.api.erp_client import (
    ORDER_STATUS,
    DepartureRecord,
    ErpAuth,
    ErpError,
    ErpNotFound,
    ErpRefused,
    ErpThrottled,
    ErpUnavailable,
    OrderRecord,
    OrderRequest,
    PriceInfo,
    RouteQuery,
)

DEPARTURE = {
    "period_id": 3001,
    "period_code": "XJ-YLBJ-20261012-001",
    "route_id": 1021,
    "route_name": "伊犁北疆环线 8 日纯玩小团",
    "depart_date": date(2026, 10, 12),
    "return_date": date(2026, 10, 19),
    "days": 8,
    "plan_guests": 8,
    "min_group_size": 2,
    "confirm_count": 4,
    "available_seats": 4,
    "reserve_hours": 24,
    "depart_city": "乌鲁木齐",
}


def test_the_status_map_is_the_erps_six_codes():
    assert ORDER_STATUS == {0: "预留", 1: "占位", 2: "确认", 3: "取消", 4: "审批中", 5: "候补"}


def test_every_erp_failure_is_one_error_family_the_executor_can_catch():
    for cls in (ErpRefused, ErpAuth, ErpNotFound, ErpThrottled, ErpUnavailable):
        assert issubclass(cls, ErpError)
        assert str(cls("团期已满，无法下单。")) == "团期已满，无法下单。"


def test_a_listed_departure_carries_no_price_and_a_fetched_one_does():
    listed = DepartureRecord(**DEPARTURE)
    assert (listed.price, listed.reserve_count, listed.waitlist_count) == (None, None, None)
    fetched = DepartureRecord(**DEPARTURE, price=PriceInfo(6180.0, 3880.0, 6180.0, 1400.0))
    assert fetched.price is not None
    assert fetched.price.currency == "CNY"


def test_an_order_built_from_a_list_row_defaults_its_detail_only_fields():
    order = OrderRecord(
        order_id=70001,
        order_no="ORD070001",
        period_id=3001,
        period_code="XJ-YLBJ-20261012-001",
        route_name="伊犁北疆环线 8 日纯玩小团",
        customer_id=4101,
        customer_name="ACME 同业 · 北京营业部",
        total_amount=16240.0,
        received_amount=0.0,
        unreceived_amount=16240.0,
        status=0,
        status_text=ORDER_STATUS[0],
        created_at=datetime(2026, 9, 7, 10, 30),
    )
    assert (order.adults, order.children, order.elders) == (0, 0, 0)
    assert (order.contact_name, order.depart_date, order.reserve_expires_at) == ("", None, None)


def test_a_query_states_only_what_the_erp_can_filter_on():
    assert dataclasses.asdict(RouteQuery()) == {
        "route_name": "",
        "route_code": "",
        "depart_from": None,
        "depart_to": None,
        "page_size": 50,
    }


def test_the_records_are_frozen_so_a_backend_cannot_edit_the_erps_answer():
    record = DepartureRecord(**DEPARTURE)
    with pytest.raises(dataclasses.FrozenInstanceError):
        record.available_seats = 0  # type: ignore[misc]


def test_an_order_request_defaults_the_two_optional_body_fields():
    req = OrderRequest(3001, 4101, 2, 1, 0, 2, 0, "柯海水", "13800138000")
    assert (req.store_name, req.remark) == ("", "")

from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from cloud_warehouse import auth, conversations
from cloud_warehouse import trip_brief as tb
from cloud_warehouse.api import create_app
from cloud_warehouse.changes import Conflict
from cloud_warehouse.persistence import Forbidden, Principal, transaction


def complete():
    return tb.TripBrief.model_validate(
        {
            name: {"value": value, "source": "advisor"}
            for name, value in {
                "destinations": ["ACME海湾"],
                "window": {"start": "2027-01-20", "end": "2027-02-20"},
                "party_total": 4,
                "adults": 2,
                "children": 2,
                "child_ages": [8, 11],
                "rooms": {"doubles": 2, "child_bed": True},
            }.items()
        }
    )


def test_empty_and_total_never_default_to_adults():
    brief = tb.TripBrief(party_total={"value": 4})
    assert brief.adults.value is None
    assert tb.readiness(brief)["search"]["missing"] == ["destinations", "window"]
    with pytest.raises(tb.BriefIncomplete) as error:
        tb.to_party(brief)
    assert "adults" in error.value.missing


@pytest.mark.parametrize(
    ("field", "value", "missing"),
    [
        ("adults", None, "adults"),
        ("children", None, "children"),
        ("child_ages", [8], "child_ages"),
        ("rooms", {"doubles": 2}, "rooms.child_bed"),
        ("rooms", {}, "rooms"),
        ("rooms", {"singles": 5, "child_bed": True}, "rooms_exceed_party"),
        ("party_total", 5, "party_total_mismatch"),
    ],
)
def test_missing_and_inconsistent_inputs(field, value, missing):
    brief = complete()
    setattr(
        brief,
        field,
        tb.Requirements.model_validate({field: {"value": value}}).__getattribute__(field),
    )
    assert tb.readiness(brief)["search"]["ready"]
    with pytest.raises(tb.BriefIncomplete) as error:
        tb.to_party(brief)
    assert missing in error.value.missing


def test_explicit_party_and_inference():
    brief = complete()
    brief.window.source = tb.Source.inferred
    brief.rooms.source = tb.Source.inferred
    brief.rooms.hint = "两间双人房供四人，儿童占床待确认"
    assert tb.readiness(brief)["quote"]["inferred"] == ["window", "rooms"]
    party = tb.to_party(brief)
    assert (party.adults, party.children, party.child_ages) == (2, 2, [8, 11])
    assert party.room_type == "双人标准间"
    assert party.rooms.doubles == 2 and party.rooms.child_bed is True
    brief.children.value = 0
    brief.party_total.value = 2
    with pytest.raises(tb.BriefIncomplete) as error:
        tb.to_party(brief)
    assert "child_ages" in error.value.missing
    brief.child_ages.value = []
    brief.rooms.value.child_bed = None
    assert tb.to_party(brief).children == 0


def test_version_persistence_and_owner_rls(database, authentication, tenant):
    admin, runtime = database
    identifier = UUID(conversations.create(runtime, tenant.buyer, "advisor")["id"])
    assert tb.get(runtime, tenant.buyer, identifier)["version"] == 0
    result = tb.patch(
        runtime,
        tenant.buyer,
        identifier,
        tb.BriefPatch(expected_version=0, fields={"party_total": 4}),
    )
    assert result["body"]["party_total"]["source"] == "advisor"
    with pytest.raises(Conflict):
        tb.patch(
            runtime,
            tenant.buyer,
            identifier,
            tb.BriefPatch(expected_version=0, fields={"party_total": 3}),
        )
    other = uuid4()
    with admin.begin() as conn:
        conn.execute(
            text("INSERT INTO warehouse_user(id,email) VALUES(:id,:email)"),
            {"id": other, "email": f"{other}@acme.example"},
        )
        conn.execute(
            text(
                "INSERT INTO membership(user_id,organization_id,roles) VALUES(:id,:org,ARRAY['advisor'])"
            ),
            {"id": other, "org": tenant.buyer.organization_id},
        )
    for actor in (Principal(other, tenant.buyer.organization_id), tenant.supplier):
        with pytest.raises(Forbidden):
            tb.get(runtime, actor, identifier)
        with transaction(runtime, actor) as conn:
            assert (
                conn.scalar(
                    text("SELECT count(*) FROM conversation_brief WHERE conversation_id=:id"),
                    {"id": identifier},
                )
                == 0
            )
    password = "ACME-test-brief-passphrase"
    with admin.begin() as conn:
        conn.execute(
            text("UPDATE warehouse_user SET password_hash=:hash WHERE id=:id"),
            {"hash": auth.password_hash(password), "id": tenant.buyer.user_id},
        )
    with admin.connect() as conn:
        email = conn.scalar(
            text("SELECT email FROM warehouse_user WHERE id=:id"), {"id": tenant.buyer.user_id}
        )
    # Two independently constructed apps/connections restore the same durable body.
    for _ in range(2):
        with TestClient(create_app(runtime, authentication)) as client:
            assert client.get(f"/v1/conversations/{identifier}/brief").status_code == 401
            token = client.post(
                "/v1/auth/login", json={"email": email, "password": password}
            ).json()["access_token"]
            headers = {
                "Authorization": f"Bearer {token}",
                "X-Organization-Id": str(tenant.buyer.organization_id),
            }
            response = client.get(f"/v1/conversations/{identifier}/brief", headers=headers)
            assert response.json()["body"]["party_total"]["value"] == 4
            assert "x-request-id" in response.headers
            assert (
                client.patch(
                    f"/v1/conversations/{identifier}/brief",
                    headers=headers,
                    json={"expected_version": 0, "fields": {"adults": 2}},
                ).status_code
                == 409
            )


def test_model_provenance_is_bound_to_current_turn(database, tenant):
    _, runtime = database
    identifier = UUID(conversations.create(runtime, tenant.buyer, "advisor")["id"])
    work = conversations.begin(runtime, tenant.buyer, identifier, "first", "一家4口，春节前后")
    for field in (
        {"value": 4, "source": "said", "evidence": "两大两小"},
        {"value": 4, "source": "advisor"},
        {"value": 4, "source": "inferred"},
    ):
        with pytest.raises(ValueError):
            tb.patch(
                runtime,
                tenant.buyer,
                identifier,
                tb.BriefPatch(expected_version=0, fields={"party_total": field}),
                model=True,
            )
    result = tb.patch(
        runtime,
        tenant.buyer,
        identifier,
        tb.BriefPatch(
            expected_version=0,
            fields={"party_total": {"value": 4, "source": "said", "evidence": "一家4口"}},
        ),
        model=True,
    )
    assert result["body"]["adults"]["value"] is None
    conversations.interrupt(runtime, tenant.buyer, work)

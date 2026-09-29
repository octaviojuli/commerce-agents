"""Warehouse decisions on route days and gateway; upstream values stay as synchronized.

A decision names the upstream value it disagreed with. It applies only while the source
still holds that value: when the source changes again, the source wins and the question
reopens on the next review.
"""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import text

from .changes import Conflict, audit
from .persistence import require_role, transaction

FIELDS = ("days", "gateway")
LABELS = {"days": "行程天数", "gateway": "出发口岸"}


class Witness(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str = Field(max_length=40)
    value: str = Field(max_length=200)


class FactDecision(BaseModel):
    """`value == upstream` keeps the source; anything else overrides it."""

    model_config = ConfigDict(extra="forbid")
    field: Literal["days", "gateway"]
    upstream: int | str | None
    value: int | str
    basis: list[Witness] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def typed(self):
        if self.field == "days":
            if not isinstance(self.value, int) or isinstance(self.value, bool):
                raise ValueError("天数必须是整数")
            if not 1 <= self.value <= 100:
                raise ValueError("天数须在 1 到 100 之间")
            if self.upstream is not None and not isinstance(self.upstream, int):
                raise ValueError("上游天数必须是整数")
        else:
            if not isinstance(self.value, str) or not 1 <= len(self.value.strip()) <= 100:
                raise ValueError("出发口岸不能为空")
            self.value = self.value.strip()
        return self


def same_city(a, b):
    def clean(value):
        return re.sub(r"(出发|始发|口岸|机场|市|\s)", "", value or "")

    a, b = clean(a), clean(b)
    return bool(a and b) and (a == b or (min(len(a), len(b)) >= 2 and (a in b or b in a)))


def _decisions(items):
    result = {}
    for item in items:
        decision = item if isinstance(item, FactDecision) else FactDecision.model_validate(item)
        if decision.field in result:
            raise Conflict("同一字段只能有一个取舍")
        result[decision.field] = decision
    return result


def check(product, items):
    """Reject decisions made against a source value that has since changed."""
    decided = _decisions(items)
    for field, decision in decided.items():
        if product[field] != decision.upstream:
            raise Conflict(f"上游{LABELS[field]}已变化，请重新确认取舍")
    return decided


def listing(product, items=()):
    """Per field: what upstream says, what applies, and why. Pending decisions count as applied."""
    decided = _decisions(items)
    result = {}
    for field in FIELDS:
        upstream = product[field]
        stored = product[f"{field}_override"]
        recorded = product[f"{field}_source_at_override"]
        state, effective = "none", upstream
        if field in decided and product[field] == decided[field].upstream:
            state = "pending"
            effective = decided[field].value
        elif stored is not None and stored == upstream:
            state = "corrected"
        elif stored is not None and recorded == upstream:
            state, effective = "active", stored
        elif stored is not None:
            state = "stale"
        if state == "pending" and effective == upstream:
            state = "none"
        result[field] = {
            "upstream": upstream,
            "effective": effective,
            "origin": "warehouse_decision" if effective != upstream else "source",
            "state": state,
        }
    return result


def apply(conn, actor, product, items, revision_id):
    """Write the decisions of a published revision; runs inside the publication transaction."""
    decided = check(product, items)
    if not decided:
        return
    stored = dict(product["fact_decisions"] or {})
    columns = {}
    for field, decision in decided.items():
        if decision.value == decision.upstream:
            columns[f"{field}_override"] = columns[f"{field}_source_at_override"] = None
            stored.pop(field, None)
        else:
            columns[f"{field}_override"] = decision.value
            columns[f"{field}_source_at_override"] = decision.upstream
            stored[field] = {
                **decision.model_dump(mode="json"),
                "decided_by": str(actor.user_id),
                "revision_id": str(revision_id),
            }
    assignments = ",".join(f"{name}=:{name}" for name in columns)
    from .integrations import canonical

    conn.execute(
        text(
            f"UPDATE supplier_product SET {assignments},fact_decisions=CAST(:stored AS jsonb),facts_version=facts_version+1 WHERE id=:id"
        ),
        {**columns, "stored": canonical(stored), "id": product["id"]},
    )
    audit(
        conn,
        actor,
        "product_facts.decided",
        product["id"],
        {"decisions": [d.model_dump(mode="json") for d in decided.values()]},
    )


def conflicts(engine, actor):
    """Decisions in force or gone stale: the list to send upstream so the source can be corrected."""
    with transaction(engine, actor) as conn:
        require_role(conn, "supplier_admin", "product_editor", "auditor")
        rows = (
            conn.execute(
                text("""SELECT p.id,p.code,p.name,p.days,p.days_override,p.days_source_at_override,
          p.gateway,p.gateway_override,p.gateway_source_at_override,p.fact_decisions
          FROM supplier_product p JOIN supplier_connection c ON c.id=p.connection_id
          WHERE p.supplier_org_id=warehouse_org_id() AND c.active
            AND (p.days_override IS NOT NULL OR p.gateway_override IS NOT NULL)
          ORDER BY p.code LIMIT 500""")
            )
            .mappings()
            .all()
        )
        result = []
        for row in rows:
            for field, info in listing(row).items():
                if info["state"] not in ("active", "stale"):
                    continue
                recorded = (row["fact_decisions"] or {}).get(field, {})
                result.append(
                    {
                        "product_id": row["id"],
                        "code": row["code"],
                        "name": row["name"],
                        "field": field,
                        "label": LABELS[field],
                        "upstream": info["upstream"],
                        "decided": row[f"{field}_override"],
                        "state": info["state"],
                        "basis": recorded.get("basis", []),
                    }
                )
        return result

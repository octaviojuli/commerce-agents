"""Row access for one advisor. Every read and write is filtered by organization and user."""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import and_, func, insert, select, update

from . import db
from .need import Need


class NotFound(Exception):
    pass


class Conflict(Exception):
    pass


@dataclass(frozen=True)
class Owner:
    org_id: UUID
    user_id: UUID
    name: str = ""
    org_name: str = ""

    def where(self, table):
        return and_(table.c.org_id == self.org_id, table.c.user_id == self.user_id)

    def row(self):
        return {"org_id": self.org_id, "user_id": self.user_id}


def now():
    return datetime.now(UTC)


# ---------------------------------------------------------------- deals


def create_deal(conn, owner: Owner, title: str, customer_id=None):
    need = Need().dump()
    row = (
        conn.execute(
            insert(db.deals)
            .values(
                **owner.row(),
                title=title[:100] or "新客人",
                customer_id=customer_id,
                need=need,
                next_step="粘贴客人的第一句话",
            )
            .returning(db.deals)
        )
        .mappings()
        .one()
    )
    conn.execute(
        insert(db.need_versions).values(
            deal_id=row["id"], version=0, **owner.row(), body=need, reason="新建"
        )
    )
    return dict(row)


def deal(conn, owner: Owner, deal_id, *, lock=False):
    query = select(db.deals).where(owner.where(db.deals), db.deals.c.id == deal_id)
    if lock:
        query = query.with_for_update()
    row = conn.execute(query).mappings().one_or_none()
    if not row:
        raise NotFound("这一单不存在")
    return dict(row)


def deals(conn, owner: Owner, *, status=None, limit=100):
    query = (
        select(db.deals)
        .where(owner.where(db.deals))
        .order_by(db.deals.c.updated_at.desc())
        .limit(limit)
    )
    if status:
        query = query.where(db.deals.c.status == status)
    return [dict(r) for r in conn.execute(query).mappings()]


def update_deal(conn, owner: Owner, deal_id, **values):
    conn.execute(
        update(db.deals)
        .where(owner.where(db.deals), db.deals.c.id == deal_id)
        .values(**values, updated_at=now())
    )


def save_need(conn, owner: Owner, deal_row, need: Need, fields, reason, turn=None):
    version = deal_row["need_version"] + 1
    body = need.dump()
    conn.execute(
        insert(db.need_versions).values(
            deal_id=deal_row["id"],
            version=version,
            **owner.row(),
            body=body,
            fields=list(fields),
            reason=reason[:200],
            turn=turn,
        )
    )
    update_deal(conn, owner, deal_row["id"], need=body, need_version=version)
    deal_row.update(need=body, need_version=version)
    return version


def versions(conn, owner: Owner, deal_id):
    rows = conn.execute(
        select(db.need_versions)
        .where(owner.where(db.need_versions), db.need_versions.c.deal_id == deal_id)
        .order_by(db.need_versions.c.version)
    ).mappings()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- turns


def next_seq(conn, owner: Owner, deal_id):
    value = conn.execute(
        select(func.coalesce(func.max(db.turns.c.seq), 0)).where(
            owner.where(db.turns), db.turns.c.deal_id == deal_id
        )
    ).scalar()
    return int(value) + 1


def add_turn(conn, owner: Owner, deal_id, seq, kind, text, understanding=None, result=None):
    conn.execute(
        insert(db.turns).values(
            deal_id=deal_id,
            seq=seq,
            **owner.row(),
            kind=kind,
            text=text,
            understanding=understanding,
            result=result or {},
        )
    )


def set_turn_result(conn, owner: Owner, deal_id, seq, result):
    conn.execute(
        update(db.turns)
        .where(owner.where(db.turns), db.turns.c.deal_id == deal_id, db.turns.c.seq == seq)
        .values(result=result)
    )


def turns(conn, owner: Owner, deal_id, *, limit=60):
    rows = conn.execute(
        select(db.turns)
        .where(owner.where(db.turns), db.turns.c.deal_id == deal_id)
        .order_by(db.turns.c.seq.desc())
        .limit(limit)
    ).mappings()
    return list(reversed([dict(r) for r in rows]))


# ---------------------------------------------------------------- generic


def rows(conn, owner: Owner, table, deal_id=None, *, order=None, where=()):
    query = select(table).where(owner.where(table), *where)
    if deal_id is not None:
        query = query.where(table.c.deal_id == deal_id)
    if order is not None:
        query = query.order_by(order)
    return [dict(r) for r in conn.execute(query).mappings()]


def one(conn, owner: Owner, table, row_id):
    row = (
        conn.execute(select(table).where(owner.where(table), table.c.id == row_id))
        .mappings()
        .one_or_none()
    )
    if not row:
        raise NotFound("记录不存在")
    return dict(row)


def add(conn, owner: Owner, table, **values):
    return dict(
        conn.execute(insert(table).values(**owner.row(), **values).returning(table))
        .mappings()
        .one()
    )


def change(conn, owner: Owner, table, row_id, **values):
    result = (
        conn.execute(
            update(table)
            .where(owner.where(table), table.c.id == row_id)
            .values(**values, updated_at=now())
            .returning(table)
        )
        .mappings()
        .one_or_none()
    )
    if not result:
        raise NotFound("记录不存在")
    return dict(result)

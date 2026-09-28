"""The advisor's own notes on suppliers: which they use gladly, which with care, and why.

Supplier names reach the advisor only. Drafts and the customer's plan page never carry them.
"""

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from . import db

STANCES = {"": "", "preferred": "常用", "cautious": "慎用"}


def notes(conn, owner) -> dict:
    rows = conn.execute(select(db.supplier_notes).where(owner.where(db.supplier_notes)))
    return {r.supplier_id: dict(r._mapping) for r in rows}


def save(conn, owner, supplier_id: str, name: str, stance: str, note: str) -> dict:
    if stance not in STANCES:
        raise ValueError("只能标常用、慎用或不标")
    values = {"name": name.strip()[:40], "stance": stance, "note": note.strip()[:200]}
    row = (
        conn.execute(
            insert(db.supplier_notes)
            .values(**owner.row(), supplier_id=supplier_id, **values)
            .on_conflict_do_update(index_elements=["org_id", "user_id", "supplier_id"], set_=values)
            .returning(db.supplier_notes)
        )
        .mappings()
        .one()
    )
    return view(row["supplier_id"], row["name"], {row["supplier_id"]: row})


def view(supplier_id: str, name: str, notes: dict | None = None) -> dict:
    """A supplier as the advisor sees it; ``notes`` is the advisor's notes by supplier id."""
    note = (notes or {}).get(supplier_id) or {}
    stance = note.get("stance", "")
    return {
        "id": supplier_id,
        "name": name or note.get("name") or "供应商未标注",
        "stance": stance,
        "stance_text": STANCES.get(stance, ""),
        "note": note.get("note", ""),
    }

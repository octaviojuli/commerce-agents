"""What this deal remembers: concerns by how often they come up, things to avoid, how to talk
to the customer, the question book, and the statements the advisor has already sent."""

import re

from . import db, store

TOPIC_WORDS = {
    "行程节奏": r"累|赶|慢|节奏|车程|休息|体力|走路",
    "预算": r"预算|贵|便宜|价格|多少钱|费用",
    "购物自费": r"购物|自费|进店",
    "儿童": r"孩子|儿童|小孩|宝宝|占床",
    "老人": r"老人|长辈|阿姨|爸妈|妈妈|爸爸|岁数|70",
    "住宿": r"酒店|住|星|房间|挨着",
    "餐食": r"吃|餐|饭",
    "签证证件": r"签证|护照|证件|材料",
    "退改": r"退|取消|改期",
    "景点": r"景点|城堡|雪山|博物馆|门票|能进",
}


def topic_of(text: str, given: str = "") -> str:
    if given and given != "其他":
        return given
    return next((name for name, pattern in TOPIC_WORDS.items() if re.search(pattern, text)), "其他")


def _upsert(conn, owner, deal_id, kind, topic, text, seq):
    rows = store.rows(
        conn,
        owner,
        db.memory,
        deal_id,
        where=[db.memory.c.kind == kind, db.memory.c.topic == topic],
    )
    if kind == "concern" and rows:
        row = rows[0]
        sources = [*row["sources"], {"turn": seq, "text": text}]
        store.change(
            conn, owner, db.memory, row["id"], count=row["count"] + 1, sources=sources[-10:]
        )
        return
    if any(r["text"] == text for r in rows):
        return
    store.add(
        conn,
        owner,
        db.memory,
        deal_id=deal_id,
        kind=kind,
        topic=topic,
        text=text[:300],
        sources=[{"turn": seq, "text": text}],
    )


def remember(conn, owner, deal_id, understanding, message, seq):
    for c in understanding.concerns:
        text = c.text if c.text in message else c.text
        _upsert(conn, owner, deal_id, "concern", topic_of(text, c.topic), text, seq)
    for text in understanding.avoid:
        _upsert(conn, owner, deal_id, "avoid", "要避开", text, seq)
    for text in understanding.habits:
        if "称呼" in text:
            continue  # the salutation is kept once, on its own
        _upsert(conn, owner, deal_id, "habit", "沟通", text, seq)
    for q in understanding.questions:
        # A question asked again is also a concern being raised again.
        if q.topic in ("行程节奏", "老人", "儿童", "预算", "购物自费"):
            _upsert(conn, owner, deal_id, "concern", q.topic, q.text, seq)


def salutation(conn, owner, deal, name):
    name = name.strip()[:20]
    rows = store.rows(conn, owner, db.memory, deal["id"], where=[db.memory.c.kind == "salutation"])
    if rows:
        store.change(conn, owner, db.memory, rows[0]["id"], text=name)
    else:
        store.add(
            conn, owner, db.memory, deal_id=deal["id"], kind="salutation", topic="称呼", text=name
        )


def summary(conn, owner, deal_id) -> dict:
    rows = store.rows(conn, owner, db.memory, deal_id, order=db.memory.c.created_at)
    concerns = sorted(
        [r for r in rows if r["kind"] == "concern" and r["status"] == "active"],
        key=lambda r: -r["count"],
    )
    said = [r for r in rows if r["kind"] == "said"]
    return {
        "salutation": next((r["text"] for r in rows if r["kind"] == "salutation"), ""),
        "concerns": [
            {
                "id": str(r["id"]),
                "topic": r["topic"],
                "text": r["text"],
                "count": r["count"],
                "latest": r["sources"][-1]["text"] if r["sources"] else r["text"],
                "turns": [s.get("turn") for s in r["sources"]],
            }
            for r in concerns
        ],
        "avoid": [{"id": str(r["id"]), "text": r["text"]} for r in rows if r["kind"] == "avoid"],
        "habits": [{"id": str(r["id"]), "text": r["text"]} for r in rows if r["kind"] == "habit"],
        "said": [
            {
                "id": str(r["id"]),
                "text": r["text"],
                "status": r["status"],
                "product_id": r["product_id"],
                "at": r["created_at"].isoformat() if r["created_at"] else "",
            }
            for r in said
        ],
    }


def book(conn, owner, deal_id, answered, seq):
    """File each answered question under its route and topic, counting repeats."""
    for a in answered:
        product = a.get("product_id")
        topic = topic_of(a["question"], a.get("topic", ""))
        existing = [
            r
            for r in store.rows(conn, owner, db.qa, deal_id, where=[db.qa.c.topic == topic])
            if r["product_id"] == product
        ]
        status = {"fact": "answered", "advice": "advice", "unknown": "unknown"}[a["kind"]]
        if existing:
            row = existing[0]
            turns = [*row["turns"], {"turn": seq, "question": a["question"]}]
            store.change(
                conn,
                owner,
                db.qa,
                row["id"],
                question=a["question"],
                answer=a["answer"],
                facts=a["facts"],
                status=status,
                turns=turns[-20:],
            )
        else:
            store.add(
                conn,
                owner,
                db.qa,
                deal_id=deal_id,
                product_id=product,
                topic=topic,
                question=a["question"],
                answer=a["answer"],
                facts=a["facts"],
                status=status,
                turns=[{"turn": seq, "question": a["question"]}],
            )


def qa_book(conn, owner, deal_id):
    rows = store.rows(conn, owner, db.qa, deal_id, order=db.qa.c.updated_at.desc())
    out = []
    for r in rows:
        out.append(
            {
                "id": str(r["id"]),
                "product_id": r["product_id"],
                "topic": r["topic"],
                "question": r["question"],
                "answer": r["answer"],
                "facts": r["facts"],
                "status": r["status"],
                "count": len(r["turns"]),
                "critical": len(r["turns"]) >= 3,
                "questions": list(dict.fromkeys(t["question"] for t in r["turns"])),
                "updated_at": r["updated_at"].isoformat(),
            }
        )
    return out


def mark_sent(conn, owner, deal_id, text, claims, product_id=None):
    """The advisor sent this draft: its factual claims become statements to keep true."""
    for claim in claims:
        if not claim.get("fact_id") or str(claim["fact_id"]).startswith(("need:", "said:", "dir:")):
            continue
        store.add(
            conn,
            owner,
            db.memory,
            deal_id=deal_id,
            kind="said",
            topic="我说过的话",
            text=claim["text"][:300],
            product_id=product_id,
            facts=[claim["fact_id"]],
        )


async def recheck_said(engine, owner, wh, deal_id, route_facts):
    """Mark sent statements whose facts are no longer in the current published itinerary."""
    from .facts import Route
    from .routes import document

    with engine.connect() as conn:
        rows = store.rows(conn, owner, db.memory, deal_id, where=[db.memory.c.kind == "said"])
    changed = []
    by_product = {}
    for r in rows:
        if not r["product_id"] or str(r["facts"][0]).startswith("price:"):
            continue
        if r["product_id"] not in by_product:
            doc = await document(wh, r["product_id"])
            by_product[r["product_id"]] = {
                f["fact_id"] for f in Route(r["product_id"], "", (doc or {}).get("body")).facts()
            }
        if r["facts"] and r["facts"][0] not in by_product[r["product_id"]]:
            changed.append(r)
    if changed:
        with engine.begin() as conn:
            for r in changed:
                store.change(conn, owner, db.memory, r["id"], status="outdated")
    return len(changed)

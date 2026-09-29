"""The advisor's own records. The warehouse keeps the catalog; this keeps the deals.

Every row carries the warehouse organization and user it belongs to, and every query in
``store`` filters by both. Nothing here is shared between advisors.
"""

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

SCHEMA_VERSION = 4
metadata = MetaData(schema="advisor")
J = JSON().with_variant(JSONB(), "postgresql")


def _id():
    return Column(
        "id", UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )


def _owner():
    return [
        Column("org_id", UUID(as_uuid=True), nullable=False, index=True),
        Column("user_id", UUID(as_uuid=True), nullable=False, index=True),
    ]


def _stamps():
    return [
        Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
        Column(
            "updated_at",
            DateTime(timezone=True),
            server_default=func.now(),
            onupdate=func.now(),
            nullable=False,
        ),
    ]


sessions = Table(
    "session",
    metadata,
    Column("id", String(64), primary_key=True),
    *_owner(),
    Column("advisor_name", String(100), nullable=False, server_default=""),
    Column("org_name", String(200), nullable=False, server_default=""),
    Column("warehouse_token", Text, nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    # When the warehouse last confirmed this login and its advisor role.
    Column("checked_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
)

customers = Table(
    "customer",
    metadata,
    _id(),
    *_owner(),
    Column("name", String(100), nullable=False),
    Column("salutation", String(40), nullable=False, server_default=""),
    Column("contact", String(200), nullable=False, server_default=""),
    Column("profile", J, nullable=False, server_default=text("'{}'")),
    Column("travelers", J, nullable=False, server_default=text("'[]'")),
    *_stamps(),
)

deals = Table(
    "deal",
    metadata,
    _id(),
    *_owner(),
    Column("customer_id", UUID(as_uuid=True), nullable=True),
    Column("title", String(100), nullable=False),
    Column("status", String(20), nullable=False, server_default="open"),
    Column("need_version", Integer, nullable=False, server_default="0"),
    Column("need", J, nullable=False, server_default=text("'{}'")),
    Column("query_context", J, nullable=False, server_default=text("'{}'")),
    Column("route", J, nullable=True),
    Column("departure", J, nullable=True),
    Column("waiting_reply", Boolean, nullable=False, server_default="false"),
    Column("last_customer_at", DateTime(timezone=True), nullable=True),
    Column("next_step", String(120), nullable=False, server_default=""),
    *_stamps(),
)

need_versions = Table(
    "need_version",
    metadata,
    Column("deal_id", UUID(as_uuid=True), primary_key=True),
    Column("version", Integer, primary_key=True),
    *_owner(),
    Column("body", J, nullable=False),
    Column("fields", J, nullable=False, server_default=text("'[]'")),
    Column("reason", String(200), nullable=False, server_default=""),
    Column("turn", Integer, nullable=True),
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
)

proposals = Table(
    "proposal",
    metadata,
    _id(),
    *_owner(),
    Column("deal_id", UUID(as_uuid=True), nullable=False, index=True),
    Column("base_version", Integer, nullable=False),
    Column("turn", Integer, nullable=True),
    Column("items", J, nullable=False),
    Column("impact", J, nullable=False, server_default=text("'[]'")),
    *_stamps(),
)

turns = Table(
    "turn",
    metadata,
    Column("deal_id", UUID(as_uuid=True), primary_key=True),
    Column("seq", Integer, primary_key=True),
    *_owner(),
    Column("kind", String(20), nullable=False),
    Column("text", Text, nullable=False),
    Column("understanding", J, nullable=True),
    Column("result", J, nullable=False, server_default=text("'{}'")),
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
)

supplier_notes = Table(
    "supplier_note",
    metadata,
    _id(),
    *_owner(),
    # The advisor's own view of a supplier: stays with this advisor, never reaches the warehouse.
    Column("supplier_id", String(80), nullable=False),
    Column("name", String(40), nullable=False, server_default=""),
    Column("stance", String(20), nullable=False, server_default=""),
    Column("note", String(200), nullable=False, server_default=""),
    *_stamps(),
    UniqueConstraint("org_id", "user_id", "supplier_id"),
)

memory = Table(
    "memory",
    metadata,
    _id(),
    *_owner(),
    Column("deal_id", UUID(as_uuid=True), nullable=False, index=True),
    Column("kind", String(20), nullable=False),
    Column("topic", String(40), nullable=False, server_default=""),
    Column("text", String(300), nullable=False),
    Column("count", Integer, nullable=False, server_default="1"),
    Column("sources", J, nullable=False, server_default=text("'[]'")),
    Column("status", String(20), nullable=False, server_default="active"),
    Column("product_id", String(80), nullable=True),
    Column("facts", J, nullable=False, server_default=text("'[]'")),
    *_stamps(),
)

qa = Table(
    "qa",
    metadata,
    _id(),
    *_owner(),
    Column("deal_id", UUID(as_uuid=True), nullable=False, index=True),
    Column("product_id", String(80), nullable=True),
    Column("topic", String(40), nullable=False),
    Column("question", String(300), nullable=False),
    Column("answer", Text, nullable=False, server_default=""),
    Column("facts", J, nullable=False, server_default=text("'[]'")),
    Column("status", String(20), nullable=False),
    Column("turns", J, nullable=False, server_default=text("'[]'")),
    *_stamps(),
)

notes = Table(
    "note",
    metadata,
    _id(),
    *_owner(),
    Column("deal_id", UUID(as_uuid=True), nullable=False, index=True),
    Column("product_id", String(80), nullable=False),
    Column("day", Integer, nullable=True),
    Column("node", String(200), nullable=False, server_default=""),
    Column("category", String(20), nullable=False),
    Column("text", String(500), nullable=False),
    Column("amount", Numeric(12, 2), nullable=True),
    Column("currency", String(3), nullable=False, server_default="CNY"),
    Column("quantity", Integer, nullable=False, server_default="1"),
    *_stamps(),
)

searches = Table(
    "search",
    metadata,
    _id(),
    *_owner(),
    Column("deal_id", UUID(as_uuid=True), nullable=False, index=True),
    Column("need_version", Integer, nullable=False),
    Column("body", J, nullable=False),
    *_stamps(),
)

plans = Table(
    "plan",
    metadata,
    _id(),
    *_owner(),
    Column("deal_id", UUID(as_uuid=True), nullable=False, index=True),
    Column("version", Integer, nullable=False),
    Column("need_version", Integer, nullable=False),
    Column("body", J, nullable=False),
    Column("status", String(20), nullable=False, server_default="draft"),
    Column("token_hash", String(64), nullable=True, unique=True),
    Column("sent_at", DateTime(timezone=True), nullable=True),
    Column("views", Integer, nullable=False, server_default="0"),
    Column("last_view_at", DateTime(timezone=True), nullable=True),
    Column("signals", J, nullable=False, server_default=text("'[]'")),
    *_stamps(),
    UniqueConstraint("deal_id", "version"),
)

confirmations = Table(
    "confirmation",
    metadata,
    _id(),
    *_owner(),
    Column("deal_id", UUID(as_uuid=True), nullable=False, index=True),
    Column("need_version", Integer, nullable=False),
    # A sheet is for one route, departure and offer; changing any of them ends it.
    Column("product_id", String(80), nullable=False, server_default=""),
    Column("departure_id", String(80), nullable=False),
    Column("offer_id", String(80), nullable=False, server_default=""),
    Column("items", J, nullable=False),
    Column("status", String(20), nullable=False, server_default="open"),
    Column("evidence", Text, nullable=False, server_default=""),
    *_stamps(),
)

quotes = Table(
    "quote",
    metadata,
    _id(),
    *_owner(),
    Column("deal_id", UUID(as_uuid=True), nullable=False, index=True),
    Column("need_version", Integer, nullable=False),
    Column("departure_id", String(80), nullable=False),
    Column("offer_id", String(80), nullable=True),
    Column("warehouse_quote_id", String(80), nullable=True),
    Column("snapshot", J, nullable=False),
    Column("kind", String(20), nullable=False, server_default="check"),
    Column("sales_total", Numeric(14, 2), nullable=True),
    Column("settlement_total", Numeric(14, 2), nullable=True),
    Column("currency", String(3), nullable=False, server_default="CNY"),
    Column("extras", J, nullable=False, server_default=text("'[]'")),
    Column("valid_until", DateTime(timezone=True), nullable=True),
    Column("status", String(20), nullable=False, server_default="active"),
    Column("void_reason", String(200), nullable=False, server_default=""),
    *_stamps(),
)

ledger = Table(
    "ledger",
    metadata,
    _id(),
    *_owner(),
    Column("deal_id", UUID(as_uuid=True), nullable=False, index=True),
    Column("kind", String(20), nullable=False),
    Column("amount", Numeric(14, 2), nullable=False),
    Column("currency", String(3), nullable=False, server_default="CNY"),
    Column("note", String(300), nullable=False, server_default=""),
    Column("occurred_on", Date, nullable=False),
    # The client's key for one money entry: a retried request finds the entry it already made.
    Column("op_key", String(80)),
    *_stamps(),
)

documents = Table(
    "document",
    metadata,
    _id(),
    *_owner(),
    Column("deal_id", UUID(as_uuid=True), nullable=False, index=True),
    Column("traveler", Integer, nullable=True),
    Column("filename", String(200), nullable=False),
    Column("media_type", String(80), nullable=False),
    Column("path", String(300), nullable=False),
    Column("recognized", Text, nullable=True),
    Column("status", String(20), nullable=False, server_default="review"),
    *_stamps(),
)

tasks = Table(
    "task",
    metadata,
    _id(),
    *_owner(),
    Column("deal_id", UUID(as_uuid=True), nullable=False, index=True),
    Column("kind", String(30), nullable=False),
    Column("text", String(300), nullable=False),
    Column("due_at", DateTime(timezone=True), nullable=True),
    Column("status", String(20), nullable=False, server_default="open"),
    Column("amount", Numeric(14, 2), nullable=True),
    *_stamps(),
)

versions = Table(
    "schema_version",
    metadata,
    Column("version", BigInteger, primary_key=True),
    Column("applied_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
)


def connect(url: str):
    return create_engine(url, pool_pre_ping=True, future=True)


def migrate(engine):
    """Create the schema once; later versions add explicit steps here."""
    with engine.begin() as conn:
        conn.execute(text("CREATE SCHEMA IF NOT EXISTS advisor"))
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
    metadata.create_all(engine)
    with engine.begin() as conn:
        current = conn.execute(text("SELECT max(version) FROM advisor.schema_version")).scalar()
        if current is None:
            conn.execute(versions.insert().values(version=SCHEMA_VERSION))
            current = SCHEMA_VERSION
        for version, steps in sorted(STEPS.items()):
            if current < version:
                for step in steps:
                    conn.execute(text(step))
                conn.execute(versions.insert().values(version=version))
        doubled = conn.execute(
            text(
                "SELECT deal_id FROM advisor.ledger WHERE kind = 'sale'"
                " GROUP BY deal_id HAVING count(*) > 1"
            )
        ).scalars()
        doubled = [str(d) for d in doubled]
        if doubled:
            # Money rows are never removed here: someone decides which sale stands.
            raise RuntimeError("这些客户单有重复成交记录，请先核对再升级：" + "、".join(doubled))
        for statement in INDEXES:
            conn.execute(text(statement))


STEPS = {
    4: [
        "ALTER TABLE advisor.deal ADD COLUMN IF NOT EXISTS query_context jsonb"
        " NOT NULL DEFAULT '{}'::jsonb",
    ],
    2: [
        "ALTER TABLE advisor.session ADD COLUMN IF NOT EXISTS checked_at timestamptz"
        " NOT NULL DEFAULT now()",
        "ALTER TABLE advisor.ledger ADD COLUMN IF NOT EXISTS op_key varchar(80)",
    ],
    3: [
        "ALTER TABLE advisor.confirmation ADD COLUMN IF NOT EXISTS product_id varchar(80)"
        " NOT NULL DEFAULT ''",
        "ALTER TABLE advisor.confirmation ADD COLUMN IF NOT EXISTS offer_id varchar(80)"
        " NOT NULL DEFAULT ''",
        # Sheets made before the binding cannot say which route they were for: they end here.
        "UPDATE advisor.confirmation SET status = 'void' WHERE product_id = '' AND status <> 'void'",
    ],
}
INDEXES = [
    # A deal is sold once; later changes are adjustments, not a second sale.
    "CREATE UNIQUE INDEX IF NOT EXISTS ledger_one_sale ON advisor.ledger (deal_id)"
    " WHERE kind = 'sale'",
    "CREATE UNIQUE INDEX IF NOT EXISTS ledger_op_key ON advisor.ledger (org_id, user_id, op_key)"
    " WHERE op_key IS NOT NULL",
]

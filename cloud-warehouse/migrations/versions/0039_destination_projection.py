"""Versioned, source-labelled destination fields on the existing RLS search projection."""

from alembic import op

revision = "0039_destination_projection"
down_revision = "0038_trip_brief"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    ALTER TABLE product_search ADD destination_document text NOT NULL DEFAULT '';
    ALTER TABLE product_search ADD destination_facts jsonb NOT NULL DEFAULT '{}';
    ALTER TABLE product_search ADD destination_rule_version integer NOT NULL DEFAULT 0;
    ALTER TABLE product_search ADD content_version integer NOT NULL DEFAULT 0;
    ALTER TABLE product_search ADD publication_id uuid;
    ALTER TABLE product_search ADD CHECK(jsonb_typeof(destination_facts)='object');
    """)


def downgrade():
    raise RuntimeError("Restore a verified backup to retain destination provenance")

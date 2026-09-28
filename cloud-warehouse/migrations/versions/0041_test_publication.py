"""Distinguish explicitly enabled test publications from human review."""

from alembic import op

revision = "0041_test_publication"
down_revision = "0040_route_kit"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""ALTER TABLE document_publication
      ADD review_mode text NOT NULL DEFAULT 'human' CHECK(review_mode IN ('human','test_auto')),
      ADD quality_metrics jsonb NOT NULL DEFAULT '{}' CHECK(jsonb_typeof(quality_metrics)='object');""")


def downgrade():
    raise RuntimeError("Test publication provenance must be retained")

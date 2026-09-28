"""Keep immutable parse generations for one private source file."""

from alembic import op

revision = "0028_document_reparses"
down_revision = "0027_departure_quarantine"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    ALTER TABLE document_asset ADD parse_generation integer NOT NULL DEFAULT 1
      CHECK(parse_generation>0);
    ALTER TABLE document_parse ADD generation integer NOT NULL DEFAULT 1 CHECK(generation>0);
    ALTER TABLE document_parse DROP CONSTRAINT document_parse_asset_id_key;
    ALTER TABLE document_parse ADD UNIQUE(asset_id,generation);
    """)


def downgrade():
    raise RuntimeError("Restore a verified backup rather than deleting parse history")

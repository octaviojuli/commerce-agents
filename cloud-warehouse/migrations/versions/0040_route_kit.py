"""Content-bound route-kit review resolutions alongside immutable revisions."""

from alembic import op

revision = "0040_route_kit"
down_revision = "0039_destination_projection"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""ALTER TABLE route_content_revision ADD review_resolutions jsonb NOT NULL DEFAULT '[]';
    ALTER TABLE route_content_revision ADD CHECK(jsonb_typeof(review_resolutions)='array');
    ALTER TABLE product_search ADD content_facts jsonb NOT NULL DEFAULT '{}', ADD content_rule_version integer NOT NULL DEFAULT 0;
    CREATE TABLE document_media (
      id uuid PRIMARY KEY, parse_id uuid NOT NULL, asset_id uuid NOT NULL,
      supplier_org_id uuid NOT NULL, connection_id uuid NOT NULL, product_id uuid NOT NULL,
      kind text NOT NULL CHECK(kind='cover'), file_hash text NOT NULL,
      byte_size integer NOT NULL CHECK(byte_size BETWEEN 1 AND 4000000),
      created_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE(parse_id,kind),
      FOREIGN KEY(parse_id,asset_id,supplier_org_id,connection_id,product_id)
        REFERENCES document_parse(id,asset_id,supplier_org_id,connection_id,product_id)
    );
    ALTER TABLE document_media ENABLE ROW LEVEL SECURITY;
    ALTER TABLE document_media FORCE ROW LEVEL SECURITY;
    CREATE POLICY media_owner ON document_media FOR SELECT USING(
      warehouse_has_org(supplier_org_id) AND warehouse_has_role(ARRAY['supplier_admin','product_editor','auditor','sync_worker']));
    CREATE POLICY media_write ON document_media FOR INSERT WITH CHECK(
      warehouse_has_org(supplier_org_id) AND warehouse_has_role(ARRAY['sync_worker']));
    CREATE POLICY media_buyer ON document_media FOR SELECT USING(
      warehouse_catalog_access(supplier_org_id,connection_id) AND EXISTS(
        SELECT 1 FROM supplier_product p JOIN document_publication pub ON pub.id=p.published_document_id
        WHERE p.id=document_media.product_id AND p.status='published'
        AND pub.parse_id=document_media.parse_id AND (pub.content_version>0 OR pub.product_version=p.version)));
    """)


def downgrade():
    raise RuntimeError("Restore a verified backup to retain route content review history")

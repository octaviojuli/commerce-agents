"""Persistent route drafts, independent content versions and approved tags."""

from alembic import op
from sqlalchemy import text

revision = "0033_route_content"
down_revision = "0032_source_cooldowns"
branch_labels = None
depends_on = None


def upgrade():
    original = (
        op.get_bind()
        .execute(
            text(
                "SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name='supplier_product' ORDER BY ordinal_position"
            )
        )
        .scalars()
        .all()
    )
    op.execute("""
    ALTER TABLE supplier_product ADD route_draft_version integer NOT NULL DEFAULT 0 CHECK(route_draft_version>=0);
    ALTER TABLE supplier_product ADD content_version integer NOT NULL DEFAULT 0 CHECK(content_version>=0);
    ALTER TABLE supplier_product ADD latest_route_revision_id uuid;
    ALTER TABLE supplier_product ADD approved_tags jsonb NOT NULL DEFAULT '[]'::jsonb CHECK(jsonb_typeof(approved_tags)='array');
    ALTER TABLE supplier_product ADD tags_search text NOT NULL DEFAULT '';
    CREATE TABLE route_content_revision (
      id uuid PRIMARY KEY, supplier_org_id uuid NOT NULL, connection_id uuid NOT NULL,
      product_id uuid NOT NULL, revision integer NOT NULL CHECK(revision>0),
      parent_id uuid REFERENCES route_content_revision(id),
      base_publication_id uuid REFERENCES document_publication(id),
      source_kind text NOT NULL CHECK(source_kind IN ('document','manual')),
      source_parse_id uuid REFERENCES document_parse(id), source_content_hash text NOT NULL,
      source_note text NOT NULL, body jsonb NOT NULL, body_hash text NOT NULL,
      created_by uuid NOT NULL REFERENCES warehouse_user(id), created_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE(product_id,revision), UNIQUE(id,product_id),
      FOREIGN KEY(product_id,supplier_org_id,connection_id) REFERENCES supplier_product(id,supplier_org_id,connection_id),
      CHECK((source_kind='document')=(source_parse_id IS NOT NULL))
    );
    ALTER TABLE route_content_revision ENABLE ROW LEVEL SECURITY;
    ALTER TABLE route_content_revision FORCE ROW LEVEL SECURITY;
    CREATE POLICY route_content_revision_read ON route_content_revision FOR SELECT USING(
      warehouse_has_org(supplier_org_id) AND warehouse_has_role(ARRAY['supplier_admin','product_editor','auditor']));
    CREATE POLICY route_content_revision_insert ON route_content_revision FOR INSERT WITH CHECK(
      warehouse_has_org(supplier_org_id) AND warehouse_has_role(ARRAY['supplier_admin','product_editor']));
    ALTER TABLE supplier_product ADD FOREIGN KEY(latest_route_revision_id,id) REFERENCES route_content_revision(id,product_id);
    ALTER TABLE document_publication ALTER COLUMN asset_id DROP NOT NULL;
    ALTER TABLE document_publication ALTER COLUMN parse_id DROP NOT NULL;
    ALTER TABLE document_publication ADD source_kind text NOT NULL DEFAULT 'document';
    ALTER TABLE document_publication ADD source_note text NOT NULL DEFAULT '';
    ALTER TABLE document_publication ADD content_version integer NOT NULL DEFAULT 0;
    ALTER TABLE document_publication ADD source_content_hash text;
    ALTER TABLE document_publication ADD revision_id uuid REFERENCES route_content_revision(id);
    ALTER TABLE document_publication ADD CHECK(
      (source_kind='document' AND asset_id IS NOT NULL AND parse_id IS NOT NULL) OR
      (source_kind='manual' AND asset_id IS NULL AND parse_id IS NULL AND length(btrim(source_note))>0));
    CREATE UNIQUE INDEX document_content_version ON document_publication(product_id,content_version) WHERE content_version>0;
    CREATE OR REPLACE FUNCTION warehouse_change_write(org uuid,change_kind text) RETURNS boolean
      LANGUAGE sql STABLE AS $$ SELECT warehouse_has_org(org) AND (
        warehouse_has_role(ARRAY['supplier_admin']) OR
        (change_kind IN ('inventory','excel_import','merchant') AND warehouse_has_role(ARRAY['inventory_manager'])) OR
        (change_kind IN ('route_content','route_tags','product_display','pricing','price_book','offer','catalog','merchant','document','departure_sales') AND warehouse_has_role(ARRAY['product_editor']))) $$;
    """)
    # Append new view columns; replacing p.* in place would rename existing view
    # columns and fail. Security-invoker semantics and all existing columns remain.
    columns = ",".join('p."' + name + '"' for name in original)
    op.execute(f"""CREATE OR REPLACE VIEW product_listing WITH (security_invoker=true) AS SELECT {columns},
      COALESCE(name_override,name) AS effective_name,COALESCE(description_override,description) AS effective_description,
      CASE WHEN name_override IS NULL THEN 'source' ELSE 'warehouse_display' END AS name_origin,
      CASE WHEN description_override IS NULL THEN 'source' ELSE 'warehouse_display' END AS description_origin,
      route_draft_version,content_version,latest_route_revision_id,approved_tags,tags_search FROM supplier_product p""")


def downgrade():
    raise RuntimeError("Restore a verified backup to retain route revisions and reviews")

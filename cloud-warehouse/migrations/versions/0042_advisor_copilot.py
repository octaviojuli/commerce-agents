"""Private advisor CRM, immutable deal history and explicitly shared supplier inquiries."""

from alembic import op

revision = "0042_advisor_copilot"
down_revision = "0041_test_publication"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE FUNCTION warehouse_advisor_owner(org uuid, actor uuid) RETURNS boolean
      LANGUAGE sql STABLE SECURITY INVOKER AS $$
      SELECT warehouse_has_org(org) AND actor=warehouse_user_id()
        AND warehouse_has_role(ARRAY['advisor','buyer_admin'])
    $$;
    REVOKE ALL ON FUNCTION warehouse_advisor_owner(uuid,uuid) FROM PUBLIC;
    CREATE TABLE advisor_customer (
      id uuid PRIMARY KEY, organization_id uuid NOT NULL REFERENCES organization(id),
      user_id uuid NOT NULL REFERENCES warehouse_user(id), body jsonb NOT NULL,
      version integer NOT NULL DEFAULT 1 CHECK(version>0), request_id uuid NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE(id,organization_id,user_id), UNIQUE(organization_id,user_id,request_id)
    );
    CREATE TABLE advisor_deal (
      id uuid PRIMARY KEY, organization_id uuid NOT NULL, user_id uuid NOT NULL,
      customer_id uuid, title text NOT NULL DEFAULT '',
      created_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE(id,organization_id,user_id),
      FOREIGN KEY(id,organization_id,user_id) REFERENCES agent_conversation(id,organization_id,user_id),
      FOREIGN KEY(customer_id,organization_id,user_id) REFERENCES advisor_customer(id,organization_id,user_id)
    );
    CREATE TABLE advisor_record (
      id uuid PRIMARY KEY, deal_id uuid NOT NULL, organization_id uuid NOT NULL, user_id uuid NOT NULL,
      kind text NOT NULL CHECK(kind IN ('state','proposal','adoption','memory','qa','note','plan',
        'confirmation','retail_quote','sale','receipt','task','task_done','inquiry_adoption','material_review')),
      brief_version integer NOT NULL CHECK(brief_version>=0), body jsonb NOT NULL,
      request_key text NOT NULL CHECK(length(request_key) BETWEEN 1 AND 160), request_hash text NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE(organization_id,user_id,request_key),
      FOREIGN KEY(deal_id,organization_id,user_id) REFERENCES advisor_deal(id,organization_id,user_id)
    );
    CREATE INDEX advisor_record_deal ON advisor_record(deal_id,created_at,id);
    CREATE TABLE advisor_asset (
      id uuid PRIMARY KEY, deal_id uuid NOT NULL, organization_id uuid NOT NULL, user_id uuid NOT NULL,
      filename text NOT NULL, media_type text NOT NULL, file_hash text NOT NULL,
      byte_size bigint NOT NULL CHECK(byte_size>0 AND byte_size<=20971520),
      request_id uuid NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE(organization_id,user_id,request_id),
      FOREIGN KEY(deal_id,organization_id,user_id) REFERENCES advisor_deal(id,organization_id,user_id)
    );
    """)
    for table in ("advisor_customer", "advisor_deal", "advisor_record", "advisor_asset"):
        op.execute(f"""
        ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
        ALTER TABLE {table} FORCE ROW LEVEL SECURITY;
        CREATE POLICY owner_read ON {table} FOR SELECT
          USING(warehouse_advisor_owner(organization_id,user_id));
        CREATE POLICY owner_insert ON {table} FOR INSERT
          WITH CHECK(warehouse_advisor_owner(organization_id,user_id));
        """)
    op.execute("""
    CREATE POLICY owner_update ON advisor_customer FOR UPDATE
      USING(warehouse_advisor_owner(organization_id,user_id))
      WITH CHECK(warehouse_advisor_owner(organization_id,user_id));
    CREATE TABLE supplier_inquiry (
      id uuid PRIMARY KEY, deal_id uuid NOT NULL, organization_id uuid NOT NULL, user_id uuid NOT NULL,
      supplier_org_id uuid NOT NULL REFERENCES organization(id),
      connection_id uuid NOT NULL REFERENCES supplier_connection(id),
      product_id uuid NOT NULL REFERENCES supplier_product(id),
      brief_version integer NOT NULL, question text NOT NULL CHECK(length(question) BETWEEN 1 AND 2000),
      context jsonb NOT NULL, request_id uuid NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE(organization_id,user_id,request_id),
      FOREIGN KEY(deal_id,organization_id,user_id) REFERENCES advisor_deal(id,organization_id,user_id)
    );
    CREATE INDEX supplier_inquiry_inbox ON supplier_inquiry(supplier_org_id,created_at,id);
    ALTER TABLE supplier_inquiry ENABLE ROW LEVEL SECURITY;
    ALTER TABLE supplier_inquiry FORCE ROW LEVEL SECURITY;
    CREATE POLICY inquiry_read ON supplier_inquiry FOR SELECT USING(
      (warehouse_advisor_owner(organization_id,user_id)
        AND warehouse_catalog_access(supplier_org_id,connection_id))
      OR (warehouse_has_org(supplier_org_id)
        AND warehouse_has_role(ARRAY['supplier_admin','product_editor','auditor'])));
    CREATE POLICY inquiry_insert ON supplier_inquiry FOR INSERT WITH CHECK(
      warehouse_advisor_owner(organization_id,user_id)
      AND EXISTS(SELECT 1 FROM supplier_product p WHERE p.id=product_id
        AND p.connection_id=supplier_inquiry.connection_id
        AND p.supplier_org_id=supplier_inquiry.supplier_org_id)
      AND warehouse_catalog_access(supplier_org_id,connection_id));
    CREATE TABLE supplier_inquiry_reply (
      id uuid PRIMARY KEY, inquiry_id uuid NOT NULL REFERENCES supplier_inquiry(id),
      supplier_org_id uuid NOT NULL REFERENCES organization(id),
      author_id uuid NOT NULL REFERENCES warehouse_user(id),
      answer text NOT NULL CHECK(length(answer) BETWEEN 1 AND 4000), request_id uuid NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE(supplier_org_id,author_id,request_id)
    );
    ALTER TABLE supplier_inquiry_reply ENABLE ROW LEVEL SECURITY;
    ALTER TABLE supplier_inquiry_reply FORCE ROW LEVEL SECURITY;
    CREATE POLICY reply_read ON supplier_inquiry_reply FOR SELECT USING(
      EXISTS(SELECT 1 FROM supplier_inquiry i WHERE i.id=inquiry_id));
    CREATE POLICY reply_insert ON supplier_inquiry_reply FOR INSERT WITH CHECK(
      warehouse_has_org(supplier_org_id) AND author_id=warehouse_user_id()
      AND warehouse_has_role(ARRAY['supplier_admin','product_editor'])
      AND EXISTS(SELECT 1 FROM supplier_inquiry i WHERE i.id=inquiry_id
        AND i.supplier_org_id=supplier_inquiry_reply.supplier_org_id));
    ALTER TABLE quote_share ADD COLUMN advisor_record_id uuid REFERENCES advisor_record(id);
    """)


def downgrade():
    raise RuntimeError("Restore a verified backup rather than deleting advisor customer records")

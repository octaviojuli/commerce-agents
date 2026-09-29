"""Owner-only customer association and minimum supplier adoption receipts."""

from alembic import op

revision = "0043_copilot_handoffs"
down_revision = "0042_advisor_copilot"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE POLICY owner_update ON advisor_deal FOR UPDATE
      USING(warehouse_advisor_owner(organization_id,user_id))
      WITH CHECK(warehouse_advisor_owner(organization_id,user_id));
    CREATE TABLE supplier_inquiry_ack (
      id uuid PRIMARY KEY, inquiry_id uuid NOT NULL REFERENCES supplier_inquiry(id),
      reply_id uuid NOT NULL REFERENCES supplier_inquiry_reply(id),
      organization_id uuid NOT NULL REFERENCES organization(id), user_id uuid NOT NULL REFERENCES warehouse_user(id),
      brief_version integer NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE(inquiry_id,reply_id,organization_id,user_id,brief_version)
    );
    ALTER TABLE supplier_inquiry_ack ENABLE ROW LEVEL SECURITY;
    ALTER TABLE supplier_inquiry_ack FORCE ROW LEVEL SECURITY;
    CREATE POLICY ack_read ON supplier_inquiry_ack FOR SELECT USING(
      EXISTS(SELECT 1 FROM supplier_inquiry i WHERE i.id=inquiry_id));
    CREATE POLICY ack_insert ON supplier_inquiry_ack FOR INSERT WITH CHECK(
      warehouse_advisor_owner(organization_id,user_id) AND EXISTS(
        SELECT 1 FROM supplier_inquiry i JOIN supplier_inquiry_reply r ON r.inquiry_id=i.id
        WHERE i.id=supplier_inquiry_ack.inquiry_id AND r.id=reply_id
          AND i.organization_id=warehouse_org_id() AND i.user_id=warehouse_user_id()
          AND i.brief_version=supplier_inquiry_ack.brief_version));
    """)


def downgrade():
    raise RuntimeError("Restore a verified backup rather than deleting adoption receipts")

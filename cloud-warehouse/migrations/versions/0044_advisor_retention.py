"""Bound advisor material retention to a known departure; keep purged metadata auditable."""

from alembic import op

revision = "0044_advisor_retention"
down_revision = "0043_copilot_handoffs"
branch_labels = depends_on = None


def upgrade():
    op.execute(
        "ALTER TABLE advisor_asset ADD COLUMN retain_until timestamptz, ADD COLUMN purged_at timestamptz, ADD COLUMN erased_at timestamptz"
    )
    op.execute("UPDATE advisor_asset SET retain_until=created_at+interval '90 days'")
    op.execute("ALTER TABLE advisor_asset ALTER COLUMN retain_until SET NOT NULL")
    op.execute("CREATE INDEX advisor_asset_retention ON advisor_asset(retain_until)")
    op.execute(
        "CREATE POLICY advisor_asset_extend ON advisor_asset FOR UPDATE USING (warehouse_advisor_owner(organization_id,user_id)) WITH CHECK (warehouse_advisor_owner(organization_id,user_id))"
    )


def downgrade():
    raise RuntimeError("Retention audit metadata must not be discarded")

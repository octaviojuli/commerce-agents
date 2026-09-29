"""Revocable platform sessions; ERP credentials remain separate from platform identity."""

from alembic import op

revision = "0004_auth"
down_revision = "0003_managed_inventory"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE auth_session (
          token_hash text PRIMARY KEY, user_id uuid NOT NULL REFERENCES warehouse_user(id),
          created_at timestamptz NOT NULL DEFAULT now(), expires_at timestamptz NOT NULL,
          revoked_at timestamptz, CHECK(expires_at>created_at)
        );
        CREATE INDEX auth_session_by_user ON auth_session(user_id,expires_at);
        CREATE TABLE auth_rate_limit (
          bucket_hash text PRIMARY KEY, attempts integer NOT NULL CHECK(attempts>0),
          window_start timestamptz NOT NULL DEFAULT now()
        );
    """)


def downgrade() -> None:
    op.execute("DROP TABLE auth_rate_limit; DROP TABLE auth_session")

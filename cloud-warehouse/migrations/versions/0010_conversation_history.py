"""Indexes for owner-scoped conversation lists and chronological transcript windows."""

from alembic import op

revision = "0010_conversation_history"
down_revision = "0009_sync_jobs"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE INDEX conversations_created
      ON agent_conversation(organization_id,user_id,role,created_at DESC,id DESC);
    CREATE INDEX conversation_turn_history
      ON agent_turn(conversation_id,started_at DESC,id DESC);
    """)


def downgrade():
    op.execute("DROP INDEX conversation_turn_history; DROP INDEX conversations_created;")

"""Share supplier-directed cooldowns across processes and explicitly grouped connections."""

from alembic import op

revision = "0032_source_cooldowns"
down_revision = "0031_authorization_plans"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE source_cooldown (
      group_id uuid PRIMARY KEY,
      not_before timestamptz NOT NULL DEFAULT '-infinity',
      requires_review boolean NOT NULL DEFAULT false
    );
    CREATE TABLE source_cooldown_binding (
      connection_id uuid PRIMARY KEY REFERENCES supplier_connection(id),
      group_id uuid NOT NULL REFERENCES source_cooldown(group_id)
    );
    REVOKE ALL ON source_cooldown,source_cooldown_binding FROM PUBLIC;
    INSERT INTO source_cooldown(group_id,not_before,requires_review)
      SELECT connection_id,coalesce(provider_not_before,'-infinity'),
        coalesce(last_error='SOURCE_COOLDOWN_REQUIRES_REVIEW',false)
      FROM sync_job WHERE provider_not_before IS NOT NULL
        OR last_error='SOURCE_COOLDOWN_REQUIRES_REVIEW';

    CREATE FUNCTION warehouse_source_cooldown(source_id uuid,delay_seconds bigint)
      RETURNS TABLE(wait_seconds integer,requires_review boolean)
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
    DECLARE account_id uuid;
    BEGIN
      IF delay_seconds IS NULL OR delay_seconds<0 THEN
        RAISE EXCEPTION 'INVALID_SOURCE_DELAY' USING ERRCODE='22023';
      END IF;
      IF NOT EXISTS (
        SELECT 1 FROM public.supplier_connection c
        JOIN public.organization o ON o.id=c.supplier_org_id
        WHERE c.id=source_id AND c.active AND o.active AND c.connector_type='tour_b2b'
          AND ((c.supplier_org_id=public.warehouse_org_id()
                AND public.warehouse_has_role(ARRAY['supplier_admin','sync_worker']))
            OR (public.warehouse_has_role(ARRAY['advisor','buyer_admin'])
                AND c.id IN (SELECT public.warehouse_buyer_connections())))
      ) THEN
        RAISE EXCEPTION 'SOURCE_ACCESS_DENIED' USING ERRCODE='42501';
      END IF;
      PERFORM pg_advisory_xact_lock_shared(hashtextextended('warehouse:source-cooldown-bindings',0));
      SELECT coalesce((SELECT b.group_id FROM public.source_cooldown_binding b
        WHERE b.connection_id=source_id),source_id) INTO account_id;
      IF delay_seconds>0 THEN
        INSERT INTO public.source_cooldown(group_id,not_before,requires_review)
          VALUES(account_id,clock_timestamp()+make_interval(secs=>least(delay_seconds,604800)::integer),
            delay_seconds>604800)
        ON CONFLICT(group_id) DO UPDATE SET
          not_before=greatest(source_cooldown.not_before,EXCLUDED.not_before),
          requires_review=source_cooldown.requires_review OR EXCLUDED.requires_review;
      END IF;
      RETURN QUERY SELECT
        greatest(0,least(604800,ceil(extract(epoch FROM (s.not_before-clock_timestamp())))))::integer,
        s.requires_review FROM public.source_cooldown s WHERE s.group_id=account_id;
      IF NOT FOUND THEN RETURN QUERY SELECT 0,false; END IF;
    END $$;
    REVOKE ALL ON FUNCTION warehouse_source_cooldown(uuid,bigint) FROM PUBLIC;
    """)


def downgrade():
    raise RuntimeError("Preserve supplier cooldowns; restore only through the recovery procedure")

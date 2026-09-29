"""Audited operator controls and stable synchronization-history indexes."""

from alembic import op

revision = "0013_sync_controls"
down_revision = "0012_document_fetches"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    ALTER TABLE sync_job ADD COLUMN enabled boolean NOT NULL DEFAULT true;
    ALTER TABLE sync_job ADD COLUMN config_version integer NOT NULL DEFAULT 1 CHECK(config_version>0);
    ALTER TABLE sync_job ADD COLUMN worker_last_seen_at timestamptz;
    CREATE INDEX sync_run_history ON sync_run(supplier_org_id,started_at DESC,id DESC);
    CREATE INDEX source_issue_history ON source_issue(supplier_org_id,sync_run_id,external_id,id);

    -- Operators cannot update worker-owned rows directly. This narrow function changes
    -- only schedule controls, retains lease ownership, and always writes its own audit.
    CREATE FUNCTION warehouse_sync_control(
      job uuid, expected_version integer, action text, new_interval integer,
      new_max_attempts integer, new_enabled boolean, note text
    ) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
    DECLARE old_row sync_job; updated sync_job; source_ok boolean;
    BEGIN
      IF NOT warehouse_has_role(ARRAY['supplier_admin']) THEN
        RAISE EXCEPTION 'SYNC_CONTROL_FORBIDDEN' USING ERRCODE='42501';
      END IF;
      SELECT * INTO old_row FROM sync_job
        WHERE id=job AND organization_id=warehouse_org_id() FOR UPDATE;
      IF NOT FOUND THEN
        RAISE EXCEPTION 'SYNC_CONTROL_FORBIDDEN' USING ERRCODE='42501';
      END IF;
      IF old_row.config_version IS DISTINCT FROM expected_version THEN
        RAISE EXCEPTION 'SYNC_CONFIG_STALE';
      END IF;
      IF length(btrim(coalesce(note,''))) NOT BETWEEN 1 AND 500 THEN
        RAISE EXCEPTION 'SYNC_NOTE_REQUIRED';
      END IF;
      SELECT active AND connector_type<>'excel' AND capabilities->>'catalog_read'='true'
        INTO source_ok FROM supplier_connection WHERE id=old_row.connection_id;
      IF action='configure' THEN
        IF new_interval IS NULL OR new_interval NOT BETWEEN 60 AND 86400
          OR new_max_attempts IS NULL OR new_max_attempts NOT BETWEEN 1 AND 10
          OR new_enabled IS NULL THEN RAISE EXCEPTION 'SYNC_CONFIG_INVALID'; END IF;
        IF new_enabled AND NOT coalesce(source_ok,false) THEN
          RAISE EXCEPTION 'SYNC_SOURCE_INACTIVE';
        END IF;
        IF old_row.status='running' AND
          (new_interval<>old_row.interval_seconds OR new_max_attempts<>old_row.max_attempts) THEN
          RAISE EXCEPTION 'SYNC_JOB_RUNNING';
        END IF;
        UPDATE sync_job SET interval_seconds=new_interval,max_attempts=new_max_attempts,
          enabled=new_enabled,config_version=config_version+1,updated_at=now(),
          status=CASE WHEN status='waiting' AND attempts>=new_max_attempts THEN 'failed' ELSE status END,
          next_run_at=CASE WHEN status='waiting' AND new_enabled AND
            (NOT old_row.enabled OR new_interval<>old_row.interval_seconds)
            THEN now()+make_interval(secs=>new_interval) ELSE next_run_at END
          WHERE id=job RETURNING * INTO updated;
      ELSIF action IN ('run_now','retry') THEN
        IF NOT coalesce(source_ok,false) THEN RAISE EXCEPTION 'SYNC_SOURCE_INACTIVE'; END IF;
        IF NOT old_row.enabled THEN RAISE EXCEPTION 'SYNC_JOB_DISABLED'; END IF;
        IF old_row.status='running' THEN RAISE EXCEPTION 'SYNC_JOB_RUNNING'; END IF;
        IF action='retry' AND old_row.status<>'failed' THEN RAISE EXCEPTION 'SYNC_JOB_NOT_FAILED'; END IF;
        IF action='run_now' AND old_row.status<>'waiting' THEN RAISE EXCEPTION 'SYNC_JOB_NOT_WAITING'; END IF;
        UPDATE sync_job SET status='waiting',attempts=0,next_run_at=now(),last_error=NULL,
          config_version=config_version+1,updated_at=now() WHERE id=job RETURNING * INTO updated;
      ELSE
        RAISE EXCEPTION 'SYNC_ACTION_INVALID';
      END IF;
      INSERT INTO audit_event(id,organization_id,actor_id,action,resource_id,details)
        VALUES(gen_random_uuid(),warehouse_org_id(),warehouse_user_id(),'sync.'||action,job,
          jsonb_build_object('note',btrim(note),'before_version',old_row.config_version,
          'config_version',updated.config_version,'interval_seconds',updated.interval_seconds,
          'max_attempts',updated.max_attempts,'enabled',updated.enabled,
          'previous_status',old_row.status,'status',updated.status));
      RETURN to_jsonb(updated);
    END $$;
    REVOKE ALL ON FUNCTION warehouse_sync_control(uuid,integer,text,integer,integer,boolean,text) FROM PUBLIC;
    """)


def downgrade():
    raise RuntimeError("Restore a verified backup rather than deleting synchronization controls")

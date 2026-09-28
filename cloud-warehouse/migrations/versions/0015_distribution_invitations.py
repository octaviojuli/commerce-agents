"""Mutual supplier/buyer onboarding without a public organization directory."""

from alembic import op

revision = "0015_distribution_invitations"
down_revision = "0014_source_controls"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE distribution_invitation (
      id uuid PRIMARY KEY, supplier_org_id uuid NOT NULL, connection_id uuid NOT NULL,
      source_version integer NOT NULL, supplier_name text NOT NULL, source_name text NOT NULL,
      token_hash text NOT NULL UNIQUE CHECK(token_hash ~ '^[a-f0-9]{64}$'),
      request_key uuid NOT NULL, request_hash text NOT NULL,
      created_by uuid NOT NULL REFERENCES warehouse_user(id), created_at timestamptz NOT NULL DEFAULT now(),
      invite_expires_at timestamptz NOT NULL, valid_from timestamptz NOT NULL, expires_at timestamptz,
      status text NOT NULL DEFAULT 'offered' CHECK(status IN ('offered','requested','approved','revoked')),
      version integer NOT NULL DEFAULT 1 CHECK(version>0), note text NOT NULL,
      buyer_org_id uuid REFERENCES organization(id), buyer_name text,
      claimed_by uuid REFERENCES warehouse_user(id), claimed_at timestamptz,
      decided_by uuid REFERENCES warehouse_user(id), decided_at timestamptz, decision_note text,
      grant_id uuid REFERENCES distribution_grant(id),
      UNIQUE(supplier_org_id,request_key),
      FOREIGN KEY(connection_id,supplier_org_id) REFERENCES supplier_connection(id,supplier_org_id),
      CHECK(expires_at IS NULL OR expires_at>valid_from),
      CHECK(buyer_org_id IS NULL OR buyer_org_id<>supplier_org_id)
    );
    ALTER TABLE distribution_invitation ENABLE ROW LEVEL SECURITY;
    ALTER TABLE distribution_invitation FORCE ROW LEVEL SECURITY;
    CREATE POLICY invitation_read ON distribution_invitation FOR SELECT USING(
      (warehouse_has_org(supplier_org_id) AND warehouse_has_role(ARRAY['supplier_admin','auditor']))
      OR (warehouse_has_org(buyer_org_id) AND warehouse_has_role(ARRAY['buyer_admin','auditor'])));

    CREATE FUNCTION warehouse_invitation_create(source uuid, request_id uuid, digest text,
      secret_hash text, starts timestamptz, ends timestamptz, hours integer, reason text)
    RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
    DECLARE existing distribution_invitation; connection supplier_connection;
      identifier uuid:=gen_random_uuid(); supplier_title text;
    BEGIN
      IF NOT warehouse_has_role(ARRAY['supplier_admin']) THEN
        RAISE EXCEPTION 'INVITATION_FORBIDDEN' USING ERRCODE='42501'; END IF;
      SELECT * INTO connection FROM supplier_connection WHERE id=source
        AND supplier_org_id=warehouse_org_id() FOR UPDATE;
      IF NOT FOUND THEN RAISE EXCEPTION 'INVITATION_FORBIDDEN' USING ERRCODE='42501'; END IF;
      SELECT * INTO existing FROM distribution_invitation
        WHERE supplier_org_id=warehouse_org_id() AND request_key=request_id;
      IF FOUND THEN
        IF existing.request_hash<>digest THEN RAISE EXCEPTION 'INVITATION_REQUEST_CONFLICT'; END IF;
        RETURN jsonb_build_object('id',existing.id,'duplicate',true);
      END IF;
      IF NOT connection.active THEN RAISE EXCEPTION 'INVITATION_SOURCE_CHANGED'; END IF;
      IF request_id IS NULL OR starts IS NULL OR (ends IS NOT NULL AND (ends<=starts OR ends<=now()))
        OR hours IS NULL OR hours NOT BETWEEN 1 AND 168 OR length(btrim(coalesce(reason,''))) NOT BETWEEN 1 AND 500
        OR digest IS NULL OR digest !~ '^[a-f0-9]{64}$' OR secret_hash IS NULL OR secret_hash !~ '^[a-f0-9]{64}$'
        THEN RAISE EXCEPTION 'INVITATION_INVALID'; END IF;
      SELECT name INTO supplier_title FROM organization WHERE id=warehouse_org_id();
      INSERT INTO distribution_invitation(id,supplier_org_id,connection_id,source_version,
        supplier_name,source_name,token_hash,request_key,request_hash,created_by,
        invite_expires_at,valid_from,expires_at,note)
      VALUES(identifier,warehouse_org_id(),source,connection.version,supplier_title,connection.name,
        secret_hash,request_id,digest,warehouse_user_id(),now()+make_interval(hours=>hours),starts,ends,btrim(reason))
        ON CONFLICT(supplier_org_id,request_key) DO NOTHING RETURNING id INTO identifier;
      IF identifier IS NULL THEN
        SELECT * INTO existing FROM distribution_invitation
          WHERE supplier_org_id=warehouse_org_id() AND request_key=request_id;
        IF existing.request_hash<>digest THEN RAISE EXCEPTION 'INVITATION_REQUEST_CONFLICT'; END IF;
        RETURN jsonb_build_object('id',existing.id,'duplicate',true);
      END IF;
      INSERT INTO audit_event(id,organization_id,actor_id,action,resource_id,details)
        VALUES(gen_random_uuid(),warehouse_org_id(),warehouse_user_id(),'invitation.created',identifier,
          jsonb_build_object('connection_id',source,'valid_from',starts,'expires_at',ends,'note',btrim(reason)));
      INSERT INTO outbox_event(id,organization_id,kind,resource_id,payload)
        VALUES(gen_random_uuid(),warehouse_org_id(),'invitation.created',identifier,'{}');
      RETURN jsonb_build_object('id',identifier,'duplicate',false);
    END $$;

    CREATE FUNCTION warehouse_invitation_preview(secret_hash text) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
    DECLARE invitation distribution_invitation;
    BEGIN
      IF NOT warehouse_has_role(ARRAY['buyer_admin']) OR NOT EXISTS(
        SELECT 1 FROM organization WHERE id=warehouse_org_id() AND 'buyer'=ANY(kinds)) THEN
        RAISE EXCEPTION 'INVITATION_FORBIDDEN' USING ERRCODE='42501'; END IF;
      SELECT * INTO invitation FROM distribution_invitation WHERE token_hash=secret_hash;
      IF NOT FOUND OR invitation.supplier_org_id=warehouse_org_id() OR invitation.status<>'offered'
        OR invitation.invite_expires_at<=now() OR NOT warehouse_org_active(invitation.supplier_org_id)
        OR (invitation.expires_at IS NOT NULL AND invitation.expires_at<=now())
        OR NOT EXISTS(SELECT 1 FROM supplier_connection WHERE id=invitation.connection_id
          AND active AND version=invitation.source_version) THEN RAISE EXCEPTION 'INVITATION_UNAVAILABLE'; END IF;
      RETURN jsonb_build_object('id',invitation.id,'supplier_name',invitation.supplier_name,
        'source_name',invitation.source_name,'valid_from',invitation.valid_from,
        'expires_at',invitation.expires_at,'invite_expires_at',invitation.invite_expires_at);
    END $$;

    CREATE FUNCTION warehouse_invitation_claim(secret_hash text) RETURNS uuid
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
    DECLARE invitation distribution_invitation; buyer_title text;
    BEGIN
      IF NOT warehouse_has_role(ARRAY['buyer_admin']) OR NOT EXISTS(
        SELECT 1 FROM organization WHERE id=warehouse_org_id() AND 'buyer'=ANY(kinds)) THEN
        RAISE EXCEPTION 'INVITATION_FORBIDDEN' USING ERRCODE='42501'; END IF;
      SELECT * INTO invitation FROM distribution_invitation WHERE token_hash=secret_hash FOR UPDATE;
      IF NOT FOUND OR invitation.supplier_org_id=warehouse_org_id() THEN
        RAISE EXCEPTION 'INVITATION_UNAVAILABLE'; END IF;
      IF invitation.buyer_org_id=warehouse_org_id() AND invitation.status IN ('requested','approved') THEN
        RETURN invitation.id; END IF;
      IF invitation.status<>'offered' OR invitation.invite_expires_at<=now()
        OR (invitation.expires_at IS NOT NULL AND invitation.expires_at<=now())
        OR NOT warehouse_org_active(invitation.supplier_org_id)
        OR NOT EXISTS(SELECT 1 FROM supplier_connection WHERE id=invitation.connection_id
          AND active AND version=invitation.source_version)
        THEN RAISE EXCEPTION 'INVITATION_UNAVAILABLE'; END IF;
      SELECT name INTO buyer_title FROM organization WHERE id=warehouse_org_id();
      UPDATE distribution_invitation SET status='requested',version=version+1,
        buyer_org_id=warehouse_org_id(),buyer_name=buyer_title,
        claimed_by=warehouse_user_id(),claimed_at=now() WHERE id=invitation.id;
      INSERT INTO audit_event(id,organization_id,actor_id,action,resource_id,details)
        VALUES(gen_random_uuid(),warehouse_org_id(),warehouse_user_id(),'invitation.requested',invitation.id,
          jsonb_build_object('supplier_org_id',invitation.supplier_org_id,'connection_id',invitation.connection_id));
      INSERT INTO outbox_event(id,organization_id,kind,resource_id,payload)
        VALUES(gen_random_uuid(),warehouse_org_id(),'invitation.requested',invitation.id,'{}');
      RETURN invitation.id;
    END $$;

    CREATE FUNCTION warehouse_invitation_decide(identifier uuid, expected integer, decision text, reason text)
    RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
    DECLARE invitation distribution_invitation; granted uuid; connection supplier_connection;
    BEGIN
      IF NOT warehouse_has_role(ARRAY['supplier_admin']) THEN
        RAISE EXCEPTION 'INVITATION_FORBIDDEN' USING ERRCODE='42501'; END IF;
      SELECT * INTO invitation FROM distribution_invitation
        WHERE id=identifier AND supplier_org_id=warehouse_org_id() FOR UPDATE;
      IF NOT FOUND THEN RAISE EXCEPTION 'INVITATION_FORBIDDEN' USING ERRCODE='42501'; END IF;
      IF expected IS NULL OR invitation.version<>expected THEN RAISE EXCEPTION 'INVITATION_STALE'; END IF;
      IF decision IS NULL OR decision NOT IN ('approve','revoke') OR length(btrim(coalesce(reason,''))) NOT BETWEEN 1 AND 500
        THEN RAISE EXCEPTION 'INVITATION_INVALID'; END IF;
      IF invitation.status NOT IN ('offered','requested') THEN RAISE EXCEPTION 'INVITATION_CLOSED'; END IF;
      IF decision='approve' THEN
        IF invitation.status<>'requested' OR invitation.invite_expires_at<=now()
          OR (invitation.expires_at IS NOT NULL AND invitation.expires_at<=now())
          THEN RAISE EXCEPTION 'INVITATION_UNAVAILABLE'; END IF;
        SELECT * INTO connection FROM supplier_connection WHERE id=invitation.connection_id FOR UPDATE;
        IF NOT connection.active OR connection.version<>invitation.source_version THEN
          RAISE EXCEPTION 'INVITATION_SOURCE_CHANGED'; END IF;
        IF NOT EXISTS(SELECT 1 FROM membership m JOIN warehouse_user u ON u.id=m.user_id
          JOIN organization o ON o.id=m.organization_id WHERE m.user_id=invitation.claimed_by
          AND m.organization_id=invitation.buyer_org_id AND m.active AND u.active AND o.active
          AND 'buyer_admin'=ANY(m.roles) AND 'buyer'=ANY(o.kinds)) THEN
          RAISE EXCEPTION 'INVITATION_BUYER_CHANGED'; END IF;
        INSERT INTO distribution_grant(id,supplier_org_id,buyer_org_id,connection_id,valid_from,expires_at)
          VALUES(gen_random_uuid(),invitation.supplier_org_id,invitation.buyer_org_id,
            invitation.connection_id,invitation.valid_from,invitation.expires_at)
          ON CONFLICT(connection_id,buyer_org_id) DO NOTHING RETURNING id INTO granted;
        IF granted IS NULL THEN RAISE EXCEPTION 'INVITATION_RELATION_EXISTS'; END IF;
      END IF;
      UPDATE distribution_invitation SET status=CASE WHEN decision='approve' THEN 'approved' ELSE 'revoked' END,
        version=version+1,decided_by=warehouse_user_id(),decided_at=now(),decision_note=btrim(reason),grant_id=granted
        WHERE id=identifier;
      INSERT INTO audit_event(id,organization_id,actor_id,action,resource_id,details)
        VALUES(gen_random_uuid(),warehouse_org_id(),warehouse_user_id(),'invitation.'||decision,identifier,
          jsonb_build_object('buyer_org_id',invitation.buyer_org_id,'grant_id',granted,'note',btrim(reason)));
      INSERT INTO outbox_event(id,organization_id,kind,resource_id,payload)
        VALUES(gen_random_uuid(),warehouse_org_id(),'invitation.'||decision,identifier,
          jsonb_build_object('grant_id',granted));
      RETURN identifier;
    END $$;
    REVOKE ALL ON FUNCTION warehouse_invitation_create(uuid,uuid,text,text,timestamptz,timestamptz,integer,text) FROM PUBLIC;
    REVOKE ALL ON FUNCTION warehouse_invitation_claim(text) FROM PUBLIC;
    REVOKE ALL ON FUNCTION warehouse_invitation_preview(text) FROM PUBLIC;
    REVOKE ALL ON FUNCTION warehouse_invitation_decide(uuid,integer,text,text) FROM PUBLIC;
    """)


def downgrade():
    raise RuntimeError("Restore a verified backup rather than deleting onboarding history")

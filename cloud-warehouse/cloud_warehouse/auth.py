"""Platform passwords and opaque sessions, using a separate, narrowly granted auth role."""

import hashlib
import hmac
import secrets
from functools import lru_cache
from uuid import UUID, uuid4

from sqlalchemy import Engine, text

from .persistence import Forbidden


class Unauthenticated(PermissionError):
    pass


class RateLimited(PermissionError):
    pass


def password_hash(password: str) -> str:
    if not 12 <= len(password) <= 256:
        raise ValueError("平台密码需为 12–256 个字符")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode(), salt=salt, n=32768, r=8, p=1, maxmem=64 * 1024 * 1024
    )
    return "scrypt1$" + salt.hex() + "$" + digest.hex()


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    return password_hash(secrets.token_urlsafe(24))


def verify_password(password: str, encoded: str | None) -> bool:
    valid = encoded is not None
    try:
        scheme, salt, expected = (encoded or _dummy_hash()).split("$")
        if scheme != "scrypt1" or len(password) > 256:
            return False
        actual = hashlib.scrypt(
            password.encode(), salt=bytes.fromhex(salt), n=32768, r=8, p=1, maxmem=64 * 1024 * 1024
        )
        return hmac.compare_digest(actual, bytes.fromhex(expected)) and valid
    except (ValueError, TypeError):
        return False


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_user(
    admin: Engine, email: str, password: str, memberships: dict[UUID, list[str]]
) -> UUID:
    """Offline administrator command; an organization is never granted by a login form."""
    identity = uuid4()
    encoded = password_hash(password)
    with admin.begin() as conn:
        conn.execute(
            text("INSERT INTO warehouse_user(id,email,password_hash) VALUES(:id,:email,:password)"),
            {"id": identity, "email": email.strip().lower(), "password": encoded},
        )
        for organization, roles in memberships.items():
            conn.execute(
                text(
                    "INSERT INTO membership(user_id,organization_id,roles) VALUES(:user,:org,:roles)"
                ),
                {"user": identity, "org": organization, "roles": roles},
            )
    return identity


def login(engine: Engine, email: str, password: str, remote: str) -> dict:
    email = email.strip().lower()
    blocked = False
    # Commit counters even on authentication failure. Counts are shared across API workers.
    with engine.begin() as conn:
        for bucket, maximum in (("user:" + email, 20), ("remote:" + remote, 60)):
            attempts = conn.scalar(
                text("""INSERT INTO auth_rate_limit(bucket_hash,attempts) VALUES(:key,1)
                ON CONFLICT(bucket_hash) DO UPDATE SET
                attempts=CASE WHEN auth_rate_limit.window_start<now()-interval '15 minutes' THEN 1 ELSE auth_rate_limit.attempts+1 END,
                window_start=CASE WHEN auth_rate_limit.window_start<now()-interval '15 minutes' THEN now() ELSE auth_rate_limit.window_start END
                RETURNING attempts"""),
                {"key": _digest(bucket)},
            )
            blocked = blocked or attempts > maximum
    if blocked:
        raise RateLimited("尝试次数过多，请稍后重试")
    with engine.begin() as conn:
        user = (
            conn.execute(
                text("SELECT id,password_hash FROM warehouse_user WHERE email=:email AND active"),
                {"email": email},
            )
            .mappings()
            .one_or_none()
        )
        if not verify_password(password, user["password_hash"] if user else None):
            raise Unauthenticated("账号或密码错误")
        token = secrets.token_urlsafe(32)
        expires = conn.scalar(
            text(
                "INSERT INTO auth_session(token_hash,user_id,expires_at) VALUES(:hash,:user,now()+interval '7 days') RETURNING expires_at"
            ),
            {"hash": _digest(token), "user": user["id"]},
        )
    return {"access_token": token, "token_type": "bearer", "expires_at": expires.isoformat()}


def authenticate(
    engine: Engine, token: str, *, organization_id: UUID | None = None, refresh: bool = True
) -> UUID:
    """Read live session and optional membership together; business RLS still rechecks."""
    if not 32 <= len(token) <= 128:
        raise Unauthenticated("请重新登录")
    with engine.begin() as conn:
        row = (
            conn.execute(
                text("""WITH live AS MATERIALIZED (
            SELECT s.user_id,(CAST(:org AS uuid) IS NULL OR EXISTS (
              SELECT 1 FROM membership m JOIN organization o ON o.id=m.organization_id
              WHERE m.user_id=s.user_id AND m.organization_id=:org AND m.active AND o.active
            )) AS organization_allowed
            FROM auth_session s JOIN warehouse_user u ON u.id=s.user_id
            WHERE s.token_hash=:hash AND s.revoked_at IS NULL AND s.expires_at>now() AND u.active
          ), touched AS (
            UPDATE auth_session s SET expires_at=now()+interval '7 days'
            FROM live WHERE :refresh AND live.organization_allowed AND s.user_id=live.user_id
              AND s.token_hash=:hash AND s.revoked_at IS NULL AND s.expires_at>now()
            RETURNING s.user_id
          ) SELECT live.*,touched.user_id IS NOT NULL AS refreshed FROM live
            LEFT JOIN touched ON touched.user_id=live.user_id"""),
                {"hash": _digest(token), "org": organization_id, "refresh": refresh},
            )
            .mappings()
            .one_or_none()
        )
        if not row:
            raise Unauthenticated("请重新登录")
        if not row["organization_allowed"]:
            raise Forbidden("当前账号不属于该有效组织")
        if refresh and not row["refreshed"]:
            raise Unauthenticated("请重新登录")
        return row["user_id"]


def organizations(engine: Engine, user_id: UUID) -> list[dict]:
    with engine.connect() as conn:
        return [
            dict(row)
            for row in conn.execute(
                text(
                    "SELECT o.id,o.name,o.kinds,m.roles FROM organization o JOIN membership m ON m.organization_id=o.id JOIN warehouse_user u ON u.id=m.user_id WHERE m.user_id=:user AND m.active AND o.active AND u.active ORDER BY o.name,o.id"
                ),
                {"user": user_id},
            ).mappings()
        ]


def logout(engine: Engine, token: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE auth_session SET revoked_at=now() WHERE token_hash=:hash AND revoked_at IS NULL"
            ),
            {"hash": _digest(token)},
        )


def require_auth_role(engine: Engine) -> None:
    with engine.connect() as conn:
        if conn.scalar(
            text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user")
        ) or conn.scalar(
            text("SELECT has_table_privilege(current_user,'supplier_product','SELECT')")
        ):
            raise RuntimeError("认证服务账号必须与业务及迁移账号分离，不能读取产品表或绕过 RLS")

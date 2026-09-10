# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""Who is logged in to the workbench, and the ERP client that speaks for them.

Each advisor signs in with their own ERP mobile and password. ``AdvisorRegistry.login``
forwards the pair to the ERP's ``POST /login`` once, through a client built for that one call,
and keeps what the ERP answered with: the token, the salesperson it names, the departments the
account reads, and a ``HttpErpClient.with_token`` client on that token. The password is not
stored, not written to a log, and not held by the client that stays — the client that had it is
closed and dropped as soon as the token is in hand.

A login lives in this process and nowhere else, so it is gone on a restart, and the token is
gone eight hours after the ERP issued it whatever happens here; the advisor signs in again, and
until they do the ERP calls their session would make raise ``ErpAuth``. ``user_id`` is
``erp-{userId}``, the ERP employee behind the login, and that is what the host's session and
the advisor's memory are keyed by, so two advisors of one deployment share neither.

``FixtureAdvisorRegistry`` is the same registry over ``data/users.json``, for the demo: the
fixtures hold no passwords, so any password signs a fixture advisor in and the workbench flow
is the one the live deployment has.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from demo_common.storefront_fixtures import example_data_dir, load_json

from .erp_client import ErpAuth, ErpClient
from .http_erp import HttpErpClient

DATA_DIR = example_data_dir(__file__)

# What a session whose advisor has no live login is told, in the advisor's own language: the
# executor relays it into the conversation, and the workbench asks ``/api/advisor`` again.
NEED_LOGIN = "请先登录 ERP 账号"
# A login the ERP answered without naming the salesperson: there is no advisor to key a
# session by, so it is not a login.
NO_SALESPERSON = "旅行社 ERP 未返回登录人信息，请重新登录"
NOT_A_FIXTURE = "该手机号不在演示账号里（examples/tour/data/users.json）"
# How long a fixture login lasts, the ERP token's own eight hours.
FIXTURE_TTL_SECONDS = 8 * 3600

log = logging.getLogger(__name__)


@dataclass
class AdvisorLogin:
    """One logged-in advisor: the ERP client their session's reads and writes go out on, who
    the ERP says they are, and when the token behind the client dies."""

    client: ErpClient
    user_id: str
    name: str
    mobile: str
    department: str
    departments: int
    expires_at: float


class AdvisorRegistry:
    """The live logins, by ``user_id``. ``base_url`` is the ERP's ``/aicli`` root and
    ``transport`` the tests' mock wire, as in ``http_erp.py``."""

    def __init__(
        self, base_url: str = "", *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._base_url = base_url
        self._transport = transport
        self._logins: dict[str, AdvisorLogin] = {}

    async def login(self, mobile: str, password: str) -> AdvisorLogin:
        """Sign one advisor in against the ERP and keep the token. The ERP's own errors are
        raised as they are — bad credentials, the ten-failure throttle, an outage — because the
        message is written for the advisor. Nothing about the attempt is logged but its
        outcome."""
        client = HttpErpClient.from_login(
            self._base_url, mobile.strip(), password, transport=self._transport
        )
        try:
            user_info = await client.identify()
            token, expires_at = client.token()
        finally:
            # The client that held the password has done its one call; the token is what stays.
            await client.aclose()
        user_id = int(user_info.get("userId") or 0)
        if not user_id:
            raise ErpAuth(NO_SALESPERSON)
        kept = HttpErpClient.with_token(
            self._base_url,
            token,
            expires_at,
            user_info,
            list(client.companies),
            mobile.strip(),
            transport=self._transport,
        )
        return self._keep(
            AdvisorLogin(
                client=kept,
                user_id=f"erp-{user_id}",
                name=str(user_info.get("userName") or ""),
                mobile=mobile.strip(),
                department=str(user_info.get("companyName") or ""),
                departments=len(client.companies) or 1,
                expires_at=expires_at,
            )
        )

    def get(self, user_id: str) -> AdvisorLogin | None:
        """The advisor's live login, or ``None`` when there is none: nobody signed in on this
        process, or the token they signed in with has run out. Either way the workbench asks
        them to sign in again."""
        login = self._logins.get(user_id)
        if login is None:
            return None
        if time.time() >= login.expires_at:
            del self._logins[user_id]
            return None
        return login

    def logout(self, user_id: str) -> None:
        """Drop the token. The session itself stands, and its next ERP call says
        ``NEED_LOGIN``."""
        self._logins.pop(user_id, None)

    def _keep(self, login: AdvisorLogin) -> AdvisorLogin:
        self._logins[login.user_id] = login
        log.info("advisor login ok user=%s", login.user_id)
        return login


class FixtureAdvisorRegistry(AdvisorRegistry):
    """The demo's registry: the advisors ``data/users.json`` names, all reading through the one
    ``MockErpClient`` the host built. The fixtures carry no passwords and there is nothing here
    to check one against, so any password signs in a mobile the file names and an unknown
    mobile is refused. The advisor's own ``user_id`` is the key, which is what the fixture
    memory seed and their earlier conversations are already under."""

    def __init__(self, client: ErpClient, data_dir: Path = DATA_DIR) -> None:
        super().__init__()
        self._client = client
        self._profiles = {
            str(user.get("mobile") or ""): user
            for user in load_json(data_dir, "users.json")["users"]
        }

    async def login(self, mobile: str, password: str) -> AdvisorLogin:
        del password
        profile = self._profiles.get(mobile.strip())
        if profile is None:
            raise ErpAuth(NOT_A_FIXTURE)
        # The 门店 the profile names, up to what it sells, is this advisor's department; the
        # ERP names one department per login the same way.
        store = str(profile.get("preferences", {}).get("门店", "")).split("，")[0]
        return self._keep(
            AdvisorLogin(
                client=self._client,
                user_id=str(profile["user_id"]),
                # The 工号 the display name carries is not part of a 联系人 the ERP accepts.
                name=str(profile.get("display_name") or "").split("（")[0],
                mobile=mobile.strip(),
                department=store or str(profile.get("loyalty_tier") or ""),
                departments=1,
                expires_at=time.time() + FIXTURE_TTL_SECONDS,
            )
        )

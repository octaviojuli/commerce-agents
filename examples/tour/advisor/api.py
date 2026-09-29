"""HTTP interface of the advisor service. The web app is its only client.

Sign-in is the warehouse's: the service keeps the warehouse token encrypted server-side and
gives the browser only an opaque, httpOnly session cookie.
"""

import base64
import secrets
from contextlib import asynccontextmanager, suppress
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

import httpx
from fastapi import (
    Cookie,
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Request,
    Response,
    UploadFile,
)
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from route_kit.render import page as route_page_html
from sqlalchemy import text

from . import closing, db, memory, papers, privacy, routes, selling, store, suppliers
from . import need as needs
from .facts import Route
from .judge import Judge
from .model import ModelUnavailable, TypedModel
from .need import Need
from .store import Owner
from .turns import Turns, chips, requirement_facts, stage
from .warehouse import Warehouse, WarehouseError

COOKIE = "advisor_session"
CHECK_EVERY = timedelta(minutes=5)


class Settings(BaseModel):
    database_url: str
    warehouse_url: str
    material_dir: Path
    warehouse_verify: bool = True
    secure_cookie: bool = False


class Login(BaseModel):
    email: str = Field(min_length=3, max_length=200)
    password: str = Field(min_length=1, max_length=200)


class NewDeal(BaseModel):
    title: str = Field(default="新客人", max_length=100)


class TurnIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    kind: str = Field(default="customer", pattern="^(customer|advisor)$")


class Adopt(BaseModel):
    fields: list[str] | None = None
    keep: bool = False


class FieldEdit(BaseModel):
    value: Any


class Ids(BaseModel):
    product_ids: list[str] = Field(min_length=1, max_length=3)


class SupplierRef(BaseModel):
    id: str = Field(default="", max_length=80)
    name: str = Field(default="", max_length=40)


class RouteChoice(BaseModel):
    product_id: str
    title: str = ""
    supplier: SupplierRef | None = None


class SupplierNote(BaseModel):
    name: str = Field(default="", max_length=40)
    stance: str = Field(default="", max_length=20)
    note: str = Field(default="", max_length=200)


class SearchIn(BaseModel):
    suppliers: list[str] = Field(default_factory=list, max_length=20)


class QueryRelax(BaseModel):
    field: str = Field(pattern="^(window|days|depart_city)$")
    amount: int = Field(default=10, ge=1, le=60)


class QueryResolve(BaseModel):
    query_id: str
    adopt: bool = False


class Departure(BaseModel):
    departure_id: str
    offer_id: str | None = None
    date: str
    return_date: str = ""


class ConfirmIn(BaseModel):
    evidence: str = Field(default="", max_length=1000)
    confirmed: bool = True


class SalesTotal(BaseModel):
    sales_total: float = Field(gt=0)


class Sale(BaseModel):
    quote_id: UUID
    deposit: float | None = Field(default=None, ge=0)


class Receipt(BaseModel):
    amount: float = Field(gt=0)
    note: str = Field(default="收款", max_length=100)


class NoteIn(BaseModel):
    product_id: str
    day: int | None = None
    node: str = Field(default="", max_length=200)
    category: str
    text: str = Field(min_length=1, max_length=500)
    amount: float | None = None
    currency: str = "CNY"
    quantity: int = Field(default=1, ge=1, le=100)


class Sent(BaseModel):
    seq: int


class Direction(BaseModel):
    label: str = Field(min_length=1, max_length=40)
    region: bool = False
    start: str
    end: str


class Ask(BaseModel):
    question: str = Field(min_length=1, max_length=300)
    deal_id: UUID | None = None


def create_app(settings: Settings, *, model=None, transport=None, judge=None):
    engine = db.connect(settings.database_url)
    model = model or TypedModel()
    judge = judge or Judge()
    turns = Turns(engine, model, judge)

    @asynccontextmanager
    async def lifespan(app):
        db.migrate(engine)
        yield
        engine.dispose()
        if hasattr(model, "aclose"):
            await model.aclose()
        await judge.aclose()

    app = FastAPI(title="ACME 顾问搭档", lifespan=lifespan)

    @app.get("/api/health")
    def health():
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ok"}

    def warehouse(session=None):
        token = (
            papers.unseal(base64.b64decode(session["warehouse_token"])).decode()
            if session
            else None
        )
        return Warehouse(
            settings.warehouse_url,
            token,
            str(session["org_id"]) if session else None,
            transport=transport,
            verify=settings.warehouse_verify,
        )

    def revoke(session_id):
        with engine.begin() as conn:
            conn.execute(db.sessions.delete().where(db.sessions.c.id == session_id))

    async def current(advisor_session: Annotated[str | None, Cookie()] = None):
        if not advisor_session:
            raise HTTPException(401, "请先登录")
        with engine.connect() as conn:
            row = (
                conn.execute(db.sessions.select().where(db.sessions.c.id == advisor_session))
                .mappings()
                .one_or_none()
            )
        if not row or row["expires_at"] <= datetime.now(UTC):
            raise HTTPException(401, "登录已过期，请重新登录")
        if row["checked_at"] <= datetime.now(UTC) - CHECK_EVERY:
            # The warehouse owns the login: a revoked token or a lost advisor role ends this one.
            wh = warehouse(row)
            try:
                alive = await wh.still_advisor()
            except WarehouseError as error:
                alive = error.status not in (401, 403)
            except httpx.HTTPError:
                alive = True  # a network failure is not a revocation
            finally:
                await wh.aclose()
            if not alive:
                revoke(advisor_session)
                raise HTTPException(401, "登录已失效，请重新登录")
            with engine.begin() as conn:
                conn.execute(
                    db.sessions.update()
                    .where(db.sessions.c.id == advisor_session)
                    .values(checked_at=datetime.now(UTC))
                )
        return dict(row)

    Session = Annotated[dict, Depends(current)]

    def owner_of(session) -> Owner:
        return Owner(
            session["org_id"], session["user_id"], session["advisor_name"], session["org_name"]
        )

    @app.exception_handler(store.NotFound)
    async def not_found(request: Request, error: store.NotFound):
        return JSONResponse({"message": str(error)}, status_code=404)

    @app.exception_handler(store.OfferChoice)
    async def offer_choice(request: Request, error: store.OfferChoice):
        return JSONResponse({"message": str(error), "offers": error.offers}, status_code=409)

    @app.exception_handler(store.Conflict)
    async def conflict(request: Request, error: store.Conflict):
        return JSONResponse({"message": str(error)}, status_code=409)

    @app.exception_handler(ValueError)
    async def invalid(request: Request, error: ValueError):
        return JSONResponse({"message": str(error)}, status_code=422)

    @app.exception_handler(WarehouseError)
    async def upstream(request: Request, error: WarehouseError):
        if error.status == 401 and request.cookies.get(COOKIE):
            # The warehouse refused this login outright: nothing local stays readable with it.
            revoke(request.cookies[COOKIE])
        status = 401 if error.status == 401 else 502 if error.status >= 500 else 409
        return JSONResponse({"message": "云仓：" + error.message}, status_code=status)

    @app.exception_handler(ModelUnavailable)
    async def model_down(request: Request, error: ModelUnavailable):
        return JSONResponse({"message": "助手暂时没有响应，请稍后重试"}, status_code=503)

    # ------------------------------------------------------------ session

    @app.post("/api/login")
    async def login(body: Login, response: Response):
        wh = warehouse()
        try:
            result = await wh.login(body.email, body.password)
        finally:
            await wh.aclose()
        sid = secrets.token_urlsafe(32)
        expires = datetime.now(UTC) + timedelta(hours=8)
        if result.expires_at:
            with suppress(ValueError):
                expires = min(expires, datetime.fromisoformat(result.expires_at))
        name = body.email.split("@")[0]
        with engine.begin() as conn:
            conn.execute(
                db.sessions.insert().values(
                    id=sid,
                    org_id=UUID(result.org_id),
                    user_id=UUID(result.user_id),
                    advisor_name=name,
                    org_name=result.org_name,
                    warehouse_token=base64.b64encode(papers.seal(result.token.encode())).decode(),
                    expires_at=expires,
                )
            )
        response.set_cookie(
            COOKIE,
            sid,
            httponly=True,
            samesite="lax",
            secure=settings.secure_cookie,
            max_age=8 * 3600,
        )
        return {"name": name, "org": result.org_name}

    @app.post("/api/logout", status_code=204)
    def logout(session: Session, response: Response):
        with engine.begin() as conn:
            conn.execute(db.sessions.delete().where(db.sessions.c.id == session["id"]))
        response.delete_cookie(COOKIE)

    @app.get("/api/me")
    def me(session: Session):
        return {"name": session["advisor_name"], "org": session["org_name"]}

    # ------------------------------------------------------------ work lists

    @app.get("/api/today")
    def today_view(session: Session):
        owner = owner_of(session)
        return today(engine, owner)

    @app.get("/api/deals")
    def deal_list(session: Session):
        owner = owner_of(session)
        with engine.connect() as conn:
            return {"items": [deal_card(conn, owner, d) for d in store.deals(conn, owner)]}

    @app.post("/api/deals", status_code=201)
    def new_deal(body: NewDeal, session: Session):
        owner = owner_of(session)
        with engine.begin() as conn:
            row = store.create_deal(conn, owner, body.title)
        return {"id": str(row["id"])}

    @app.get("/api/deals/{deal_id}")
    def deal_detail(deal_id: UUID, session: Session):
        owner = owner_of(session)
        with engine.connect() as conn:
            return detail(conn, owner, deal_id)

    @app.patch("/api/deals/{deal_id}")
    def rename(deal_id: UUID, body: NewDeal, session: Session):
        owner = owner_of(session)
        with engine.begin() as conn:
            store.deal(conn, owner, deal_id)
            store.update_deal(conn, owner, deal_id, title=body.title)
        return {"ok": True}

    # ------------------------------------------------------------ conversation

    @app.post("/api/deals/{deal_id}/turns")
    async def turn(deal_id: UUID, body: TurnIn, session: Session):
        owner = owner_of(session)
        wh = warehouse(session)
        try:
            return await turns.run(owner, wh, deal_id, body.text.strip(), body.kind)
        finally:
            await wh.aclose()

    @app.post("/api/deals/{deal_id}/sent")
    def sent(deal_id: UUID, body: Sent, session: Session):
        owner = owner_of(session)
        with engine.begin() as conn:
            store.deal(conn, owner, deal_id)
            row = (
                conn.execute(
                    db.turns.select().where(
                        owner.where(db.turns),
                        db.turns.c.deal_id == deal_id,
                        db.turns.c.seq == body.seq,
                    )
                )
                .mappings()
                .one_or_none()
            )
            if not row:
                raise store.NotFound("这一轮不存在")
            result = dict(row["result"])
            if result.get("sent"):
                return {"ok": True}
            draft = result.get("draft") or {}
            deal = store.deal(conn, owner, deal_id)
            memory.mark_sent(
                conn,
                owner,
                deal_id,
                draft.get("text", ""),
                draft.get("claims", []),
                (deal.get("route") or {}).get("product_id"),
            )
            result["sent"] = True
            store.set_turn_result(conn, owner, deal_id, body.seq, result)
            store.update_deal(conn, owner, deal_id, waiting_reply=False)
        return {"ok": True}

    @app.post("/api/deals/{deal_id}/proposals/{proposal_id}")
    def adopt(deal_id: UUID, proposal_id: UUID, body: Adopt, session: Session):
        return closing.adopt(
            engine, owner_of(session), deal_id, proposal_id, body.fields, body.keep
        )

    @app.put("/api/deals/{deal_id}/need/{field}")
    def edit(deal_id: UUID, field: str, body: FieldEdit, session: Session):
        if field not in needs.FIELDS:
            raise HTTPException(404, "没有这个字段")
        return closing.edit_need(engine, owner_of(session), deal_id, field, body.value)

    @app.get("/api/deals/{deal_id}/memory")
    def deal_memory(deal_id: UUID, session: Session):
        owner = owner_of(session)
        with engine.connect() as conn:
            deal = store.deal(conn, owner, deal_id)
            need = Need.model_validate(deal["need"])
            return {
                "need": need_view(need),
                "version": deal["need_version"],
                "versions": [
                    {
                        "version": v["version"],
                        "fields": [needs.LABELS.get(f, f) for f in v["fields"]],
                        "reason": v["reason"],
                        "at": v["created_at"].isoformat(),
                    }
                    for v in store.versions(conn, owner, deal_id)
                ],
                "turns": store.next_seq(conn, owner, deal_id) - 1,
                **memory.summary(conn, owner, deal_id),
            }

    @app.delete("/api/deals/{deal_id}/memory/{item_id}", status_code=204)
    def forget(deal_id: UUID, item_id: UUID, session: Session):
        owner = owner_of(session)
        with engine.begin() as conn:
            store.change(conn, owner, db.memory, item_id, status="removed")

    @app.get("/api/deals/{deal_id}/qa")
    async def qa(deal_id: UUID, session: Session):
        owner = owner_of(session)
        wh = warehouse(session)
        try:
            outdated = await memory.recheck_said(engine, owner, wh, deal_id, None)
        finally:
            await wh.aclose()
        with engine.connect() as conn:
            deal = store.deal(conn, owner, deal_id)
            said = memory.summary(conn, owner, deal_id)["said"]
            return {
                "route": deal.get("route"),
                "items": memory.qa_book(conn, owner, deal_id),
                "said": said,
                "outdated": outdated,
            }

    # ------------------------------------------------------------ routes

    @app.get("/api/directions")
    async def directions(session: Session):
        from .turns import explore

        wh = warehouse(session)
        try:
            return {"items": await explore(wh)}
        finally:
            await wh.aclose()

    @app.post("/api/deals/{deal_id}/direction")
    def direction(deal_id: UUID, body: Direction, session: Session):
        return closing.choose_direction(
            engine, owner_of(session), deal_id, body.label, body.region, body.start, body.end
        )

    @app.post("/api/deals/{deal_id}/search")
    async def search(deal_id: UUID, session: Session, body: SearchIn | None = None):
        owner = owner_of(session)
        wh = warehouse(session)
        try:
            return await turns.search(owner, wh, deal_id, (body or SearchIn()).suppliers)
        finally:
            await wh.aclose()

    @app.post("/api/deals/{deal_id}/query/relax")
    async def relax_query(deal_id: UUID, body: QueryRelax, session: Session):
        from . import queries

        owner = owner_of(session)
        with engine.begin() as conn:
            deal = store.deal(conn, owner, deal_id, lock=True)
            queries.save(
                conn,
                owner,
                deal,
                [queries.relaxed(queries.effective(deal), body.field, body.amount)],
            )
        wh = warehouse(session)
        try:
            return await turns.search(owner, wh, deal_id)
        finally:
            await wh.aclose()

    @app.post("/api/deals/{deal_id}/query/resolve")
    async def resolve_query(deal_id: UUID, body: QueryResolve, session: Session):
        from . import queries

        owner = owner_of(session)
        changed = queries.resolve(engine, owner, deal_id, body.query_id, adopt=body.adopt)
        wh = warehouse(session)
        try:
            found = await turns.search(owner, wh, deal_id)
            return {**changed, "search": found}
        finally:
            await wh.aclose()

    @app.get("/api/routes")
    async def browse(
        session: Session,
        query: str = "",
        start: date | None = None,
        end: date | None = None,
        supplier: str = "",
    ):
        owner = owner_of(session)
        wh = warehouse(session)
        try:
            page = await wh.products(query, start=start, end=end, limit=40)
        finally:
            await wh.aclose()
        with engine.connect() as conn:
            notes = suppliers.notes(conn, owner)
        items = [
            {
                "product_id": i["product_id"],
                "title": routes.display(i.get("title", "")),
                "days": routes._days(i),
                "depart_city": routes._city(i),
                "supplier": suppliers.view(*routes.supplier_of(i), notes),
            }
            for i in page.get("items", [])
        ]
        facet = {}
        for i in items:
            facet.setdefault(i["supplier"]["id"], {**i["supplier"], "count": 0})["count"] += 1
        return {
            "items": [i for i in items if not supplier or i["supplier"]["id"] == supplier],
            "suppliers": sorted(facet.values(), key=lambda f: -f["count"]),
        }

    @app.get("/api/suppliers")
    def supplier_list(session: Session):
        with engine.connect() as conn:
            notes = suppliers.notes(conn, owner_of(session))
        return {"items": [suppliers.view(k, v["name"], notes) for k, v in notes.items()]}

    @app.put("/api/suppliers/{supplier_id}")
    def supplier_note(supplier_id: str, body: SupplierNote, session: Session):
        with engine.begin() as conn:
            return suppliers.save(
                conn, owner_of(session), supplier_id, body.name, body.stance, body.note
            )

    @app.get("/api/routes/{product_id}")
    async def route(product_id: str, session: Session, deal: UUID | None = None):
        owner = owner_of(session)
        wh = warehouse(session)
        try:
            detail_ = await wh.product(product_id)
            departure = None
            if deal:
                with engine.connect() as conn:
                    d = store.deal(conn, owner, deal)
                    if (d.get("route") or {}).get("product_id") == product_id:
                        departure = (d.get("departure") or {}).get("departure_id")
            doc = await routes.document(wh, product_id, departure)
        finally:
            await wh.aclose()
        view = Route(product_id, routes.display(detail_.get("title", "")), (doc or {}).get("body"))
        with engine.connect() as conn:
            notes = suppliers.notes(conn, owner)
        return {
            "product_id": product_id,
            "title": view.title,
            "supplier": suppliers.view(*routes.supplier_of(detail_), notes),
            "days": routes._days(detail_),
            "depart_city": routes._city(detail_),
            "published": bool(view.days),
            "reviewed": view.reviewed,
            "notice": view.notice,
            "grid": view.grid(),
            "watch": [{"text": f["text"], "section": f["section"]} for f in view.watch()],
            "days_outline": view.outline(),
            "facts": len(view.facts()),
        }

    @app.get("/api/routes/{product_id}/page")
    async def route_page(product_id: str, session: Session, deal: UUID | None = None):
        """The whole published route, drawn by the route kit's own page in its customer view."""
        owner = owner_of(session)
        departure = None
        if deal:
            with engine.connect() as conn:
                d = store.deal(conn, owner, deal)
                if (d.get("route") or {}).get("product_id") == product_id:
                    departure = (d.get("departure") or {}).get("departure_id")
        wh = warehouse(session)
        try:
            doc = await routes.document(wh, product_id, departure)
            body = (doc or {}).get("body")
            if not body or body.get("schema") != "route-kit/1":
                raise HTTPException(404, "这条线路还没有发布完整行程")
            cover = await wh.picture(body["cover_asset_id"]) if body.get("cover_asset_id") else None
        finally:
            await wh.aclose()
        html = route_page_html(body, cover[0] if cover else None, audience="customer")
        return HTMLResponse(
            html,
            headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
        )

    @app.post("/api/routes/{product_id}/ask")
    async def ask_route(product_id: str, body: Ask, session: Session):
        owner = owner_of(session)
        wh = warehouse(session)
        try:
            doc = await routes.document(wh, product_id)
            detail_ = await wh.product(product_id)
            view = Route(
                product_id, routes.display(detail_.get("title", "")), (doc or {}).get("body")
            )
            facts = [f for f in view.facts() if f["reviewed"]] or view.facts()
            from .model import Question

            fake_deal = {
                "route": {"product_id": product_id, "title": view.title},
                "departure": None,
            }
            items, _ = (
                await turns.answer(
                    owner,
                    _Fixed(doc),
                    fake_deal,
                    Need(),
                    {"visible": []},
                    [Question(text=privacy.mask(body.question)[0], route="")],
                )
                if facts
                else ([], [])
            )
        finally:
            await wh.aclose()
        return {"items": items}

    @app.post("/api/deals/{deal_id}/compare")
    async def compare(deal_id: UUID, body: Ids, session: Session):
        wh = warehouse(session)
        try:
            return await selling.compare(engine, owner_of(session), wh, deal_id, body.product_ids)
        finally:
            await wh.aclose()

    @app.post("/api/deals/{deal_id}/plans", status_code=201)
    async def plan(deal_id: UUID, body: Ids, session: Session):
        wh = warehouse(session)
        try:
            return await selling.build_plan(
                engine, owner_of(session), wh, deal_id, body.product_ids
            )
        finally:
            await wh.aclose()

    @app.get("/api/deals/{deal_id}/plans")
    def plan_list(deal_id: UUID, session: Session):
        owner = owner_of(session)
        with engine.connect() as conn:
            store.deal(conn, owner, deal_id)
            return {"items": selling.plans(conn, owner, deal_id)}

    @app.post("/api/deals/{deal_id}/plans/{plan_id}/share")
    def share(deal_id: UUID, plan_id: UUID, session: Session):
        token = selling.share(engine, owner_of(session), deal_id, plan_id)
        return {"token": token, "path": f"/p/{token}"}

    @app.get("/api/public/plans/{token}")
    def public_plan(token: str, signal: str | None = None, route: int = 0):
        result = selling.public(engine, token, signal, route)
        if result is None:
            raise HTTPException(404, "方案不存在")
        return JSONResponse(
            result, headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex"}
        )

    @app.post("/api/deals/{deal_id}/route")
    def choose_route(deal_id: UUID, body: RouteChoice, session: Session):
        return closing.choose_route(
            engine,
            owner_of(session),
            deal_id,
            body.product_id,
            body.title,
            body.supplier.model_dump() if body.supplier else None,
        )

    # ------------------------------------------------------------ closing

    @app.delete("/api/deals/{deal_id}/route")
    def release_route(deal_id: UUID, session: Session):
        return closing.release_route(engine, owner_of(session), deal_id)

    @app.get("/api/deals/{deal_id}/dates")
    async def dates(deal_id: UUID, session: Session, compare: str = ""):
        wh = warehouse(session)
        try:
            return await closing.dates(
                engine, owner_of(session), wh, deal_id, [c for c in compare.split(",") if c]
            )
        finally:
            await wh.aclose()

    @app.post("/api/deals/{deal_id}/dates/{departure_id}/price")
    async def price(
        deal_id: UUID, departure_id: str, session: Session, offer_id: str | None = None
    ):
        wh = warehouse(session)
        try:
            return await closing.price_departure(
                engine, owner_of(session), wh, deal_id, departure_id, offer_id
            )
        finally:
            await wh.aclose()

    @app.post("/api/deals/{deal_id}/departure")
    def departure(deal_id: UUID, body: Departure, session: Session):
        return closing.choose_departure(engine, owner_of(session), deal_id, body.model_dump())

    @app.get("/api/deals/{deal_id}/confirmation")
    def confirmation(deal_id: UUID, session: Session):
        owner = owner_of(session)
        with engine.connect() as conn:
            deal = store.deal(conn, owner, deal_id)
            rows = store.rows(
                conn,
                owner,
                db.confirmations,
                deal_id,
                order=db.confirmations.c.created_at.desc(),
                where=[db.confirmations.c.status != "void"],
            )
            return {"current": closing.confirmation_view(rows[0], deal) if rows else None}

    @app.post("/api/deals/{deal_id}/confirmation", status_code=201)
    def open_confirmation(deal_id: UUID, session: Session):
        return closing.open_confirmation(engine, owner_of(session), deal_id)

    @app.post("/api/deals/{deal_id}/confirmation/{confirmation_id}")
    def confirm(deal_id: UUID, confirmation_id: UUID, body: ConfirmIn, session: Session):
        return closing.record_confirmation(
            engine, owner_of(session), deal_id, confirmation_id, body.evidence, body.confirmed
        )

    @app.post("/api/deals/{deal_id}/quotes/formal", status_code=201)
    async def formal(deal_id: UUID, session: Session):
        wh = warehouse(session)
        try:
            return await closing.formal_quote(engine, owner_of(session), wh, deal_id)
        finally:
            await wh.aclose()

    @app.get("/api/deals/{deal_id}/quotes")
    def quotes(deal_id: UUID, session: Session):
        owner = owner_of(session)
        with engine.connect() as conn:
            deal = store.deal(conn, owner, deal_id)
            need = Need.model_validate(deal["need"])
            rows = store.rows(conn, owner, db.quotes, deal_id, order=db.quotes.c.created_at.desc())
            return {"items": [closing.quote_view(r, deal, need) for r in rows[:10]]}

    @app.put("/api/deals/{deal_id}/quotes/{quote_id}")
    def sales_total(deal_id: UUID, quote_id: UUID, body: SalesTotal, session: Session):
        return closing.set_sales_total(
            engine, owner_of(session), deal_id, quote_id, body.sales_total
        )

    @app.post("/api/deals/{deal_id}/quotes/{quote_id}/send")
    def send_quote(deal_id: UUID, quote_id: UUID, session: Session):
        return closing.send_quote(engine, owner_of(session), deal_id, quote_id)

    @app.post("/api/deals/{deal_id}/sale")
    def sale(deal_id: UUID, body: Sale, session: Session):
        return closing.record_sale(engine, owner_of(session), deal_id, body.quote_id, body.deposit)

    @app.post("/api/deals/{deal_id}/receipts")
    def receipt(
        deal_id: UUID,
        body: Receipt,
        session: Session,
        idempotency_key: Annotated[str | None, Header(max_length=80)] = None,
    ):
        return closing.add_receipt(
            engine, owner_of(session), deal_id, body.amount, body.note, key=idempotency_key
        )

    @app.get("/api/deals/{deal_id}/after")
    def after(deal_id: UUID, session: Session):
        return closing.after(engine, owner_of(session), deal_id)

    @app.post("/api/tasks/{task_id}/done", status_code=204)
    def task_done(task_id: UUID, session: Session):
        closing.finish_task(engine, owner_of(session), task_id)

    @app.get("/api/deals/{deal_id}/itinerary")
    async def itinerary(deal_id: UUID, session: Session, product_id: str | None = None):
        owner = owner_of(session)
        with engine.connect() as conn:
            deal = store.deal(conn, owner, deal_id)
            product = product_id or (deal.get("route") or {}).get("product_id")
            if not product:
                raise store.Conflict("先选定一条线路")
            notes = [
                closing.note_view(n)
                for n in store.rows(
                    conn,
                    owner,
                    db.notes,
                    deal_id,
                    order=db.notes.c.created_at,
                    where=[db.notes.c.product_id == product],
                )
            ]
            qa_rows = [
                q for q in memory.qa_book(conn, owner, deal_id) if q["product_id"] == product
            ]
        wh = warehouse(session)
        try:
            doc = await routes.document(
                wh, product, (deal.get("departure") or {}).get("departure_id")
            )
            detail_ = await wh.product(product)
        finally:
            await wh.aclose()
        view = Route(product, routes.display(detail_.get("title", "")), (doc or {}).get("body"))
        return {
            "product_id": product,
            "title": view.title,
            "days": view.outline(),
            "notes": notes,
            "questions": qa_rows,
            "flow": closing.NOTE_FLOW,
        }

    @app.post("/api/deals/{deal_id}/notes", status_code=201)
    def note(deal_id: UUID, body: NoteIn, session: Session):
        return closing.add_note(
            engine,
            owner_of(session),
            deal_id,
            body.product_id,
            body.day,
            body.node,
            body.category,
            privacy.mask(body.text)[0],
            body.amount,
            body.currency,
            body.quantity,
        )

    @app.get("/api/deals/{deal_id}/documents")
    def documents(deal_id: UUID, session: Session):
        return papers.overview(engine, owner_of(session), deal_id)

    @app.post("/api/deals/{deal_id}/documents", status_code=201)
    async def upload(deal_id: UUID, session: Session, file: Annotated[UploadFile, File()]):
        body = await file.read(papers.MAX_BYTES + 1)
        return papers.upload(
            engine,
            owner_of(session),
            settings.material_dir,
            deal_id,
            file.filename or "扫描件",
            file.content_type or "",
            body,
        )

    @app.post("/api/deals/{deal_id}/documents/{document_id}/confirm")
    def confirm_document(
        deal_id: UUID,
        document_id: UUID,
        session: Session,
        traveler: Annotated[int | None, Form()] = None,
    ):
        return papers.confirm(engine, owner_of(session), deal_id, document_id, traveler)

    return app


class _Fixed:
    """A warehouse stand-in that already holds one document (for route-only questions)."""

    def __init__(self, doc):
        self.doc = doc

    async def document(self, product_id, departure_id=None):
        return self.doc


# ---------------------------------------------------------------- views


def need_view(need: Need):
    out = []
    for field in needs.FIELDS:
        v = getattr(need, field)
        value = need.get(field)
        out.append(
            {
                "field": field,
                "label": needs.LABELS[field],
                "value": v.value,
                "text": needs.show(field, value) if value not in (None, []) else "",
                "source": v.source,
                "evidence": v.evidence,
                "hint": v.hint,
            }
        )
    beds = needs.beds_text(need.get("party"))
    return {"fields": out, "beds": beds, "summary": needs.summary(need)}


def deal_card(conn, owner, d):
    need = Need.model_validate(d["need"])
    gates = needs.gates(need)
    s = stage(d, need)
    pending = [
        p
        for p in store.rows(conn, owner, db.proposals, d["id"])
        if any(i["status"] == "pending" for i in p["items"])
    ]
    quotes = store.rows(
        conn,
        owner,
        db.quotes,
        d["id"],
        where=[db.quotes.c.status == "active"],
        order=db.quotes.c.created_at.desc(),
    )
    tag = None
    if pending:
        tag = {"text": "有变更待确认", "tone": "sun"}
    elif (
        quotes
        and quotes[0]["valid_until"]
        and quotes[0]["valid_until"] - datetime.now(UTC) < timedelta(hours=24)
        and d["status"] != "won"
    ):
        tag = {"text": "报价今天到期", "tone": "sun"}
    elif d["waiting_reply"]:
        tag = {"text": "有新消息", "tone": "br"}
    elif d["status"] == "won":
        tag = {"text": "已成交", "tone": "ok"}
    elif not gates["quote"]["ready"] and gates["search"]["ready"]:
        tag = {
            "text": "缺" + needs.MISSING_TEXT[gates["quote"]["missing"][0]].replace("？", ""),
            "tone": "gray",
        }
    parts = []
    party = need.get("party")
    if party and party.adults is not None:
        parts.append(needs.party_text(party).replace(" ", ""))
    route = (
        (d.get("route") or {}).get("title") or needs.show("destinations", need.get("destinations"))
        if need.get("destinations")
        else (d.get("route") or {}).get("title")
    )
    if route:
        parts.append(route)
    when = (d.get("departure") or {}).get("date") or (
        needs.show("window", need.get("window")) if need.get("window") else ""
    )
    if when:
        parts.append(when)
    return {
        "id": str(d["id"]),
        "title": d["title"],
        "line": " · ".join(parts) or "还没有需求",
        "stage": s,
        "status": d["status"],
        "tag": tag,
        "next": d["next_step"],
        "needs_me": bool(pending or d["waiting_reply"] or tag and tag["tone"] == "sun"),
        "updated_at": d["updated_at"].isoformat(),
    }


def detail(conn, owner, deal_id):
    from . import queries

    deal = store.deal(conn, owner, deal_id)
    need = Need.model_validate(deal["need"])
    gates = needs.gates(need)
    turns_ = store.turns(conn, owner, deal_id, limit=40)
    proposals = [
        {
            "id": str(p["id"]),
            "from": p["base_version"],
            "items": p["items"],
            "impact": p["impact"],
            "turn": p["turn"],
        }
        for p in store.rows(conn, owner, db.proposals, deal_id, order=db.proposals.c.created_at)
    ]
    pending = [p for p in proposals if any(i["status"] == "pending" for i in p["items"])]
    latest = store.rows(conn, owner, db.searches, deal_id, order=db.searches.c.created_at.desc())
    latest = [r for r in latest if queries.matches_search(deal, r)]
    plans = selling.plans(conn, owner, deal_id)
    quotes = store.rows(conn, owner, db.quotes, deal_id, order=db.quotes.c.created_at.desc())
    confirmations = store.rows(
        conn,
        owner,
        db.confirmations,
        deal_id,
        order=db.confirmations.c.created_at.desc(),
        where=[db.confirmations.c.status != "void"],
    )
    result_stub = {
        "cards": [{"type": "routes", "earlier": True, **latest[0]["body"]}] if latest else []
    }
    return {
        "id": str(deal["id"]),
        "title": deal["title"],
        "status": deal["status"],
        "stage": stage(deal, need),
        "version": deal["need_version"],
        "need": need_view(need),
        "query": queries.view(deal),
        "gates": gates,
        "clarity": needs.clarity(need),
        "route": deal.get("route"),
        "departure": deal.get("departure"),
        "turns": [
            {
                "seq": t["seq"],
                "kind": t["kind"],
                "text": t["text"],
                "result": t["result"],
                "at": t["created_at"].isoformat(),
            }
            for t in turns_
        ],
        "pending": pending,
        "proposals": proposals,
        "search": {"id": str(latest[0]["id"]), **latest[0]["body"]} if latest else None,
        "plans": plans[:3],
        "quote": closing.quote_view(quotes[0], deal, need) if quotes else None,
        "confirmation": closing.confirmation_view(confirmations[0], deal)
        if confirmations
        else None,
        "chips": chips(deal, need, gates, bool(pending), result_stub),
        "facts": len(requirement_facts(need)),
    }


def today(engine, owner):
    now = datetime.now(UTC)
    with engine.connect() as conn:
        deals_ = store.deals(conn, owner, limit=200)
        by_id = {d["id"]: d for d in deals_}
        urgent, waiting, timeline, chances = [], [], [], []
        for d in deals_:
            if d["status"] == "won":
                continue
            for q in store.rows(
                conn,
                owner,
                db.quotes,
                d["id"],
                where=[db.quotes.c.status == "active"],
                order=db.quotes.c.created_at.desc(),
            )[:1]:
                if q["valid_until"] and now < q["valid_until"] < now + timedelta(hours=24):
                    urgent.append(
                        {
                            "deal_id": str(d["id"]),
                            "title": d["title"],
                            "text": "报价今天失效",
                            "until": q["valid_until"].isoformat(),
                            "kind": "quote",
                        }
                    )
        for t in conn.execute(
            db.tasks.select()
            .where(owner.where(db.tasks), db.tasks.c.status == "open")
            .order_by(db.tasks.c.due_at)
        ).mappings():
            d = by_id.get(t["deal_id"])
            if not d or not t["due_at"]:
                continue
            if t["due_at"] <= now + timedelta(days=1):
                (urgent if t["due_at"] < now else timeline).append(
                    {
                        "deal_id": str(d["id"]),
                        "title": d["title"],
                        "text": t["text"],
                        "until": t["due_at"].isoformat(),
                        "kind": t["kind"],
                        "task_id": str(t["id"]),
                    }
                )
        for d in deals_:
            if not d["waiting_reply"]:
                continue
            last = store.turns(conn, owner, d["id"], limit=1)
            last = last[0] if last else None
            result = (last or {}).get("result") or {}
            waiting.append(
                {
                    "deal_id": str(d["id"]),
                    "title": d["title"],
                    "at": (d["last_customer_at"] or d["updated_at"]).isoformat(),
                    "message": (last or {}).get("text", "")[:80],
                    "hint": result.get("to_advisor") or d["next_step"],
                    "action": (result.get("chips") or [{"label": "看看"}])[0]["label"],
                }
            )
        for p in conn.execute(
            db.plans.select()
            .where(owner.where(db.plans), db.plans.c.last_view_at.isnot(None))
            .order_by(db.plans.c.last_view_at.desc())
            .limit(10)
        ).mappings():
            if p["last_view_at"] > now - timedelta(days=1) and p["deal_id"] in by_id:
                signal = p["signals"][-1] if p["signals"] else None
                chances.append(
                    {
                        "deal_id": str(p["deal_id"]),
                        "title": by_id[p["deal_id"]]["title"],
                        "text": (
                            (
                                "客人点了“就选这个”："
                                if signal["signal"] == "select"
                                else "客人想问问："
                            )
                            + signal["route"]
                        )
                        if signal
                        else f"打开了方案 {p['views']} 次",
                        "at": p["last_view_at"].isoformat(),
                    }
                )
    urgent.sort(key=lambda x: x["until"])
    return {
        "date": date.today().isoformat(),
        "advisor": owner.name,
        "urgent": urgent,
        "waiting": waiting,
        "timeline": timeline,
        "chances": chances,
    }

"""Organization-scoped API for human clients; model tools never receive auth tokens."""

import asyncio
import os
from contextlib import asynccontextmanager, suppress
from datetime import date
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import quote as url_quote
from urllib.parse import unquote
from uuid import UUID

from anyio import CancelScope
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool

from commerce_common.streaming import AgentEvent, to_sse
from merchant_agent.changes import ChangeNotApplicable, GuardrailViolation
from merchant_agent.types import (
    InventoryActionItem,
    MerchantSessionContext,
    MerchantSessionState,
    PriceUpdateItem,
)
from shopping_agent import NotOffered, Product, SearchFilters, ShoppingSessionContext
from shopping_agent.types import ShoppingSessionState

from . import (
    advisor_actions,
    advisor_stages,
    auth,
    catalog,
    conversations,
    distribution,
    document_fetches,
    documents,
    imports,
    inventory,
    invitations,
    legacy_cases,
    legacy_references,
    management,
    merchant_business,
    merchant_source_reads,
    operations,
    outbox,
    price_books,
    pricing,
    product_display,
    product_facts,
    quote_shares,
    quotes,
    route_editor,
    route_tags,
    sales,
    sources,
    sync_management,
    trip_brief,
)
from . import changes as change_service
from .advisor import WarehouseAdvisorBackend, parse_id
from .assets import ObjectStore
from .http_metrics import HttpMetrics
from .http_observation import HttpObservation, configure_logging
from .integrations import SourceError, canonical
from .merchant import WarehouseMerchantBackend
from .offers import Command as OfferCommand
from .persistence import (
    Forbidden,
    Principal,
    engine_for,
    require_role,
    require_runtime_role,
    transaction,
)


class LoginBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)


class ApprovalBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    payload_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class VerifyCustomerBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connection_id: UUID
    customer_code: str = Field(min_length=1, max_length=128)


class BindingAcceptanceBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1, strict=True)


class MerchantContentBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    listing_id: UUID
    title: str | None = Field(default=None, min_length=1, max_length=300)
    long_description: str | None = Field(default=None, max_length=10000)
    note: str | None = Field(default=None, max_length=200)


class MerchantPriceItem(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    listing_id: UUID
    new_price: float = Field(gt=0, strict=True)


class MerchantPriceBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    buyer_org_id: UUID
    items: list[MerchantPriceItem] = Field(min_length=1, max_length=25)
    note: str | None = Field(default=None, max_length=200)


class MerchantStockItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    listing_id: UUID
    action: str = Field(pattern="^(restock|pause|activate)$")
    quantity: int | None = Field(default=None, ge=1, strict=True)


class MerchantStockBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[MerchantStockItem] = Field(min_length=1, max_length=25)
    note: str | None = Field(default=None, max_length=200)


class AdvisorQuoteBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    departure_id: str
    offer_id: UUID | None = None
    party: quotes.Party


class AdvisorProductPage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[Product]
    next_cursor: str | None


class AdvisorDeparturePage(AdvisorProductPage):
    ordering: Literal["window_date_id"]
    out_of_window_total: int
    reservation_created: Literal[False]


class ConversationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: str = Field(pattern="^(advisor|merchant)$")
    buyer_context: UUID | None = None


class ChatBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=4000)


def create_app(
    runtime: Engine,
    authentication: Engine,
    connectors: quotes.Connectors | None = None,
    agent_factory=None,
    object_store: ObjectStore | None = None,
    metrics_token: str | None = None,
) -> FastAPI:
    from .chat_delivery import DeliveryPool

    deliveries = DeliveryPool()

    @asynccontextmanager
    async def lifespan(app):
        await run_in_threadpool(require_runtime_role, runtime)
        await run_in_threadpool(auth.require_auth_role, authentication)
        try:
            yield
        finally:
            await deliveries.close()

    app = FastAPI(title="Tour 云仓", version="0.1.0", lifespan=lifespan)
    metrics = HttpMetrics(metrics_token) if metrics_token is not None else None
    bearer = HTTPBearer(auto_error=False)

    async def token(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ) -> str:
        if not credentials or credentials.scheme.lower() != "bearer":
            raise auth.Unauthenticated("请先登录")
        return credentials.credentials

    def user(access: Annotated[str, Depends(token)]) -> UUID:
        return auth.authenticate(authentication, access)

    def principal(
        request: Request,
        access: Annotated[str, Depends(token)],
        x_organization_id: Annotated[UUID, Header()],
    ) -> Principal:
        user_id = auth.authenticate(authentication, access, organization_id=x_organization_id)
        request.state.observed_organization_id = x_organization_id
        return Principal(user_id, x_organization_id)

    @app.exception_handler(auth.Unauthenticated)
    async def unauthorized(request, error):
        return JSONResponse(
            {"code": "UNAUTHENTICATED", "message": str(error)},
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )

    @app.exception_handler(auth.RateLimited)
    async def limited(request, error):
        return JSONResponse(
            {"code": "RATE_LIMITED", "message": str(error)},
            status_code=429,
            headers={"Retry-After": "900"},
        )

    @app.exception_handler(Forbidden)
    async def forbidden(request, error):
        return JSONResponse({"code": "FORBIDDEN", "message": str(error)}, status_code=403)

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request, error):
        # Driver messages can contain source values; a lost commit response is ambiguous.
        return JSONResponse(
            {
                "code": "DATABASE_OPERATION_UNCONFIRMED",
                "message": "数据库操作暂未取得可确认的结果；若刚提交变更，请先查询原变更状态，再决定是否重试。",
            },
            status_code=503,
        )

    @app.exception_handler(ValueError)
    async def invalid_input(request, error):
        return JSONResponse({"code": "INVALID_INPUT", "message": str(error)}, status_code=422)

    @app.exception_handler(trip_brief.BriefIncomplete)
    async def brief_incomplete(request, error):
        return JSONResponse(
            {"code": "BRIEF_INCOMPLETE", "message": str(error), "missing": error.missing},
            status_code=422,
        )

    @app.exception_handler(inventory.Conflict)
    async def conflict(request, error):
        return JSONResponse(
            {"code": "VERSION_OR_BUSINESS_CONFLICT", "message": str(error)}, status_code=409
        )

    @app.exception_handler(SourceError)
    async def source_error(request, error):
        return JSONResponse(
            {
                "code": error.code,
                "message": "供应商数据暂不能完成验证或查询",
                "retryable": error.retryable,
            },
            status_code=503 if error.retry_after_seconds else 502,
            headers={"Retry-After": str(error.retry_after_seconds)}
            if error.retry_after_seconds
            else None,
        )

    @app.exception_handler(ChangeNotApplicable)
    @app.exception_handler(NotOffered)
    @app.exception_handler(GuardrailViolation)
    async def merchant_conflict(request, error):
        return JSONResponse(
            {"code": "MERCHANT_CHANGE_REFUSED", "message": str(error)}, status_code=409
        )

    app.add_middleware(HttpObservation, metrics=metrics)

    if metrics:

        @app.get("/metrics", include_in_schema=False)
        def metrics_export(request: Request):
            if not metrics.authorized(request.headers.get("authorization", "")):
                raise HTTPException(
                    401, "指标访问需要运维凭据", headers={"WWW-Authenticate": "Bearer"}
                )
            content, content_type = metrics.render()
            return Response(content, headers={"Content-Type": content_type})

    @app.get("/health")
    def health():
        with runtime.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ok", "advisor_transactions": False}

    @app.post("/v1/auth/login")
    def login(body: LoginBody, request: Request):
        return auth.login(
            authentication,
            body.email,
            body.password,
            request.client.host if request.client else "unknown",
        )

    @app.post("/v1/auth/logout", status_code=204)
    def logout(access: Annotated[str, Depends(token)], user_id: Annotated[UUID, Depends(user)]):
        auth.logout(authentication, access)

    @app.get("/v1/me/organizations")
    def organizations(user_id: Annotated[UUID, Depends(user)]):
        return {"items": auth.organizations(authentication, user_id)}

    @app.get("/v1/products")
    def products(
        actor: Annotated[Principal, Depends(principal)], limit: int = 50, after: UUID | None = None
    ):
        rows = catalog.list_products(runtime, actor, limit=limit, after=after)
        return {
            "items": rows,
            "next_cursor": str(rows[-1]["id"]) if len(rows) == max(1, min(limit, 100)) else None,
        }

    @app.get("/v1/departures")
    def departures(
        *,
        product_id: UUID | None = None,
        start: date | None = None,
        end: date | None = None,
        limit: int = 100,
        offset: int = 0,
        actor: Annotated[Principal, Depends(principal)],
    ):
        if start and end and end < start:
            raise HTTPException(422, "结束日期不得早于开始日期")
        return {
            "items": [
                catalog.departure_projection(row, actor)
                for row in catalog.list_departures(
                    runtime,
                    actor,
                    product_id=product_id,
                    start=start,
                    end=end,
                    limit=limit,
                    offset=offset,
                )
            ]
        }

    @app.get("/v1/operations")
    def operations_snapshot(actor: Annotated[Principal, Depends(principal)]):
        return operations.snapshot(runtime, actor)

    @app.get("/v1/search-worker")
    def search_worker_status(actor: Annotated[Principal, Depends(principal)]):
        return outbox.status(runtime, actor)

    @app.get("/v1/sync-runs")
    def sync_runs(
        actor: Annotated[Principal, Depends(principal)],
        connection_id: UUID | None = None,
        before: UUID | None = None,
        limit: int = Query(default=50, ge=1, le=100),
    ):
        return sync_management.runs(
            runtime, actor, connection_id=connection_id, before=before, limit=limit
        )

    @app.get("/v1/sync-jobs")
    def sync_jobs(
        actor: Annotated[Principal, Depends(principal)],
        after: UUID | None = None,
        limit: int = Query(default=100, ge=1, le=100),
    ):
        return sync_management.jobs(runtime, actor, after=after, limit=limit)

    @app.post("/v1/sync-jobs/{job_id}/control")
    def control_sync_job(
        job_id: UUID,
        body: sync_management.Control,
        actor: Annotated[Principal, Depends(principal)],
    ):
        return sync_management.control(runtime, actor, job_id, body)

    @app.get("/v1/source-issues")
    def source_issues(
        run_id: UUID,
        actor: Annotated[Principal, Depends(principal)],
        after: UUID | None = None,
        limit: int = Query(default=100, ge=1, le=100),
    ):
        return sync_management.issues(runtime, actor, run_id, after=after, limit=limit)

    @app.get("/v1/source-issues/{issue_id}")
    def source_issue_detail(issue_id: UUID, actor: Annotated[Principal, Depends(principal)]):
        return sync_management.issue_detail(runtime, actor, issue_id)

    @app.post("/v1/inventory/proposals", status_code=201)
    def propose_inventory(
        body: inventory.InventoryCommand, actor: Annotated[Principal, Depends(principal)]
    ):
        return inventory.stage(runtime, actor, body)

    @app.get("/v1/changes")
    def changes(
        actor: Annotated[Principal, Depends(principal)],
        before: UUID | None = None,
        limit: int = Query(default=25, ge=1, le=100),
        status: Literal["staged", "applied", "discarded"] | None = None,
    ):
        return management.changes(
            runtime, authentication, actor, before=before, limit=limit, status=status
        )

    def document_store():
        if object_store is None:
            raise HTTPException(503, "当前未配置私有文档存储")
        return object_store

    @app.get("/v1/merchant/routes/{product_id}/content")
    def route_content_draft(
        product_id: UUID,
        actor: Annotated[Principal, Depends(principal)],
        include_source: bool = False,
    ):
        return route_editor.get(runtime, actor, product_id, include_source=include_source)

    @app.post("/v1/merchant/routes/{product_id}/content")
    def save_route_content(
        product_id: UUID,
        body: route_editor.SaveDraft,
        actor: Annotated[Principal, Depends(principal)],
    ):
        return route_editor.save(runtime, actor, product_id, body)

    @app.post("/v1/route-content-proposals", status_code=201)
    def propose_route_content(
        body: route_editor.PublishDraft, actor: Annotated[Principal, Depends(principal)]
    ):
        return route_editor.propose(runtime, actor, body)

    @app.post("/v1/merchant/routes/{product_id}/content/validate")
    def validate_route_content(
        product_id: UUID,
        body: route_editor.SaveDraft,
        actor: Annotated[Principal, Depends(principal)],
    ):
        return route_editor.validate(runtime, actor, product_id, body)

    @app.get("/v1/route-content-revisions/{revision_id}")
    def route_content_revision(revision_id: UUID, actor: Annotated[Principal, Depends(principal)]):
        return route_editor.revision_detail(runtime, actor, revision_id)

    @app.get("/v1/merchant/product-fact-conflicts")
    def product_fact_conflicts(actor: Annotated[Principal, Depends(principal)]):
        return {"items": product_facts.conflicts(runtime, actor)}

    @app.get("/v1/merchant/routes/{product_id}/customer-preview")
    def route_customer_preview(product_id: UUID, actor: Annotated[Principal, Depends(principal)]):
        return route_editor.preview(runtime, actor, product_id)

    @app.get("/v1/documents/{identifier}/parses/{parse_id}/extraction")
    def document_extraction_evidence(
        identifier: UUID, parse_id: UUID, actor: Annotated[Principal, Depends(principal)]
    ):
        return documents.extraction_detail(runtime, actor, identifier, parse_id)

    @app.get("/v1/merchant/routes/{product_id}/tags")
    def route_tag_detail(product_id: UUID, actor: Annotated[Principal, Depends(principal)]):
        return route_tags.get(runtime, actor, product_id)

    @app.post("/v1/route-tag-proposals", status_code=201)
    def propose_route_tags(
        body: route_tags.Command, actor: Annotated[Principal, Depends(principal)]
    ):
        return route_tags.propose(runtime, actor, body)

    @app.get("/v1/route-media/{media_id}")
    def route_image(media_id: UUID, actor: Annotated[Principal, Depends(principal)]):
        from . import route_media

        return Response(
            route_media.read(runtime, actor, document_store(), media_id),
            media_type="image/jpeg",
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )

    @app.post("/v1/documents", status_code=201)
    async def upload_document(
        request: Request,
        product_id: UUID,
        actor: Annotated[Principal, Depends(principal)],
        x_file_name: Annotated[str, Header()],
    ):
        store = document_store()
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > documents.MAX_BYTES:
                raise HTTPException(413, "文档超过 20 MB")
        return await run_in_threadpool(
            documents.upload, runtime, actor, store, product_id, unquote(x_file_name), bytes(body)
        )

    @app.get("/v1/documents")
    def document_list(
        product_id: UUID,
        actor: Annotated[Principal, Depends(principal)],
        before: UUID | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
    ):
        return documents.listing(runtime, actor, product_id, before=before, limit=limit)

    @app.get("/v1/documents/{asset_id}")
    def document_detail(asset_id: UUID, actor: Annotated[Principal, Depends(principal)]):
        return documents.get(runtime, actor, asset_id)

    @app.get("/v1/document-fetches")
    def document_fetch_list(product_id: UUID, actor: Annotated[Principal, Depends(principal)]):
        return {"items": document_fetches.listing(runtime, actor, product_id)}

    @app.post("/v1/document-fetches/{job_id}/retry")
    def retry_document_fetch(job_id: UUID, actor: Annotated[Principal, Depends(principal)]):
        return document_fetches.retry(runtime, actor, job_id)

    @app.get("/v1/documents/{asset_id}/file")
    def document_file(asset_id: UUID, actor: Annotated[Principal, Depends(principal)]):
        result = documents.download(runtime, actor, document_store(), asset_id)
        return Response(
            result["body"],
            media_type=result["media_type"],
            headers={
                "Content-Disposition": "attachment; filename*=UTF-8''"
                + url_quote(result["name"], safe=""),
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.post("/v1/documents/{asset_id}/retry")
    def retry_document(asset_id: UUID, actor: Annotated[Principal, Depends(principal)]):
        return documents.retry(runtime, actor, asset_id)

    @app.post("/v1/documents/{asset_id}/reparse", status_code=202)
    def reparse_document(
        asset_id: UUID,
        body: documents.ReparseRequest,
        actor: Annotated[Principal, Depends(principal)],
    ):
        return documents.reparse(runtime, actor, asset_id, body)

    @app.get("/v1/documents/{asset_id}/parses")
    def document_parse_history(
        asset_id: UUID,
        actor: Annotated[Principal, Depends(principal)],
        before: UUID | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 10,
    ):
        return documents.parse_history(runtime, actor, asset_id, before=before, limit=limit)

    @app.get("/v1/documents/{asset_id}/parses/{parse_id}")
    def document_parse_detail(
        asset_id: UUID,
        parse_id: UUID,
        actor: Annotated[Principal, Depends(principal)],
    ):
        return documents.parse_detail(runtime, actor, asset_id, parse_id)

    @app.post("/v1/document-proposals", status_code=201)
    def propose_document(
        body: documents.DocumentReview, actor: Annotated[Principal, Depends(principal)]
    ):
        return documents.stage(runtime, actor, body)

    @app.get("/v1/published-documents/{document_id}")
    def published_document(document_id: UUID, actor: Annotated[Principal, Depends(principal)]):
        return documents.published(runtime, actor, document_id)

    @app.post("/v1/imports", status_code=201)
    async def upload_import(
        request: Request,
        connection_id: UUID,
        actor: Annotated[Principal, Depends(principal)],
        x_file_name: Annotated[str, Header()] = "import.xlsx",
    ):
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > imports.MAX_BYTES:
                raise HTTPException(413, "文件超过 10 MB 限制")
        return await run_in_threadpool(
            imports.upload, runtime, actor, connection_id, unquote(x_file_name), bytes(body)
        )

    @app.post("/v1/imports/{batch_id}/preview")
    def preview_import(batch_id: UUID, actor: Annotated[Principal, Depends(principal)]):
        return imports.preview(runtime, actor, batch_id)

    @app.get("/v1/imports/{batch_id}/file")
    def import_file(batch_id: UUID, actor: Annotated[Principal, Depends(principal)]):
        with transaction(runtime, actor) as conn:
            require_role(conn, "supplier_admin", "inventory_manager", "auditor")
            row = (
                conn.execute(
                    text("SELECT file_body FROM import_batch WHERE id=:id"), {"id": batch_id}
                )
                .mappings()
                .one_or_none()
            )
            if not row:
                raise Forbidden("文件不存在或无权限")
            change_service.audit(conn, actor, "import.downloaded", batch_id, {})
            return Response(
                bytes(row["file_body"]),
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                headers={"Content-Disposition": 'attachment; filename="warehouse-import.xlsx"'},
            )

    @app.post("/v1/changes/{change_id}/approve")
    def approve(
        change_id: UUID, body: ApprovalBody, actor: Annotated[Principal, Depends(principal)]
    ):
        return change_service.approve(runtime, actor, change_id, body.payload_hash)

    @app.post("/v1/changes/{change_id}/apply")
    def apply(change_id: UUID, actor: Annotated[Principal, Depends(principal)]):
        return change_service.apply(runtime, actor, change_id)

    @app.post("/v1/changes/{change_id}/discard", status_code=204)
    def discard(change_id: UUID, actor: Annotated[Principal, Depends(principal)]):
        change_service.discard(runtime, actor, change_id)

    @app.get("/v1/advisor/legacy-cases")
    def legacy_case_list(
        actor: Annotated[Principal, Depends(principal)],
        before: UUID | None = None,
        limit: int = Query(default=25, ge=1, le=100),
    ):
        return legacy_cases.list_page(runtime, actor, before=before, limit=limit)

    @app.get("/v1/advisor/legacy-cases/{identifier}")
    def legacy_case_get(identifier: UUID, actor: Annotated[Principal, Depends(principal)]):
        return legacy_cases.get(runtime, actor, identifier)

    @app.post("/v1/advisor/legacy-references/resolve")
    def resolve_legacy_reference(
        body: legacy_references.Reference, actor: Annotated[Principal, Depends(principal)]
    ):
        return legacy_references.resolve(runtime, actor, body)

    @app.get("/v1/offers")
    def offers(
        departure_id: UUID,
        actor: Annotated[Principal, Depends(principal)],
        after: UUID | None = None,
        limit: int = Query(default=25, ge=1, le=100),
    ):
        from . import offers as offer_management

        return offer_management.list_for_departure(
            runtime, actor, departure_id, after=after, limit=limit
        )

    @app.post("/v1/prices/proposals", status_code=201)
    def price_proposal(
        body: pricing.ContractProposal, actor: Annotated[Principal, Depends(principal)]
    ):
        return pricing.propose(runtime, actor, body)

    @app.post("/v1/customer-bindings/verify")
    async def verify_customer(
        body: VerifyCustomerBody, actor: Annotated[Principal, Depends(principal)]
    ):
        if body.connection_id not in (connectors or {}):
            raise SourceError("SOURCE_PRICE_CONNECTOR_NOT_CONFIGURED")
        from .source_limits import open_connector

        async with open_connector(
            runtime, actor, body.connection_id, connectors[body.connection_id]
        ) as connector:
            return await pricing.verify_customer(
                runtime, actor, body.connection_id, body.customer_code, connector
            )

    @app.post("/v1/customer-bindings/proposals", status_code=201)
    def binding_proposal(
        body: pricing.BindingProposal, actor: Annotated[Principal, Depends(principal)]
    ):
        return pricing.propose(runtime, actor, body)

    @app.get("/v1/customer-bindings")
    def bindings(
        actor: Annotated[Principal, Depends(principal)],
        after: UUID | None = None,
        limit: int = 50,
        connection_id: UUID | None = None,
        buyer_org_id: UUID | None = None,
    ):
        limit = max(1, min(limit, 100))
        rows = pricing.list_bindings(
            runtime,
            actor,
            after=after,
            limit=limit + 1,
            connection_id=connection_id,
            buyer_org_id=buyer_org_id,
        )
        names = management.organization_names(
            authentication,
            list({row[key] for row in rows for key in ("buyer_org_id", "supplier_org_id")}),
        )
        for row in rows:
            row["buyer_name"] = names.get(str(row["buyer_org_id"]))
            row["supplier_name"] = names.get(str(row["supplier_org_id"]))
        return {
            "items": rows[:limit],
            "next_cursor": str(rows[limit - 1]["id"]) if len(rows) > limit else None,
        }

    @app.post("/v1/customer-bindings/{binding_id}/accept")
    def accept_binding(
        binding_id: UUID,
        body: BindingAcceptanceBody,
        actor: Annotated[Principal, Depends(principal)],
    ):
        return pricing.accept_binding(runtime, actor, binding_id, body.version)

    @app.post("/v1/quotes", status_code=201)
    async def quote(
        body: quotes.QuoteRequest,
        actor: Annotated[Principal, Depends(principal)],
        idempotency_key: Annotated[str, Header(min_length=1, max_length=128)],
    ):
        return await quotes.create(runtime, actor, body, idempotency_key, connectors)

    @app.get("/v1/merchant/price-books")
    def price_book_list(
        actor: Annotated[Principal, Depends(principal)],
        offer_id: UUID,
        after: UUID | None = None,
        limit: int = Query(default=25, ge=1, le=100),
    ):
        return price_books.list_prices(runtime, actor, offer_id=offer_id, after=after, limit=limit)

    @app.post("/v1/merchant/price-books/proposals", status_code=201)
    def price_book_proposal(
        body: price_books.PriceProposal, actor: Annotated[Principal, Depends(principal)]
    ):
        return price_books.propose(runtime, actor, body)

    @app.post("/v1/merchant/buyer-grades/proposals", status_code=201)
    def buyer_grade_proposal(
        body: price_books.GradeProposal, actor: Annotated[Principal, Depends(principal)]
    ):
        return price_books.propose(runtime, actor, body)

    @app.get("/v1/merchant/buyer-grades")
    def buyer_grade_list(
        actor: Annotated[Principal, Depends(principal)],
        connection_id: UUID,
        after: UUID | None = None,
        limit: int = Query(default=25, ge=1, le=100),
    ):
        return price_books.list_grades(
            runtime, actor, connection_id=connection_id, after=after, limit=limit
        )

    @app.get("/v1/quotes/{quote_id}")
    def quote_snapshot(quote_id: UUID, actor: Annotated[Principal, Depends(principal)]):
        return quotes.get(runtime, actor, quote_id)

    @app.get("/v1/quotes/{quote_id}/customer-view")
    def customer_quote_view(quote_id: UUID, actor: Annotated[Principal, Depends(principal)]):
        return quotes.customer_view(quotes.get(runtime, actor, quote_id))

    @app.post("/v1/quotes/{quote_id}/shares", status_code=201)
    def share_quote(
        quote_id: UUID, body: quote_shares.Create, actor: Annotated[Principal, Depends(principal)]
    ):
        return quote_shares.create(runtime, actor, quote_id, body)

    @app.get("/v1/quotes/{quote_id}/shares")
    def quote_share_list(quote_id: UUID, actor: Annotated[Principal, Depends(principal)]):
        return quote_shares.list_for_quote(runtime, actor, quote_id)

    @app.post("/v1/quote-shares/{share_id}/revoke")
    def revoke_quote_share(share_id: UUID, actor: Annotated[Principal, Depends(principal)]):
        return quote_shares.revoke(runtime, actor, share_id)

    @app.get("/v1/quote-shares")
    def quote_share_history(
        actor: Annotated[Principal, Depends(principal)],
        quote_id: UUID | None = None,
        before: UUID | None = None,
        limit: int = Query(default=25, ge=1, le=100),
        status: Literal["all", "active", "revoked", "expired"] = "all",
    ):
        return quote_shares.list_page(
            runtime, actor, quote_id=quote_id, before=before, limit=limit, status=status
        )

    @app.post("/v1/public/quote")
    def public_quote(body: quote_shares.Read):
        result = quote_shares.read(runtime, authentication, body.token)
        headers = {"X-Robots-Tag": "noindex, nofollow", "Referrer-Policy": "no-referrer"}
        if result is None:
            return JSONResponse(
                {"message": "报价已更新，请联系顾问"}, status_code=404, headers=headers
            )
        return JSONResponse(result, headers=headers)

    @app.get("/v1/inventory/{pool_id}/movements")
    def movements(
        pool_id: UUID,
        actor: Annotated[Principal, Depends(principal)],
        before: UUID | None = None,
        limit: int = Query(default=50, ge=1, le=100),
    ):
        return inventory.movements(runtime, actor, pool_id, before=before, limit=limit)

    def merchant(actor, buyer_org_id=None):
        # Context comes exclusively from the authenticated host, never from model text.
        backend = WarehouseMerchantBackend(runtime, actor, buyer_org_id=buyer_org_id)
        context = MerchantSessionContext(
            session_id="http", merchant_id=str(actor.organization_id), operator=str(actor.user_id)
        )
        return backend, context

    @app.get("/v1/merchant/overview")
    def merchant_overview(actor: Annotated[Principal, Depends(principal)]):
        return management.overview(runtime, actor)

    @app.get("/v1/merchant/offers")
    def merchant_offers(
        actor: Annotated[Principal, Depends(principal)],
        query: str = "",
        after: UUID | None = None,
        limit: int = 25,
    ):
        return management.offers(runtime, actor, query=query, after=after, limit=limit)

    @app.get("/v1/merchant/departures/{departure_id}/offers")
    def merchant_departure_offers(
        departure_id: UUID,
        actor: Annotated[Principal, Depends(principal)],
        after: UUID | None = None,
        limit: int = Query(default=25, ge=1, le=100),
    ):
        from . import offers as offer_management

        return offer_management.list_for_departure(
            runtime, actor, departure_id, after=after, limit=limit, merchant=True
        )

    @app.post("/v1/merchant/offers/proposals", status_code=201)
    def offer_proposal(body: OfferCommand, actor: Annotated[Principal, Depends(principal)]):
        from . import offers as offer_management

        return offer_management.propose(runtime, actor, body)

    @app.get("/v1/merchant/contract")
    def merchant_contract(
        offer_id: UUID, buyer_org_id: UUID, actor: Annotated[Principal, Depends(principal)]
    ):
        return management.contract(runtime, actor, offer_id, buyer_org_id)

    @app.get("/v1/merchant/connections")
    def merchant_connections(actor: Annotated[Principal, Depends(principal)]):
        return {"items": management.connections(runtime, actor)}

    @app.get("/v1/merchant/sources")
    def merchant_sources(
        actor: Annotated[Principal, Depends(principal)],
        after: UUID | None = None,
        limit: int = Query(default=25, ge=1, le=100),
    ):
        return sources.listing(runtime, actor, after=after, limit=limit)

    @app.get("/v1/distribution-invitations")
    def invitation_list(
        actor: Annotated[Principal, Depends(principal)],
        after: UUID | None = None,
        limit: int = Query(default=25, ge=1, le=100),
    ):
        return invitations.listing(runtime, actor, after=after, limit=limit)

    @app.post("/v1/distribution-invitations", status_code=201)
    def create_invitation(
        body: invitations.Create, actor: Annotated[Principal, Depends(principal)]
    ):
        return invitations.create(runtime, actor, body)

    @app.post("/v1/distribution-invitations/claim")
    def claim_invitation(body: invitations.Claim, actor: Annotated[Principal, Depends(principal)]):
        return invitations.claim(runtime, actor, body)

    @app.post("/v1/distribution-invitations/preview")
    def preview_invitation(
        body: invitations.Claim, actor: Annotated[Principal, Depends(principal)]
    ):
        return invitations.preview(runtime, actor, body)

    @app.post("/v1/distribution-invitations/{invitation_id}/decision")
    def decide_invitation(
        invitation_id: UUID,
        body: invitations.Decide,
        actor: Annotated[Principal, Depends(principal)],
    ):
        return invitations.decide(runtime, actor, invitation_id, body)

    @app.post("/v1/merchant/sources", status_code=201)
    def create_excel_source(body: sources.Create, actor: Annotated[Principal, Depends(principal)]):
        return sources.create_excel(runtime, actor, body)

    @app.post("/v1/merchant/sources/{source_id}/control")
    def control_source(
        source_id: UUID, body: sources.Control, actor: Annotated[Principal, Depends(principal)]
    ):
        return sources.control(runtime, actor, source_id, body)

    @app.get("/v1/merchant/partners")
    def merchant_partners(actor: Annotated[Principal, Depends(principal)]):
        return {"items": management.partners(runtime, authentication, actor)}

    @app.get("/v1/merchant/departures/{departure_id}/sales")
    def departure_sales(departure_id: UUID, actor: Annotated[Principal, Depends(principal)]):
        return sales.get(runtime, actor, departure_id)

    @app.post("/v1/merchant/departure-sales/preview", status_code=201)
    def propose_departure_sales(
        body: sales.Control, actor: Annotated[Principal, Depends(principal)]
    ):
        return sales.propose(runtime, actor, body)

    @app.get("/v1/merchant/grants")
    def merchant_grants(
        actor: Annotated[Principal, Depends(principal)],
        after: UUID | None = None,
        limit: int = Query(default=25, ge=1, le=100),
    ):
        return distribution.listing(runtime, authentication, actor, after=after, limit=limit)

    @app.post("/v1/merchant/grants/{grant_id}/control")
    def control_grant(
        grant_id: UUID,
        body: distribution.Control,
        actor: Annotated[Principal, Depends(principal)],
    ):
        return distribution.control(runtime, actor, grant_id, body)

    @app.get("/v1/merchant/imports")
    def merchant_imports(
        actor: Annotated[Principal, Depends(principal)],
        before: UUID | None = None,
        limit: int = Query(default=25, ge=1, le=100),
    ):
        return management.imports(runtime, actor, before=before, limit=limit)

    @app.get("/v1/merchant/inventory")
    def merchant_inventory_list(
        actor: Annotated[Principal, Depends(principal)], after: UUID | None = None, limit: int = 50
    ):
        return management.pools(runtime, actor, after=after, limit=limit)

    @app.get("/v1/merchant/products/{product_id}/display")
    def merchant_product_display(product_id: UUID, actor: Annotated[Principal, Depends(principal)]):
        return product_display.get(runtime, actor, product_id)

    @app.post("/v1/merchant/product-display/proposals", status_code=201)
    def propose_product_display(
        body: product_display.Command, actor: Annotated[Principal, Depends(principal)]
    ):
        return product_display.propose(runtime, actor, body)

    @app.get("/v1/merchant/routes")
    def business_routes(
        actor: Annotated[Principal, Depends(principal)],
        query: str = "",
        status: str = "",
        connection_id: UUID | None = None,
        page: int = Query(1, ge=1, le=10000),
    ):
        return merchant_business.routes(
            runtime, actor, query=query, status=status, connection_id=connection_id, page=page
        )

    @app.get("/v1/merchant/routes/{identifier}")
    def business_route(identifier: UUID, actor: Annotated[Principal, Depends(principal)]):
        return merchant_business.route(runtime, actor, identifier)

    @app.get("/v1/merchant/departures")
    def business_departures(
        actor: Annotated[Principal, Depends(principal)],
        query: str = "",
        product_id: UUID | None = None,
        connection_id: UUID | None = None,
        start: date | None = None,
        end: date | None = None,
        page: int = Query(1, ge=1, le=10000),
    ):
        return merchant_business.departures(
            runtime,
            actor,
            query=query,
            product_id=product_id,
            connection_id=connection_id,
            start=start,
            end=end,
            page=page,
        )

    @app.get("/v1/merchant/departures/{identifier}")
    def business_departure(identifier: UUID, actor: Annotated[Principal, Depends(principal)]):
        return merchant_business.departure(runtime, actor, identifier)

    @app.get("/v1/merchant/departures/{identifier}/observation")
    async def business_observation(
        identifier: UUID,
        actor: Annotated[Principal, Depends(principal)],
        verification_id: UUID | None = None,
    ):
        return await merchant_source_reads.observation(
            runtime, actor, connectors, identifier, verification_id
        )

    @app.get("/v1/merchant/external-orders/departments")
    async def order_departments(
        connection_id: UUID, actor: Annotated[Principal, Depends(principal)]
    ):
        return await merchant_source_reads.read(
            runtime, actor, connectors, connection_id, "read_order_departments", orders=True
        )

    @app.get("/v1/merchant/external-orders")
    async def external_orders(
        connection_id: UUID,
        company_id: int,
        actor: Annotated[Principal, Depends(principal)],
        page: int = Query(1, ge=1, le=10000),
        query: str = Query("", max_length=100),
        departure_id: int | None = None,
    ):
        return await merchant_source_reads.read(
            runtime,
            actor,
            connectors,
            connection_id,
            "read_orders",
            orders=True,
            company_id=company_id,
            page=page,
            query=query,
            departure_id=departure_id,
        )

    @app.get("/v1/merchant/external-orders/{order_id}")
    async def external_order(
        order_id: int,
        connection_id: UUID,
        company_id: int,
        actor: Annotated[Principal, Depends(principal)],
    ):
        return await merchant_source_reads.read(
            runtime,
            actor,
            connectors,
            connection_id,
            "read_order",
            orders=True,
            company_id=company_id,
            order_id=order_id,
        )

    @app.get("/v1/merchant/catalog")
    def merchant_catalog(
        actor: Annotated[Principal, Depends(principal)],
        query: str = "",
        after: UUID | None = None,
        limit: int = 25,
        buyer_org_id: UUID | None = None,
    ):
        backend, context = merchant(actor, buyer_org_id)
        return backend.catalog_page(context, query=query, after=after, limit=limit)

    @app.get("/v1/import-template")
    def import_template(actor: Annotated[Principal, Depends(principal)]):
        with transaction(runtime, actor) as conn:
            require_role(conn, "supplier_admin", "inventory_manager", "product_editor", "auditor")
        local = Path(__file__).resolve().parent.parent / "templates/供应商团期导入模板.xlsx"
        packaged = Path(__file__).resolve().parent / "templates/供应商团期导入模板.xlsx"
        return FileResponse(
            local if local.exists() else packaged,
            filename="供应商团期导入模板.xlsx",
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    def advisor(actor):
        return WarehouseAdvisorBackend(runtime, actor, connectors), ShoppingSessionContext(
            session_id="http", user_id=str(actor.user_id)
        )

    @app.post("/v1/conversations", status_code=201)
    def create_conversation(
        body: ConversationBody, actor: Annotated[Principal, Depends(principal)]
    ):
        return conversations.create(runtime, actor, body.role, body.buyer_context)

    @app.get("/v1/conversations")
    def list_conversations(
        actor: Annotated[Principal, Depends(principal)],
        query: str = Query(default="", max_length=120),
        role: Literal["advisor", "merchant"] = "advisor",
        before: UUID | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
    ):
        return conversations.list_page(
            runtime, actor, role=role, before=before, limit=limit, query=query
        )

    @app.get("/v1/conversations/{conversation_id}")
    def get_conversation(
        conversation_id: UUID,
        actor: Annotated[Principal, Depends(principal)],
        before: UUID | None = None,
        limit: Annotated[int, Query(ge=1, le=25)] = 10,
    ):
        return conversations.get(runtime, actor, conversation_id, before=before, limit=limit)

    @app.get("/v1/conversations/{conversation_id}/brief")
    def conversation_brief(conversation_id: UUID, actor: Annotated[Principal, Depends(principal)]):
        return trip_brief.get(runtime, actor, conversation_id)

    @app.patch("/v1/conversations/{conversation_id}/brief")
    def patch_brief(
        conversation_id: UUID,
        body: trip_brief.BriefPatch,
        actor: Annotated[Principal, Depends(principal)],
    ):
        return trip_brief.patch(runtime, actor, conversation_id, body)

    @app.post("/v1/conversations/{conversation_id}/actions")
    async def conversation_action(
        conversation_id: UUID,
        body: advisor_actions.Action,
        actor: Annotated[Principal, Depends(principal)],
    ):
        # Reuse the durable chat lease, so clicks and model turns cannot race.
        work = await run_in_threadpool(
            conversations.begin,
            runtime,
            actor,
            conversation_id,
            "action:" + str(body.request_id),
            "▸ "
            + {
                "search_routes": "检索线路",
                "departures": "看团期",
                "offers": "查看方案",
                "quote": "询价",
                "share_quote": "生成对客链接",
                "customer_confirmed": "标记客人确认",
                "offline_hold_recorded": "记录线下备注",
            }[body.action],
            request_payload=body.model_dump(mode="json"),
        )
        if "replay" in work:
            saved = await run_in_threadpool(trip_brief.get, runtime, actor, conversation_id)
            event = next((e for e in work["replay"] if e["type"] == "ui"), None)
            return {"brief": saved, **(event["data"] if event else {})}
        try:
            state = ShoppingSessionState.model_validate(work["state"])
            backend = WarehouseAdvisorBackend(runtime, actor, connectors)
            context = ShoppingSessionContext(
                session_id=str(conversation_id), user_id=str(actor.user_id)
            )
            result = await advisor_actions.perform(backend, context, state, body)
            public_payload = {k: v for k, v in result["payload"].items() if k != "token"}
            events = [
                AgentEvent.ui(result["component"], public_payload).model_dump(mode="json"),
                AgentEvent(type="turn_complete", data={}).model_dump(mode="json"),
            ]
            # Persist a human-readable action, never a cursor or bearer capability.
            await run_in_threadpool(
                conversations.finish_action,
                runtime,
                actor,
                work,
                state.model_dump(mode="json"),
                result["label"],
                events,
            )
            return result
        except BaseException:
            with CancelScope(shield=True):
                await run_in_threadpool(conversations.interrupt, runtime, actor, work)
            raise

    @app.post("/v1/conversations/{conversation_id}/chat")
    async def conversation_chat(
        conversation_id: UUID,
        body: ChatBody,
        actor: Annotated[Principal, Depends(principal)],
        access: Annotated[str, Depends(token)],
        idempotency_key: Annotated[str, Header(min_length=1, max_length=128)],
    ):
        if agent_factory is None:
            raise HTTPException(503, "当前未配置助手服务；目录和报价接口仍可使用")
        if not body.message.strip():
            raise HTTPException(422, "消息不能为空")
        if not deliveries.claim():
            raise HTTPException(503, "助手繁忙，请稍后重试")
        try:
            work = await run_in_threadpool(
                conversations.begin, runtime, actor, conversation_id, idempotency_key, body.message
            )
        except BaseException:
            deliveries.release()
            raise

        def check_access(turn_id=None):
            auth.authenticate(authentication, access, refresh=False)
            if turn_id is None:
                conversations.check_access(runtime, actor, conversation_id)
            else:
                conversations.checkpoint(runtime, actor, conversation_id, turn_id)

        async def stream():
            if "replay" in work:
                for item in work["replay"]:
                    await run_in_threadpool(check_access)
                    yield to_sse(AgentEvent.model_validate(item))
                return
            completed = False
            agent = None
            try:
                await run_in_threadpool(check_access, work["turn_id"])
                agent = agent_factory(work["role"], actor, work["buyer_context"])
                if hasattr(agent, "set_metrics"):
                    agent.set_metrics(metrics)
                if work["role"] == "advisor":
                    state = ShoppingSessionState.model_validate(work["state"])
                    context = ShoppingSessionContext(
                        session_id=str(conversation_id), user_id=str(actor.user_id)
                    )
                else:
                    state = MerchantSessionState.model_validate(work["state"])
                    state.approved_change_ids = await run_in_threadpool(
                        conversations.approved_changes, runtime, actor
                    )
                    context = MerchantSessionContext(
                        session_id=str(conversation_id),
                        merchant_id=str(actor.organization_id),
                        operator=str(actor.user_id),
                    )
                events, event_bytes, ending = [], 0, None
                async with asyncio.timeout(240):
                    async for event in agent.stream_turn(work["messages"], context, state):
                        await run_in_threadpool(check_access, work["turn_id"])
                        if event.type == "error":
                            raise RuntimeError("Agent turn failed")
                        if (
                            work["role"] == "advisor"
                            and event.type == "ui"
                            and event.data.get("component") == "suggestions"
                        ):
                            saved = await run_in_threadpool(
                                trip_brief.get, runtime, actor, conversation_id
                            )
                            event.data["payload"]["suggestions"] = (
                                advisor_stages.public_suggestions(
                                    trip_brief.TripBrief.model_validate(saved["body"]),
                                    event.data["payload"].get("suggestions", []),
                                    state.seen_products,
                                )
                            )
                        serialized = event.model_dump(mode="json")
                        if event.type == "ui" and event.data.get("component") == "warehouse_share":
                            serialized["data"]["payload"].pop("token", None)
                        event_bytes += len(canonical(serialized).encode())
                        if event_bytes > conversations.MAX_SAVED_BYTES:
                            raise change_service.Conflict("会话达到保存上限，请新建会话")
                        events.append(serialized)
                        if event.type == "turn_complete":
                            ending = event
                        else:
                            yield to_sse(event)
                if ending is None:
                    raise RuntimeError("Agent stream ended without completion")
                await run_in_threadpool(
                    conversations.finish,
                    runtime,
                    actor,
                    work,
                    state.model_dump(mode="json"),
                    work["messages"],
                    events,
                )
                completed = True
                yield to_sse(ending)
            except asyncio.CancelledError:
                raise
            except (Forbidden, auth.Unauthenticated):
                yield to_sse(
                    AgentEvent(type="error", data={"message": "登录或组织权限已失效，请重新登录。"})
                )
            except change_service.Conflict as error:
                yield to_sse(AgentEvent(type="error", data={"message": str(error)}))
            except Exception:
                yield to_sse(
                    AgentEvent(
                        type="error",
                        data={
                            "message": "本轮助手处理未完成；已批准的业务变更以审批中心状态为准，请核对后重试。"
                        },
                    )
                )
            finally:
                # Advisor turns belong to the application until saved or interrupted;
                # shutdown/timeout still closes resources and releases their lease.
                with CancelScope(shield=True):
                    try:
                        if not completed:
                            with suppress(Forbidden, change_service.Conflict):
                                await run_in_threadpool(
                                    conversations.interrupt, runtime, actor, work
                                )
                    finally:
                        if agent is not None and hasattr(agent, "aclose"):
                            await agent.aclose()

        if work.get("role") == "advisor" and "replay" not in work:
            delivered = deliveries.start(stream())
        else:
            deliveries.release()
            delivered = stream()
        return StreamingResponse(
            delivered,
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    @app.get("/v1/advisor/products", response_model=AdvisorProductPage)
    def advisor_products(
        actor: Annotated[Principal, Depends(principal)],
        query: str = "",
        after: UUID | None = None,
        limit: int = 50,
        start: date | None = None,
        end: date | None = None,
    ):
        backend, context = advisor(actor)
        attrs = {
            key: str(value)
            for key, value in (("depart_from", start), ("depart_to", end))
            if value is not None
        }
        return backend.catalog_page(
            context, query=query, after=after, limit=limit, filters=SearchFilters(attributes=attrs)
        )

    @app.get("/v1/advisor/context")
    def advisor_context(actor: Annotated[Principal, Depends(principal)]):
        from . import platform_sales

        return platform_sales.context(runtime, actor)

    @app.get("/v1/advisor/products/{product_id}")
    async def advisor_product(product_id: str, actor: Annotated[Principal, Depends(principal)]):
        backend, context = advisor(actor)
        result = await backend.get_product_details(context, product_id)
        if result is None:
            raise HTTPException(404, "线路或团期不存在或无权限")
        return result

    @app.get("/v1/advisor/products/{product_id}/document")
    def product_document(
        product_id: str,
        actor: Annotated[Principal, Depends(principal)],
        departure_id: str | None = None,
    ):
        result = documents.current(
            runtime,
            actor,
            parse_id(product_id, "WP-"),
            departure_id=parse_id(departure_id, "WD-") if departure_id else None,
            route_preview=departure_id is None,
        )
        if result is None:
            raise HTTPException(404, "当前线路暂无可读取的已发布行程")
        return result

    @app.get("/v1/advisor/departures", response_model=AdvisorDeparturePage)
    def advisor_departures(
        product_id: str,
        actor: Annotated[Principal, Depends(principal)],
        start: date | None = None,
        end: date | None = None,
        after: str | None = Query(default=None, max_length=1000),
        limit: int = 25,
    ):
        backend, context = advisor(actor)
        return backend.departures_page(
            context, product_id, start=start, end=end, after=after, limit=limit
        )

    @app.post("/v1/advisor/quotes", status_code=201)
    async def advisor_quote(
        body: AdvisorQuoteBody,
        actor: Annotated[Principal, Depends(principal)],
        idempotency_key: Annotated[str, Header(min_length=1, max_length=128)],
    ):
        backend, context = advisor(actor)
        return await backend.quote_departure(
            context,
            body.departure_id,
            body.party,
            offer_id=body.offer_id,
            idempotency_key=idempotency_key,
        )

    @app.get("/v1/advisor/offers")
    def advisor_offers(
        departure_id: str,
        actor: Annotated[Principal, Depends(principal)],
        after: UUID | None = None,
        limit: int = Query(default=25, ge=1, le=100),
    ):
        backend, context = advisor(actor)
        return backend.offers_page(context, departure_id, after=after, limit=limit)

    @app.get("/v1/merchant/listings")
    async def merchant_listings(
        actor: Annotated[Principal, Depends(principal)],
        query: str = "",
        limit: int = 50,
        buyer_org_id: UUID | None = None,
    ):
        backend, context = merchant(actor, buyer_org_id)
        return {"items": await backend.search_listings(context, query, limit=limit)}

    @app.get("/v1/merchant/listings/{listing_id}")
    async def merchant_listing(
        listing_id: UUID,
        actor: Annotated[Principal, Depends(principal)],
        buyer_org_id: UUID | None = None,
    ):
        backend, context = merchant(actor, buyer_org_id)
        listing = await backend.get_listing(context, str(listing_id))
        if not listing:
            raise HTTPException(404, "线路或团期不存在")
        return listing

    @app.get("/v1/merchant/snapshot")
    async def merchant_snapshot(actor: Annotated[Principal, Depends(principal)]):
        backend, context = merchant(actor)
        return await backend.get_business_snapshot(context)

    @app.post("/v1/merchant/content/proposals", status_code=201)
    async def merchant_content(
        body: MerchantContentBody, actor: Annotated[Principal, Depends(principal)]
    ):
        backend, context = merchant(actor)
        fields = body.model_dump(include={"title", "long_description"}, exclude_none=True)
        return await backend.stage_listing_update(context, str(body.listing_id), fields, body.note)

    @app.post("/v1/merchant/prices/proposals", status_code=201)
    async def merchant_prices(
        body: MerchantPriceBody, actor: Annotated[Principal, Depends(principal)]
    ):
        backend, context = merchant(actor, body.buyer_org_id)
        return await backend.stage_price_update(
            context,
            [
                PriceUpdateItem(listing_id=str(item.listing_id), new_price=item.new_price)
                for item in body.items
            ],
            body.note,
        )

    @app.post("/v1/merchant/inventory/proposals", status_code=201)
    async def merchant_inventory(
        body: MerchantStockBody, actor: Annotated[Principal, Depends(principal)]
    ):
        backend, context = merchant(actor)
        return await backend.stage_inventory_action(
            context,
            [
                InventoryActionItem(
                    listing_id=str(item.listing_id), action=item.action, quantity=item.quantity
                )
                for item in body.items
            ],
            body.note,
        )

    @app.get("/v1/merchant/changes")
    async def merchant_changes(actor: Annotated[Principal, Depends(principal)]):
        backend, context = merchant(actor)
        return {"items": await backend.get_pending_changes(context)}

    @app.post("/v1/orders")
    @app.post("/v1/holds")
    @app.post("/v1/payments")
    def disabled_transactions(actor: Annotated[Principal, Depends(principal)]):
        raise HTTPException(
            403,
            {
                "code": "TRANSACTIONS_DISABLED",
                "message": "一期仅支持查询和供应商库存管理，顾问占位、下单、支付未开放",
            },
        )

    if metrics:
        metrics.routes = frozenset(route.path for route in app.routes if hasattr(route, "path"))
    from .copilot_api import install as install_copilot

    install_copilot(app, runtime, principal, object_store, authentication)
    return app


def application() -> FastAPI:
    from .assets import LocalObjectStore

    configure_logging()
    root = os.environ.get("WAREHOUSE_OBJECT_ROOT")
    return create_app(
        engine_for(os.environ["WAREHOUSE_DATABASE_URL"]),
        engine_for(os.environ["WAREHOUSE_AUTH_URL"]),
        object_store=LocalObjectStore(Path(root)) if root else None,
        metrics_token=os.environ.get("WAREHOUSE_METRICS_TOKEN"),
    )

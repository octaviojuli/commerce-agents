import json
import runpy
import sys
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy import text

from cloud_warehouse import auth, cli, documents, pricing, quotes
from cloud_warehouse.admin import onboard
from cloud_warehouse.assets import LocalObjectStore
from cloud_warehouse.catalog import list_departures, list_products, synchronize
from cloud_warehouse.persistence import Forbidden, Principal, transaction
from tour.api import warehouse_app, warehouse_connector
from tour.api.http_erp import HttpErpClient

from .conftest import Tenant
from .test_api import PASSWORD
from .test_documents import docx, parsed, proposal
from .test_quotes import FUTURE, FakeSource, bind, commit, schedule
from .test_sync import Connector, batch

ROOT = Path(__file__).resolve().parents[2]


async def test_same_external_36_isolated_across_catalog_documents_and_quotes(
    database, tenant, tmp_path
):
    admin, runtime = database
    ids = onboard(admin, "ACME Supplier B", "ACME Buyer B", f"{uuid4()}@acme.example")
    supplier_id, buyer_id = UUID(ids["supplier_id"]), UUID(ids["buyer_id"])
    user = auth.create_user(
        admin,
        f"{uuid4()}@acme.example",
        PASSWORD,
        {supplier_id: ["supplier_admin"], buyer_id: ["buyer_admin", "advisor"]},
    )
    other = Tenant(
        Principal(user, supplier_id),
        Principal(user, buyer_id),
        Principal(UUID(ids["worker_id"]), supplier_id),
        UUID(ids["connection_id"]),
        UUID(ids["grant_id"]),
    )
    with admin.begin() as conn:
        conn.execute(
            text("UPDATE membership SET roles=ARRAY['advisor','buyer_admin'] WHERE user_id=:id"),
            {"id": tenant.buyer.user_id},
        )

    class Source(FakeSource):
        def __init__(self, amount, seats):
            super().__init__()
            self.amount, self.seats = amount, seats

        async def read_prices(self, departure_id, customer_id, company_id):
            value = await super().read_prices(departure_id, customer_id, company_id)
            return value.model_copy(
                update={"schedule": schedule(self.amount), "available_seats": self.seats}
            )

    def factory(source):
        @asynccontextmanager
        async def connect():
            yield source

        return connect

    store = LocalObjectStore(tmp_path / "objects")
    records, registry = [], {}
    for scope, label, amount, seats in (
        (tenant, "ACME A", "100.00", 3),
        (other, "ACME B", "200.00", 7),
    ):
        data = batch(seats)
        data.routes[0].update(routeId=36, routeCode="ACME-36", routeName=label)
        data.departures[0].update(
            periodId=36,
            routeId=36,
            periodCode="ACME-36",
            companyId=2,
            departDate=FUTURE.isoformat(),
            returnDate=(FUTURE + timedelta(days=2)).isoformat(),
        )
        await synchronize(runtime, scope.worker, scope.connection_id, Connector(data))
        product = list_products(runtime, scope.buyer)[0]
        departure = list_departures(runtime, scope.buyer)[0]
        offer = pricing.list_offers(runtime, scope.buyer, departure["id"])[0]
        body = docx(label)
        asset = UUID(
            documents.upload(runtime, scope.supplier, store, product["id"], "ACME.docx", body)["id"]
        )
        assert documents.run_once(
            runtime,
            scope.worker,
            store,
            lambda work, content: parsed(work, content).model_copy(update={"route_id": 36}),
        )
        published = commit(runtime, scope.supplier, proposal(runtime, scope, asset))
        source = Source(amount, seats)
        bound = await bind(runtime, scope, source)
        pricing.accept_binding(runtime, scope.buyer, UUID(bound["resource_id"]), 1)
        registry[scope.connection_id] = factory(source)
        request = quotes.QuoteRequest(
            offer_id=offer["id"], departure_date=FUTURE, party=quotes.Party()
        )
        quoted = await quotes.create(runtime, scope.buyer, request, "ACME-same-key-36", registry)
        assert (
            quoted["settlement_total"] == amount
            and quoted["availability"] == "available"
            and "available_seats" not in quoted
        )
        assert source.calls == [("36", "4101", "2")]
        records.append((scope, product, departure, asset, body, published, request, quoted, source))

    assert records[0][1]["id"] != records[1][1]["id"]
    assert records[0][2]["id"] != records[1][2]["id"]
    for index, record in enumerate(records):
        scope, product, departure, asset, body, published, _, quoted, source = record
        foreign = records[1 - index]
        assert [row["id"] for row in list_products(runtime, scope.buyer)] == [product["id"]]
        assert [row["id"] for row in list_departures(runtime, scope.buyer)] == [departure["id"]]
        assert list_departures(runtime, scope.buyer, product_id=foreign[1]["id"]) == []
        assert pricing.list_offers(runtime, scope.buyer, foreign[2]["id"]) == []
        assert documents.download(runtime, scope.buyer, store, asset)["body"] == body
        assert documents.current(runtime, scope.buyer, product["id"])["id"] == UUID(
            published["document_id"]
        )
        assert documents.current(runtime, scope.buyer, foreign[1]["id"]) is None
        assert (
            quotes.get(runtime, scope.buyer, UUID(quoted["quote_id"]))["quote_id"]
            == quoted["quote_id"]
        )
        with pytest.raises(Forbidden):
            documents.download(runtime, scope.buyer, store, foreign[3])
        with pytest.raises(Forbidden):
            documents.published(runtime, scope.buyer, UUID(foreign[5]["document_id"]))
        with pytest.raises(Forbidden):
            quotes.get(runtime, scope.buyer, UUID(foreign[7]["quote_id"]))
        with pytest.raises(Forbidden):
            await quotes.create(runtime, scope.buyer, foreign[6], "ACME-forged-36", registry)
    assert all(record[8].calls == [("36", "4101", "2")] for record in records)


def test_local_api_and_worker_share_settings_and_explicit_binding(tmp_path, monkeypatch):
    script = runpy.run_path(str(ROOT / "cloud-warehouse/scripts/local_warehouse.py"))
    local = tmp_path / ".warehouse"
    local.mkdir()
    environment = tmp_path / "examples/tour/.env"
    environment.parent.mkdir(parents=True)
    environment.write_text(
        "ACME_BIND_BASE_URL=https://b2b.acme.example/api\n"
        "ACME_BIND_MOBILE=ACME-user\nACME_BIND_PASSWORD=ACME-file-value\n"
    )
    namespace = script["source_registry"].__globals__
    monkeypatch.setitem(namespace, "ROOT", tmp_path)
    monkeypatch.setitem(namespace, "STATE", local)
    monkeypatch.delenv("WAREHOUSE_CONNECTORS_CONFIG", raising=False)
    monkeypatch.setenv("ACME_BIND_PASSWORD", "ACME-environment-value")
    monkeypatch.setattr(warehouse_connector, "connect", lambda settings: dict(settings))
    identifier = uuid4()
    context = {"connection_id": str(identifier)}
    (local / "connections.json").write_text(
        json.dumps([{"connection_id": str(identifier), "env_prefix": "ACME_BIND"}])
    )
    settings = script["source_settings"]()
    selected = script["source_registry"](context, settings)
    assert set(selected) == {identifier}
    assert selected[identifier]()["TOUR_ERP_PASSWORD"] == "ACME-environment-value"
    assert set(selected[identifier]()) == {
        "TOUR_ERP_BASE_URL",
        "TOUR_ERP_MOBILE",
        "TOUR_ERP_PASSWORD",
    }
    (local / "connections.json").write_text("[]")
    with pytest.raises(ValueError, match="供应商连接绑定无效"):
        script["source_registry"](context, settings)
    # An explicit override is authoritative; a blank path is not legacy fallback.
    monkeypatch.setenv("WAREHOUSE_CONNECTORS_CONFIG", "")
    with pytest.raises(ValueError, match="供应商连接绑定无效"):
        script["source_registry"](context, settings)
    monkeypatch.delenv("WAREHOUSE_CONNECTORS_CONFIG")
    (local / "connections.json").unlink()
    assert script["source_registry"](context, settings)[identifier]() == settings


@pytest.mark.parametrize("command", ["sync", "worker"])
def test_cli_binds_each_supplier_before_publishing(
    database, tenant, tmp_path, monkeypatch, capsys, command
):
    admin, runtime = database
    other = onboard(admin, "ACME Supplier B", "ACME Buyer B", f"{uuid4()}@acme.example")
    contexts = [
        (tenant.connection_id, tenant.worker, tenant.buyer, "SUPPLIER_A", 3),
        (
            UUID(other["connection_id"]),
            Principal(UUID(other["worker_id"]), UUID(other["supplier_id"])),
            # Worker is sufficient to verify its own scoped catalog, with no human identity.
            Principal(UUID(other["worker_id"]), UUID(other["supplier_id"])),
            "SUPPLIER_B",
            7,
        ),
    ]
    config = tmp_path / "connections.json"
    config.write_text(
        json.dumps([{"connection_id": str(row[0]), "env_prefix": row[3]} for row in contexts])
    )
    monkeypatch.setenv("WAREHOUSE_CONNECTORS_CONFIG", str(config))
    monkeypatch.setenv("WAREHOUSE_DATABASE_URL", "ACME-runtime")
    monkeypatch.setattr(cli, "engine_for", lambda value: runtime)
    # The CLI normally owns this engine; the fixture owns it for this test.
    monkeypatch.setattr(runtime, "dispose", lambda: None)
    seen = []

    @asynccontextmanager
    async def connect(settings):
        selected = settings["TOUR_ERP_PASSWORD"]
        seen.append(selected)
        prefix = selected.removeprefix("ACME-password-")
        seats = 3 if prefix == "SUPPLIER_A" else 7
        assert prefix in {"SUPPLIER_A", "SUPPLIER_B"}
        assert settings["TOUR_ERP_MOBILE"] == f"ACME-login-{prefix}"

        def handle(request):
            assert request.method == "GET"
            if request.url.path.endswith("/route/list"):
                rows = [{"routeId": 1, "routeCode": "ACME-R1", "routeName": f"ACME {prefix}"}]
            else:
                assert request.url.path.endswith("/period/list")
                rows = [{**Connector().data.departures[0], "availableSeats": seats}]
            return httpx.Response(
                200,
                json={"code": 200, "data": {"list": rows, "total": 1, "totalPages": 1}},
            )

        upstream = HttpErpClient.with_token(
            settings["TOUR_ERP_BASE_URL"],
            "ACME-token",
            datetime.now(UTC).timestamp() + 3600,
            {"companyId": 2},
            [],
            transport=httpx.MockTransport(handle),
        )
        try:
            yield warehouse_connector.TourWarehouseConnector(upstream)
        finally:
            await upstream.aclose()

    monkeypatch.setattr(warehouse_connector, "connect", connect)
    for identifier, worker, reader, prefix, seats in contexts:
        for source in ("SUPPLIER_A", "SUPPLIER_B"):
            for suffix in ("BASE_URL", "MOBILE", "PASSWORD"):
                monkeypatch.delenv(f"{source}_{suffix}", raising=False)
        monkeypatch.setenv(f"{prefix}_BASE_URL", "https://b2b.acme.example/aicli")
        monkeypatch.setenv(f"{prefix}_MOBILE", f"ACME-login-{prefix}")
        monkeypatch.setenv(f"{prefix}_PASSWORD", f"ACME-password-{prefix}")
        monkeypatch.setenv("TOUR_ERP_PASSWORD", "ACME-wrong-fallback")
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "warehouse",
                command,
                "--connection",
                str(identifier),
                "--organization",
                str(worker.organization_id),
                "--user",
                str(worker.user_id),
                *(["--once"] if command == "worker" else []),
            ],
        )
        cli.main()
        products = list_products(runtime, reader)
        assert len(products) == 1 and products[0]["name"] == f"ACME {prefix}"
        with transaction(runtime, reader) as conn:
            assert conn.execute(text("SELECT available_seats FROM departure")).scalars().all() == [
                seats
            ]
    assert seen == ["ACME-password-SUPPLIER_A", "ACME-password-SUPPLIER_B"]
    assert "ACME-password" not in capsys.readouterr().out


@pytest.mark.parametrize("fault", ["absent", "custom_factory", "empty_path"])
def test_cli_binding_failure_precedes_database_or_upstream_access(tmp_path, monkeypatch, fault):
    config = tmp_path / "connections.json"
    config.write_text("[]")
    monkeypatch.setenv("WAREHOUSE_CONNECTORS_CONFIG", "" if fault == "empty_path" else str(config))
    monkeypatch.setenv("TOUR_ERP_PASSWORD", "ACME-fallback-must-not-be-used")
    monkeypatch.setattr(cli, "engine_for", lambda _: pytest.fail("Database must not be accessed"))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "warehouse",
            "worker",
            "--connection",
            str(uuid4()),
            "--organization",
            str(uuid4()),
            "--user",
            str(uuid4()),
            "--once",
            *(["--factory", "ACME:forbidden"] if fault == "custom_factory" else []),
        ],
    )
    with pytest.raises(SystemExit) as caught:
        cli.main()
    assert caught.value.code != 0


async def test_production_factory_uploads_and_reads_private_documents(
    database, authentication, tenant, tmp_path, monkeypatch
):
    admin, runtime = database
    await synchronize(runtime, tenant.worker, tenant.connection_id, Connector())
    product = list_products(runtime, tenant.supplier)[0]
    email = f"{uuid4()}@acme.example"
    auth.create_user(admin, email, PASSWORD, {tenant.supplier.organization_id: ["supplier_admin"]})
    config = tmp_path / "connections.json"
    config.write_text("[]")
    monkeypatch.setenv("WAREHOUSE_CONNECTORS_CONFIG", str(config))
    monkeypatch.setenv("WAREHOUSE_OBJECT_ROOT", str(tmp_path / "objects"))
    monkeypatch.setenv("WAREHOUSE_DATABASE_URL", "ACME-runtime")
    monkeypatch.setenv("WAREHOUSE_AUTH_URL", "ACME-auth")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.setattr(
        warehouse_app,
        "engine_for",
        lambda value: runtime if value == "ACME-runtime" else authentication,
    )
    with TestClient(warehouse_app.application()) as client:
        token = client.post("/v1/auth/login", json={"email": email, "password": PASSWORD}).json()[
            "access_token"
        ]
        headers = {
            "Authorization": "Bearer " + token,
            "X-Organization-Id": str(tenant.supplier.organization_id),
            "X-File-Name": "ACME.docx",
        }
        body = docx()
        uploaded = client.post(
            f"/v1/documents?product_id={product['id']}", headers=headers, content=body
        )
        assert uploaded.status_code == 201
        asset = uploaded.json()["id"]
        assert client.get(f"/v1/documents/{asset}/file", headers=headers).content == body
        assert client.get(f"/v1/documents/{asset}/file").status_code == 401
    monkeypatch.delenv("WAREHOUSE_OBJECT_ROOT")
    with pytest.raises(KeyError, match="WAREHOUSE_OBJECT_ROOT"):
        warehouse_app.application()


def test_deployment_keeps_database_and_api_private_and_separates_worker_secrets():
    config = yaml.safe_load((ROOT / "cloud-warehouse/deploy/compose.yaml").read_text())
    services = config["services"]
    for name in (
        "database",
        "api",
        "catalog-worker",
        "outbox-worker",
        "attachment-worker",
        "document-worker",
        "admin",
    ):
        assert "ports" not in services[name]
    for name in ("advisor", "merchant"):
        assert all(port.startswith("127.0.0.1:") for port in services[name]["ports"])
        assert "env_file" not in services[name]
        assert all(mount.startswith("/") for mount in services[name]["tmpfs"])
    assert services["advisor"]["environment"]["TOUR_BACKEND_MODE"] == "warehouse"
    assert services["document-worker"]["volumes"] == [
        "${WAREHOUSE_DATA_DIR}/objects:/objects",
        "route-kit-cache:/route-kit-cache",
    ]
    assert (
        services["catalog-worker"]["environment"]["WAREHOUSE_CONNECTORS_CONFIG"]
        == (services["api"]["environment"]["WAREHOUSE_CONNECTORS_CONFIG"])
    )
    assert services["catalog-worker"]["volumes"] == [services["api"]["volumes"][0]]
    assert (
        len(
            {
                services[name]["env_file"][0]["path"]
                for name in (
                    "api",
                    "catalog-worker",
                    "outbox-worker",
                    "attachment-worker",
                    "document-worker",
                    "admin",
                )
            }
        )
        == 6
    )
    assert services["database"]["profiles"] == ["bundled-db"]
    assert all(
        services[name]["profiles"] == ["workers"]
        for name in ("catalog-worker", "outbox-worker", "attachment-worker", "document-worker")
    )
    assert services["admin"]["restart"] == "no"
    assert services["outbox-worker"]["networks"] == ["database"]
    assert "outbound" in services["admin"]["networks"]
    assert all(
        entry["format"] == "raw"
        for service in services.values()
        for entry in service.get("env_file", [])
    )
    assert all(mount.startswith("/") for mount in services["api"]["tmpfs"])


def test_monitoring_has_private_network_storage_and_no_application_secrets():
    config = yaml.safe_load((ROOT / "cloud-warehouse/deploy/compose.yaml").read_text())
    monitor = config["services"]["prometheus"]
    assert monitor["profiles"] == ["observability"]
    assert monitor["networks"] == ["monitoring"]
    assert config["networks"]["monitoring"]["internal"] is True
    assert "monitoring" in config["services"]["api"]["networks"]
    assert "ports" not in monitor and "env_file" not in monitor
    assert all("database" not in item and "objects" not in item for item in monitor["volumes"])
    assert "--web.enable-admin-api" not in monitor["command"]
    assert "--web.enable-lifecycle" not in monitor["command"]
    assert "--web.enable-remote-write-receiver" not in monitor["command"]
    assert any(item.endswith("/metrics:/prometheus") for item in monitor["volumes"])
    scrape = yaml.safe_load((ROOT / "cloud-warehouse/deploy/prometheus.yml").read_text())
    assert "remote_write" not in scrape and "alerting" not in scrape
    source = scrape["scrape_configs"][0]
    assert source["follow_redirects"] is False
    assert source["authorization"] == {
        "type": "Bearer",
        "credentials_file": "/run/secrets/warehouse_metrics_token",
    }

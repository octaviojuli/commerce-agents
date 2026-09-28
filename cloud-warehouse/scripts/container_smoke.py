"""Verify prebuilt Linux images in an isolated, disposable ACME Docker environment.

Uses no application configuration, publishes no ports and registers no real connector.
The daemon must have at least 4 GiB RAM. This is an image smoke test, not ECS acceptance.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
import time
from pathlib import Path
from uuid import uuid4

LABEL = "tour.warehouse.container-smoke"
PASSWORD = "ACME-container-fixture-only-42"
DATABASE = "warehouse_container_test"

BOOTSTRAP = r"""
import asyncio,json,os
from datetime import UTC,datetime,timedelta
from pathlib import Path
from uuid import UUID
from sqlalchemy import text
from cloud_warehouse.admin import migrate,grant_runtime,grant_auth,onboard
from cloud_warehouse.auth import create_user
from cloud_warehouse.catalog import synchronize
from cloud_warehouse.integrations import CatalogBatch
from cloud_warehouse.persistence import engine_for,Principal
password="ACME-container-fixture-only-42"
url="postgresql+psycopg://warehouse_admin:"+password+"@database/warehouse_container_test"
admin=engine_for(url)
assert admin.url.database=="warehouse_container_test" and admin.url.host=="database"
with admin.begin() as conn:
    for role in ("warehouse_runtime","warehouse_auth"):
        # All identifiers and passwords are fixed fictional fixture values.
        conn.execute(text("CREATE ROLE "+role+" LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD '"+password+"'"))
migrate(url)
grant_runtime(admin,"warehouse_runtime");grant_auth(admin,"warehouse_auth")
first=onboard(admin,"ACME Supplier","ACME Buyer","sync@acme.example")
other=onboard(admin,"ACME Other Supplier","ACME Other Buyer","other-sync@acme.example")
create_user(admin,"employee@acme.example",password,{
    UUID(first["supplier_id"]):["supplier_admin"],UUID(first["buyer_id"]):["advisor"],
    UUID(other["buyer_id"]):["advisor"]})
runtime=engine_for(url.replace("warehouse_admin:","warehouse_runtime:"))
class Source:
    async def read_catalog(self,start=None,end=None):
        day=(datetime.now(UTC)+timedelta(days=30)).date()
        return CatalogBatch([{"routeId":1,"routeCode":"ACME-R1","routeName":"ACME Linux route"}],
          [{"periodId":n,"routeId":1 if n==1 else 0,"periodCode":"ACME-D"+str(n),
            "departDate":str(day),"returnDate":str(day+timedelta(days=2)),"availableSeats":9}
           for n in (1,2)])
result=asyncio.run(synchronize(runtime,Principal(UUID(first["worker_id"]),UUID(first["supplier_id"])),UUID(first["connection_id"]),Source()))
Path("/config/connections.json").write_text("[]")
os.chown("/config/connections.json",10001,10001);os.chmod("/config/connections.json",0o600)
for folder in ("/objects","/config"):
    os.chown(folder,10001,10001);os.chmod(folder,0o700)
runtime.dispose();admin.dispose()
print(json.dumps({"supplier":first["supplier_id"],"buyer":first["buyer_id"],
                  "other_buyer":other["buyer_id"],"run":result["sync_run_id"]}))
"""

PROBE = r"""
import io,json,os,sys,urllib.request,urllib.error,zipfile
from zoneinfo import ZoneInfo
from tour.api.warehouse_documents import parse
assert os.getuid()==10001
ZoneInfo("Asia/Shanghai");ZoneInfo("America/New_York")
# Exercise the actual bounded subprocess, including its packaged normalization
# helpers and data files. Importing the API alone does not cover this boundary.
fixture=io.BytesIO()
with zipfile.ZipFile(fixture,"w") as archive:
    archive.writestr("[Content_Types].xml",'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
    archive.writestr("word/document.xml",'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>D1 ACME city</w:t></w:r></w:p><w:p><w:r><w:t>ACME fictional visit with explicit programme.</w:t></w:r></w:p></w:body></w:document>')
parsed=parse({"product_snapshot":{"external_id":"1","code":"ACME-R1","name":"ACME Linux route","days":1,"gateway":"ACME"},"file_name":"ACME.docx","file_hash":"fixture-only"},fixture.getvalue())
assert parsed.schema_version=="3.0" and len(parsed.days)==1 and parsed.days[0].blocks
state=json.load(sys.stdin)
def request(base,path,*,org=None,token=None,body=None,expected=200,extra=None):
    headers={**(extra or {})}
    if org: headers["X-Organization-Id"]=org
    if token: headers["Authorization"]="Bearer "+token
    if isinstance(body,dict):
        body=json.dumps(body).encode();headers["Content-Type"]="application/json"
    req=urllib.request.Request(base+path,data=body,headers=headers)
    try: response=urllib.request.urlopen(req,timeout=10)
    except urllib.error.HTTPError as error: response=error
    with response:
        assert response.status==expected,(path,response.status,expected)
        data=response.read()
        return data,response.headers
def data(base,path,**kwargs): return json.loads(request(base,path,**kwargs)[0])
api="http://127.0.0.1:8005"
request(api,"/v1/products",expected=401)
if "token" not in state:
    state["token"]=data(api,"/v1/auth/login",body={"email":"employee@acme.example","password":"ACME-container-fixture-only-42"})["access_token"]
token=state["token"]
catalog=data(api,"/v1/products",org=state["buyer"],token=token)["items"]
assert len(catalog)==1
product=catalog[0]["id"]
assert data(api,"/v1/products",org=state["other_buyer"],token=token)["items"]==[]
departures=data(api,"/v1/departures?product_id="+product,org=state["buyer"],token=token)["items"]
assert len(departures)==1
assert "available_seats" not in departures[0]
assert (departures[0]["availability_status"],departures[0]["availability"]) in (("known","available"),("stale","unknown"))
issues=data(api,"/v1/source-issues?run_id="+state["run"],org=state["supplier"],token=token)["items"]
assert len(issues)==1
detail="/v1/source-issues/"+issues[0]["id"]
assert data(api,detail,org=state["supplier"],token=token)["latest_successful_scan"]["state"]=="unlinked"
request(api,detail,org=state["buyer"],token=token,expected=403)
if "asset" not in state:
    output=io.BytesIO()
    with zipfile.ZipFile(output,"w") as archive:
        archive.writestr("[Content_Types].xml",'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        archive.writestr("word/document.xml",'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>ACME private document</w:t></w:r></w:p></w:body></w:document>')
    body=output.getvalue()
    state["asset"]=data(api,"/v1/documents?product_id="+product,org=state["supplier"],token=token,
        body=body,expected=201,extra={"X-File-Name":"ACME.docx","Content-Type":"application/octet-stream"})["id"]
    state["document_hex"]=body.hex()
file="/v1/documents/"+state["asset"]+"/file"
assert request(api,file,org=state["supplier"],token=token)[0].hex()==state["document_hex"]
request(api,file,expected=401)
request(api,file,org=state["other_buyer"],token=token,expected=403)
for host in ("advisor","merchant"):
    base="http://"+host+":3000"
    body,headers=request(base,"/")
    assert "text/html" in headers.get("Content-Type","") and body
    request(base,"/warehouse-api/v1/products",expected=401)
    body,headers=request(base,"/warehouse-api/v1/products",org=state["buyer"],token=token)
    assert json.loads(body)["items"][0]["id"]==product
    assert headers.get("Cache-Control")=="no-store" and headers.get("X-Request-ID")
    request(base,"/warehouse-api/metrics",expected=404)
print(json.dumps(state))
"""


class SmokeError(RuntimeError):
    pass


class Docker:
    def __init__(self):
        self.owner = uuid4().hex
        self.prefix = "warehouse-smoke-" + self.owner[:12]
        self.resources: list[tuple[str, str]] = []
        self.stage = "daemon_preflight"

    def call(self, *args, content=None, check=True, timeout=30):
        try:
            result = subprocess.run(
                ["docker", *args], input=content, capture_output=True, text=True, timeout=timeout
            )
        except FileNotFoundError:
            raise SmokeError(
                "Docker CLI is unavailable; no image verification was performed"
            ) from None
        if check and result.returncode:
            # Container output may include a generated session; never echo it on failure.
            raise SmokeError(f"Docker {args[0]} failed; exit {result.returncode}")
        return result

    def create(self, kind, suffix, *args):
        name = self.prefix + "-" + suffix
        self.resources.append((kind, name))
        label = f"{LABEL}={self.owner}"
        if kind == "container":
            self.call("run", "-d", "--name", name, "--label", label, *args)
        else:
            self.call(kind, "create", "--label", label, *args, name)
        return name

    def cleanup(self):
        failures = []
        for kind, name in reversed(self.resources):
            result = self.call(kind, "inspect", name, check=False)
            if result.returncode:
                # Missing resources are normal when creation failed. Verify absence
                # from an actual owned-resource listing; an inspect error alone is
                # not proof that the resource no longer exists.
                extra = ["--all"] if kind == "container" else []
                field = "{{.Names}}" if kind == "container" else "{{.Name}}"
                listing = self.call(
                    kind,
                    "ls",
                    *extra,
                    "--filter",
                    f"label={LABEL}={self.owner}",
                    "--format",
                    field,
                    check=False,
                )
                if listing.returncode or name in listing.stdout.splitlines():
                    failures.append(name)
                continue
            item = json.loads(result.stdout)[0]
            labels = (
                item.get("Config", {}).get("Labels") if kind == "container" else item.get("Labels")
            )
            if (labels or {}).get(LABEL) != self.owner:
                failures.append(name)
                continue
            args = ("rm", "--force", name) if kind == "container" else (kind, "rm", name)
            if self.call(*args, check=False).returncode:
                failures.append(name)
        if failures:
            raise SmokeError(
                "Some smoke resources could not be verified and removed: " + ",".join(failures)
            )

    def ready(self, name, *command):
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            state = json.loads(self.call("container", "inspect", name).stdout)[0]["State"]
            if not state["Running"]:
                raise SmokeError("Smoke container exited before readiness: " + name)
            if not self.call("exec", name, *command, check=False, timeout=15).returncode:
                return
            time.sleep(1)
        raise SmokeError("Readiness timed out for " + name)


def exercise(docker: Docker, images: dict[str, str]) -> dict:
    info = json.loads(docker.call("info", "--format", "{{json .}}").stdout)
    if info["OSType"] != "linux" or info["MemTotal"] < 4 * 1024**3:
        raise SmokeError("Use a dedicated Linux Docker daemon with at least 4 GiB RAM")
    inspected = {}
    docker.stage = "image_platforms"
    for role, tag in images.items():
        item = json.loads(docker.call("image", "inspect", tag).stdout)[0]
        if item["Os"] != "linux" or item["Architecture"] != "amd64":
            raise SmokeError("ECS smoke images must target linux/amd64")
        inspected[role] = item["Id"]
    docker.stage = "isolated_database"
    network = docker.create("network", "network", "--internal")
    objects = docker.create("volume", "objects")
    config = docker.create("volume", "config")
    database = docker.create(
        "container",
        "database",
        "--network",
        network,
        "--network-alias",
        "database",
        "--memory",
        "512m",
        "--shm-size",
        "128m",
        "--tmpfs",
        "/var/lib/postgresql/data",
        "-e",
        "POSTGRES_USER=warehouse_admin",
        "-e",
        "POSTGRES_DB=" + DATABASE,
        "-e",
        "POSTGRES_PASSWORD=" + PASSWORD,
        "postgres:16-bookworm",
    )
    docker.ready(database, "pg_isready", "-U", "warehouse_admin", "-d", DATABASE)
    docker.stage = "migrations_and_fixture"
    bootstrap = docker.create(
        "container",
        "bootstrap",
        "--network",
        network,
        "--user",
        "0:0",
        "--memory",
        "768m",
        "-v",
        objects + ":/objects",
        "-v",
        config + ":/config",
        "--entrypoint",
        "python",
        images["api"],
        "-c",
        BOOTSTRAP,
    )
    code = docker.call("wait", bootstrap, timeout=120).stdout.strip()
    if code != "0":
        raise SmokeError("Image bootstrap, migrations or fictional seeding failed")
    fixture = json.loads(docker.call("logs", bootstrap).stdout)
    docker.stage = "api_startup"
    with tempfile.TemporaryDirectory(prefix="warehouse-image-smoke-") as folder:
        env = Path(folder) / "api.env"
        env.write_text(
            "\n".join(
                [
                    "WAREHOUSE_DATABASE_URL=postgresql+psycopg://warehouse_runtime:"
                    + PASSWORD
                    + "@database/"
                    + DATABASE,
                    "WAREHOUSE_AUTH_URL=postgresql+psycopg://warehouse_auth:"
                    + PASSWORD
                    + "@database/"
                    + DATABASE,
                    "WAREHOUSE_CONNECTORS_CONFIG=/config/connections.json",
                    "WAREHOUSE_OBJECT_ROOT=/objects",
                ]
            )
            + "\n"
        )
        env.chmod(0o600)
        api = docker.create(
            "container",
            "api",
            "--network",
            network,
            "--network-alias",
            "api",
            "--memory",
            "768m",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--tmpfs",
            "/tmp:uid=10001,gid=10001,mode=0700,size=256m",
            "--env-file",
            str(env),
            "-v",
            objects + ":/objects",
            "-v",
            config + ":/config:ro",
            images["api"],
        )
    health = (
        "python",
        "-c",
        "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8005/health',timeout=3)",
    )
    docker.ready(api, *health)
    docker.call("exec", api, "pdftotext", "-v")
    for role, app in (("advisor", "storefront-web"), ("merchant", "merchant-web")):
        docker.stage = role + "_startup"
        web = docker.create(
            "container",
            role,
            "--network",
            network,
            "--network-alias",
            role,
            "--memory",
            "384m",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--tmpfs",
            "/tmp:uid=1000,gid=1000,mode=0700,size=32m",
            "--tmpfs",
            f"/app/tour/{app}/.next/cache:uid=1000,gid=1000,mode=0700,size=32m",
            "-e",
            "WAREHOUSE_API_INTERNAL_URL=http://api:8005",
            "-e",
            "TOUR_BACKEND_MODE=warehouse",
            "-e",
            "NODE_OPTIONS=--max-old-space-size=192",
            images[role],
        )
        docker.ready(
            web,
            "node",
            "-e",
            "fetch('http://127.0.0.1:3000/').then(r=>{if(!r.ok)process.exit(1)}).catch(()=>process.exit(1))",
        )
        docker.call("exec", web, "node", "-e", "if(process.getuid()!==1000)process.exit(1)")
    docker.stage = "authenticated_http_and_proxies"
    first = docker.call(
        "exec", "-i", api, "python", "-c", PROBE, content=json.dumps(fixture), timeout=60
    )
    session = json.loads(first.stdout)
    docker.stage = "api_restart_persistence"
    docker.call("restart", api)
    docker.ready(api, *health)
    second = docker.call(
        "exec", "-i", api, "python", "-c", PROBE, content=json.dumps(session), timeout=60
    )
    assert json.loads(second.stdout) == session
    return {
        "passed": True,
        "image_ids": inspected,
        "platform": "linux/amd64",
        "checks": [
            "migration",
            "platform_login",
            "organization_isolation",
            "catalog_and_quarantine",
            "private_document",
            "web_proxies",
            "session_and_object_restart",
            "non_root",
            "timezone",
            "pdf_tool",
            "document_parser",
        ],
        "external_connectors": 0,
        "published_ports": 0,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for role in ("api", "advisor", "merchant"):
        parser.add_argument(f"--{role}-image", required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    docker = Docker()
    report: dict = {"passed": False}
    try:
        report = exercise(
            docker,
            {role: getattr(args, role + "_image") for role in ("api", "advisor", "merchant")},
        )
    except (
        SmokeError,
        subprocess.SubprocessError,
        OSError,
        ValueError,
        KeyError,
        AssertionError,
    ) as error:
        report["failure"] = str(error) if isinstance(error, SmokeError) else type(error).__name__
        report["stage"] = docker.stage
    finally:
        try:
            docker.cleanup()
            report["cleanup_complete"] = True
        except (SmokeError, subprocess.SubprocessError, OSError, ValueError) as error:
            report.update(
                passed=False,
                cleanup_complete=False,
                cleanup_error=str(error) if isinstance(error, SmokeError) else type(error).__name__,
                resource_label=f"{LABEL}={docker.owner}",
            )
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()

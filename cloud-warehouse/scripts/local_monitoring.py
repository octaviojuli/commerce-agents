"""Prepare and run a loopback-only Prometheus using verified, separately installed tools."""

import argparse
import json
import os
import platform
import subprocess
from pathlib import Path
from uuid import uuid4

import yaml

from cloud_warehouse.http_metrics import TOKEN_PATTERN

ROOT = Path(__file__).resolve().parents[2]
VERSION = "3.14.0"
STATE = ROOT / ".warehouse" / "monitoring"


def binary(name: str) -> Path:
    architecture = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "amd64"}.get(platform.machine())
    if not architecture or platform.system().lower() not in {"darwin", "linux"}:
        raise ValueError("Unsupported local monitoring platform")
    path = (
        ROOT
        / ".warehouse/tools"
        / f"prometheus-{VERSION}.{platform.system().lower()}-{architecture}"
        / name
    )
    result = subprocess.run([path, "--version"], capture_output=True, text=True, check=True)
    if not (result.stdout + result.stderr).startswith(f"{name}, version {VERSION} "):
        raise ValueError("Unverified monitoring tool version")
    return path


def private_write(path: Path, value: str) -> None:
    if path.is_symlink():
        raise ValueError("Refuse a linked monitoring file")
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    with temporary.open("x", encoding="utf-8") as stream:
        os.chmod(temporary, 0o600)
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def prepare() -> None:
    for directory in (STATE, STATE / "data"):
        if directory.is_symlink():
            raise ValueError("Refuse linked monitoring storage")
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(directory, 0o700)
    config = json.loads((ROOT / ".warehouse/database.json").read_text())
    token = config["metrics_token"]
    if not TOKEN_PATTERN.fullmatch(token):
        raise ValueError("Missing valid API metrics credential")
    template = yaml.safe_load((ROOT / "cloud-warehouse/deploy/prometheus.yml").read_text())
    source = template["scrape_configs"][0]
    source["authorization"]["credentials_file"] = str(STATE / "metrics-token")
    source["static_configs"] = [{"targets": ["127.0.0.1:8005"]}]
    template["rule_files"] = [str(ROOT / "cloud-warehouse/deploy/warehouse-rules.yml")]
    private_write(STATE / "metrics-token", token + "\n")
    private_write(
        STATE / "prometheus.yml", yaml.safe_dump(template, allow_unicode=True, sort_keys=False)
    )
    subprocess.run([binary("promtool"), "check", "config", STATE / "prometheus.yml"], check=True)


def serve() -> None:
    # Explicit prepare refreshes the owned configuration and copied token before startup.
    # The TSDB directory is preserved; Prometheus itself owns its exclusive lock.
    prepare()
    executable = binary("prometheus")
    args = [
        str(executable),
        f"--config.file={STATE / 'prometheus.yml'}",
        f"--storage.tsdb.path={STATE / 'data'}",
        "--storage.tsdb.retention.time=7d",
        "--storage.tsdb.retention.size=2GB",
        "--web.listen-address=127.0.0.1:9096",
        "--query.max-concurrency=4",
        "--query.timeout=15s",
        "--query.max-samples=500000",
    ]
    environment = {
        key: os.environ[key] for key in ("PATH", "TMPDIR", "LANG", "TZ") if key in os.environ
    }
    environment["GOMEMLIMIT"] = "384MiB"
    os.execve(executable, args, environment)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "serve"))
    args = parser.parse_args()
    os.umask(0o077)
    if args.command == "prepare":
        prepare()
    else:
        serve()


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError, subprocess.SubprocessError):
        raise SystemExit("LOCAL_MONITORING_SETUP_FAILED") from None

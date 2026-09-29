import json
import runpy
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = runpy.run_path(str(ROOT / "cloud-warehouse/scripts/local_monitoring.py"))
TOKEN = "e2a4925fc72189a31d5e900d0a3e10da46e3f7bfa432def30210c9cb0cbd2637"


@pytest.fixture
def local(tmp_path, monkeypatch):
    state = tmp_path / ".warehouse"
    state.mkdir()
    (state / "database.json").write_text(
        json.dumps(
            {
                "metrics_token": TOKEN,
                "admin_url": "ACME-private-database-secret",
            }
        )
    )
    deploy = tmp_path / "cloud-warehouse/deploy"
    deploy.mkdir(parents=True)
    (deploy / "prometheus.yml").write_text(
        (ROOT / "cloud-warehouse/deploy/prometheus.yml").read_text()
    )
    namespace = SCRIPT["prepare"].__globals__
    monkeypatch.setitem(namespace, "ROOT", tmp_path)
    monkeypatch.setitem(namespace, "STATE", state / "monitoring")
    monkeypatch.setitem(namespace, "binary", lambda name: Path("/ACME-tools") / name)
    calls = []
    monkeypatch.setattr(namespace["subprocess"], "run", lambda args, **kwargs: calls.append(args))
    return state, calls


def test_local_preparation_copies_only_metrics_secret_and_keeps_storage(local):
    state, calls = local
    SCRIPT["prepare"]()
    directory = state / "monitoring"
    (directory / "data/ACME-existing-history").write_text("preserve")
    SCRIPT["prepare"]()
    configuration = directory / "prometheus.yml"
    contents = configuration.read_text()
    assert TOKEN not in contents and "database-secret" not in contents
    source = yaml.safe_load(contents)["scrape_configs"][0]
    assert source["static_configs"] == [{"targets": ["127.0.0.1:8005"]}]
    token_path = Path(source["authorization"]["credentials_file"])
    assert token_path.read_text().strip() == TOKEN
    assert token_path.stat().st_mode & 0o777 == 0o600
    assert configuration.stat().st_mode & 0o777 == 0o600
    assert (directory / "data").stat().st_mode & 0o777 == 0o700
    assert (directory / "data/ACME-existing-history").read_text() == "preserve"
    assert len(calls) == 2 and all(str(call[0]) == "/ACME-tools/promtool" for call in calls)


def test_local_preparation_refuses_linked_monitoring_directory(local, tmp_path):
    state, calls = local
    outside = tmp_path / "ACME-outside"
    outside.mkdir()
    (state / "monitoring").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="linked"):
        SCRIPT["prepare"]()
    assert not list(outside.iterdir()) and not calls

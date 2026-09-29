"""Resource safety for the disposable image verifier; no Docker daemon is contacted."""

import json
import runpy
import subprocess
from pathlib import Path

import pytest

SMOKE = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/container_smoke.py"))


def result(data=None, code=0):
    return subprocess.CompletedProcess([], code, json.dumps(data) if data is not None else "", "")


def test_cleanup_never_removes_a_resource_with_different_ownership(monkeypatch):
    docker = SMOKE["Docker"]()
    docker.resources = [("network", "foreign"), ("volume", "own"), ("container", "own")]
    calls = []

    def call(*args, **kwargs):
        calls.append(args)
        if args[1] == "inspect":
            labels = {SMOKE["LABEL"]: "another-task" if args[2] == "foreign" else docker.owner}
            return result(
                [{"Config": {"Labels": labels}}] if args[0] == "container" else [{"Labels": labels}]
            )
        return result()

    monkeypatch.setattr(docker, "call", call)
    with pytest.raises(SMOKE["SmokeError"], match="foreign"):
        docker.cleanup()
    assert ("rm", "--force", "own") in calls
    assert ("volume", "rm", "own") in calls
    assert ("network", "rm", "foreign") not in calls


def test_cleanup_does_not_mistake_unreachable_daemon_for_removed_resources(monkeypatch):
    docker = SMOKE["Docker"]()
    docker.resources = [("container", "unconfirmed")]
    monkeypatch.setattr(docker, "call", lambda *args, **kwargs: result(code=1))
    with pytest.raises(SMOKE["SmokeError"], match="unconfirmed"):
        docker.cleanup()


@pytest.mark.parametrize("listed", [True, False])
def test_inspect_failure_needs_confirmed_absence_from_owned_resources(monkeypatch, listed):
    docker = SMOKE["Docker"]()
    docker.resources = [("container", "own")]

    def call(*args, **kwargs):
        if args[1] == "inspect":
            return result(code=1)
        assert args[:2] == ("container", "ls")
        assert f"label={SMOKE['LABEL']}={docker.owner}" in args
        return subprocess.CompletedProcess([], 0, "own\n" if listed else "", "")

    monkeypatch.setattr(docker, "call", call)
    if listed:
        with pytest.raises(SMOKE["SmokeError"], match="own"):
            docker.cleanup()
    else:
        docker.cleanup()


def test_shared_small_daemon_is_rejected_before_resource_creation(monkeypatch):
    docker = SMOKE["Docker"]()
    calls = []

    def call(*args, **kwargs):
        calls.append(args)
        return result({"OSType": "linux", "MemTotal": 1609 * 1024**2})

    monkeypatch.setattr(docker, "call", call)
    with pytest.raises(SMOKE["SmokeError"], match="4 GiB"):
        SMOKE["exercise"](docker, {"api": "unused"})
    assert not docker.resources and len(calls) == 1


def test_failed_creation_can_only_leave_a_resource_with_this_runs_label(monkeypatch):
    docker = SMOKE["Docker"]()
    calls = []

    def call(*args, **kwargs):
        calls.append(args)
        if args[0] == "run":
            assert f"{SMOKE['LABEL']}={docker.owner}" in args
            raise SMOKE["SmokeError"]("create failed after resource allocation")
        if args[1] == "inspect":
            return result([{"Config": {"Labels": {SMOKE["LABEL"]: docker.owner}}}])
        return result()

    monkeypatch.setattr(docker, "call", call)
    with pytest.raises(SMOKE["SmokeError"], match="create failed"):
        docker.create("container", "api", "ACME-image")
    docker.cleanup()
    assert calls[-1] == ("rm", "--force", docker.prefix + "-api")

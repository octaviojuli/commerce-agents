"""Warehouse API composition with explicitly configured B2B connection-to-secret bindings."""

import os
from pathlib import Path

from cloud_warehouse.api import create_app
from cloud_warehouse.assets import LocalObjectStore
from cloud_warehouse.http_observation import configure_logging
from cloud_warehouse.persistence import engine_for

from .warehouse_chat import factory
from .warehouse_connector import connector_registry


def application():
    configure_logging()
    # The full Tour composition includes document upload/download and shares this
    # private volume with attachment and parsing workers. Fail before serving if absent.
    object_store = LocalObjectStore(Path(os.environ["WAREHOUSE_OBJECT_ROOT"]))
    runtime = engine_for(os.environ["WAREHOUSE_DATABASE_URL"])
    connectors = connector_registry(os.environ["WAREHOUSE_CONNECTORS_CONFIG"])
    return create_app(
        runtime,
        engine_for(os.environ["WAREHOUSE_AUTH_URL"]),
        connectors,
        metrics_token=os.environ.get("WAREHOUSE_METRICS_TOKEN"),
        object_store=object_store,
        agent_factory=factory(runtime, connectors)
        if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
        else None,
    )

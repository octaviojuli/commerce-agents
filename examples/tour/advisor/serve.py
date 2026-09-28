"""Run the advisor service: ``python -m tour.advisor.serve`` from ``examples/``.

Configuration comes from the environment:
``ADVISOR_DATABASE_URL`` (PostgreSQL), ``WAREHOUSE_URL`` (the warehouse API root, before ``/v1``),
``ADVISOR_MATERIAL_DIR`` and ``ADVISOR_MATERIAL_KEY`` (base64, 32 bytes) for document scans,
``ADVISOR_PORT`` (default 8006) and the model variables read by ``model.TypedModel``.
"""

import logging
import os
from pathlib import Path

import uvicorn

from .api import Settings, create_app


def settings_from_env() -> Settings:
    return Settings(
        database_url=os.environ["ADVISOR_DATABASE_URL"],
        warehouse_url=os.environ["WAREHOUSE_URL"],
        material_dir=Path(os.environ.get("ADVISOR_MATERIAL_DIR", ".advisor-materials")),
        warehouse_verify=os.environ.get("WAREHOUSE_VERIFY_TLS", "1") != "0",
        secure_cookie=os.environ.get("ADVISOR_SECURE_COOKIE", "0") == "1",
    )


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    app = create_app(settings_from_env())
    uvicorn.run(
        app,
        host=os.environ.get("ADVISOR_HOST", "127.0.0.1"),
        port=int(os.environ.get("ADVISOR_PORT", "8006")),
        access_log=False,
    )


if __name__ == "__main__":
    main()

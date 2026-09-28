"""Export the inactive phase-two contract bundle for review, or check it for drift."""

import argparse
import json
from pathlib import Path

from cloud_warehouse.order_contracts import schemas

DESTINATION = Path(__file__).resolve().parents[2] / "docs/cloud-warehouse/order-contracts.json"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    rendered = json.dumps(schemas(), ensure_ascii=False, indent=2) + "\n"
    if args.check:
        if not DESTINATION.exists() or DESTINATION.read_text() != rendered:
            raise SystemExit("Order contract artifact is stale; run export_order_contracts.py")
        print("Order contract artifact matches code; phase-one transactions remain disabled")
    else:
        DESTINATION.write_text(rendered)
        print(DESTINATION)


if __name__ == "__main__":
    main()
